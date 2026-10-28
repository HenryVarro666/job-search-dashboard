#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""healthcare 岗位抓取 —— 从 终评_N岗.json 渲染 vault 归档件。

用法：
    python3 render_notes.py --batch-dir "求职机会/科技公司-医疗AI/Healthcare-AI-扫描-2026-09" [--vault /path/to/local-vault]

产出（全部写进批次目录）：
    岗位明细/<领域>/<公司> - <职位> (<短编号>).md  🥇 / 🟢 / 🟡 / stretch + 经核验或资格审计的 🔴
    ../投递进展.md（类别级，render_progress.py）  职位名即申请链接的进展表
    已排除岗位.md                                 JD 硬限制丢弃 + 未核验 🔴 表 + 漏斗丢弃统计
    数据/hub_清单表.md                            hub 用的按领域分组表（人工粘进 岗位信息.md）
    数据/公司池_入围索引.md                        公司池 §二 表
    数据/JD高频短语_按簇.json                      每簇 stand-out/preferred 的 2–4 gram top 15
    数据/渲染统计.json                             会计恒等式各项

领域分簇（按 domain_overlap 里 CORE_TO_JD 的 tag）：
    影像与多模态基础模型 ← neuroimaging / medical-imaging-general / VLM-post-training
    临床NLP与LLM应用     ← clinical-NLP
    Agent与ML平台工程    ← agentic-LLM / ML-platform-SWE
    组学与EHR建模        ← genomics-omics / EHR
    其他医疗AI           ← 其余
