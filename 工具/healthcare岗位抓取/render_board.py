#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""healthcare 岗位抓取 —— 生成本地可视化岗位面板（2026-09-29 本人要求）。

用法：
    python3 render_board.py [--batch-dir <批次目录>] [--open]

读（与 render_progress.py 同一套数据，面板只是另一种呈现）：
    <批次>/数据/终评_N岗.json（最新）      档位、应届资格、判读、漏斗、成本档
    out/jobs_all.json                       地点、薪资、发布日、JD（解析截止日）
    <批次>/数据/追踪/closed.json            已下线
    <批次>/数据/追踪/delta_*.json（最新）   本次新增
    <批次>/数据/申请链接核验.json           申请链接实测
    工具/求职进度面板/applications.json     投递状态以它为准（见 app_status.py）
    求职机会/科技公司-医疗AI/投递状态.json    只补 applications.json 没有的
写：
    工具/healthcare岗位抓取/board.html      自包含单文件，不联网、不上传、不发布（vault 隐私规则）
    工具/healthcare岗位抓取/pool_snapshot.json  入围岗的小快照，求职进度面板的「医疗 AI 扫描池」读它

render_progress.py 结束时会自动调用本脚本，所以日常不用单独跑。
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import urllib.parse
from datetime import date, datetime

from common import OUT_DIR, TOOL_DIR, read_json
from apply_links import apply_url
from render_notes import note_basename
import app_status
import parse_jd
import visa_signals

if app_status.PANEL_DIR not in sys.path:
    sys.path.insert(0, app_status.PANEL_DIR)
import opportunity_updates

VAULT = os.path.dirname(os.path.dirname(TOOL_DIR))
CAT_REL = "求职机会/科技公司-医疗AI"
BATCH_REL = "科技公司-医疗AI/Healthcare-AI-扫描-2026-09"
TEMPLATE = os.path.join(TOOL_DIR, "board_template.html")
OUT = os.path.join(TOOL_DIR, "board.html")
SNAPSHOT = os.path.join(TOOL_DIR, "pool_snapshot.json")  # 求职进度面板读它，见 snapshot()
OBSIDIAN_VAULT = "Work"
TIERS = ("🥇 投", "🟢 投", "🟡 条件投", "stretch")
TRACKS = {"A": "科技大厂 health 组", "B": "医疗 AI 原生公司", "C": "影像·器械·药企", "D": "传统医疗公司"}
# 统一求职面板（2026-10-06 本人要求两个面板合一）：本脚本只做重计算并写 POOL_BOARD，页面由 求职进度面板/make_dashboard.py 生成
MANUAL_TRACK = ("M", "手工调研（内推帖 / 本人贴链接）")
TARGETS = os.path.join(app_status.PANEL_DIR, "targets.json")        # 手工调研、还没发出的岗位
PENDING_AUDIT = os.path.join(app_status.PANEL_DIR, "pending_availability_audit.json")  # 仅待投岗的官方在招核验
PRIORITY_REVIEW = os.path.join(app_status.PANEL_DIR, "priority_review.json")
POOL_BOARD = os.path.join(TOOL_DIR, "pool_board.json")              # 扫描池卡片的完整数据（静态属性 + 渲染时的状态）
DASHBOARD = os.path.join(app_status.PANEL_DIR, "dashboard.html")
# 口径同 00_岗位总览：研究类岗用本批 申请材料/ 的通用 healthcare 研究模板，工程类岗从 resume_cc_short.tex 派生
# 岗位类型（2026-10-05 本人要求按 RS / MLE / SDE / Postdoc 分类展示）。顺序就是面板里的显示顺序。
ROLES = {"rs": "研究科学家 RS", "postdoc": "博后 / Fellow", "mle": "机器学习工程 MLE", "re": "研究工程师 RE",
         "ds": "数据科学 DS", "sde": "软件工程 SDE", "fde": "解决方案 / FDE", "other": "其他"}
ROLE_OF_FAMILY = {"RESEARCH_SCI": "rs", "APPLIED_SCI": "rs", "RESEARCH_ENG": "re", "MLE": "mle", "AI_ENG": "mle",
                  "SDE": "sde", "SDE_ML": "sde", "DATA_SCI": "ds", "SA_HEALTH": "fde"}
POSTDOC = re.compile(r"post[- ]?doc|postdoctoral|\bfellow(?:ship)?\b", re.I)
FDE_TITLE = re.compile(r"forward deploy", re.I)   # parse_jd 只认 "forward deployed"，"Forward Deployment AI Engineer" 会落到 AI_ENG
SWE_TITLE = re.compile(r"full[- ]?stack|front[- ]?end|back[- ]?end|site reliability|\bsre\b|data engineer|platform engineer"
                       r"|infrastructure|devops|software (?:development )?engineer|software developer", re.I)
