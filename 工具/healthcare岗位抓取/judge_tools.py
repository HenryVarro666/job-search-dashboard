#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""healthcare 岗位抓取 —— 判读阶段的确定性工具（分批 / 复算档位 / 选核验集 / 收口）。

用法：
    python3 judge_tools.py batches  --batch-dir <批次目录>            # kept+stretch+sa_health → 数据/判读输入/batch_NN.json
    python3 judge_tools.py recompute --batch-dir <批次目录>           # 合并 判读输出/*.json → 数据/LLM判读结果_N岗.json，复算 derived_tier，旗标不一致
    python3 judge_tools.py verifyset --batch-dir <批次目录> [--seed 1] # 选核验集 → 数据/核验输入/verify_NN_<lens>.json
    python3 judge_tools.py reconcile --batch-dir <批次目录>           # 合并 核验输出/*.json → 数据/对抗核验结果_N岗.json + 终评_N岗.json + 分歧记录.md
    python3 judge_tools.py eligset  --batch-dir <批次目录> --prefix elig_<stamp>
                                                                     # 未审计的入围岗 → 数据/资格审计输入/，参数给 外部资格审阅者，之后再 reconcile

档位由外部审阅者填写，脚本复算并标记不一致；本仓库不附带外部判读 runner。
"""
import argparse
import copy
import datetime
import json
import os
import random
import re
import sys
from collections import defaultdict

from common import OUT_DIR, read_json, write_json

TIERS = ["🔴 不投", "stretch", "🟡 条件投", "🟢 投", "🥇 投"]
RANK = {t: i for i, t in enumerate(TIERS)}
OVERLAP = {("CORE_TO_JD", "STRONG"): "CORE_MATCH", ("CORE_TO_JD", "STRONG_WITH_CAVEAT"): "CORE_MATCH_CAVEAT",
           ("CORE_TO_JD", "PARTIAL"): "ADJACENT", ("CORE_TO_JD", "NONE"): "GAP",
           ("MENTIONED", "STRONG"): "ADJACENT", ("MENTIONED", "STRONG_WITH_CAVEAT"): "ADJACENT",
           ("MENTIONED", "PARTIAL"): "MINOR_GAP", ("MENTIONED", "NONE"): "MINOR_GAP"}
DOMAIN_TAGS = ("neuroimaging", "medical-imaging-general", "VLM-post-training", "clinical-NLP",
               "agentic-LLM", "ML-platform-SWE", "genomics-omics", "EHR", "devices-FDA")
# No candidate evidence is bundled. Ranking CLI commands require a local fact sheet.
STRENGTH = dict.fromkeys(DOMAIN_TAGS, "NONE")


def load_strength(batch_dir):
    """Read the explicit domain-strength table in local 候选人事实表.md §二.

    Missing or incomplete profiles stop ranking. Clear prior state first so a later
    batch cannot inherit another candidate's strengths or publication evidence.
    """
    STRENGTH.update(dict.fromkeys(DOMAIN_TAGS, "NONE"))
    EVID_TAGS.clear()
    p = os.path.join(batch_dir, "候选人事实表.md")
    if not os.path.isfile(p):
        raise SystemExit(f"Candidate fact sheet required: {p}. See docs/CRAWLER_SETUP.md.")
    with open(p, encoding="utf-8") as source:
        text = source.read()
    if "## 二" not in text:
        raise SystemExit(f"Missing domain-strength section (## 二): {p}")
    sec = text.split("## 二", 1)[1].split("\n## ", 1)[0]
    got = {}
    for line in sec.split("\n"):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] in DOMAIN_TAGS and cells[1] in ("STRONG", "STRONG_WITH_CAVEAT", "PARTIAL", "NONE"):
            got[cells[0]] = cells[1]
    missing = set(DOMAIN_TAGS) - set(got)
    if missing:
        raise SystemExit(f"Incomplete domain-strength table: {sorted(missing)}. See {p}")
    STRENGTH.update(got)
    # Optional first/co-first-author evidence uses explicit local evidence IDs.
    for m in re.finditer(r"^\|\s*(E-PUB-[A-Z0-9-]+-(?:1ST|CO1ST))\s*\|([^\n]*)", text, re.M):
        mt = re.search(r"tag 归属：([^|]+)", m.group(2))
        if mt:
            EVID_TAGS[m.group(1)] = {t for t in re.split(r"\s*/\s*", mt.group(1).strip()) if t in DOMAIN_TAGS}
    return STRENGTH


EVID_TAGS = {}     # 一作 / 共一发表 → 所属领域 tag，load_strength() 从事实表 §一 读
POSTED = {}        # uid → 发布日，load_posted() 从 jobs_all.json 读
STALE_DAYS = 365   # Older listings are capped at conditional pending current verification.
ELIGIBILITY_POLICY_VERSION = "2026-10-17e"
DOMAIN_POLICY_VERSION = "2026-10-17-domain1"


def load_domain_audits(batch_dir):
    """单独保存领域复核；不覆盖首轮判读或应届资格审计。日期较新的同 req 记录优先。"""
    folder = os.path.join(batch_dir, "数据", "领域审计输出")
    by_uid = {}
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if not name.endswith(".json") or name.startswith("_"):
            continue
        for row in read_json(os.path.join(folder, name), []) or []:
            fit = row.get("domain_fit") or {}
            if fit.get("policy_version") != DOMAIN_POLICY_VERSION:
                continue
            uid = row.get("uid")
            old = by_uid.get(uid, {})
            if fit.get("as_of_date", "") >= old.get("as_of_date", ""):
                by_uid[uid] = fit
    return by_uid


def apply_domain_audit(j, fit):
    """更新当前建议与投入，首轮说明另存；不把领域待学写成已满足。"""
    current = j.get("domain_fit") or {}
    if (current.get("policy_version") == DOMAIN_POLICY_VERSION and current.get("as_of_date")
            and current["as_of_date"] >= fit.get("as_of_date", "")):
        return j  # 新 JD 已重新判读时，旧的单次领域审计不能覆盖新判读。
    j["domain_fit"] = copy.deepcopy(fit)
    status = fit.get("status")
    if status in ("METHOD_TRANSFER", "GAP", "UNVERIFIED"):
        j.setdefault("domain_review_previous", {k: copy.deepcopy(j.get(k)) for k in ("one_line_cn", "cost_tiers")})
        label = {"METHOD_TRANSFER": "仅方法迁移", "GAP": "必需领域/模态不符", "UNVERIFIED": "领域要求待确认"}[status]
        j["one_line_cn"] = (label + "：" + fit.get("gap_cn", ""))[:120]
        costs = j.get("cost_tiers") or {}
        for key in ("A", "B", "C"):
            c = costs.get(key)
            if not c:
                continue
            c["note_cn"] = fit.get("gap_cn", "")[:160]
            if status == "GAP" or key == "B":
                c.update(judgement_cn="不做", p_offer_band="不适用")
            elif key == "A":
                c["cost"] = "0.5–1 h"
            else:
                c["note_cn"] = ("确认领域要求与训练支持后再决定。" + fit.get("gap_cn", ""))[:160]
    return j


def load_posted():
    if not POSTED:
        for r in read_json(os.path.join(OUT_DIR, "jobs_all.json"), []) or []:
            if r.get("posted"):
                POSTED[r["uid"]] = str(r["posted"])[:10]
    return POSTED


def posted_days(uid):
    try:
        return (datetime.date.today() - datetime.date.fromisoformat(POSTED.get(uid, ""))).days
    except ValueError:
        return None


def parse_val(v):
    """核验输出里 old/new 一律是字符串；能按 JSON 解析成非字符串的（数字、null、列表、对象、布尔）就解析。"""
    if not isinstance(v, str):
        return v
    t = v.strip()
    if t in ("null", "None", ""):
        return None
    try:
        x = json.loads(t)
        return x
    except ValueError:
        return v


_SEG_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def set_path(obj, path, value):
    """按 a.b[2].c 路径赋值；中间节点缺失即放弃（返回 False），末位下标等于长度时追加。"""
    segs = [(m.group(1), m.group(2)) for m in _SEG_RE.finditer(path)]
    if not segs:
        return False
    node = obj
    for key, idx in segs[:-1]:
        if key is not None:
            if not isinstance(node, dict) or key not in node:
                return False
            node = node[key]
        else:
            i = int(idx)
            if not isinstance(node, list) or i >= len(node):
                return False
            node = node[i]
    key, idx = segs[-1]
    if key is not None:
        if not isinstance(node, dict):
            return False
        node[key] = value
        return True
    i = int(idx)
    if not isinstance(node, list):
        return False
    if i < len(node):
        node[i] = value
    elif i == len(node):
        node.append(value)
    else:
        return False
    return True


def normalize_counts(j):
    """修正后按条目重新计数、按冻结映射重算重叠，避免核验员只改了条目没改计数。"""
    cov = j.get("standout_coverage") or {}
    items = [it for it in cov.get("items", []) if isinstance(it, dict)]
    if items:
        cov["n_items"] = len(items)
        cov["n_have"] = sum(1 for it in items if str(it.get("candidate_status", "")).startswith("HAVE"))
        cov["n_partial"] = sum(1 for it in items if it.get("candidate_status") == "PARTIAL")
        cov["coverage_ratio"] = round((cov["n_have"] + 0.5 * cov["n_partial"]) / max(1, cov["n_items"]), 3)
    dom = j.get("domain_overlap") or {}
    for t in dom.get("tags", []):
        st = STRENGTH.get(t.get("tag"))
        if st:
            t["candidate_strength"] = st
            t["overlap"] = OVERLAP.get((t.get("jd_weight"), st), "N_A")
    tags = dom.get("tags", [])
    dom["core_match_count"] = sum(1 for t in tags if t["overlap"] in ("CORE_MATCH", "CORE_MATCH_CAVEAT"))
    dom["gap_on_core_count"] = sum(1 for t in tags if t["overlap"] == "GAP")


# ---------------------------------------------------------------- 分批

def make_batches(batch_dir, size=9, uids=None, prefix="batch"):
    """uids=None 为全量模式（删除旧 batch_*）；给 uids 时只为这些 uid 出批，文件名用 prefix，不动其他批。"""
    fd = os.path.join(OUT_DIR, "funnel")
    recs = []
    for name, tag in (("kept_for_llm.json", "kept"), ("stretch.json", "stretch"), ("sa_health.json", "sa_health")):
        for r in read_json(os.path.join(fd, name), []) or []:
            r["_pool"] = tag
            recs.append(r)
    if uids is not None:
        want = set(uids)
        recs = [r for r in recs if r["uid"] in want]
        if not recs:
            print("没有需要判读的新 uid")
            return []
    by_co = defaultdict(list)
    for r in recs:
        by_co[r["company_key"]].append(r)
    groups = []
    small = []
    for key, rs in sorted(by_co.items(), key=lambda kv: -len(kv[1])):
        if len(rs) <= 3:
            small.extend(rs)
            continue
        for i in range(0, len(rs), size):
            groups.append(rs[i:i + size])
    for i in range(0, len(small), size):
        groups.append(small[i:i + size])
    outdir = os.path.join(batch_dir, "数据", "判读输入")
    os.makedirs(outdir, exist_ok=True)
    if uids is None and prefix == "batch":
        for f in os.listdir(outdir):
            if f.startswith("batch_"):
                os.remove(os.path.join(outdir, f))
    keep_fields = ["uid", "req_id", "job_id", "company", "company_key", "track", "title", "url", "api_url", "org_unit",
                   "department", "location", "region", "work_site", "posted", "can_apply", "time_type", "level_tag",
                   "level_source", "multi_level", "stretch", "eff_min_years", "min_years_required", "phd_branch_years",
                   "phd_alt_path", "has_equivalent_clause", "years_evidence", "restriction_flags", "restriction_evidence",
                   "pay_range", "pay_low", "pay_high", "role_family", "role_family_evidence", "healthcare_hits",
                   "sections", "sections_found", "jd_text", "jd_len", "sibling_req_ids", "sibling_locations", "_pool", "extra",
                   "fetched_at", "source_authority"]
    manifest = []
    for n, g in enumerate(groups, 1):
        items = [{k: r.get(k) for k in keep_fields if k in r} for r in g]
        p = os.path.join(outdir, f"{prefix}_{n:02d}.json")
        write_json(p, {"batch_id": f"{prefix}_{n:02d}", "n": len(items), "companies": sorted({r['company'] for r in g}), "records": items})
        manifest.append({"batch_id": f"{prefix}_{n:02d}", "n": len(items), "companies": sorted({r['company'] for r in g}),
                         "pools": dict(zip(*[[x for x in y] for y in zip(*[(p, sum(1 for r in g if r['_pool'] == p)) for p in ('kept', 'stretch', 'sa_health')])]))})
    write_json(os.path.join(outdir, f"_manifest_{prefix}.json"), manifest)
    print(f"{len(recs)} 条 → {len(groups)} 批（每批 ≤{size}），{outdir}")
    return manifest


# ---------------------------------------------------------------- 档位推导

def years_gate(years):
    """复核旧判读中的宽松 PASS；博士分支后的额外年限仍须满足。"""
    status = years.get("status")
    if status == "FAIL":
        return "FAIL", "years FAIL"
    needed = years.get("phd_branch_years")
    if needed is None:
        needed = years.get("required_years_min")
    if needed is None or needed == 0:
        return status, ""
    counts = years.get("counts_research_as_experience", "SILENT")
    if counts == "NO_EXPLICIT":
        return "FAIL", "额外 industry/professional/post-degree 年限不满足"
    if counts == "SILENT":
        if needed <= 2:
            return "CONDITIONAL", "一般经验 1–2 年；研究是否计入未确认，最高条件投"
        return "FAIL", "一般经验超过 2 年，未明确接受博士研究或替代"
    if counts == "YES_EXPLICIT":
        # The reviewer must verify non-overlapping duration from this candidate's
        # local evidence. A job's acceptance of research proves no candidate tenure.
        research_years = years.get("verified_research_years")
        evidence_ids = years.get("research_evidence_ids")
        if (type(research_years) not in (int, float) or not 0 <= research_years < float("inf")
                or not isinstance(evidence_ids, list) or not evidence_ids
                or not all(isinstance(item, str) and item.strip() for item in evidence_ids)):
            return "CONDITIONAL", "JD 明确接受研究，但候选人可证研究年限或证据尚未核实"
        if needed > research_years:
            return "FAIL", f"明确接受研究，但需 {needed} 年，当前可证研究不足（{research_years:.2f} 年）"
    return status, ""


def derive(j):
    """资格先于技能；与 候选人事实表.md §五 的 2026-10-17e 规则一致。

    另加两条 §五 已同步写入的规则：🟢 至少 1 个 HAVE；挂牌超过 STALE_DAYS 天封顶 🟡。
    """
    hg = j["hard_gates"]
    cov = j["standout_coverage"]
    dom = j["domain_overlap"]
    reach = j["official_level"]["reachability"]
    tags = dom["tags"]
    core_match = sum(1 for t in tags if t["overlap"] == "CORE_MATCH")
    core_caveat = sum(1 for t in tags if t["overlap"] == "CORE_MATCH_CAVEAT")
    gap_core = sum(1 for t in tags if t["overlap"] == "GAP")
    n_items = max(1, cov.get("n_items", 0))
    ratio = (cov.get("n_have", 0) + 0.5 * cov.get("n_partial", 0)) / n_items
    reasons = []
    if hg["restrictions"]["status"] != "NONE":
        return "🔴 不投", ["restriction " + hg["restrictions"]["status"]], ratio
    if hg["degree"]["status"] == "FAIL":
        return "🔴 不投", ["degree FAIL"], ratio
    if hg["role_family_fit"]["status"] == "FAIL":
        return "🔴 不投", ["role_family FAIL"], ratio
    if hg["hard_skill_andgate"]["status"] == "FAIL":
        return "🔴 不投", ["hard_skill_andgate FAIL " + ",".join(hg["hard_skill_andgate"].get("missing", []))], ratio
    fit = j.get("domain_fit") or {}
    if fit.get("required_domain_status") == "UNMET" or fit.get("status") == "GAP":
        return "🔴 不投", ["必需领域/模态经验未满足：" + fit.get("gap_cn", "")], ratio
    ys, years_reason = years_gate(hg["years"])
    if ys == "FAIL":
        return "🔴 不投", [years_reason], ratio
    if reach == "STAFF_PLUS":
        return "🔴 不投", ["staff/管理/技术负责人职级，应届不可达；技能不能抵消"], ratio
    cap = None
    def setcap(t, why):
        nonlocal cap
        if cap is None or RANK[t] < RANK[cap]:
            cap = t
        reasons.append(why)
    if fit.get("status") == "METHOD_TRANSFER":
        setcap("🟡 条件投", "仅 ML 方法可迁移，尚无目标医学细分领域经历：" + fit.get("gap_cn", ""))
    if fit.get("status") not in ("DIRECT", "METHOD_TRANSFER", "GENERAL_TECH") or fit.get("required_domain_status") not in ("MET", "NOT_REQUIRED"):
        setcap("🟡 条件投", "医学细分领域/数据模态尚未完成核查，不能由科室或通用影像标签证明匹配")
    if ys == "CONDITIONAL":
        setcap("🟡 条件投", years_reason or "研究年限能否计入待确认")
    if reach == "UNKNOWN":
        setcap("🟡 条件投", "职级/应届入口未证实，不能凭未写年限认定可达")
    if reach == "SENIOR_NO_BRANCH":   # 2026-09-23 补：资深职级且无 PhD 分支，应届最多观察
        setcap("stretch", "senior 职级且无 PhD 分支")
    if j["official_level"].get("dual_level") and reach != "ENTRY_OR_NCG":
        setcap("🟡 条件投", "dual level")
    if gap_core >= 1 and core_match == 0 and core_caveat == 0:
        return "🔴 不投", ["core GAP with no core match"], ratio
    if gap_core >= 1:
        setcap("🟡 条件投", "core GAP present")
    jm = j["judge_meta"]
    # §五：只有职责段可依，或 JD 没有 preferred 段 → coverage 不可验证，封顶 🟡。
    # 段落清单 recompute 时已用当前解析器补过（只补不删）；判读员明确标了 STANDOUT_SECTION
    # （"Bonus Points" 这类解析器认不出的标题）时算有 preferred 段。
    if jm["standout_source"] == "RESPONSIBILITIES_FALLBACK":
        setcap("🟡 条件投", "UNVERIFIED coverage (no stand-out section)")
    elif "preferred" not in (jm.get("jd_sections_present") or []) and jm["standout_source"] != "STANDOUT_SECTION":
        setcap("🟡 条件投", "UNVERIFIED coverage (JD 无 preferred 段)")
    pd_ = posted_days(j.get("uid"))
    if pd_ is not None and pd_ > STALE_DAYS:
        setcap("🟡 条件投", f"挂牌 {pd_} 天（>{STALE_DAYS}）")
    fn = j["funnel"]
    if fn["S1"]["severity"] == "HARD" or fn["S2"]["severity"] == "HARD":
        setcap("🟡 条件投", "S1/S2 HARD blocker")
    # §五 末段：一作 / 共一发表所属的 tag 里，至少一个在本 JD 是 CORE_TO_JD
    core_tags = {t["tag"] for t in tags if t.get("jd_weight") == "CORE_TO_JD"}
    first_author_core = any(
        it.get("candidate_status") == "HAVE_FIRST_AUTHOR"
        and any(EVID_TAGS.get(e, set()) & core_tags for e in it.get("evidence_ids", []))
        for it in cov.get("items", []))
    if core_match >= 1 and ratio >= 0.60 and first_author_core and reach in ("ENTRY_OR_NCG", "MID_IC_REACHABLE", "SENIOR_WITH_PHD_BRANCH"):
        tier = "🥇 投"
    elif (core_match >= 1 or core_caveat >= 1) and ratio >= 0.40 and cov.get("n_have", 0) >= 1:
        tier = "🟢 投"   # 至少 1 个 HAVE：全靠 PARTIAL 凑出的 coverage 不给 🟢（2026-09-30）
    elif ratio >= 0.25:
        tier = "🟡 条件投"
    else:
        tier = "🔴 不投"
    if cap and RANK[cap] < RANK[tier]:
        tier = cap
    return tier, reasons, ratio


def recompute(batch_dir):
    load_strength(batch_dir)
    load_posted()
    outdir = os.path.join(batch_dir, "数据", "判读输出")
    allj = []
    files = [f for f in (os.listdir(outdir) if os.path.isdir(outdir) else []) if f.endswith(".json") and not f.startswith("_")]
    files.sort(key=lambda f: os.path.getmtime(os.path.join(outdir, f)))   # 旧 → 新；同 uid 后写入者覆盖
    by_uid_latest = {}
    for f in files:
        d = read_json(os.path.join(outdir, f), None)
        if isinstance(d, dict):
            d = d.get("judgments") or d.get("records") or []
        for j in d or []:
            j["_batch_file"] = f
            by_uid_latest[j.get("uid") or f"{f}:{j.get('req_id')}"] = j
    allj = list(by_uid_latest.values())
    # 段落清单是机械字段：用当前解析器结果覆盖判读里抄来的旧值（2026-09-23 解析器修复后需要）
    cur = {r["uid"]: r.get("sections_found") for r in (read_json(os.path.join(OUT_DIR, "jobs_all.json"), []) or [])}
    for j in allj:
        sf = cur.get(j.get("uid"))
        if sf is None:
            continue
        jm = j.setdefault("judge_meta", {})
        old = list(jm.get("jd_sections_present") or [])
        newp = sorted(set(old) | {x for x in sf if x in ("responsibilities", "minimum", "preferred", "pay")})   # 只补不删
        if sorted(old) != newp:
            jm["jd_sections_present_judged"] = old
            jm["jd_sections_present"] = newp
    flags = []
    domain_audits = load_domain_audits(batch_dir)
    for j in allj:
        if j.get("uid") in domain_audits:
            apply_domain_audit(j, domain_audits[j["uid"]])
        try:
            tier, reasons, ratio = derive(j)
        except KeyError as e:
            flags.append({"uid": j.get("uid"), "issue": f"schema 缺字段 {e}"})
            continue
        j["script_tier"] = tier
        j["script_reasons"] = reasons
        j["script_coverage_ratio"] = round(ratio, 3)
        # 复算 overlap
        for t in j["domain_overlap"]["tags"]:
            exp_strength = STRENGTH.get(t["tag"])
            if t["candidate_strength"] != exp_strength:
                flags.append({"uid": j.get("uid"), "issue": f"tag {t['tag']} candidate_strength {t['candidate_strength']} ≠ 事实表 {exp_strength}"})
            exp_ov = OVERLAP.get((t["jd_weight"], exp_strength), "N_A")
            if t["overlap"] != exp_ov:
                flags.append({"uid": j.get("uid"), "issue": f"tag {t['tag']} overlap {t['overlap']} ≠ 映射 {exp_ov}"})
                t["overlap"] = exp_ov
        if j.get("derived_tier") != tier or j.get("judge_tier") != tier:
            flags.append({"uid": j.get("uid"), "issue": f"档位不一致 judge={j.get('judge_tier')} derived={j.get('derived_tier')} script={tier} ({'; '.join(reasons)})"})
        if abs(j["standout_coverage"].get("coverage_ratio", 0) - ratio) > 0.02:
            flags.append({"uid": j.get("uid"), "issue": f"coverage_ratio {j['standout_coverage'].get('coverage_ratio')} ≠ 复算 {ratio:.2f}"})
        fc = j.get("forbidden_claims_check", {})
        leaks = [e for it in j["standout_coverage"].get("items", []) for e in it.get("evidence_ids", []) if e.startswith("X-")]
        leaks += [e for e in j.get("evidence_used", []) if e.startswith("X-")]
        if leaks or fc.get("clean") is False:
            flags.append({"uid": j.get("uid"), "issue": f"X 泄漏 {leaks} clean={fc.get('clean')}"})
    n = len(allj)
    write_json(os.path.join(batch_dir, "数据", f"LLM判读结果_{n}岗.json"), allj)
    write_json(os.path.join(batch_dir, "数据", "判读复算旗标.json"), flags)
    from collections import Counter
    print(f"合并 {n} 条判读；script_tier 分布 {dict(Counter(j.get('script_tier') for j in allj))}；旗标 {len(flags)}")
    for f in flags[:20]:
        print("  ", f)
    return allj, flags


# ---------------------------------------------------------------- 核验集

LENSES = ["资格视角", "技术视角", "帖子真实性"]


def verifyset(batch_dir, seed=1, size=6, uids=None, prefix="verify"):
    """uids=None 为全量模式（清空 核验输入/）；给 uids 时只在这些 uid 里选核验集，文件名用 prefix。"""
    files = [f for f in os.listdir(os.path.join(batch_dir, "数据")) if f.startswith("LLM判读结果_")]
    if not files:
        raise SystemExit("先跑 recompute")
    allj = read_json(os.path.join(batch_dir, "数据", sorted(files, key=lambda f: os.path.getmtime(os.path.join(batch_dir, "数据", f)))[-1]), [])
    if uids is not None:
        want = set(uids)
        allj = [j for j in allj if j["uid"] in want]
    random.seed(seed)
    sel, why = [], {}
    reds = []
    for j in allj:
        t = j.get("script_tier")
        cov = j.get("script_coverage_ratio", 0)
        hg = j["hard_gates"]
        if t in ("🥇 投", "🟢 投", "stretch"):
            sel.append(j); why[j["uid"]] = "全核验档"
        elif t == "🟡 条件投" and cov >= 0.40:
            sel.append(j); why[j["uid"]] = "🟡 且 coverage≥0.40（可能被误封顶）"
        elif t == "🔴 不投":
            only_years = (hg["years"]["status"] == "FAIL" and hg["restrictions"]["status"] == "NONE"
                          and hg["degree"]["status"] != "FAIL" and hg["role_family_fit"]["status"] == "PASS"
                          and hg["hard_skill_andgate"]["status"] == "NONE")
            if only_years and (hg["years"].get("equivalence_clause_quote") or j["official_level"]["reachability"] == "SENIOR_WITH_PHD_BRANCH"):
                sel.append(j); why[j["uid"]] = "🔴 仅年限失败且有等价条款/PhD 分支（假阴性样本）"
            else:
                reds.append(j)
    k = max(8 if uids is None else 1, int(round(len(reds) * 0.15))) if reds else 0
    by_track = defaultdict(list)
    for j in reds:
        by_track[j.get("track")].append(j)
    picked = []
    for tr, js in by_track.items():
        random.shuffle(js)
        picked += js[:max(1, int(round(k * len(js) / max(1, len(reds)))))]
    for j in picked[:k]:
        sel.append(j); why[j["uid"]] = "🔴 随机 15% 分层样本"
    # 附 JD 文本
    jd_by_uid = {}
    inp = os.path.join(batch_dir, "数据", "判读输入")
    in_files = [f for f in os.listdir(inp) if f.endswith(".json") and not f.startswith("_")]
    in_files.sort(key=lambda f: os.path.getmtime(os.path.join(inp, f)))       # 全量批与增量批都读，新文件覆盖旧文件
    for f in in_files:
        if True:
            for r in (read_json(os.path.join(inp, f), {}) or {}).get("records", []):
                jd_by_uid[r["uid"]] = {"jd_text": r.get("jd_text"), "sections": r.get("sections"), "jd_len": r.get("jd_len"),
                                       "level_tag": r.get("level_tag"), "eff_min_years": r.get("eff_min_years"),
                                       "phd_branch_years": r.get("phd_branch_years"), "url": r.get("url"), "can_apply": r.get("can_apply"),
                                       "posted": r.get("posted"), "company_key": r.get("company_key"),
                                       "fetched_at": r.get("fetched_at"), "source_authority": r.get("source_authority"),
                                       "sibling_req_ids": r.get("sibling_req_ids")}
    # 公司 JD 长度中位数（帖子真实性镜头用）
    lens_by_co = defaultdict(list)
    for u, d in jd_by_uid.items():
        lens_by_co[d["company_key"]].append(d.get("jd_len") or 0)
    med = {c: sorted(v)[len(v) // 2] for c, v in lens_by_co.items() if v}
    outdir = os.path.join(batch_dir, "数据", "核验输入")
    os.makedirs(outdir, exist_ok=True)
    if uids is None and prefix == "verify":
        for f in os.listdir(outdir):
            os.remove(os.path.join(outdir, f))
    n = 0
    for i in range(0, len(sel), size):
        chunk = sel[i:i + size]
        n += 1
        for lens in LENSES:
            items = []
            for j in chunk:
                jj = {k: v for k, v in j.items() if not k.startswith("_")}
                d = jd_by_uid.get(j["uid"], {})
                jj["_jd"] = d
                jj["_company_jd_len_median"] = med.get(d.get("company_key"))
                jj["_why_selected"] = why.get(j["uid"])
                # 核验提示词约定的字段名（与 _jd 冗余，便于镜头直接读）
                jj["jd_char_count"] = d.get("jd_len")
                jj["company_median_chars"] = med.get(d.get("company_key"))
                jj["sections_present"] = [k for k, v in (d.get("sections") or {}).items() if v and k != "other"]
                jj["mirror_only"] = bool(d.get("source_authority") == "display")
                jj["can_apply"] = d.get("can_apply")
                jj["posted"] = d.get("posted")
                jj["fetched_at"] = d.get("fetched_at")
                jj["sibling_req_ids"] = j.get("sibling_req_ids") or d.get("sibling_req_ids") or []
                items.append(jj)
            write_json(os.path.join(outdir, f"{prefix}_{n:02d}_{lens}.json"), {"lens": lens, "verify_id": f"{prefix}_{n:02d}", "items": items})
    write_json(os.path.join(outdir, f"_manifest_{prefix}.json"), {"selected": len(sel), "chunks": n, "why": why,
               "files": [f"{prefix}_{i:02d}_{l}.json" for i in range(1, n + 1) for l in LENSES]})
    print(f"核验集 {len(sel)} 条（其中 🔴 样本 {len(picked[:k])}）→ {n} 组 × 3 镜头，{outdir}")


# ---------------------------------------------------------------- 收口

ELIG_OK = ("ELIGIBLE_NEW_GRAD", "ELIGIBLE_PHD_COUNTS", "CONDITIONAL_RESEARCH_YEARS")
ELIG_CN = {"ELIGIBLE_NEW_GRAD": "应届可投", "ELIGIBLE_PHD_COUNTS": "可投（JD 允许 PhD 折算年限）",
           "CONDITIONAL_RESEARCH_YEARS": "有条件（要看招聘方认不认博士研究年限）", "LEVEL_MISMATCH": "职级不符（资深岗）",
           "NOT_ELIGIBLE": "不符合（明文硬门槛）", "TIMING_CONFLICT": "时间冲突（毕业/入职窗口）", "UNCLEAR": "JD 信息不足"}


def load_eligibility(batch_dir):
    """读 数据/资格审计输出/elig_NN.json 与 elig_NN_复核.json（外部资格审阅者 产出）。
    生效结论：有复核且复核推翻 → 用复核结论；否则用审计结论。
    raise_ok：审计与复核都判可投（ELIG_OK）时才允许升档。"""
    d = os.path.join(batch_dir, "数据", "资格审计输出")
    audit, chal, latest = {}, {}, {}
    # 同批审计/复核配对；按规则版本、审计日、mtime选新结果，避免旧 zfix 前缀覆盖新版。
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not f.endswith(".json") or f.endswith("_复核.json"):
            continue
        cf = os.path.join(d, f[:-5] + "_复核.json")
        cmap = {it["uid"]: it for it in (read_json(cf, []) or []) if it.get("uid")} if os.path.exists(cf) else {}
        for it in read_json(os.path.join(d, f), []) or []:
            if it.get("uid"):
                key = (it.get("policy_version") == ELIGIBILITY_POLICY_VERSION,
                       it.get("as_of_date") or "", os.path.getmtime(os.path.join(d, f)))
                if it["uid"] in latest and key < latest[it["uid"]]:
                    continue
                latest[it["uid"]] = key
                audit[it["uid"]] = it
                if it["uid"] in cmap:
                    chal[it["uid"]] = cmap[it["uid"]]
                else:
                    chal.pop(it["uid"], None)
    out = {}
    for uid, a in audit.items():
        c = chal.get(uid)
        current = a.get("policy_version") == ELIGIBILITY_POLICY_VERSION and (not c or (
            c.get("policy_version") == ELIGIBILITY_POLICY_VERSION and a.get("as_of_date")
            and c.get("as_of_date") == a.get("as_of_date")))
        if a.get("policy_version") == ELIGIBILITY_POLICY_VERSION and c and not current:
            c = None   # 旧复核不改新版结论；policy_current=False 仍会重新出批。
        v = a.get("verdict")
        if c and c.get("upheld") is False and c.get("challenger_verdict"):
            v = c["challenger_verdict"]
        out[uid] = {"verdict": v, "verdict_cn": ELIG_CN.get(v, v), "audit_verdict": a.get("verdict"),
                    "policy_version": a.get("policy_version"), "as_of_date": a.get("as_of_date"),
                    "policy_current": bool(current),
                    "confidence": a.get("confidence"), "challenged": bool(c),
                    "challenge_upheld": (c or {}).get("upheld"),
                    "raise_ok": bool(c) and c.get("upheld") is True and a.get("verdict") in ELIG_OK
                                and bool(current),
                    "level_mismatch_kind": (c if c and c.get("upheld") is False else a).get("level_mismatch_kind"),
                    "qualifying_years_min": (c if c and c.get("upheld") is False else a).get("qualifying_years_min"),
                    "counts_research_as_experience": (c if c and c.get("upheld") is False else a).get("counts_research_as_experience"),
                    "one_line_cn": (c or {}).get("one_line_cn") if c and c.get("upheld") is False else a.get("one_line_cn"),
                    "reasoning_cn": a.get("reasoning_cn"), "challenge_reasoning_cn": (c or {}).get("reasoning_cn"),
                    "decisive_quote": (c or {}).get("decisive_quote"), "years_quote": a.get("years_quote"),
                    "timing_clauses": a.get("timing_clauses"), "experienced_hire_signals": a.get("experienced_hire_signals"),
                    "new_grad_signals": a.get("new_grad_signals"), "official_level_text": a.get("official_level_text")}
    return out


def apply_eligibility(j, e, final):
    """按资格结论改写 reachability / years 再重推；TIMING_CONFLICT 直接 🔴。返回 (新档位, 新记录)。"""
    jx = copy.deepcopy(j)
    ol, yy = jx["official_level"], jx["hard_gates"]["years"]
    v = e["verdict"]
    # 旧审计按 1–4 年条件投、industry <2 年可试等口径作出，不能用于升档。
    quoted = bool(e.get("decisive_quote") or e.get("years_quote") or e.get("new_grad_signals"))
    needed = e.get("qualifying_years_min")
    counts = e.get("counts_research_as_experience")
    can_raise = bool(e.get("raise_ok") and quoted and e.get("policy_version") == ELIGIBILITY_POLICY_VERSION
                     and type(needed) is int and needed >= 0 and counts in ("YES_EXPLICIT", "NO_EXPLICIT", "SILENT"))
    senior = ol.get("reachability") == "SENIOR_NO_BRANCH"
    if can_raise and v in ("ELIGIBLE_NEW_GRAD", "ELIGIBLE_PHD_COUNTS"):
        yy["counts_research_as_experience"] = counts
        if v == "ELIGIBLE_PHD_COUNTS":
            yy["phd_branch_years"] = needed
        else:
            yy["required_years_min"], yy["phd_branch_years"] = needed, None
        yy["jd_quote"] = e.get("decisive_quote") or e.get("years_quote") or "NO_CLAUSE"
    if v == "ELIGIBLE_NEW_GRAD" and can_raise:
        if senior:
            ol["reachability"] = "ENTRY_OR_NCG"
        if yy.get("status") == "FAIL":
            yy["status"] = "PASS"
    elif v == "ELIGIBLE_PHD_COUNTS" and can_raise:
        if senior:
            ol["reachability"] = "SENIOR_WITH_PHD_BRANCH"
        if yy.get("status") == "FAIL":
            yy["status"] = "PASS_VIA_PHD_BRANCH"
    elif v == "CONDITIONAL_RESEARCH_YEARS":
        if can_raise and needed in (1, 2) and counts == "SILENT" and yy.get("counts_research_as_experience") != "NO_EXPLICIT":
            yy["required_years_min"], yy["phd_branch_years"] = needed, None
            yy["counts_research_as_experience"] = "SILENT"
            yy["status"] = "CONDITIONAL"
            yy["jd_quote"] = e.get("decisive_quote") or e.get("years_quote")
        if yy.get("status") != "FAIL":
            yy["status"] = "CONDITIONAL"
    elif v == "LEVEL_MISMATCH":
        kind = e.get("level_mismatch_kind")
        if ol.get("reachability") != "STAFF_PLUS" and (kind == "SENIOR_NO_BRANCH" or (not kind and senior)):
            ol["reachability"] = "SENIOR_NO_BRANCH"
        else:
            ol["reachability"] = "STAFF_PLUS"
    elif v == "NOT_ELIGIBLE":
        yy["status"] = "FAIL"
    d2, reasons, _ = derive(jx)
    if v == "TIMING_CONFLICT":
        d2, reasons = "🔴 不投", ["timing conflict"]
    if v == "UNCLEAR" and RANK[d2] > RANK["🟡 条件投"]:
        d2 = "🟡 条件投"
    if RANK[d2] > RANK[final] and not can_raise:
        d2 = final
    if jx.get("jd_unverified") and RANK[d2] > RANK["🟡 条件投"]:
        d2 = "🟡 条件投"
    jx["eligibility"] = dict(e, reasons=reasons)
    return d2, jx


def reconcile(batch_dir):
    load_strength(batch_dir)
    load_posted()
    files = [f for f in os.listdir(os.path.join(batch_dir, "数据")) if f.startswith("LLM判读结果_")]
    allj = read_json(os.path.join(batch_dir, "数据", sorted(files, key=lambda f: os.path.getmtime(os.path.join(batch_dir, "数据", f)))[-1]), [])
    by_uid = {j["uid"]: j for j in allj}
    vdir = os.path.join(batch_dir, "数据", "核验输出")
    notes = defaultdict(list)
    for f in sorted(os.listdir(vdir)) if os.path.isdir(vdir) else []:
        if not f.endswith(".json") or f.startswith("_"):
            continue
        d = read_json(os.path.join(vdir, f), None)
        items = d.get("items") if isinstance(d, dict) else d
        vt = os.path.getmtime(os.path.join(vdir, f))
        for v in items or []:
            v["_verify_file"], v["_verify_mtime"] = f, vt
            notes[v.get("uid")].append(v)
    # 截断判定所需的机械字段（来自核验输入，不信任 verdict 措辞）
    trunc_info = {}
    vin = os.path.join(batch_dir, "数据", "核验输入")
    for f in (os.listdir(vin) if os.path.isdir(vin) else []):
        if f.startswith("verify") and f.endswith(".json"):
            for it in (read_json(os.path.join(vin, f), {}) or {}).get("items", []):
                trunc_info[it.get("uid")] = (it.get("jd_char_count"), it.get("company_median_chars"), it.get("mirror_only"))
    elig = load_eligibility(batch_dir)
    domain_audits = load_domain_audits(batch_dir)
    results, disagreements = [], []
    for uid, j in by_uid.items():
        if uid in domain_audits:
            apply_domain_audit(j, domain_audits[uid])
        first = j.get("script_tier")
        jfile = os.path.join(batch_dir, "数据", "判读输出", j.get("_batch_file", ""))
        jt = os.path.getmtime(jfile) if j.get("_batch_file") and os.path.exists(jfile) else 0
        vs = [v for v in notes.get(uid, []) if v.get("_verify_mtime", 0) >= jt]   # 只用晚于本次判读的核验
        final, rule = first, "NOT_VERIFIED"
        if vs:
            jj = copy.deepcopy(j)
            applied = []
            rescue = False
            for v in vs:
                for c in v.get("field_corrections", []) or []:
                    path = c.get("path", "") or ""
                    if path in ("judge_tier", "derived_tier") or path.startswith("script_"):
                        continue                                   # 档位由脚本重推，不直接采纳
                    if path.startswith("hard_gates.") and not c.get("jd_quote"):
                        continue                                   # 规则 3：硬门槛修正必须附 JD 原句
                    mi = re.fullmatch(r"standout_coverage\.items\[(\d+)\]", path)
                    if mi:                                         # 条目级修正：新值必须是对象；"移除 / remove / null" 视为删除该条
                        nv = parse_val(c.get("new"))
                        if not isinstance(nv, dict):
                            if nv is None or re.match(r"\s*(移除|删除|remove|delete|drop)", str(nv), re.I):
                                items_ = jj["standout_coverage"].get("items", [])
                                k_ = int(mi.group(1))
                                if 0 <= k_ < len(items_) and isinstance(items_[k_], dict):
                                    items_[k_] = {"_removed": True}
                                    applied.append((v, c))
                            continue
                    if set_path(jj, path, parse_val(c.get("new"))):
                        applied.append((v, c))
                        if v.get("lens") == "资格视角" and path.startswith("hard_gates.") and c.get("jd_quote"):
                            rescue = True
            jj["standout_coverage"]["items"] = [it for it in jj["standout_coverage"].get("items", []) if isinstance(it, dict) and not it.get("_removed")]
            normalize_counts(jj)
            derived, reasons, ratio = derive(jj)
            rule = "FIELD_CORRECTIONS_REDERIVED" if applied else "CONFIRMED_ALL"
            if RANK[derived] > RANK[first] and not rescue:
                derived, rule = first, rule + "+NO_RAISE"
            final = derived
            jl, med, mirror = trunc_info.get(uid, (None, None, None))
            if (jl and med and jl < 0.5 * med) or mirror:
                jj["jd_unverified"] = True
                if RANK[final] > RANK["🟡 条件投"]:
                    final, rule = "🟡 条件投", rule + "+CAP_TRUNCATED"
            jj["script_tier"], jj["script_reasons"], jj["script_coverage_ratio"] = first, j.get("script_reasons"), j.get("script_coverage_ratio")
            jj["reverified_reasons"], jj["reverified_coverage_ratio"] = reasons, round(ratio, 3)
            j.clear(); j.update(jj)
            for v, c in applied:
                disagreements.append({"uid": uid, "req": j.get("req_id"), "首轮结论": first, "核验视角": v.get("lens"),
                                      "核验判定": v.get("verdict_word"), "字段": c.get("path"), "old": c.get("old"), "new": c.get("new"),
                                      "理由": (v.get("reasoning_cn") or "")[:120]})
        e = elig.get(uid)
        if e:
            before = final
            final, jx = apply_eligibility(j, e, final)
            j.clear(); j.update(jx)
            rule += f"+ELIG_{e['verdict']}"
            if final != before:
                disagreements.append({"uid": uid, "req": j.get("req_id"), "首轮结论": before, "核验视角": "应届资格审计",
                                      "核验判定": e["verdict"], "字段": "eligibility", "old": before, "new": final,
                                      "理由": (e.get("one_line_cn") or "")[:120]})
        # 最后再套领域门，防止资格复核、历史核验改动绕过临床/模态缺口。
        domain_tier, domain_reasons, _ = derive(j)
        if RANK[domain_tier] < RANK[final]:
            before = final
            final = domain_tier
            rule += "+DOMAIN_CAP"
            disagreements.append({"uid": uid, "req": j.get("req_id"), "首轮结论": before,
                                  "核验视角": "领域适配审计", "核验判定": "CORRECTED", "字段": "domain_fit",
                                  "old": before, "new": final, "理由": "; ".join(domain_reasons)[:120]})
        j["first_round_tier"] = first
        j["final_tier"] = final
        j["final_rule_applied"] = rule
        j["verifier_notes"] = vs
        results.append({"uid": uid, "req_id": j.get("req_id"), "first_round_tier": first, "final_tier": final,
                        "final_rule_applied": rule, "verifier_notes": vs})
    n = len(allj)
    write_json(os.path.join(batch_dir, "数据", f"对抗核验结果_{sum(1 for r in results if r['verifier_notes'])}岗.json"), [r for r in results if r["verifier_notes"]])
    write_json(os.path.join(batch_dir, "数据", f"终评_{n}岗.json"), allj)
    seen = set()
    lines = ["| # | req | 首轮结论 | 核验视角 | 核验判定 | 终评 | 理由 |", "|---|---|---|---|---|---|---|"]
    k = 0
    keep_prefix = ("hard_gates.", "standout_coverage.items", "domain_overlap.tags", "domain_fit", "official_level.", "forbidden_claims_check.clean", "eligibility")
    disagreements = [d for d in disagreements if str(d["字段"]).startswith(keep_prefix)
                     or by_uid[d["uid"]]["final_tier"] != by_uid[d["uid"]]["first_round_tier"]]
    for d in disagreements:
        key = (d["uid"], d["核验视角"], d["字段"], str(d["new"]))
        if key in seen:
            continue
        seen.add(key)
        k += 1
        lines.append(f"| {k} | {d['req']} | {d['首轮结论']} | {d['核验视角']} | {d['核验判定']}（{d['字段']}: {d['old']} → {d['new']}） | {by_uid[d['uid']]['final_tier']} | {d['理由']} |")
    with open(os.path.join(batch_dir, "数据", "分歧记录.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    from collections import Counter
    print(f"终评 {n} 条：{dict(Counter(j['final_tier'] for j in allj))}；核验 {sum(1 for r in results if r['verifier_notes'])} 条；分歧行 {k}")


def eligset(batch_dir, size, prefix, force_uids=None):
    """为还没做应届资格审计的入围岗（🥇/🟢/🟡/stretch）写 数据/资格审计输入/<prefix>_NN.json，
    并写 外部资格审阅者 的参数 _args_<prefix>.json（batchDir / batches / tiers）。"""
    ddir = os.path.join(batch_dir, "数据")
    ff = sorted((f for f in os.listdir(ddir) if f.startswith("终评_") and f.endswith("岗.json")), key=lambda f: os.path.getmtime(os.path.join(ddir, f)))
    fin = read_json(os.path.join(ddir, ff[-1]), []) or []
    # 还没收口的新判读（只在 LLM判读结果 里）用 script_tier 先行入选，免得等核验收口
    fu = {j["uid"] for j in fin}
    lf = sorted((f for f in os.listdir(ddir) if f.startswith("LLM判读结果_") and f.endswith("岗.json")), key=lambda f: os.path.getmtime(os.path.join(ddir, f)))
    for j in (read_json(os.path.join(ddir, lf[-1]), []) or []) if lf else []:
        if j["uid"] not in fu:
            j = dict(j, final_tier=j.get("script_tier"))
            fin.append(j)
    done = load_eligibility(batch_dir)
    if force_uids:   # 强制重审（例如审计口径改过）：只出这些 uid，档位取最新判读
        want = set(force_uids)
        by = {j["uid"]: j for j in fin if j["uid"] in want}
        for j in (read_json(os.path.join(ddir, lf[-1]), []) or []) if lf else []:
            if j["uid"] in want:       # 最新判读的 script_tier 优先于旧终评
                by[j["uid"]] = dict(j, final_tier=j.get("script_tier"))
        todo = list(by.values())
    else:
        todo = [j for j in fin if j.get("final_tier") in ("🥇 投", "🟢 投", "🟡 条件投", "stretch")
                and not done.get(j["uid"], {}).get("policy_current")]
    jobs = {r["uid"]: r for r in (read_json(os.path.join(OUT_DIR, "jobs_all.json"), []) or [])}
    keep = ["uid", "req_id", "company", "track", "title", "location", "pay_range", "org_unit", "posted", "url",
            "level_tag", "multi_level", "eff_min_years", "phd_branch_years", "jd_text"]
    idir = os.path.join(ddir, "资格审计输入")
    os.makedirs(idir, exist_ok=True)
    os.makedirs(os.path.join(ddir, "资格审计输出"), exist_ok=True)
    todo.sort(key=lambda j: (j["company"], j["title"]))
    names = []
    for i in range(0, len(todo), size):
        n = f"{prefix}_{i // size + 1:02d}"
        write_json(os.path.join(idir, f"{n}.json"), [{k: jobs.get(j["uid"], {}).get(k) for k in keep} for j in todo[i:i + size]])
        names.append(n)
    summ = read_json(os.path.join(idir, "_首轮判读摘要.json"), {}) or {}
    for j in todo:
        summ[j["uid"]] = {"final_tier": j["final_tier"], "official_level": j["official_level"], "years": j["hard_gates"]["years"],
                          "degree": j["hard_gates"]["degree"], "one_line_cn": j.get("one_line_cn")}
    write_json(os.path.join(idir, "_首轮判读摘要.json"), summ)
    args = {"batchDir": os.path.abspath(batch_dir), "batches": names,
            "tiers": {j["uid"]: [j["final_tier"], j["official_level"].get("reachability")] for j in todo}}
    write_json(os.path.join(idir, f"_args_{prefix}.json"), args)
    print(f"待审计 {len(todo)} 条 → {len(names)} 批；workflow 参数 {os.path.join(idir, f'_args_{prefix}.json')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["batches", "recompute", "verifyset", "reconcile", "eligset"])
    ap.add_argument("--batch-dir", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--size", type=int, default=9)
    ap.add_argument("--uids-file", help="增量模式：只处理该 JSON 数组里的 uid（track.py stage 生成）")
    ap.add_argument("--prefix", help="增量模式的文件名前缀，如 inc20261001")
    a = ap.parse_args()
    uids = read_json(a.uids_file, []) if a.uids_file else None
    if a.cmd == "batches":
        make_batches(a.batch_dir, a.size, uids, a.prefix or "batch")
    elif a.cmd == "recompute":
        recompute(a.batch_dir)
    elif a.cmd == "verifyset":
        verifyset(a.batch_dir, a.seed, 6, uids, (f"verify_{a.prefix}" if a.prefix else "verify"))
    elif a.cmd == "reconcile":
        reconcile(a.batch_dir)
    elif a.cmd == "eligset":
        eligset(a.batch_dir, 7, a.prefix or "elig", uids)
