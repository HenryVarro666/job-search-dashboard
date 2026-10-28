#!/usr/bin/env python3
"""求职面板（2026-10-06 起与医疗 AI 岗位面板合一）：读 applications.json + targets.json + 扫描池数据，生成自包含的本地 HTML。

五个标签：今天 / 近7天新发现 / 待投岗位 / 投递进度 / 春季 PhD 实习（独立条件池）。

纯 Python 3 标准库。面板只在本机打开，不上传、不发布（vault 隐私规则）。

用法：
    python3 make_dashboard.py            # 校验 + 生成 dashboard.html + 终端摘要
    python3 make_dashboard.py --open     # 生成后用默认浏览器打开
    python3 make_dashboard.py --check    # 只校验 applications.json，不生成
    python3 make_dashboard.py --today 2026-10-01   # 按指定日期计算"已等 N 天"
    python3 make_dashboard.py --no-healthcare      # 不接医疗 AI 扫描池（默认有快照就接）
退出码：0 正常 / 1 applications.json 有错误（不生成面板）
"""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo

from oa_analytics import derive_analytics, unmapped_updates, analytics_summary, verified_application_existence
from internship_updates import decorate as decorate_internships

HERE = Path(__file__).resolve().parent
VAULT = HERE.parent.parent
DATA_FILE = HERE / "applications.json"
TEMPLATE_FILE = HERE / "dashboard_template.html"
OUT_FILE = HERE / "dashboard.html"
OBSIDIAN_VAULT = "Work"
# 医疗 AI 扫描池：healthcare岗位抓取/render_board.py 每次渲染时写的入围岗快照
HC_DIR = HERE.parent / "healthcare岗位抓取"
HC_SNAPSHOT = HC_DIR / "pool_snapshot.json"
HC_POOL = HC_DIR / "pool_board.json"          # 扫描池卡片完整数据，render_board.py 写
TARGETS_FILE = HERE / "targets.json"          # 手工调研、还没发出的岗位
INTERNSHIPS_FILE = HERE / "internships.json"  # 已核验的春季实习条件池
MANUAL_FOLLOWUPS_FILE = HERE / "manual_followups.json"  # 联网复核、材料与待确认事项
TIER_ORDER = ("🥇 投", "🟢 投", "🟡 条件投", "stretch")
HC_FOLDER_TAG = "医疗AI"  # 求职机会/ 下类别名带这个的（科技公司-医疗AI、创业公司-医疗AI、咨询公司-医疗AI）算医疗 AI 线
HC_TIERS = ("🥇 投", "🟢 投", "🟡 条件投")  # stretch 只观察，不进进度条
HC_ELIG = {"ELIGIBLE_NEW_GRAD": "✅ 应届可投", "ELIGIBLE_PHD_COUNTS": "✅ PhD 折算", "CONDITIONAL_RESEARCH_YEARS": "⚠️ 看是否认研究年限",
           "LEVEL_MISMATCH": "⛔ 资深岗", "NOT_ELIGIBLE": "⛔ 硬门槛", "TIMING_CONFLICT": "⛔ 时间冲突", "UNCLEAR": "❓ 信息不足",
           "UNAUDITED": "未审计"}
HC_ELIG_ORDER = {"ELIGIBLE_NEW_GRAD": 0, "ELIGIBLE_PHD_COUNTS": 1, "CONDITIONAL_RESEARCH_YEARS": 2, "UNCLEAR": 3}

EVENT_TYPES = {
    "referral_request": "发内推请求",
    "referral_submitted": "内推已提交",
    "referrer_reply": "内推人回复",
    "applied": "投递",
    "ack": "系统确认收到",
    "human_reply": "对方回复",
    "assessment": "测评",
    "interview": "面试",
    "offer": "Offer",
    "rejected": "拒信",
    "closed": "关闭",
    "withdrawn": "本人撤回",
    "other": "其他",
}
STAGES = {
    "referral_pending": ("pending", "内推请求中"),
    "referred": ("pending", "内推已到·待投递"),
    "applied": ("waiting", "等回音"),
    "reactivation_pending": ("paused", "暂停 · 重新激活待确认"),
    "screening": ("waiting", "已联系·待下一步"),
    "assessment": ("progress", "测评中"),
    "interview": ("progress", "面试中"),
    "offer": ("progress", "Offer"),
    "rejected": ("rejected", "已拒"),
    "closed": ("closed", "已关闭"),
    "withdrawn": ("closed", "已撤回"),
}
OUTBOUND = ("referral_request", "referral_submitted", "applied", "ack")
APPLIED = ("applied", "ack")
# 对方给了明确答复（含拒信、说不招）；手动按失效关闭的不算
ANSWER_TYPES = ("human_reply", "assessment", "interview", "offer", "rejected", "closed")
# 投后记录（2026-09-30 起）：submitted = {"resume": 用的哪版简历, "desired_salary": 表单填的期望薪资}
# 这天之后投出的岗位缺这两项会进「待补投递记录」；更早的不追溯。表单没问薪资就填 "未问"。
SUBMITTED_FIELDS = {"resume": "简历版本", "desired_salary": "期望薪资"}
SUBMITTED_SINCE = "2026-09-30"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
TERMINAL_EVENTS = ("offer", "rejected", "closed", "withdrawn")
HISTORY_STAGES = {
    "human_reply": ("screening", "招聘联系"),
    "assessment": ("assessment", "测评记录"),
    "interview": ("interview", "面试记录"),
    "offer": ("offer", "Offer记录"),
}