AI_TITLE = re.compile(r"\b(?:AI|ML|machine learning|gen ?ai|generative|agentic|LLMs?|deep learning|computer vision|NLP)\b", re.I)
BASES = {"short": "工程版（从 resume_cc_short 派生）", "research": "研究版（本批 申请材料/ 的通用 healthcare 研究简历）"}
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
# 同档内优先级（2026-09-30 本人定：票数与签证信号只用于显示和同档内排序，不改档位）。
# 旧启发式辅助分，未校准、非百分制；手工/扫描来源缺项不同，不可横比。
# 当前推荐优先读 priority_review.rank；旧分仅作后续平分兜底，构成保留在详情。
PRI = {
    "elig": {"ELIGIBLE_NEW_GRAD": 30, "ELIGIBLE_PHD_COUNTS": 25, "CONDITIONAL_RESEARCH_YEARS": 10, "UNCLEAR": 5},
    "tix": {4: 20, 3: 14, 2: 8, 1: 3, 0: 0}, "tix_unknown": 8,   # 按建议期望薪资能到的档
    "dfw": 15, "remote": 15, "remote_state": 8, "tx": 10,       # 不搬家 > 德州内 > 其他
    "stem_no": -15, "h1b_freeze": -15, "h1b_known": 3,          # 雇主政策登记表
    "lca_cap5": 8, "lca_cap1": 5, "lca_any": 2,                 # 雇主 LCA：cap 季新雇 ≥5 / ≥1 / 有申报
    "jd_no_sponsor": -20, "jd_not_guaranteed": -3,              # JD 原文措辞
    "fresh30": 8, "fresh90": 4, "stale365": -8,                 # 挂牌天数
    "due7": 6,                                                  # 7 天内截止
    "coverage": 10, "core": 6,                                  # coverage × 10；有纯核心匹配 +6
    "same_co_applied": -4, "same_co_cap": 3,                    # 同公司每多一条已投 −4，最多算 3 条
}
VISA_WORDING = {"NO_SPONSOR": "JD 写明不 sponsor", "AUTH_NO_SPONSOR": "JD 要求无需 sponsorship", "NO_STEM_OPT": "不参加 STEM OPT",
                "SPONSOR_NOT_GUARANTEED": "JD 写 sponsorship 不保证"}
DFW = re.compile(r"Dallas|Irving|Plano|Frisco|Fort Worth|Arlington, TX|Richardson|Carrollton|Grapevine|Addison", re.I)
TX = re.compile(r"\bTX\b|Texas|Houston|Austin|San Antonio", re.I)


def obsidian_url(path):
    rel = os.path.relpath(path, VAULT)
    rel = rel[:-3] if rel.endswith(".md") else rel
    return "obsidian://open?" + urllib.parse.urlencode({"vault": OBSIDIAN_VAULT, "file": rel}, quote_via=urllib.parse.quote)


def parse_date(seg):
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", seg)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            return None
    m = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", seg, re.I)
    if m:
        try:
            return date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2))).isoformat()
        except ValueError:
            return None
    return None


def pay_bounds(text):
    """从 "$71,992.20-$84,220.50 / year"、"156,190.05USD -211,315.95 USD" 一类文字里取上下限；时薪按 ×2080 折年。"""
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{2,}(?:\.\d+)?", text or "")]
    hourly = re.search(r"hour|/\s*hr\b", text or "", re.I)
    nums = [n * 2080 if hourly and n < 1000 else n for n in nums]
    nums = [n for n in nums if 20000 <= n <= 1000000]
    return (min(nums[:2]), max(nums[:2])) if len(nums) >= 2 else (None, None)


def deadline(jd):
    """只认 JD 里写明的具体日期；"没有截止日""至少挂 2 个工作日"一律不算。"""
    t = jd or ""
    if re.search(r"does not have an application deadline", t, re.I):
        return None
    for m in re.finditer(r"(deadline|close[sd]? on|closing date|accept(?:ing)? applications until|apply by|posting (?:will )?(?:close|end)s?)[^\n]{0,90}", t, re.I):
        d = parse_date(m.group(0))
        if d:
            return d
    return None