"""
import argparse
import html
import json
import os
import re
from collections import Counter, defaultdict

from common import OUT_DIR, read_json, write_json
from apply_links import apply_url
from visa_signals import employer_signal

CLUSTER_OF = {"neuroimaging": "影像与多模态基础模型", "medical-imaging-general": "影像与多模态基础模型", "VLM-post-training": "影像与多模态基础模型",
              "clinical-NLP": "临床NLP与LLM应用", "agentic-LLM": "Agent与ML平台工程", "ML-platform-SWE": "Agent与ML平台工程",
              "genomics-omics": "组学与EHR建模", "EHR": "组学与EHR建模", "devices-FDA": "其他医疗AI"}
CLUSTERS = ["影像与多模态基础模型", "临床NLP与LLM应用", "Agent与ML平台工程", "组学与EHR建模", "其他医疗AI"]
TIER_ORDER = {"🥇 投": 0, "🟢 投": 1, "🟡 条件投": 2, "stretch": 3, "🔴 不投": 4}
STOP = set("the and of or to in for with a an on as by is are be at from that this will you our we your their its into across using use used experience".split())



def replace_region(md_path, name, content):
    """把 md 文件里 <!-- BEGIN 自动:name --> 与 <!-- END 自动:name --> 之间的内容换成 content；没有标记就不动。"""
    if not os.path.exists(md_path):
        return False
    s = open(md_path, encoding="utf-8").read()
    b, e = f"<!-- BEGIN 自动:{name} -->", f"<!-- END 自动:{name} -->"
    i, j = s.find(b), s.find(e)
    if i < 0 or j < 0 or j < i:
        return False
    s = s[:i + len(b)] + "\n" + content.rstrip() + "\n" + s[j:]
    open(md_path, "w", encoding="utf-8").write(s)
    return True

def safe_req(req_id):
    """req 号可能带 / 或空格（如 Function Health 的 SCANS-MIL-26-07/08），文件名与 wikilink 都用安全化版本。"""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(req_id)).strip("-")[:60] or "req"


def alias(j):
    """wikilink 显示名：公司 · 职位（去掉会破坏链接的字符）。"""
    t = re.sub(r"[\[\]|#^]", " ", f"{j['company']} · {j['title']}")
    return re.sub(r"\s+", " ", t).strip()[:70]


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-", re.I)


def short_id(req_id):
    """文件名里的短编号：Ashby 的 UUID 只取前 8 位，其余用安全化后的 req 号。"""
    r = str(req_id).strip()
    return r[:8] if _UUID_RE.match(r) else safe_req(r)


def _fname_clean(s):
    s = html.unescape(str(s)).replace("\u2014", "-").replace("\u2013", "-")
    s = s.replace(" / ", " ").replace("/", "-").replace("\\", "-")
    s = s.replace(":", " -").replace("[", "(").replace("]", ")").replace("|", "-")
    s = re.sub(r'[#^*?"<>]', "", s)
    return re.sub(r"\s+", " ", s).strip(" .-")


def short_company(company):
    c = re.sub(r"\s*\([^)]*\)\s*$", "", str(company)).strip()
    return _fname_clean(c) or _fname_clean(company)


def note_basename(j):
    """笔记文件名（不含 .md）：公司 - 职位 (短编号)。2026-09-24 本人指定，取代 <req>_<slug>。"""
    title = _fname_clean(j["title"])
    if len(title) > 90:
        title = title[:90].rsplit(" ", 1)[0]
    return f"{short_company(j['company'])} - {title} ({short_id(j['req_id'])})"


BASE_LABEL = {"research": "通用 healthcare 研究模板 `申请材料/Resume_Healthcare_Research.tex`（一页用 `_1page.tex`）",
              "short": "`CV_overleaf/resume_cc_short.tex` 派生"}


def slug(title):
    s = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")
    return s[:80] or "req"


def cluster_of(j):
    weights = defaultdict(int)
    for t in j.get("domain_overlap", {}).get("tags", []):
        if t.get("jd_weight") == "CORE_TO_JD":
            weights[CLUSTER_OF.get(t["tag"], "其他医疗AI")] += 2
        elif t.get("jd_weight") == "MENTIONED":
            weights[CLUSTER_OF.get(t["tag"], "其他医疗AI")] += 1
    if not weights:
        return "其他医疗AI"
    return max(CLUSTERS, key=lambda c: (weights.get(c, 0), -CLUSTERS.index(c)))


def rel(batch_dir_rel, *parts):
    return "/".join([batch_dir_rel.replace("求职机会/", "", 1)] + list(parts))


APPLY_OK = {}


def apply_md(j, rec):
    """🥇/🟢/🟡/stretch 给申请链接；🔴 不给。"""
    if (j.get("final_tier") or j.get("script_tier")) == "🔴 不投" or not rec:
        return None
    u, how = apply_url(rec)
    chk = APPLY_OK.get(j["uid"]) or {}
    mark = f"实测 ✅ {str(chk.get('checked_at', ''))[:10]}" if chk.get("ok") else ("实测 ⚠️ 未通过，先打开原帖确认" if chk else "未实测")
    return u, how, mark


def elig_label(j):
    e = j.get("eligibility")
    if not e:
        return "未审计"
    return f"**{e.get('verdict_cn')}**　{e.get('one_line_cn') or ''}"


def elig_short(j):
    e = j.get("eligibility")
    return {"ELIGIBLE_NEW_GRAD": "✅ 应届可投", "ELIGIBLE_PHD_COUNTS": "✅ PhD 折算", "CONDITIONAL_RESEARCH_YEARS": "⚠️ 看是否认研究年限",
            "LEVEL_MISMATCH": "⛔ 资深岗", "NOT_ELIGIBLE": "⛔ 硬门槛", "TIMING_CONFLICT": "⛔ 时间冲突", "UNCLEAR": "❓ 信息不足"}.get((e or {}).get("verdict"), "未审计")


def elig_detail(j):
    e = j.get("eligibility")
    if not e:
        return "未审计"
    ch = "未复核" if not e.get("challenged") else ("复核维持" if e.get("challenge_upheld") else f"复核推翻（原判 {e.get('audit_verdict')}）")
    q = e.get("decisive_quote") or e.get("years_quote") or ""
    return f"{e.get('verdict')}（{e.get('confidence')}，{ch}）" + (f"；依据 `{q[:140]}`" if q else "") + (f"；{(e.get('reasoning_cn') or '')[:160]}" if e.get("reasoning_cn") else "")


def note_md(j, rec, batch_rel, neighbors, company_hub, closed=None, applied=None):
    hg = j["hard_gates"]
    fn = j["funnel"]
    tiers = f"{j.get('first_round_tier')} → 核验 {j.get('final_rule_applied')}" if j.get("verifier_notes") else f"{j.get('first_round_tier')}（未经三镜头核验）"
    if j.get("eligibility") and not j.get("verifier_notes"):
        tiers += f" → 应届资格审计 {j['eligibility'].get('verdict')}"
    v_words = " ／ ".join(f"{v.get('lens')} {v.get('verdict_word')}" for v in j.get("verifier_notes", []) or []) or "未核验"
    sec = rec.get("sections") or {}
    ev = ", ".join(j.get("evidence_used", [])[:10])
    x = ", ".join(sorted({t["forbidden_id"] for t in j.get("forbidden_claims_check", {}).get("tempted", [])})) or "无"
    vh = j.get("visa_hook", {})
    lines = [f"# {j['title']}", ""]
    if applied:
        lines += [f"> ✅ **{applied.get('status', '已投递')}**（{applied.get('date', '')}）：{applied.get('note', '')}。进展见 [[科技公司-医疗AI/投递进展|投递进展]]", ""]
    if closed:
        lines += [f"> ❌ **已下线**（{closed.get('date')} 追踪发现：{closed.get('reason')}）。保留本笔记作记录。", ""]
    if j.get("final_tier") == "🥇 投":
        lines += [f"> 🥇 **优先投** —— 依据见 [[{batch_rel}/调研报告_匹配判读_2026-09-23|匹配判读报告]]", ""]
    lines += [
        f"- **Job ID**：`{j['req_id']}`　|　**公司·track**：{j['company']} · {j['track']}",
        f"- **领域分类**：{j['_cluster']}　|　**匹配档位**：**{j.get('final_tier')}**（{tiers}）",
        f"- **官方职级（元数据原文）**：{j['official_level'].get('raw')}　|　**可达性**：{j['official_level'].get('reachability')}",
        f"- **应届资格（PhD Dec 2026 毕业、全职年限 0）**：{elig_label(j)}",
        f"- **地点**：{rec.get('location') or rec.get('location_raw')}　|　**用工类型**：{rec.get('time_type') or '未标'}　|　**region**：{rec.get('region')}",
        f"- **base band（官方 pay transparency）**：{rec.get('pay_range') or '未披露'}",
        f"- **职位链接（ATS 一手源）**：{rec.get('url')}",
    ]
    fit = j.get("domain_fit") or {}
    labels = {"DIRECT": "直接研究匹配", "METHOD_TRANSFER": "仅方法可迁移，需学习目标领域",
              "GENERAL_TECH": "通用技术职责，不主张医学专长", "GAP": "必需领域/模态经验不符",
              "UNVERIFIED": "项目/领域要求待确认"}
    lines.append(f"- **领域适配**：{labels.get(fit.get('status'), '尚未审计')}；{fit.get('gap_cn', '')}")
    if fit.get("jd_quote"):
        lines.append(f"- **领域依据（JD 原句）**：{fit['jd_quote']}")
    if fit.get("transfer_basis_cn"):
        lines.append(f"- **可迁移范围**：{fit['transfer_basis_cn']}")
    ap = apply_md(j, rec)
    if ap:
        lines.append(f"- **申请链接（直接提交）**：[提交申请]({ap[0]})　|　{ap[1]}　|　{ap[2]}")
    lines += [
        f"- **抓取时间**：{str(rec.get('fetched_at', ''))[:10]}（`canApply={rec.get('can_apply')}`，posted {rec.get('posted') or rec.get('posted_raw') or '未标'}）",
    ]
    if j.get("sibling_req_ids"):
        lines.append(f"- **同 JD 镜像 req**：{', '.join(j['sibling_req_ids'])}（{', '.join(str(x) for x in rec.get('sibling_locations', []) or [])}）")
    lines += ["", "## 判读要点",
              f"- 硬门槛：学位 {hg['degree']['status']} ／ 年限 {hg['years']['status']}（`{(hg['years'].get('jd_quote') or '')[:100]}`）／ 限制 {hg['restrictions']['status']} ／ 职族 {hg['role_family_fit']['role_family']}",
              f"- stand-out 覆盖：{j['standout_coverage'].get('n_have')} have + {j['standout_coverage'].get('n_partial')} partial / {j['standout_coverage'].get('n_items')}（ratio {j.get('script_coverage_ratio')}；来源 {j['judge_meta'].get('standout_source')}）",
              f"- 漏斗卡点：S1 {fn['S1']['blocker_cn']}｜S2 {fn['S2']['blocker_cn']}｜S3 {fn['S3']['blocker_cn']}｜S4 {fn['S4']['blocker_cn']}｜S5 {fn['S5']['blocker_cn']}",
              f"- 成本分档：A {j['cost_tiers']['A']['cost']} / {j['cost_tiers']['A']['p_offer_band']} → {j['cost_tiers']['A']['judgement_cn']}；B {j['cost_tiers']['B']['cost']} / {j['cost_tiers']['B']['p_offer_band']} → {j['cost_tiers']['B']['judgement_cn']}；C {j['cost_tiers']['C']['cost']} / {j['cost_tiers']['C']['p_offer_band']} → {j['cost_tiers']['C']['judgement_cn']}",
              f"- 推荐底稿：{BASE_LABEL.get(j['recommended_base']['base'], j['recommended_base']['base'])}（判读理由：{j['recommended_base']['reason_cn']}）　|　证据：{ev}　|　禁止声明命中：{x}",
              f"- 一句话：{j.get('one_line_cn')}",
              f"- 核验：{v_words}",
              f"- 应届资格审计：{elig_detail(j)}"]
    if j.get("watch_reason_cn"):
        lines.append(f"- stretch 观察理由：{j['watch_reason_cn']}")
    def block(title, key):
        txt = (sec.get(key) or "").strip()
        return [f"## {title}", "", txt if txt else "_（JD 无此段）_", ""]
    lines += [""] + block("Responsibilities（原文）", "responsibilities") + block("Minimum Qualifications（原文）", "minimum") \
             + block("Preferred / Ways to stand out（原文）", "preferred") + block("Pay Transparency（原文）", "pay")
    policy = employer_signal(j["uid"].split(":")[0]).get("policy") or {}
    if policy.get("checked") and policy.get("source"):
        source = str(policy["source"]).removesuffix(".md")
        visa_reuse = f"公司级证据复用 [[{source}|原记录]]（原核查日 {policy['checked']}）；仅核本岗差异，未知项不重复查询。"
    else:
        visa_reuse = "先查公司既有档案与签证登记；同一法律实体复用原证据，仅补本岗差异或明确缺口。"
    lines += ["## 签证挂钩（公司证据复用与岗位差异）", "",
              f"- {visa_reuse}",
              f"- 实体：{vh.get('entity_name') or '未见'}｜hiring org：{vh.get('hiring_org') or rec.get('org_unit') or '未见'}｜SOC 猜测：{vh.get('soc_guess')}｜措辞命中：{'; '.join(vh.get('sponsorship_wording_hits') or []) or '无'}",
              "", "---", "",
              f"相关：[[{batch_rel}/岗位信息|批次归档入口]]｜[[{batch_rel}/调研报告_匹配判读_2026-09-23|匹配判读报告]]｜[[00_岗位总览]]"]
    if company_hub:
        lines.append(f"公司档案：{company_hub}")
    if neighbors:
        lines.append(f"同领域（{j['_cluster']}）：" + " · ".join(neighbors))
    return "\n".join(lines) + "\n"


def phrases(text, n_min=2, n_max=4):
    words = [w for w in re.findall(r"[a-z][a-z0-9+/.-]*", (text or "").lower()) if w not in STOP and len(w) > 2]
    out = Counter()
    for n in range(n_min, n_max + 1):
        for i in range(len(words) - n + 1):
            out[" ".join(words[i:i + n])] += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-dir", required=True)
    ap.add_argument("--vault", default=os.path.dirname(os.path.dirname(TOOL_DIR)))
    a = ap.parse_args()
    bdir = os.path.join(a.vault, a.batch_dir) if not os.path.isabs(a.batch_dir) else a.batch_dir
    batch_rel = os.path.relpath(bdir, os.path.join(a.vault, "求职机会"))
    files = sorted((f for f in os.listdir(os.path.join(bdir, "数据")) if f.startswith("终评_")),
                   key=lambda f: os.path.getmtime(os.path.join(bdir, "数据", f)))
    allj = read_json(os.path.join(bdir, "数据", files[-1]), [])
    jobs_all = {r["uid"]: r for r in read_json(os.path.join(OUT_DIR, "jobs_all.json"), [])}
    closed_map = read_json(os.path.join(bdir, "数据", "追踪", "closed.json"), {}) or {}
    APPLY_OK.update(read_json(os.path.join(bdir, "数据", "申请链接核验.json"), {}) or {})
    company_hubs = {"nvidia": "[[科技公司-AI基础设施/NVIDIA-NCG-2026/岗位信息|NVIDIA NCG 2026 档案]]",
                    "microsoft": "[[科技公司-云计算/Microsoft/岗位信息|Microsoft 档案]]"}
    for j in allj:
        j["_cluster"] = cluster_of(j)
        j["final_tier"] = j.get("final_tier") or j.get("script_tier")
    try:
        from render_progress import load_status
        applied_map = load_status()[0]
    except Exception:
        applied_map = {}
    with_note = [j for j in allj if j["final_tier"] in ("🥇 投", "🟢 投", "🟡 条件投", "stretch") or j.get("verifier_notes") or j.get("eligibility")]
    no_note = [j for j in allj if j not in with_note]
    # 同领域邻居（同簇按档位排序，取相邻两条）
    by_cluster = defaultdict(list)
    for j in with_note:
        by_cluster[j["_cluster"]].append(j)
    for c in by_cluster:
        by_cluster[c].sort(key=lambda j: (TIER_ORDER.get(j["final_tier"], 9), j["company"], j["req_id"]))
    note_path = {}
    for c, js in by_cluster.items():
        for j in js:
            fn = f"{note_basename(j)}.md"
            note_path[j["uid"]] = f"{batch_rel}/岗位明细/{c}/{fn[:-3]}"
    for c, js in by_cluster.items():
        os.makedirs(os.path.join(bdir, "岗位明细", c), exist_ok=True)
        for i, j in enumerate(js):
            nb = [js[k] for k in (i - 1, i + 1) if 0 <= k < len(js)]
            neighbors = [f"[[{note_path[n['uid']]}|{alias(n)}]]" for n in nb]
            rec = jobs_all.get(j["uid"], {})
            ck = rec.get("company_key") or j.get("company_key") or ""
            md = note_md(j, rec, batch_rel, neighbors, company_hubs.get(ck), closed_map.get(j["uid"]), applied_map.get(j["uid"]))
            fname = f"{note_basename(j)}.md"
            for other in CLUSTERS:   # 领域簇变了时删掉旧簇里的同名旧笔记（2026-09-24 重判后出现 3 篇重复）
                op = os.path.join(bdir, "岗位明细", other, fname)
                if other != c and os.path.exists(op):
                    os.remove(op)
            with open(os.path.join(bdir, "岗位明细", c, fname), "w", encoding="utf-8") as f:
                f.write(md)
    # hub 清单表
    lines = []
    for c in CLUSTERS:
        js = by_cluster.get(c, [])
        if not js:
            continue
        lines += [f"### {c}（{len(js)} 个）", "", "| 匹配 | 应届资格 | 岗位档案 | 公司·track | 职位名称 | 地点 | base band | 官方职级 | 原帖 | 申请 |", "|---|---|---|---|---|---|---|---|---|---|"]
        for j in js:
            rec = jobs_all.get(j["uid"], {})
            flag = " ⚠️ JD 不全" if j.get("jd_unverified") else ""
            if j["uid"] in closed_map:
                flag += f" ❌ 已下线 {closed_map[j['uid']].get('date')}"
            lines.append(f"| {j['final_tier']}{flag} | {elig_short(j)} | [[{note_path[j['uid']]}\\|{alias(j)}]] | {j['company']} · {j['track']} | {j['title']} | {rec.get('location') or ''} | {rec.get('pay_range') or '未披露'} | {j['official_level'].get('raw')} | [原帖]({rec.get('url')}) | {("[提交](" + apply_md(j, rec)[0] + ")") if apply_md(j, rec) else "—"} |")
        lines.append("")
    with open(os.path.join(bdir, "数据", "hub_清单表.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    replace_region(os.path.join(bdir, "岗位信息.md"), "清单", "\n".join(lines))
    # 申请入口：🥇/🟢 快速清单（点"提交"直达申请表）
    quick = ["| 档位 | 应届资格 | 公司 | 职位 | 地点 | base band | 推荐底稿 | 提交方式 | 申请 | 签证 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for j in sorted([x for x in with_note if x["final_tier"] in ("🥇 投", "🟢 投")], key=lambda j: (TIER_ORDER.get(j["final_tier"], 9), j["company"])):
        rec = jobs_all.get(j["uid"], {})
        ap = apply_md(j, rec)
        if not ap:
            continue
        base = "通用研究模板" if j["recommended_base"]["base"] == "research" else "short 派生"
        quick.append(f"| {j['final_tier']} | {elig_short(j)} | {j['company']} | [[{note_path[j['uid']]}\\|{j['title'][:50]}]] | {rec.get('location') or ''} | {rec.get('pay_range') or '未披露'} | {base} | {ap[1]} | [提交]({ap[0]}) | 先复用公司记录，仅补本岗差异 |")
    quick += ["", f"🟡 条件投与 stretch 的申请链接在下方「岗位清单」表的「申请」列，也在每篇岗位明细头部。链接实测结果：`数据/申请链接核验.json`（{__import__('datetime').date.today().isoformat()} 渲染）。"]
    replace_region(os.path.join(bdir, "岗位信息.md"), "申请入口", "\n".join(quick))
    # 公司池 §二
    idx = ["| 公司 | 终评 | req | 岗位明细 | 领域 | 推荐底稿 | 申请 |", "|---|---|---|---|---|---|---|"]
    for j in sorted(with_note, key=lambda j: (TIER_ORDER.get(j["final_tier"], 9), j["company"])):
        idx.append(f"| {j['company']} | {j['final_tier']} | {j['req_id']} | [[{note_path[j['uid']]}\\|{j['title'][:60]}]] | {j['_cluster']} | {'通用研究模板' if j['recommended_base']['base'] == 'research' else 'short 派生'} | {("[提交](" + apply_md(j, jobs_all.get(j['uid'], {}))[0] + ")") if apply_md(j, jobs_all.get(j['uid'], {})) else "—"} |")
    with open(os.path.join(bdir, "数据", "公司池_入围索引.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(idx) + "\n")
    # 已排除岗位.md
    fd = os.path.join(OUT_DIR, "funnel")
    counts = read_json(os.path.join(fd, "counts.json"), {}) or {}
    ex = ["---", "tags:", "  - 域/求职", "  - 类型/排除记录", "---", "", f"# Healthcare-AI 扫描批次 · 已排除岗位（2026-09）", "",
          "> 删除可逆：全部记录（含 `excluded:true` + `排除理由`）在 `数据/jobs_raw.json`；漏斗逐门丢弃明细在 `数据/被漏斗丢弃的岗位明细.json`。", "",
          "## 一、漏斗丢弃统计", "", "| 层 | 入 | 丢弃 | 出 | 理由 |", "|---|---|---|---|---|"]
    for g in counts.get("gates", []):
        ex.append(f"| {g['gate']} | {g['in']} | {g['dropped']} | {g['out']} | {g.get('note', '')} |")
    ex += ["", f"输入 {counts.get('input')} = 丢弃 {sum(g['dropped'] for g in counts.get('gates', []))} + 主判读 {counts.get('kept')} + stretch {counts.get('stretch')} + sa_health {counts.get('sa_health')}", ""]
    ex += ["## 二、判读 🔴（未核验，原文在 数据/终评_N岗.json）", "", "| req | 公司 | 职位 | 地点 | 一句话理由 | 原帖 |", "|---|---|---|---|---|---|"]
    for j in sorted(no_note, key=lambda j: (j["company"], j["req_id"])):
        rec = jobs_all.get(j["uid"], {})
        ex.append(f"| {j['req_id']} | {j['company']} | {j['title'][:70]} | {rec.get('location') or ''} | {(j.get('one_line_cn') or '')[:80]} | [原帖]({rec.get('url')}) |")
    ex += ["", "---", "", f"相关：[[{batch_rel}/岗位信息|批次归档入口]]｜[[{batch_rel}/调研报告_匹配判读_2026-09-23|匹配判读报告]]｜[[00_岗位总览]]"]
    with open(os.path.join(bdir, "已排除岗位.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(ex) + "\n")
    # 短语挖掘
    ph = {}
    for c, js in by_cluster.items():
        cnt = Counter()
        for j in js:
            if j["final_tier"] == "🔴 不投":
                continue
            rec = jobs_all.get(j["uid"], {})
            sec = rec.get("sections") or {}
            cnt.update(phrases((sec.get("preferred") or "") + "\n" + (sec.get("minimum") or "")))
        ph[c] = [{"phrase": p, "n": n} for p, n in cnt.most_common(40) if n >= 2][:15]
    write_json(os.path.join(bdir, "数据", "JD高频短语_按簇.json"), ph)
    stats = {"终评总数": len(allj), "有笔记": len(with_note), "未核验🔴": len(no_note),
             "按簇": {c: len(v) for c, v in by_cluster.items()},
             "按终评": dict(Counter(j["final_tier"] for j in allj)), "漏斗": counts}
    write_json(os.path.join(bdir, "数据", "渲染统计.json"), stats)
    dist = " ／ ".join(f"{t} {stats['按终评'].get(t, 0)}" for t in ("🥇 投", "🟢 投", "🟡 条件投", "stretch", "🔴 不投"))
    replace_region(os.path.join(bdir, "岗位信息.md"), "分布", "\n".join([
        f"- 终评 {stats['终评总数']} 条：{dist}",
        f"- 有逐岗笔记 {stats['有笔记']} 条（🥇/🟢/🟡/stretch，以及经三镜头核验或应届资格审计的 🔴）；其余 🔴 {stats['未核验🔴']} 条只进 已排除岗位 表",
        "- 按领域：" + " ／ ".join(f"{c} {n}" for c, n in stats["按簇"].items()),
        f"- 渲染时间：{__import__('datetime').date.today().isoformat()}（`python3 工具/healthcare岗位抓取/render_notes.py` 自动刷新本区）"]))
    replace_region(os.path.join(bdir, "公司池.md"), "入围索引", open(os.path.join(bdir, "数据", "公司池_入围索引.md"), encoding="utf-8").read())
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    try:   # 类别级投递进展文档（2026-09-24 本人要求）：职位名即申请链接，状态读 投递状态.json
        from render_progress import render as render_progress
        render_progress(bdir)
    except Exception as e:
        print(f"⚠️ 投递进展.md 未刷新：{e}")


if __name__ == "__main__":
    main()