def parse_date(s):
    return dt.date.fromisoformat(s)


def ordered_events(events):
    """按日期与已知时刻排序；未知时刻保留同日本地日志位置，不假定为凌晨。"""
    days = {}
    for e in events:
        days.setdefault(e["date"], []).append(e)
    ordered = []
    for day in sorted(days):
        rows = days[day]
        timed = iter(sorted((e for e in rows if e.get("time")), key=lambda e: e["time"]))
        ordered.extend(next(timed) if e.get("time") else e for e in rows)
    return ordered


def event_order_uncertain(events):
    """同日有多条记录且有时刻未知时，日志展示顺序不代表严格先后。"""
    counts = {}
    unknown = set()
    for e in events:
        day = e["date"]
        counts[day] = counts.get(day, 0) + 1
        if not e.get("time"):
            unknown.add(day)
    return any(counts[day] > 1 for day in unknown)


def event_snapshot(event):
    return {"date": event["date"], "time": event.get("time") or "", "type": event["type"],
            "summary": event.get("summary", ""), "time_unknown": not bool(event.get("time"))}


def derive_stage(events):
    """按事件推阶段：最后一个终态（offer / 拒 / 关 / 撤）> 面试 > 测评 > 对方回复 > 已投 > 内推已到 > 内推请求中。"""
    types = [e["type"] for e in events]
    terminal = [e for e in events if e["type"] in TERMINAL_EVENTS]
    if terminal:
        return terminal[-1]["type"]
    for t, stage in (("interview", "interview"), ("assessment", "assessment"), ("human_reply", "screening")):
        if t in types:
            return stage
    if any(t in APPLIED for t in types):
        return "applied"
    # 内推人提交了内推，但大多数 ATS（Workday / Eightfold / SmartRecruiters）还要本人去投；
    # Greenhouse 这类内推即生成申请的，用 stage 覆盖成 applied
    if "referral_submitted" in types:
        return "referred"
    return "referral_pending"


def resolve_stage(app, events):
    """真实终态事件优先；没有终态时保留人工 stage（如内推即投递、手工止损）。"""
    ordered = ordered_events(events)
    auto = derive_stage(ordered)
    if any(e["type"] in TERMINAL_EVENTS for e in ordered):
        return auto
    return app.get("stage") or auto


def lifecycle_fields(stage, events):
    """区分当前是否仍在推进和该 req 曾有的记录；邀请不等于完成面试。"""
    ordered = ordered_events(events)
    milestones = [
        {"date": e["date"], "type": e["type"], "label": HISTORY_STAGES[e["type"]][1],
         "summary": e.get("summary", "")}
        for e in ordered if e["type"] in HISTORY_STAGES
    ]
    highest = next((kind for kind in reversed(HISTORY_STAGES)
                    if any(e["type"] == kind for e in milestones)), None)
    if stage in ("rejected", "closed", "withdrawn"):
        lifecycle = "ended"
    elif stage == "reactivation_pending":
        lifecycle = "paused"
    elif stage in ("assessment", "interview", "offer"):
        lifecycle = "advancing"
    elif stage == "screening":
        lifecycle = "awaiting_next"
    else:
        lifecycle = "waiting"
    return {
        "lifecycle": lifecycle,
        "history_stage": HISTORY_STAGES[highest][0] if highest else None,
        "history_stage_label": HISTORY_STAGES[highest][1] if highest else "",
        "history_milestones": milestones,
    }


def submission_evidence(app, events):
    """收件日能证明已收到申请，但不能倒推出实际提交日；旧 stage 也不补造日期。"""
    ordered = ordered_events(events)
    applied = next((e for e in ordered if e["type"] == "applied"), None)
    receipt = next((e for e in ordered if e["type"] == "ack"), None)
    receipt_date = receipt["date"] if receipt else None
    if applied:
        return {"label": "已投递", "applied_date": applied["date"], "receipt_date": receipt_date,
                "basis": "投递流水中的提交记录"}
    if receipt:
        return {"label": "已收件", "applied_date": None, "receipt_date": receipt_date,
                "basis": "系统收件记录；实际提交日期未记录"}
    if verified_application_existence(app):
        return {"label": "历史申请已核实；原提交日期未知", "applied_date": None, "receipt_date": None,
                "basis": "撤回确认或额度通知证明申请曾存在；通知日不是提交日或收件日"}
    if app.get("stage") == "applied":
        return {"label": "已投递·既有记录", "applied_date": None, "receipt_date": None,
                "basis": "沿用既有已投阶段；缺少独立提交事件"}
    return None


def status_events(stage, events):
    """当前有效动态与任意最新记录分开；致谢、查帖不移动投后状态日期。"""
    ordered = ordered_events(events)
    group = STAGES[stage][0]
    skip = ("other",) if group == "pending" else ("other", "referrer_reply")
    meaningful = [e for e in ordered if e["type"] not in skip] or ordered
    terminal = [e for e in ordered if e["type"] in TERMINAL_EVENTS]
    if terminal:
        outcome = {"date": terminal[-1]["date"], "type": terminal[-1]["type"], "manual": False}
    elif group in ("rejected", "closed"):
        outcome = {"date": None, "type": stage, "manual": True}
    else:
        outcome = None
    return meaningful[-1], ordered[-1], outcome