def priority(item, j, today):
    """同档内优先级分 + 逐项理由（只列非零项）。同公司已投的扣分在 apply_same_company() 里另算。"""
    P, why, score = PRI, [], 0.0

    def add(v, label):
        nonlocal score
        if v:
            score += v
            why.append(f"{label} {v:+g}")
    add(P["elig"].get(item["elig"], 0), "应届资格")
    w = item.get("wage") or {}
    sg = w.get("suggest")
    pol = item.get("emp_policy") or {}
    if pol.get("h1b") == "cap-exempt":
        add(P["tix"][3], "cap-exempt 免抽签")
    else:
        add(P["tix"].get(sg["level"], 0) if sg else P["tix_unknown"], f"票数 L{sg['level']}" if sg else "票数未知")
    if item["dfw"]:
        add(P["dfw"], "DFW")
    elif w.get("remote_home"):
        add(P["remote_state"] if w.get("note") else P["remote"], "远程" + ("（限定州）" if w.get("note") else ""))
    elif item["tx"]:
        add(P["tx"], "德州")
    lca = item.get("emp_lca") or {}
    if pol.get("stem_opt") == "no":
        add(P["stem_no"], "雇主不做 STEM OPT")
    if pol.get("h1b") == "freeze":
        add(P["h1b_freeze"], "新 H-1B 冻结")
    elif pol.get("h1b") in ("yes", "cap-exempt"):
        add(P["h1b_known"], "H-1B 已核实")
    nc = lca.get("n_new_cap", 0)
    add(P["lca_cap5"] if nc >= 5 else P["lca_cap1"] if nc >= 1 else P["lca_any"] if lca.get("n_h1b") else 0,
        f"LCA cap 季新雇 {nc}" if nc else "有 LCA")
    fl = item.get("jd_visa") or []
    if "NO_SPONSOR" in fl or "AUTH_NO_SPONSOR" in fl:
        add(P["jd_no_sponsor"], "JD 不 sponsor 措辞")
    elif "SPONSOR_NOT_GUARANTEED" in fl:
        add(P["jd_not_guaranteed"], "JD 不保证 sponsor")
    try:
        age = (today - date.fromisoformat((item.get("posted") or "")[:10])).days
    except ValueError:
        age = None
    if age is not None:
        add(P["fresh30"] if age <= 30 else P["fresh90"] if age <= 90 else P["stale365"] if age > 365 else 0, f"挂牌 {age} 天")
    if item.get("deadline"):
        left = (date.fromisoformat(item["deadline"]) - today).days
        if 0 <= left <= 7:
            add(P["due7"], f"{left} 天内截止")
    sc = j.get("standout_coverage") or {}
    n = sc.get("n_items") or 0
    if n:
        add(round(P["coverage"] * ((sc.get("n_have") or 0) + 0.5 * (sc.get("n_partial") or 0)) / n, 1), "coverage")
    fit = j.get("domain_fit") or {}
    if (fit.get("status") in ("DIRECT", "GENERAL_TECH") and fit.get("candidate_evidence_ids")
            and fit.get("required_domain_status") != "UNMET"
            and any(t.get("overlap") == "CORE_MATCH" and t.get("candidate_strength") == "STRONG"
                    and t.get("jd_weight") == "CORE_TO_JD"
                    for t in (j.get("domain_overlap") or {}).get("tags", []))):
        add(P["core"], "核心领域匹配" if fit["status"] == "DIRECT" else "核心通用技术匹配")
    return round(score, 1), why


def apply_priority_review(items, review=None):
    """行动顺序独立于旧匹配档位与工资/地点参考分，不改投递事实。"""
    review = review if review is not None else read_json(PRIORITY_REVIEW, {}) or {}
    for x in items:
        entry = (review.get("items") or {}).get(x.get("uid"))
        if x.get("status") in ("pending", "todo") and entry:
            x["priority_review"] = dict(entry, reviewed_at=entry.get("checked_at") or review.get("as_of_date", ""))


def recommendation_key(x):
    return ((x.get("priority_review") or {}).get("rank", 999999),
            TIERS.index(x["tier"]) if x.get("tier") in TIERS else 99,
            -x.get("priority", 0), x.get("company", ""))


def apply_same_company(items, key_status="status"):
    """同公司已投过的，每条扣 PRI['same_co_applied']（最多算 same_co_cap 条）。原地改 priority / pri_why。"""
    from collections import Counter
    sent = Counter(x["company"] for x in items if x.get(key_status) in ("applied", "rejected"))
    for x in items:
        n = min(sent.get(x["company"], 0), PRI["same_co_cap"])
        x["priority"] = round(x["pri_base"] + n * PRI["same_co_applied"], 1)
        x["pri_why"] = x["pri_why_base"] + ([f"同公司已投 {sent[x['company']]} 条 {n * PRI['same_co_applied']:+g}"] if n else [])


def latest(pattern, key=os.path.getmtime):
    ff = glob.glob(pattern)
    return max(ff, key=key) if ff else None


def status_of(apps, bst, closed, uid, company, req):
    """投递状态：applications.json 优先，投递状态.json 补它没有的手记，closed.json 标下线。返回 (status, label, date, note)。"""
    status, status_date, status_note, status_label = "pending", "", "", ""
    a = app_status.match(apps, company, req)
    if a or uid in bst:
        if a:
            status_label, status_date, status_note = a["label"], a["date"], app_status.note_of(a)
        else:
            s = bst[uid]
            status_label, status_date, status_note = s.get("status", ""), s.get("date", ""), s.get("note", "")
        status = ("applied" if status_label in app_status.ACTIVE or status_label == "面试中"
                  else "rejected" if status_label in ("已拒", "已撤回") else "closed")
        if uid in closed:
            status_note = (status_note + "；" if status_note else "") + f"原帖 {closed[uid].get('date', '')} 已下线"
    elif uid in closed:
        status, status_date, status_label = "closed", closed[uid].get("date", ""), "已下线"
        status_note = f"判已下线：{closed[uid].get('reason', '')}"
    return status, status_label, status_date, status_note


def application_history(apps, items):
    """只读展示曾提交/收件的记录；不把仅内推、拒信或关闭反推成提交。"""
    history = []
    for a in apps:
        submission = a.get("submission")
        if not submission:
            continue
        history.append({
            "history_id": a.get("id"), "company": a.get("company", ""), "req": a.get("req", ""),
            "title": a.get("title") or a.get("req") or "职位名称未记录",
            "loc": a.get("location") or "", "role": role_of(a.get("title"), None, None),
            "applied_date": submission.get("applied_date"), "receipt_date": submission.get("receipt_date"),
            "submission_label": submission["label"], "submission_basis": submission["basis"],
            "status_label": a.get("label") or "", "status_date": a.get("date") or "",
            "status_time": a.get("time") or "", "outcome": a.get("outcome"), "last_any": a.get("last_any"),
            "note": app_status.note_of(a), "note_url": a.get("note_url"),
        })
    for x in items:
        if (x.get("status_label") != "已投递" or
                app_status.match(apps, x.get("company"), x.get("req"))):
            continue
        history.append({
            "history_id": None, "company": x.get("company", ""), "req": x.get("req", ""),
            "title": x.get("title", ""), "loc": x.get("loc") or "", "role": x.get("role") or "other",
            "applied_date": x.get("status_date") or None, "receipt_date": None,
            "submission_label": "已投递·手工记录", "submission_basis": "批次手工记录；具体依据见原注记",
            "status_label": x["status_label"], "status_date": x.get("status_date") or "",
            "note": x.get("status_note") or "", "note_url": x.get("note"),
        })
    for x in history:
        loc = x["loc"]
        x.update(remote=bool(re.search(r"remote", loc, re.I)),
                 tx=bool(TX.search(loc) or DFW.search(loc)), dfw=bool(DFW.search(loc)))
    return history


def other_rows(apps, other):
    """投递状态.json 的 other 段（扫描池以外手记的岗位），状态按 applications.json 现算。"""
    extra = []
    for o in other:
        a = app_status.match(apps, o.get("company"), o.get("req"), f"{o.get('url') or ''} {o.get('apply_url') or ''} {o.get('title') or ''}")
        st_ = a["label"] if a else o.get("status", "")
        extra.append({"company": o.get("company"), "title": o.get("title"), "status": st_,
                      "active": st_ in app_status.ACTIVE or st_ == "面试中",
                      "date": a["date"] if a else o.get("date", ""), "note": app_status.note_of(a) if a else o.get("note", ""),
                      "url": o.get("url") or o.get("apply_url"),
                      "archive": obsidian_url(os.path.join(VAULT, "求职机会", o["archive"] + ".md")) if o.get("archive") else None})
    return extra


def role_of(title, family, track):
    """标题带 postdoc / fellow 的单列一类；其余按 parse_jd 的职族归并（jobs_all 里没有时按标题现算）。"""
    t = title or ""
    if POSTDOC.search(t):
        return "postdoc"
    if FDE_TITLE.search(t):
        return "fde"
    fam = family or parse_jd.classify_role_family(t, "", "", track)[0]
    role = ROLE_OF_FAMILY.get(fam, "other")
    # 标题写明 AI/ML 的工程岗（"Engineer II, Gen AI"）归 MLE；标题写明全栈、后端、SRE 等的仍算 SDE
    if role == "sde" and AI_TITLE.search(t) and not SWE_TITLE.search(t):
        role = "mle"
    return role


def strip_tier(s):
    return re.sub(r"^\s*(🥇|🟢|🟡|🔴|stretch)\s*[：:]?\s*", "", s or "").strip()


def normalize_availability_audit(record):
    """Keep attempts traceable without presenting channel failures as job evidence.

    Older ledgers lack per-attempt timestamps. Only the final attempt that matches
    the current note can inherit the record timestamp; missing historical dates
    remain missing. Search-index snippets never become direct job confirmations.
    """
    attempts = []
    evidence = record.get("evidence") or []
    for i, evidence_row in enumerate(evidence):
        row = dict(evidence_row)
        observed = str(row.get("observed") or "")
        etype = str(row.get("evidence_type") or "")
        if re.search(r"按渠道跳过|本轮.*跳过|Workday.*维护|channel.*skip|maintenance", observed, re.I):
            kind = "channel_skip"
        elif etype == "official_search_index":
            kind = "search_index"
        elif etype == "blocked_official_page" or re.search(r"返回\s*0\s*行|无可读|无法核实|未显示可复核|JS\s*渲染|静态\s*HTML\s*未见|工具不可访问", observed, re.I):
            kind = "page_unreadable"
        else:
            kind = "job_page"
        row["kind"] = kind
        current = i == len(evidence) - 1 and observed in (record.get("note"), record.get("blocker_reason"))
        if not row.get("checked_at_utc") and current and record.get("checked_at_utc"):
            row["checked_at_utc"] = record["checked_at_utc"]
        if not (row.get("checked_at_utc") or row.get("checked_window_utc")):
            # "crawled: today (2026-10-14)" records a date, not a UTC timestamp.
            match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", str(row.get("crawled") or ""))
            if match:
                row["checked_date"] = match.group(0)
        if kind == "job_page":
            if current and record.get("status") in ("open", "closed"):
                row["status"] = record["status"]
            elif re.search(r"有效\s*Apply|显示.*Apply|及\s*Apply|在招[:：]|完整JD及申请|当前详情有申请链接", observed, re.I):
                row["status"] = "open"
        attempts.append(row)
    last_verified = next((row for row in reversed(attempts)
                          if row.get("status") in ("open", "closed") and row.get("kind") == "job_page"), None)
    canonical_url = next((url for url in (record.get("public_url"), record.get("original_url"),
                                          *(row.get("url") for row in reversed(attempts)))
                          if re.match(r"^https?://", str(url or ""))), "")
    return {**{key: record.get(key) for key in ("status", "checked_at_utc", "blocker_reason", "evidence", "preserve_preferences")},
            "canonical_url": canonical_url, "attempts": attempts,
            "latest_attempt": attempts[-1] if attempts else None,
            "last_job_verification": last_verified}