def _valid_date(s):
    if not isinstance(s, str) or not DATE_RE.match(s):
        return False
    try:
        parse_date(s)
    except ValueError:
        return False
    return True


def validate(data, today):
    errors, warnings = [], []
    apps = data.get("applications") if isinstance(data, dict) else None
    if not isinstance(apps, list) or not apps:
        return ["applications 必须是非空列表"], warnings
    seen = set()
    for i, a in enumerate(apps):
        if not isinstance(a, dict):
            errors.append(f"applications[{i}]: 应为对象")
            continue
        where = f"applications[{i}] ({a.get('id', '?')})"
        for k in ("id", "company", "title"):
            if not a.get(k) or not isinstance(a[k], str):
                errors.append(f"{where}: {k} 必须是非空字符串")
        for k in ("req", "channel", "referrer", "location", "folder", "notes", "stage"):
            if k in a and not isinstance(a[k], str):
                errors.append(f"{where}: {k} 必须是字符串（req 号也要加引号）")
        if a.get("id") in seen:
            errors.append(f"{where}: id 重复")
        seen.add(a.get("id"))
        if a.get("stage") and a["stage"] not in STAGES:
            errors.append(f"{where}: stage 取值非法 {a['stage']!r}")
        if "conflicts" in a and not (isinstance(a["conflicts"], list) and all(isinstance(c, str) for c in a["conflicts"])):
            errors.append(f"{where}: conflicts 必须是字符串列表")
        sub = a.get("submitted")
        if sub is not None and not (isinstance(sub, dict) and all(isinstance(v, (str, int, float)) for v in sub.values())):
            errors.append(f"{where}: submitted 必须是对象，值为字符串或数字（如 {{\"resume\": \"resume.pdf\", \"desired_salary\": 165000}}）")
        nxt = a.get("next")
        if nxt is not None:
            if not isinstance(nxt, dict):
                errors.append(f"{where}: next 必须是对象 {{date, action}}")
            elif not _valid_date(nxt.get("date")):
                errors.append(f"{where}: next.date 不是合法日期 YYYY-MM-DD（{nxt.get('date')!r}）")
            elif "action" in nxt and not isinstance(nxt["action"], str):
                errors.append(f"{where}: next.action 必须是字符串")
        folder = a.get("folder")
        if isinstance(folder, str) and folder and not (VAULT / folder).exists():
            warnings.append(f"{where}: folder 不存在 {folder}")
        events = a.get("events")
        if not isinstance(events, list) or not events:
            errors.append(f"{where}: events 必须是非空列表")
            continue
        for j, e in enumerate(events):
            w = f"{where}.events[{j}]"
            if not isinstance(e, dict):
                errors.append(f"{w}: 应为对象")
                continue
            if e.get("type") not in EVENT_TYPES:
                errors.append(f"{w}: type 非法 {e.get('type')!r}")
            if not _valid_date(e.get("date")):
                errors.append(f"{w}: date 不是合法日期 YYYY-MM-DD（{e.get('date')!r}）")
            elif parse_date(e["date"]) > today:
                warnings.append(f"{w}: 日期 {e['date']} 晚于今天；还没发生的安排请写进 next")
            if e.get("time") and not (isinstance(e["time"], str) and TIME_RE.match(e["time"])):
                errors.append(f"{w}: time 格式应为 HH:MM")
            for k in ("summary", "source"):
                if k in e and not isinstance(e[k], str):
                    errors.append(f"{w}: {k} 必须是字符串（多个来源用 ; 分隔）")
            if not e.get("summary"):
                warnings.append(f"{w}: 缺 summary")
        if a.get("stage") in STAGES and not errors:
            auto = derive_stage(ordered_events(events))
            resolved = resolve_stage(a, events)
            if auto != a["stage"]:
                if resolved != a["stage"]:
                    warnings.append(f"{where}: 忽略旧 stage {a['stage']}，真实终态事件优先为 {resolved}")
                else:
                    warnings.append(f"{where}: stage 手动覆盖为 {a['stage']}（按事件推算是 {auto}）")
    return errors, warnings


def obsidian_url(path, vault=OBSIDIAN_VAULT):
    p = path[:-3] if path.endswith(".md") else path
    return "obsidian://open?" + urllib.parse.urlencode({"vault": vault, "file": p}, quote_via=urllib.parse.quote)