def build(bdir):
    ddir = os.path.join(bdir, "数据")
    fin_path = latest(os.path.join(ddir, "终评_*岗.json"))
    fin = read_json(fin_path, []) or []
    jobs = {r["uid"]: r for r in (read_json(os.path.join(OUT_DIR, "jobs_all.json"), []) or [])}
    closed = read_json(os.path.join(ddir, "追踪", "closed.json"), {}) or {}
    closed = closed if isinstance(closed, dict) else {c.get("uid"): c for c in closed}
    delta_path = latest(os.path.join(ddir, "追踪", "delta_*.json"), key=lambda p: os.path.basename(p))
    delta = read_json(delta_path, {}) or {}
    new_uids = {n.get("uid") for n in delta.get("new", [])}
    links = read_json(os.path.join(ddir, "申请链接核验.json"), {}) or {}
    st = read_json(os.path.join(VAULT, CAT_REL, "投递状态.json"), {}) or {}
    bst, other = st.get("batch", {}), st.get("other", [])
    notes = {os.path.basename(p)[:-3]: p for p in glob.glob(os.path.join(bdir, "岗位明细", "*", "*.md"))}
    apps = app_status.load()

    items = []
    for j in fin:
        domain_failed = (j.get("domain_fit") or {}).get("required_domain_status") == "UNMET"
        if j.get("final_tier") not in TIERS and not domain_failed:
            continue
        uid = j["uid"]
        r = jobs.get(uid, {})
        vh = j.get("visa_hook") or {}
        el = j.get("eligibility") or {}
        ol = j.get("official_level") or {}
        nb = note_basename(j)
        npath = notes.get(nb)
        cluster = os.path.basename(os.path.dirname(npath)) if npath else "其他医疗AI"
        if r:
            ap_ = apply_url(r)
            a_url, how = (ap_, "") if isinstance(ap_, str) else (ap_ or (None, ""))
        else:
            a_url, how = None, ""
        post_url = r.get("url") or vh.get("ats_url") or j.get("ats_url")
        loc = r.get("location") or vh.get("work_location") or ""
        wt = str(r.get("workplace_type") or "")
        pay_text = r.get("pay_range") or vh.get("pay_band") or ol.get("band_pay") or ""
        try:
            pl, ph = float(r.get("pay_low") or 0) or None, float(r.get("pay_high") or 0) or None
        except (TypeError, ValueError):
            pl = ph = None
        if not (pl and ph):
            pl, ph = pay_bounds(pay_text)
        s1 = (j.get("funnel") or {}).get("S1") or {}
        ca = (j.get("cost_tiers") or {}).get("A") or {}
        rb = j.get("recommended_base") or {}
        lk = links.get(uid) or {}
        status, status_label, status_date, status_note = status_of(apps, bst, closed, uid, j["company"], j.get("req_id"))
        if status == "closed" and status_label == "已下线":
            status_note += f"；原档位 {j['final_tier']}"
        # 标题写了 Remote 但地点串里没有的（如 "… - Remote | Eden Prairie, MN"），按住址算。
        # 不用 remote_us / workplace_type：Abridge 这类 hybrid 岗会被它们误标成远程（2026-09-30 实测）
        title_remote = bool(re.search(r"\bremote\b", j["title"], re.I)) and not re.search(r"remote", loc, re.I)
        vs = visa_signals.signals(uid, j["title"], "Remote (US); " + loc if title_remote else loc, pl, ph)
        jd_flags, jd_ev = parse_jd.extract_restrictions(r.get("jd_text") or "", r.get("jd_text") or "") if r.get("jd_text") else ([], [])
        jd_visa = [f for f in jd_flags if f in VISA_WORDING]
        items.append({
            "wage": vs["wage"] if vs["wage"].get("ok") else {"note": vs["wage"].get("note", "")},
            "emp_lca": vs.get("lca"), "emp_policy": {k: v for k, v in (vs.get("policy") or {}).items() if not k.startswith("lca_")} or None,
            "jd_visa": jd_visa, "jd_visa_quote": [e.split(": ", 1)[1] for e in jd_ev if e.split(":")[0] in VISA_WORDING][:2],
            "uid": uid, "req": j.get("req_id"), "company": j["company"], "title": j["title"],
            "tier": j["final_tier"], "track": j.get("track"), "cluster": cluster,
            "role": role_of(j["title"], r.get("role_family"), j.get("track")),
            "elig": el.get("verdict") or "UNAUDITED", "elig_line": el.get("one_line_cn") or "",
            "elig_reason": el.get("reasoning_cn") or "", "elig_quote": el.get("decisive_quote") or "",
            "judge_line": strip_tier(j.get("one_line_cn")),
            "domain_fit": j.get("domain_fit"),
            "reach": ol.get("reachability") or "", "level": ol.get("raw") or "",
            "loc": loc, "remote": bool(r.get("remote_us") is True or re.search(r"remote", loc + " " + wt, re.I)),
            "tx": bool(r.get("state") == "TX" or TX.search(loc) or DFW.search(loc)), "dfw": bool(DFW.search(loc)),
            "pay": pay_text, "pay_lo": pl, "pay_hi": ph,
            "posted": r.get("posted") or "", "deadline": deadline(r.get("jd_text")),
            "apply": a_url, "how": how, "post": post_url, "note": obsidian_url(npath) if npath else None,
            "link_ok": lk.get("ok"), "link_checked": (lk.get("checked_at") or "")[:16].replace("T", " "),
            "live": lk.get("live") or "", "live_detail": lk.get("live_detail") or "",
            "s1": s1.get("blocker_cn") or "", "s1_band": s1.get("cold_band") or "", "s1_prep": s1.get("prepared_band") or "",
            "cost_a": " · ".join(x for x in (ca.get("cost"), ca.get("judgement_cn")) if x), "cost_a_note": ca.get("note_cn") or "",
            "base": BASES.get(rb.get("base"), rb.get("base") or ""), "base_reason": rb.get("reason_cn") or "",
            "entity": vh.get("hiring_org") or vh.get("entity_name") or "",
            "new": uid in new_uids, "status": status, "status_label": status_label, "status_date": status_date, "status_note": status_note,
        })
        items[-1]["pri_base"], items[-1]["pri_why_base"] = priority(items[-1], j, date.today())
    apply_same_company(items)
    extra = other_rows(apps, other)
    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "today": date.today().isoformat(),
        "final_file": os.path.basename(fin_path or ""),
        "delta": {"stamp": delta.get("stamp") or "", "date": delta.get("date") or "", "n_new": len(delta.get("new", [])),
                  "n_closed": len(delta.get("closed", [])), "n_pool": delta.get("n_pool")},
        "tracks": TRACKS, "roles": ROLES,
        "wage_year": visa_signals.WAGE_YEAR, "lca_coverage": visa_signals._load_json("lca_summary.json").get("coverage", ""),
        "visa_wording": VISA_WORDING,
        "links": {
            "progress": obsidian_url(os.path.join(VAULT, CAT_REL, "投递进展.md")),
            "hub": obsidian_url(os.path.join(bdir, "岗位信息.md")),
            "cv": obsidian_url(os.path.join(bdir, "CV贴合指南.md")),
            "tracking": obsidian_url(os.path.join(bdir, "追踪记录.md")),
        },
        "items": items, "other": extra,
    }


def manual_items(apps, today):
    """targets.json 里手工调研的待投岗，转成与扫描池同样的卡片字段（票数、优先级、类型同一套口径）。"""
    out = []
    for t in (read_json(TARGETS, {}) or {}).get("targets", []):
        uid = "manual:" + t["id"]
        loc, title = t.get("location") or "", t["title"]
        lo, hi = t.get("pay_low"), t.get("pay_high")
        vs = visa_signals.signals(uid, title, loc, lo, hi)
        status, status_label, status_date, status_note = status_of(apps, {}, {}, uid, t["company"], t.get("req"))
        folder = t.get("folder") or ""
        note = os.path.join(VAULT, folder, "岗位信息.md") if folder else ""
        item = {
            "wage": vs["wage"] if vs["wage"].get("ok") else {"note": vs["wage"].get("note", "")},
            "emp_lca": None, "emp_policy": None, "jd_visa": [], "jd_visa_quote": [],
            "uid": uid, "req": t.get("req") or "", "company": t["company"], "title": title,
            "tier": t.get("tier") or "🟡 条件投", "track": MANUAL_TRACK[0], "cluster": "手工调研",
            "role": role_of(title, None, None),
            "elig": t.get("elig") or "UNAUDITED", "elig_line": t.get("elig_line") or "", "elig_reason": "", "elig_quote": "",
            "judge_line": t.get("line") or "", "action": t.get("action") or "",
            "domain_fit": t.get("domain_fit"), "visa_paths": t.get("visa_paths"),
            "reach": "", "level": "", "loc": loc, "remote": bool(re.search(r"remote", loc, re.I)),
            "tx": bool(TX.search(loc) or DFW.search(loc)), "dfw": bool(DFW.search(loc)),
            "pay": t.get("pay") or "", "pay_lo": lo, "pay_hi": hi,
            "posted": t.get("posted") or "", "deadline": t.get("deadline") or None,
            "apply": t.get("url") or None, "how": "", "post": t.get("url") or None,
            "note": obsidian_url(note) if note and os.path.exists(note) else None,
            "link_ok": None, "link_checked": "", "live": "", "live_detail": "",
            "s1": "", "s1_band": "", "s1_prep": "", "cost_a": "", "cost_a_note": "", "base": "", "base_reason": "",
            "entity": "", "new": False,
            "source_intent": t.get("status") or "",
            "source_archive_reason": t.get("archive_reason") or t.get("drop_reason") or t.get("note") or t.get("line") or "本人移出待投；保留档案",
            "source_archive_source": t.get("archive_source") or t.get("url") or "",
            "source_archived_at": t.get("archived_at") or t.get("closed_on") or "",
            "status": status, "status_label": status_label, "status_date": status_date, "status_note": status_note,
        }
        if t.get("listing_evidence"):
            state = t.get("listing_status")
            state = state if state in ("open", "closed", "unverified") else "unverified"
            item["availability_audit"] = normalize_availability_audit({
                "status": state, "public_url": t.get("url"), "checked_at_utc": t.get("listing_checked_at"),
                "evidence": t["listing_evidence"], "note": t["listing_evidence"][-1].get("observed")})
            item.update(link_ok=state == "open", link_checked=t.get("listing_checked_at") or "",
                        live="有效" if state == "open" else "待核实")
        if status == "pending" and t.get("status") == "dropped":
            verified_closed = opportunity_updates.verification(item)["verification_status"] == "closed"
            item.update(status="closed" if verified_closed else "excluded",
                        status_label="官网已关闭" if verified_closed else "已移出待投",
                        status_date=item["source_archived_at"], status_note=item["source_archive_reason"])
        item["pri_base"], item["pri_why_base"] = priority(item, {}, today)
        out.append(item)
    return out