def load_internships(path=None, today=None):
    """独立读取条件候选，不生成投递事件或全职待投记录。"""
    path = Path(path) if path is not None else INTERNSHIPS_FILE
    if not path.exists():
        return {"items": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items", [])
    seen = set()
    for item in items:
        identity = item.get("id")
        if not identity or identity in seen:
            raise ValueError("实习 id 缺失或重复")
        seen.add(identity)
    data["items"] = items
    if data.get("report_path"):
        data["report_url"] = obsidian_url(data["report_path"])
    return decorate_internships(data, today)


def gmail_url(account, ref):
    thread = ref.split("/")[0].strip()
    try:
        tid = int(thread, 16)
    except ValueError:
        return None
    return f"https://mail.google.com/mail/?authuser={urllib.parse.quote(account)}#all/thread-f:{tid}"


def source_links(source, account):
    links = []
    for part in (source or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if part.startswith("gmail:"):
            url = gmail_url(account, part[len("gmail:"):])
            links.append({"label": "Gmail", "url": url})
        elif part.startswith("~/Documents/"):
            vault, _, rel = part[len("~/Documents/"):].partition("/")
            links.append({"label": Path(rel).stem or vault, "url": obsidian_url(rel, vault) if rel else None})
        elif part.endswith(".md") or part.startswith(("求职机会/", "OPT/", "工具/", "简历方法/")):
            label = Path(part.split("#")[0]).stem
            links.append({"label": label, "url": obsidian_url(part.split("#")[0])})
        else:
            links.append({"label": part, "url": None})
    return links


def silence_archive(app, events, stage, today, analytics):
    """按本地记录折叠无回复条目；不写回事件、不推断雇主结果。"""
    if stage not in ("applied", "referral_pending", "referred"):
        return None
    if analytics["oa"]["has_history"] or any(e["type"] in ANSWER_TYPES for e in events):
        return None
    nxt = app.get("next") or {}
    # 已有具体待办优先，不能把准备申请或等本人决定误当成沉默。
    if nxt and not re.search(r"沉默|本地观察|仍无.*(?:关闭|归档)", nxt.get("action", "")):
        return None
    retired = [x["retired_on"] for x in app.get("superseded_next", [])
               if isinstance(x, dict) and _valid_date(x.get("retired_on"))]
    stopped_on = max(retired) if retired else None
    activity = [e for e in events if e["type"] in OUTBOUND + ("referrer_reply",)]
    if (app.get("followup_status") == "stopped_no_response" and not nxt and stopped_on
            and not any(e["date"] > stopped_on for e in activity)):
        return ({"date": stopped_on, "reason": "沿用既有的无回复停止跟进记录；有新消息再恢复。"}
                if stopped_on <= today.isoformat() else None)
    # 内推人已回复时留给本人处理链接，不沿用旧的无回复期限。
    if any(e["type"] == "referrer_reply" for e in events) and stage != "applied":
        return None
    submissions = [e for e in events if e["type"] in APPLIED]
    if nxt:
        due = parse_date(nxt["date"])
        reason = "到达既定观察日：" + nxt.get("action", "")
        # 旧止损日后又有投递/收件记录，从新记录重新观察，避免立刻归档。
        if activity and parse_date(activity[-1]["date"]) >= due:
            due = parse_date(activity[-1]["date"]) + dt.timedelta(days=30)
            reason = "旧观察日后有新记录，按最新记录再观察 30 天。"
    elif stage == "applied" and submissions:
        due = parse_date((activity or submissions)[-1]["date"]) + dt.timedelta(days=30)
        reason = "未设单独观察日，投递或收件后 30 天未记录招聘回复。"
    else:
        return None
    return {"date": due.isoformat(), "reason": reason} if today >= due else None


def derive(app, today, account):
    events = ordered_events(app["events"])
    stage = resolve_stage(app, events)
    group, stage_label = STAGES[stage]
    outbound = [e for e in events if e["type"] in OUTBOUND]
    first_out = (outbound or events)[0]["date"]
    last, last_any, outcome = status_events(stage, events)
    submission = submission_evidence(app, events)
    submission_record_date = (submission or {}).get("applied_date") or (submission or {}).get("receipt_date")
    analytics = derive_analytics(app, events, stage)
    archive = silence_archive(app, events, stage, today, analytics)
    if archive:
        group, stage_label = "archived", "已归档·无回复"
        analytics["flow_outcome"] = "archived"
    nxt = {} if archive or stage == "reactivation_pending" else app.get("next") or {}
    folder = app.get("folder") or ""
    open_ = group in ("pending", "waiting", "progress")
    return {
        "id": app["id"],
        **analytics,
        "company": app["company"],
        "req": app.get("req") or "",
        "title": app["title"],
        "channel": app.get("channel") or "",
        "referrer": app.get("referrer") or "",
        "location": app.get("location") or "",
        "folder": folder,
        "folder_url": obsidian_url(folder + "/岗位信息") if folder else None,
        "stage": stage,
        "stage_label": stage_label,
        "group": group,
        **lifecycle_fields(stage, events),
        **({"lifecycle": "archived"} if archive else {}),
        "archive": archive,
        "event_order_uncertain": event_order_uncertain(events),
        "open": open_,
        "first_out": first_out,
        "applied_date": (submission or {}).get("applied_date"),
        "receipt_date": (submission or {}).get("receipt_date"),
        "submission": submission,
        **({"submission_date_unknown": True} if verified_application_existence(app)
           and submission and not submission_record_date else {}),
        "last": event_snapshot(last),
        "last_any": event_snapshot(last_any),
        "outcome": outcome,
        "answered": any(e["type"] in ANSWER_TYPES for e in events),
        "has_referral_request": any(e["type"] == "referral_request" for e in events),
        "days_since_out": (today - parse_date(first_out)).days,
        "days_since_last": (today - parse_date(last["date"])).days,
        "next": {"date": nxt["date"], "action": nxt.get("action", "")} if nxt.get("date") else None,
        "next_kind": ("follow_up" if open_ else "restart") if nxt.get("date") else None,
        # 医疗 AI 线：按档案类别先标一遍，扫描池匹配上的在 load_healthcare 里补档位
        "hc": {"tier": "", "elig": ""} if HC_FOLDER_TAG in folder else None,
        "notes": app.get("notes") or "",
        "conflicts": app.get("conflicts") or [],
        "submitted": app.get("submitted") or {},
        "missing": [label for k, label in SUBMITTED_FIELDS.items()
                    if submission_record_date and submission_record_date >= SUBMITTED_SINCE
                    and (app.get("submitted") or {}).get(k) in (None, "")],
        "events": [
            {
                "date": e["date"],
                "time": e.get("time") or "",
                "time_unknown": not bool(e.get("time")),
                "type": e["type"],
                "type_label": EVENT_TYPES[e["type"]],
                "summary": e.get("summary", ""),
                "links": source_links(e.get("source"), account),
            }
            for e in events
        ],
    }


def summarize(apps, today):
    by_group = {g: [a for a in apps if a["group"] == g] for g in ("pending", "waiting", "progress", "paused", "rejected", "closed", "archived")}
    sent = [a for a in apps]
    upcoming = sorted(
        [a for a in apps if a["next"] and (a["open"] or a.get("next_kind") == "restart")],
        key=lambda a: a["next"]["date"],
    )
    return {
        "companies": len({a["company"] for a in sent}),
        "applications": len(sent),
        "confirmed_applications": sum(bool(a.get("application_confirmed")) for a in sent),
        "unconfirmed_records": sum(not a.get("application_confirmed") for a in sent),
        "groups": {g: {"apps": len(v), "companies": len({a["company"] for a in v})} for g, v in by_group.items()},
        "answered": sum(1 for a in apps if a["answered"]),
        "missing": sum(1 for a in apps if a["missing"]),
        "first_date": min(a["first_out"] for a in apps),
        "last_date": max(a["last_any"]["date"] for a in apps),
        "upcoming": [{"id": a["id"], "date": a["next"]["date"], "company": a["company"], "req": a["req"],
                      "action": a["next"]["action"], "next_kind": a.get("next_kind")} for a in upcoming],
    }


def load_healthcare(apps, today, path=HC_SNAPSHOT):
    """医疗 AI 扫描池：档位、资格、截止日取快照，投递状态按本次的 applications 现算。

    匹配沿用 healthcare 那边的 app_status.match（同公司 + req），两边口径一致。
    匹配上的 application 顺带补上档位，面板里能按「医疗 AI」筛。没有快照就返回 None，面板不显示这一块。
    """
    try:
        snap = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if str(HC_DIR) not in sys.path:
        sys.path.insert(0, str(HC_DIR))
    import app_status  # noqa: E402  它反过来 import 本模块取 derive_stage，只在这里用到，不会循环卡住

    items = []
    for x in snap.get("items", []):
        tier = x.get("tier")
        a = app_status.match(apps, x.get("company"), x.get("req"))
        if not a and x.get("excluded_postdoc"):
            continue  # 本人排除的机会在完整档案中保留，不计待投；已投历史优先。
        m, closed = x.get("manual"), x.get("closed")
        if a:
            group, label, date = a["group"], a["stage_label"], a["last"]["date"]
            a["hc"] = {"tier": tier, "elig": HC_ELIG.get(x.get("elig"), "")}
        elif m:  # 投递状态.json 手记的：不投 / 手工关闭（投递本身都应在 applications.json）
            s = m.get("status", "")
            group = ("waiting" if s == "已投递" else "progress" if s == "面试中"
                     else "rejected" if s in ("已拒", "已撤回") else "closed")
            label, date = s, m.get("date", "")
        elif closed:
            group, label, date = "closed", "原帖已下线", closed.get("date", "")
        else:
            group, label, date = "todo", "待投", ""
        items.append({
            "uid": x.get("uid"),
            "company": x.get("company"), "title": x.get("title"), "req": x.get("req") or "", "tier": tier,
            "elig": HC_ELIG.get(x.get("elig"), x.get("elig") or ""), "elig_rank": HC_ELIG_ORDER.get(x.get("elig"), 9),
            "elig_line": x.get("elig_line") or "", "loc": x.get("loc") or "", "dfw": bool(x.get("dfw")),
            "pay": x.get("pay") or "", "deadline": x.get("deadline"), "apply": x.get("apply") or x.get("post"),
            "note": x.get("note"), "new": bool(x.get("new")), "link_checked": x.get("link_checked") or "",
            "wage": x.get("wage") or {}, "emp_lca": x.get("emp_lca"), "emp_policy": x.get("emp_policy"), "jd_visa": x.get("jd_visa") or [],
            "pri_base": x.get("pri_base") or 0, "pri_why_base": x.get("pri_why_base") or [],
            "group": group, "label": label, "date": date, "app_id": a["id"] if a else None,
            "closed_note": f"原帖 {closed.get('date', '')} 已下线" if closed and a else "",
        })
    # 同档内优先级：快照里的基础分 − 同公司已投条数的扣分（口径与 render_board.apply_same_company 相同）
    import render_board  # noqa: E402
    for i in items:
        i["status"] = "applied" if i["group"] in ("waiting", "progress", "pending", "rejected", "archived") else i["group"]
    render_board.apply_same_company(items)
    render_board.apply_priority_review(items)
    todo = sorted((i for i in items if i["group"] == "todo" and i["tier"] in HC_TIERS
                   and (i.get("priority_review") or {}).get("actionable", True)),
                  key=render_board.recommendation_key)
    tiers = []
    for t in HC_TIERS:
        its = [i for i in items if i["tier"] == t]
        counts = {g: sum(1 for i in its if i["group"] == g) for g in ("progress", "waiting", "pending", "rejected", "closed", "archived", "todo")}
        tiers.append({"tier": t, "total": len(its), "counts": counts})
    try:
        age = (today - parse_date(snap.get("generated", "")[:10])).days
    except ValueError:
        age = None
    # 在招核验（apply_links.py verify）会过期：Dexcom JR117507 10/06 核验"有效"、10/08 已撤下（2026-10-08 加）
    checked = sorted(i["link_checked"][:10] for i in todo if i.get("link_checked"))
    unchecked = sum(1 for i in todo if not i.get("link_checked"))
    try:
        verify_age = (today - parse_date(checked[0])).days if checked else None
    except ValueError:
        verify_age = None
    return {
        "verify_oldest": checked[0] if checked else "", "verify_age": verify_age, "verify_unchecked": unchecked,
        "generated": snap.get("generated", ""), "age_days": age, "final_file": snap.get("final_file", ""),
        "delta": snap.get("delta") or {}, "links": snap.get("links") or {},
        "tiers": tiers, "stretch": sum(1 for i in items if i["tier"] == "stretch"),
        "items": [i for i in items if i["tier"] in HC_TIERS],
        "next_up": [{"company": i["company"], "req": i["req"]} for i in todo[:10]],
        "wage_year": snap.get("wage_year", ""),
    }


def validate_targets(path=TARGETS_FILE):
    """targets.json 校验：缺文件不算错（待投里就只有扫描池）。"""
    errors, warnings = [], []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return errors, warnings
    except ValueError as e:
        return [f"targets.json 不是合法 JSON：{e}"], warnings
    seen = set()
    for i, t in enumerate(data.get("targets", [])):
        where = f"targets[{i}] ({t.get('id', '?')})"
        for k in ("id", "company", "title"):
            if not isinstance(t.get(k), str) or not t[k]:
                errors.append(f"{where}: {k} 必须是非空字符串")
        if t.get("id") in seen:
            errors.append(f"{where}: id 重复")
        seen.add(t.get("id"))
        if t.get("tier") not in TIER_ORDER:
            errors.append(f"{where}: tier 只能是 {' / '.join(TIER_ORDER)}")
        if t.get("status") not in (None, "", "dropped"):
            errors.append(f"{where}: status 只能留空或写 dropped")
        for k in ("posted", "deadline"):
            if t.get(k) and not _valid_date(t[k]):
                errors.append(f"{where}: {k} 不是合法日期 YYYY-MM-DD")
        for k in ("pay_low", "pay_high"):
            if t.get(k) is not None and not isinstance(t[k], (int, float)):
                errors.append(f"{where}: {k} 必须是数字")
        if t.get("folder") and not (VAULT / t["folder"]).exists():
            warnings.append(f"{where}: folder 不存在 {t['folder']}")
    return errors, warnings


def load_board(today, applications=None):
    """「待投岗位」标签的数据：扫描池（render_board 写的 pool_board.json）+ targets.json，状态按当前 applications.json 现算。"""
    if str(HC_DIR) not in sys.path:
        sys.path.insert(0, str(HC_DIR))
    import render_board  # noqa: E402
    try:
        pool = json.loads(HC_POOL.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pool = {"generated": "", "final_file": "", "delta": {}, "wage_year": "", "lca_coverage": "", "visa_wording": {},
                "links": {}, "items": [], "other": []}
    board = render_board.assemble(pool, today, applications=applications)
    board["generated"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return board, render_board.render_html(board)


def today_panel(board, summary, apps, today):
    """「今天」标签：3 天内（含已过期）的下一步、10 天内截止的待投岗、近 7 天发布的待投岗、手工待投里写了下一步的。"""
    roles = board.get("roles") or {}
    pend = [x for x in board["items"] if x["status"] == "pending"]
    main = [x for x in pend if x["tier"] != "stretch"]
    import render_board
    actionable = [x for x in main if (x.get("priority_review") or {}).get("actionable", True)]

    def age(x):
        try:
            return (today - parse_date((x.get("posted") or "")[:10])).days
        except ValueError:
            return None

    def card(x, **extra):
        review = x.get("priority_review") or {}
        return {"uid": x["uid"], "company": x["company"], "req": x.get("req", ""), "title": x["title"], "tier": x["tier"], "role": roles.get(x.get("role"), ""),
                "url": x.get("apply") or x.get("post"), "note": x.get("note"), "manual": x.get("track") == "M",
                "paused": review.get("actionable") is False,
                "reason": review.get("reason") or x.get("judge_line") or "",
                "blockers": "；".join(b["kind"] + "：" + b["detail"] for b in review.get("blockers", [])),
                "action": review.get("next_step") or x.get("action") or "",
                **{k: x.get(k) for k in ("elig_line", "loc", "pay", "deadline", "judge_line")},
                **{k: x.get(k) for k in ("new", "first_seen", "first_seen_basis", "discovery_batch", "discovery_baseline", "last_verified", "verification_status", "verification_source", "verification_note", "last_verification_attempt")}, **extra}

    soon = []
    for u in summary["upcoming"]:
        d = (parse_date(u["date"]) - today).days
        if d > 3:
            continue
        m = next((s for s in soon if (s["date"], s["company"], s["action"]) == (u["date"], u["company"], u["action"])), None)
        if m:
            m["reqs"].append(u["req"])
        else:
            soon.append({**u, "days": d, "reqs": [u["req"]]})
    due = sorted((x for x in actionable if x.get("deadline") and 0 <= (parse_date(x["deadline"]) - today).days <= 10),
                 key=lambda x: x["deadline"])
    fresh = sorted((x for x in actionable if age(x) is not None and age(x) <= 7),
                   key=render_board.recommendation_key)
    discovered = sorted((x for x in main if x.get("new")), key=render_board.recommendation_key)
    recommended = sorted((x for x in actionable if x.get("priority_review")), key=render_board.recommendation_key)
    return {
        "counts": {t: sum(1 for x in main if x["tier"] == t) for t in TIER_ORDER[:3]},
        "n_todo": len(main), "n_manual": sum(1 for x in main if x.get("track") == "M"),
        "n_actionable": len(actionable), "n_paused": len(main) - len(actionable),
        "todo": [card(x) for x in main],
        "recommended": [card(x, rank=x["priority_review"]["rank"], reason=x["priority_review"]["reason"],
                             blockers="；".join(b["kind"] + "：" + b["detail"] for b in x["priority_review"].get("blockers", []))) for x in recommended],
        "n_flight": sum(1 for a in apps if a["open"]), "missing": summary.get("missing", 0),
        "soon": soon,
        "due": [card(x, days=(parse_date(x["deadline"]) - today).days, deadline=x["deadline"]) for x in due],
        "fresh": [card(x, age=age(x)) for x in fresh], "n_fresh": len(fresh),
        "discovered": [card(x) for x in discovered], "n_discovered": len(discovered),
        "manual": [card(x, action=x["action"]) for x in actionable if x.get("track") == "M" and x.get("action")],
    }


def print_hc(hc, today):
    if not hc:
        return
    import render_board
    c = {g: sum(t["counts"][g] for t in hc["tiers"]) for g in ("progress", "waiting", "pending", "rejected", "closed", "archived", "todo")}
    todo = " ".join(f"{t['tier'].split()[0]}{t['counts']['todo']}" for t in hc["tiers"])
    print(f"医疗 AI 扫描池：入围 {sum(t['total'] for t in hc['tiers'])}（不含 stretch {hc['stretch']}）｜"
          f"已投 {c['progress'] + c['waiting'] + c['pending'] + c['rejected'] + c['archived']}（在途 {c['progress'] + c['waiting'] + c['pending']}、已拒 {c['rejected']}）｜"
          f"下线·关闭 {c['closed']}｜沉默归档 {c['archived']}｜待投 {todo}")
    for i in sorted((i for i in hc["items"] if i["group"] == "todo" and i["deadline"] and i["deadline"] >= today.isoformat()),
                    key=lambda i: i["deadline"]):
        print(f"  ⏳ {i['deadline']}（{(parse_date(i['deadline']) - today).days} 天后）{i['company']} {i['req']}：截止投递（{i['tier']}）")
    nxt = sorted((i for i in hc["items"] if i["group"] == "todo" and (i.get("priority_review") or {}).get("actionable", True)), key=render_board.recommendation_key)[:5]
    for n, i in enumerate(nxt, 1):
        sg = (i.get("wage") or {}).get("suggest")
        rank = (i.get("priority_review") or {}).get("rank")
        action_order = f"行动 #{rank}" if rank else "尚未逐岗复核"
        print(f"  {n}. {i['tier'].split()[0]} {i['company']}｜{i['title'][:44]}｜{action_order}"
              + (f"｜建议填 {sg['amount']}（L{sg['level']}）" if sg else ""))
    if hc["age_days"] is not None and hc["age_days"] > 7:
        print(f"  ⚠️  扫描池快照已 {hc['age_days']} 天没刷新（{hc['generated']}），档位与下线信息可能过时")
    if (hc.get("verify_age") or 0) >= 2 or hc.get("verify_unchecked"):
        print(f"  ⚠️  待投岗的在招核验最早一条是 {hc.get('verify_oldest') or '—'}（{hc.get('verify_age')} 天前），"
              f"另有 {hc.get('verify_unchecked', 0)} 条没核验过；投前先跑："
              "cd 工具/healthcare岗位抓取 && python3 apply_links.py verify --batch-dir \"../../求职机会/科技公司-医疗AI/Healthcare-AI-扫描-2026-09\"; python3 render_progress.py")


def print_summary(s, today, out_path):
    g = s["groups"]
    print(f"求职进度 {today.isoformat()}：{s['companies']} 家公司 / {s['applications']} 条记录"
          f"（{s['first_date']} 起）")
    print(f"  有申请存在证据 {s['confirmed_applications']} ｜ 投递证据未齐 {s['unconfirmed_records']}（不计入申请去向图）")
    print(f"  内推中未投递 {g['pending']['apps']} ｜ 待后续 {g['waiting']['apps']} ｜ 当前推进 {g['progress']['apps']}"
          f" ｜ 暂停 {g['paused']['apps']} ｜ 已拒 {g['rejected']['apps']} ｜ 已关闭 {g['closed']['apps']} ｜ 沉默归档 {g['archived']['apps']}")
    merged = []
    for u in s["upcoming"]:
        m = next((x for x in merged if (x["date"], x["company"], x["action"]) == (u["date"], u["company"], u["action"])), None)
        if m:
            m["reqs"].append(u["req"])
        else:
            merged.append({**u, "reqs": [u["req"]]})
    for u in merged:
        days = (parse_date(u["date"]) - today).days
        when = f"已过 {-days} 天" if days < 0 else ("今天" if days == 0 else f"{days} 天后")
        reqs = [r for r in u["reqs"] if r]
        req = f" {' / '.join(reqs)}" if reqs else ""
        print(f"  ⏳ {u['date']}（{when}）{u['company']}{req}：{u['action']}")
    if s.get("missing"):
        print(f"  📝 待补投递记录 {s['missing']} 条（简历版本 / 期望薪资），见面板「待补投递记录」")
    if out_path:
        print(f"面板已写入 {out_path}")


def load_manual_followups(path=MANUAL_FOLLOWUPS_FILE):
    if not path.exists():
        return {"records": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    seen = set()
    for row in data["records"]:
        if row["target_id"] in seen:
            raise ValueError("后续事项重复：" + row["target_id"])
        seen.add(row["target_id"])
        if row["disposition"] not in ("ready", "employer_confirmation", "user_confirmation", "hold", "excluded"):
            raise ValueError("后续事项状态无效：" + row["target_id"])
        for key in ("completed", "needs_user", "needs_employer", "materials"):
            if not isinstance(row[key], list):
                raise ValueError("后续事项字段须为列表：" + key)
    return data


def main():
    ap = argparse.ArgumentParser(description="生成求职进度面板（本地 HTML）")
    ap.add_argument("--today", help="按此日期计算（YYYY-MM-DD），默认今天")
    ap.add_argument("--open", action="store_true", help="生成后在浏览器打开")
    ap.add_argument("--check", action="store_true", help="只校验 applications.json")
    ap.add_argument("--out", default=str(OUT_FILE), help="输出路径")
    ap.add_argument("--data", default=str(DATA_FILE), help="数据文件（默认同目录 applications.json）")
    ap.add_argument("--no-healthcare", action="store_true", help="不接医疗 AI 扫描池（默认读 healthcare岗位抓取/pool_snapshot.json）")
    args = ap.parse_args()

    today = parse_date(args.today) if args.today else dt.datetime.now(ZoneInfo("America/Chicago")).date()
    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    errors, warnings = validate(data, today)
    t_err, t_warn = validate_targets()
    errors += t_err
    warnings += t_warn
    for w in warnings:
        print(f"⚠️  {w}", file=sys.stderr)
    if errors:
        for e in errors:
            print(f"❌ {e}", file=sys.stderr)
        print(f"applications.json / targets.json 有 {len(errors)} 处错误，未生成面板。", file=sys.stderr)
        return 1
    try:
        internships = load_internships(today=today)
        manual_followups = load_manual_followups()
    except (ValueError, KeyError, TypeError) as exc:
        print(f"实习或后续事项数据有误，未生成面板：{exc}", file=sys.stderr)
        return 1
    account = data.get("gmail_account", "")
    apps = [derive(a, today, account) for a in data["applications"]]
    summary = summarize(apps, today)
    hc = None if args.no_healthcare else load_healthcare(apps, today)
    board, board_html = load_board(today, applications=data["applications"])
    tp = today_panel(board, summary, apps, today)
    if args.check:
        print_summary(summary, today, None)
        print_hc(hc, today)
        print(f"待投岗位：{tp['n_todo']} 条（不含 stretch；其中手工调研 {tp['n_manual']}）｜近 7 天发布 {tp['n_fresh']}｜10 天内截止 {len(tp['due'])}")
        return 0

    payload = {
        "today": today.isoformat(),
        "generated": dt.datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M"),
        "data_updated": data.get("updated", ""),
        "event_labels": EVENT_TYPES,
        "apps": apps,
        "unmapped_updates": unmapped_updates(data),
        "analytics_summary": analytics_summary(apps, unmapped_updates(data)),
        "summary": summary,
        "hc": hc,
        "today_panel": tp,
        "opportunity_refresh": board.get("opportunity_refresh"),
        "internships": internships,
        "manual_followups": manual_followups,
        "board_html": board_html,
        "company_items": [{k: x.get(k, "") for k in ("company", "req", "title")} for x in board["items"] + apps + internships["items"]],
    }
    blob = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    html = (TEMPLATE_FILE.read_text(encoding="utf-8").replace("__DATA_JSON__", blob)
            .replace("__COMPANY_FILTER_CSS__", (HERE / "company_filter.css").read_text(encoding="utf-8"))
            .replace("__COMPANY_FILTER_JS__", (HERE / "company_filter.js").read_text(encoding="utf-8"))
            .replace("__CHART_MODELS_JS__", (HERE / "chart_models.js").read_text(encoding="utf-8")))
    out = Path(args.out)
    out.write_text(html, encoding="utf-8")
    print_summary(summary, today, out)
    print_hc(hc, today)
    print(f"待投岗位：{tp['n_todo']} 条（不含 stretch；其中手工调研 {tp['n_manual']}）｜近 7 天发布 {tp['n_fresh']}｜10 天内截止 {len(tp['due'])}")
    if args.open:
        subprocess.run(["open", str(out)], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