def postdoc_exclusions(audit=None):
    """按已保存的每 req 偏好归档；不扩张为所有博士后的永久规则。"""
    audit = audit if audit is not None else read_json(PENDING_AUDIT, {}) or {}
    result = {}
    for row in audit.get("records", []):
        pref = next((p for p in row.get("preserve_preferences", []) if p.get("code") == "excluded_postdoc"), None)
        if not pref:
            continue
        for s in row.get("source_records", []):
            if s.get("source_type") in ("manual_target", "scanning_pool") and not s.get("application_history_id"):
                result[s.get("source_id")] = pref.get("basis") or "本人已排除博士后"
    return result


def assemble(pool, today=None, applications=None, opportunity_state=None):
    """统一面板的「待投岗位」数据：扫描池卡片 + 手工待投，投递状态一律按此刻的 applications.json 重算。"""
    today = today or date.today()
    apps = app_status.load(applications=applications)
    st = read_json(os.path.join(VAULT, CAT_REL, "投递状态.json"), {}) or {}
    bst = st.get("batch", {})
    closed = read_json(os.path.join(VAULT, "求职机会", BATCH_REL, "数据", "追踪", "closed.json"), {}) or {}
    closed = closed if isinstance(closed, dict) else {c.get("uid"): c for c in closed}
    items = []
    for x in pool.get("items", []):
        x = dict(x)
        x["source_listing_closure"] = closed.get(x["uid"])
        x["status"], x["status_label"], x["status_date"], x["status_note"] = status_of(apps, bst, closed, x["uid"], x["company"], x.get("req"))
        if x["status"] == "closed" and x["status_label"] == "已下线":
            x["status_note"] += f"；原档位 {x['tier']}"
        items.append(x)
    items += manual_items(apps, today)
    audit = read_json(PENDING_AUDIT, {}) or {}
    audit = audit if audit.get("scope") == "pending_targets_only" else {}
    audited = {}
    for r in audit.get("records", []):
        for source in r.get("source_records", []):
            if source.get("source_type") not in ("manual_target", "scanning_pool"):
                continue
            uid = source.get("source_id") or ""
            audited[uid] = r
    for x in items:
        r = audited.get(x["uid"])
        # 投递历史仍由 applications.json 决定；核验只附在本轮待投及新下线岗。
        if not r:
            continue
        x["availability_audit"] = normalize_availability_audit(r)
        if app_status.match(apps, x["company"], x.get("req")):
            continue
        if x["status"] != "pending" and not x.get("source_intent") == "dropped" and not (x["status"] == "closed" and x["status_label"] == "已下线"):
            continue
        # A verified opening does not revoke a recorded user exclusion. Keep the
        # opportunity's availability separate from the user's application intent.
        excluded = next((p for p in r.get("preserve_preferences", [])
                         if p.get("code") == "excluded_postdoc"), None)
        if excluded and x["status"] == "pending":
            x.update(status="excluded", status_label="本人排除博士后", status_date="",
                     status_note=excluded.get("basis") or "本人已排除博士后；保留档案。")
    for x in items:
        fit = x.get("domain_fit") or {}
        if fit.get("required_domain_status") != "UNMET":
            continue
        if x["status"] == "pending":
            x.update(status="excluded", status_label="必需领域/模态不符", status_date=fit.get("as_of_date", ""),
                     status_note=fit.get("gap_cn", ""))
        elif x["status"] == "excluded":
            x["status_note"] += "；" + fit.get("gap_cn", "")
    if opportunity_state is False:
        opportunity_refresh = None  # CLI observes source data before registry-derived decoration.
    else:
        state = opportunity_updates.load_state() if opportunity_state is None else opportunity_state
        items, opportunity_refresh = opportunity_updates.decorate(items, state, today)
    apply_same_company(items)
    audit_summary = {**(audit.get("summary") or {}),
                     "current_pending": sum(x["status"] == "pending" and x["tier"] != "stretch" for x in items),
                     "current_observe": sum(x["status"] == "pending" and x["tier"] == "stretch" for x in items),
                     "excluded_postdoc": sum(x.get("status_label") == "本人排除博士后" for x in items)}
    apply_priority_review(items)
    return {**pool, "title": "待投岗位", "today": today.isoformat(),
            "tracks": {**(pool.get("tracks") or TRACKS), MANUAL_TRACK[0]: MANUAL_TRACK[1]},
            "roles": pool.get("roles") or ROLES, "items": items, "other": other_rows(apps, st.get("other", [])),
            "applied_history": application_history(apps, items),
            "opportunity_refresh": opportunity_refresh,
            "pending_audit": {**{k: audit.get(k) for k in ("baseline_total", "updated_at_utc")},
                              "summary": audit_summary} if audit else None}


def render_html(data):
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read().replace("__DATA_JSON__", blob)
    for kind in ("js", "css"):
        with open(os.path.join(app_status.PANEL_DIR, "company_filter." + kind), encoding="utf-8") as f:
            html = html.replace("__COMPANY_FILTER_" + kind.upper() + "__", f.read())
    return html


def snapshot(data, bdir):
    """给求职进度面板用的小快照（它读不动 900 MB 的 jobs_all.json）。

    只存档位、资格、截止日、下线、手工状态这些静态属性；投递状态由 make_dashboard.py 生成时
    按当时的 applications.json 现算，所以记完投递直接重跑主面板就对，不用先跑本脚本。
    """
    st = read_json(os.path.join(VAULT, CAT_REL, "投递状态.json"), {}) or {}
    bst = st.get("batch", {})
    closed = read_json(os.path.join(bdir, "数据", "追踪", "closed.json"), {}) or {}
    closed = closed if isinstance(closed, dict) else {c.get("uid"): c for c in closed}
    keep = ("uid", "req", "company", "title", "tier", "track", "role", "elig", "elig_line", "loc", "dfw", "remote", "pay",
            "deadline", "apply", "post", "note", "new", "posted", "tx", "wage", "emp_lca", "emp_policy", "jd_visa",
            "link_checked", "live",
            "pri_base", "pri_why_base")
    items = []
    exclusions = postdoc_exclusions()
    for x in data["items"]:
        it = {k: x.get(k) for k in keep}
        it["excluded_postdoc"] = exclusions.get(x["uid"])
        c = closed.get(x["uid"])
        it["closed"] = {"date": c.get("date", ""), "reason": c.get("reason", "")} if c else None
        s = bst.get(x["uid"])
        it["manual"] = {"status": s.get("status", ""), "date": s.get("date", ""), "note": s.get("note", "")} if s else None
        items.append(it)
    return {
        "generated": data["generated"], "final_file": data["final_file"], "delta": data["delta"],
        "links": {**data["links"], "board": "file://" + urllib.parse.quote(OUT)},
        "items": items, "wage_year": data.get("wage_year", ""),
    }


def render(bdir, open_=False):
    data = build(bdir)
    with open(POOL_BOARD, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    with open(SNAPSHOT, "w", encoding="utf-8") as f:
        json.dump(snapshot(data, bdir), f, ensure_ascii=False, indent=1)
    # board.html 留作跳转页：旧书签和 投递进展.md 里的链接仍能用
    target = urllib.parse.quote(os.path.relpath(DASHBOARD, TOOL_DIR)) + "#todo"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>待投岗位已并入求职面板</title>'
                f'<meta http-equiv="refresh" content="0; url={target}"></head><body style="font-family:system-ui;padding:24px">'
                f'待投岗位已并入 <a href="{target}">求职面板</a>（「待投岗位」标签）。</body></html>')
    r = subprocess.run([sys.executable, os.path.join(app_status.PANEL_DIR, "make_dashboard.py")] + (["--open"] if open_ else []),
                       cwd=app_status.PANEL_DIR, capture_output=True, text=True)
    if r.returncode:
        print(f"⚠️  求职面板没有重新生成（make_dashboard.py 退出码 {r.returncode}）：{(r.stderr or r.stdout).strip()[-400:]}")
    it = [x for x in assemble(data)["items"] if x.get("track") != "M"]
    n_pend = sum(1 for x in it if x["status"] == "pending" and x["tier"] != "stretch")
    n_dl = sum(1 for x in it if x["status"] == "pending" and x["deadline"])
    n_act = sum(1 for x in it if x["status"] == "applied") + sum(1 for o in data["other"] if o["active"])
    n_rej = sum(1 for x in it if x["status"] == "rejected") + sum(1 for o in data["other"] if o["status"] in ("已拒", "已撤回"))
    print(f"扫描池 → {POOL_BOARD}：待投 {n_pend}（不含 stretch），带截止日 {n_dl}，在途 {n_act}，已拒 {n_rej}；页面见 {DASHBOARD}#todo")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-dir", default=os.path.join(VAULT, "求职机会", BATCH_REL))
    ap.add_argument("--open", action="store_true", help="生成后用默认浏览器打开")
    a = ap.parse_args()
    render(a.batch_dir, a.open)


if __name__ == "__main__":
    main()
