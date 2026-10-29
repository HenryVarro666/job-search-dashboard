#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""healthcare 岗位抓取 —— 确定性漏斗（不用 LLM）。

用法：
    python3 funnel.py [--in out/jobs_all.json] [--visa-gate] [--export <批次目录>]

门序（每门把丢弃的整条记录落盘 out/funnel/drop_NN_<gate>.json，可回溯）：
    00 dup_source      同 uid / 同 (company, jd_hash) 且有 primary 兄弟 → 丢非 primary；同 JD 异地点记 sibling_req_ids
    01 not_us          region == 非美国（远程-美国 / 多地含美国 / 未知 通过）
    02 not_healthcare  仅 A 档：team_filter 命中 org/department，或 list_hits 含 health 专名，
                       或 healthcare_hits 有 title/org 来源，或 core 里 ≥2 个不同 jd 命中 → 通过
    03 role_family     OTHER / SDE / DATA_SCI 丢；INTERN 单独 drop_03b；SA_HEALTH 移 sa_health.json
    04 level_hard      staff / principal / director 丢；明确 PhD 0 的 Principal Scientist 留待细判
    05 stretch_split   stretch==True 或 eff_min_years ≥ 5 → stretch.json（保留并照判，不丢）
    06 visa_flags      默认关闭：只打印"若开启会丢 N 条"；--visa-gate 才生效（签证后置）

产出：out/funnel/kept_for_llm.json / stretch.json / sa_health.json / counts.json
恒等式：input == Σdropped + kept + stretch + sa_health，不成立则非零退出。
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys

from common import OUT_DIR, TOOL_DIR, read_json, write_json
from parse_jd import leadership_requirement

FUNNEL_DIR = os.path.join(OUT_DIR, "funnel")
# 只有专名才算 list_hits 级别的通过证据；泛词（health / medical / clinical）在 amazon.jobs / Eightfold 的全文搜索里会命中福利段
HEALTH_PROPER = re.compile(r"BioNeMo|MONAI|Holoscan|Parabricks|One Medical|HealthScribe|HealthLake|Comprehend Medical|Nuance|"
                           r"\bDAX\b|Dragon|Verily|digital biology", re.I)
# JD 正文强术语：≥2 个不同强术语才算 health 组（"health"/"medical"/"diagnos"/"imaging" 这类泛词不计）
STRONG_TERMS = {"clinical", "patient", "hospital", "radiolog", "patholog", "ehr", "fhir", "hipaa", "fda", "oncolog", "cardio",
                "genomic", "biomed", "biopharma", "drug discovery", "monai", "bionemo", "holoscan", "mri", "ultrasound", "surgical"}
# 'emr' = Amazon EMR（Elastic MapReduce）假阳性；'healthcare'/'health'/'medical' 只经 title/org 来源计入
# 纯 SDE 放行只认软件类标题（制造 / 焊接 / 工艺 / 材料 / 现场安装等 "Engineer" 不算）；Prin / Mgr 缩写按非 IC 处理
SOFTWARE_TITLE = re.compile(r"software|\bsde\b|\bsdet\b|developer|back[- ]?end|front[- ]?end|full[- ]?stack|platform|data engineer|"
                            r"devops|site reliability|\bsre\b|security engineer|build engineer|test automation|cloud|infrastructure|"
                            r"bioinformatics engineer", re.I)
NOT_IC_ABBR = re.compile(r"\bprin\b|\bmgr\b|manager|\blead\b|director|head of", re.I)
VISA_FLAGS = {"NO_SPONSOR", "AUTH_NO_SPONSOR", "US_CITIZEN_REQUIRED", "CLEARANCE", "ITAR", "US_PERSON", "EXPORT_CONTROL"}


def jd_hash(r):
    t = re.sub(r"\s+", " ", (r.get("jd_text") or "")).strip().lower()
    return hashlib.md5(t.encode("utf-8")).hexdigest() if t else ""


def gate_dup(records):
    keep, drop = [], []
    seen_uid = {}
    for r in records:
        if r["uid"] in seen_uid:
            prev = seen_uid[r["uid"]]
            if prev.get("source_authority") == "primary" or r.get("source_authority") != "primary":
                r["排除理由"] = f"重复 uid，保留 {prev.get('source_authority')} 记录"
                drop.append(r)
                continue
            prev["排除理由"] = "重复 uid，被 primary 记录替代"
            drop.append(prev)
            keep.remove(prev)
        seen_uid[r["uid"]] = r
        keep.append(r)
    # 同公司同 JD 异地点：只判一次，其余记 sibling
    by_hash = {}
    for r in keep:
        h = jd_hash(r)
        r["_jd_hash"] = h
        if h:
            by_hash.setdefault((r["company_key"], h), []).append(r)
    keep2 = []
    for r in keep:
        group = by_hash.get((r["company_key"], r["_jd_hash"]), [r])
        if len(group) > 1:
            head = group[0]
            head.setdefault("sibling_req_ids", [])
            if r is head:
                head["sibling_req_ids"] = [g["req_id"] for g in group[1:]]
                head.setdefault("sibling_locations", [g.get("location") for g in group[1:]])
                keep2.append(r)
            else:
                r["排除理由"] = f"同公司同 JD 镜像，主记录 {head['req_id']}（{head.get('location')}）"
                drop.append(r)
        else:
            keep2.append(r)
    return keep2, drop


def gate_not_us(records):
    keep, drop = [], []
    for r in records:
        if r.get("region") == "非美国":
            r["排除理由"] = f"非美国：{r.get('location_raw') or r.get('locations')}"
            drop.append(r)
        else:
            keep.append(r)
    return keep, drop


def gate_not_healthcare(records):
    keep, drop = [], []
    for r in records:
        if r.get("track") != "A":
            keep.append(r)
            continue
        org = f"{r.get('org_unit', '')} {r.get('department', '')}"
        tf = r.get("team_filter") or []
        ok = any(re.search(re.escape(t), org, re.I) for t in tf)
        if not ok:
            ok = any(HEALTH_PROPER.search(str(h)) for h in r.get("list_hits", []) if str(h) not in ("board", "manual"))
        if not ok:
            ok = r.get("healthcare_hit_source") in ("title", "org")
        if not ok:
            strong = {h.lower() for h in (r.get("healthcare_hits") or []) if h.lower() in STRONG_TERMS}
            ok = len(strong) >= 2
        if ok:
            keep.append(r)
        else:
            r["排除理由"] = f"A 档非 health 组：org={org.strip()[:60]!r} hits={r.get('healthcare_hits')}"
            drop.append(r)
    return keep, drop


def is_phd_zero_scientist(r):
    """科学家职称的窄例外只保留判读资格，不代表最终可投。"""
    return bool(r.get("level_tag") == "principal"
            and re.search(r"\b(?:principal|prin)\b", r.get("title") or "", re.I)
            and r.get("role_family") in ("RESEARCH_SCI", "APPLIED_SCI", "DATA_SCI")
            and r.get("phd_branch_years") == 0
            and re.search(r"\bph\.?\s?d\b|doctorate|doctoral", r.get("years_evidence") or "", re.I)
            and not leadership_requirement(r.get("title") or "", r.get("jd_text") or ""))


def gate_role(records):
    keep, drop, intern, sa = [], [], [], []
    for r in records:
        fam = r.get("role_family", "OTHER")
        if fam == "INTERN":
            r["排除理由"] = "实习/co-op"
            intern.append(r)
        elif fam == "SA_HEALTH":
            r["funnel_gate"] = "sa_health"
            sa.append(r)
        elif fam == "DATA_SCI" and r.get("track") in ("B", "C", "D") and (r.get("level_tag") not in ("staff", "principal", "director") or is_phd_zero_scientist(r)):
            r["role_family_note"] = "Data Scientist：当前漏斗规则纳入 B/C/D 档的非资深 DS 岗"
            keep.append(r)
        elif fam == "SDE" and r.get("track") in ("B", "C", "D") and r.get("level_tag") in ("new_grad", "early", "mid", "unknown") \
                and (r.get("eff_min_years") is None or r.get("eff_min_years") <= 3) \
                and SOFTWARE_TITLE.search(r.get("title") or "") and not NOT_IC_ABBR.search(r.get("title") or ""):
            r["role_family_note"] = "纯 SDE（不含 ML）：当前漏斗规则纳入 B/C/D 档非 senior、年限 ≤3 的软件岗"
            keep.append(r)
        elif fam in ("OTHER", "SDE", "DATA_SCI"):
            r["排除理由"] = f"职族 {fam}：{r.get('role_family_evidence', '')}" + ("（纯 SDE 只纳入 B/C/D 档非 senior、年限 ≤3 的软件类标题）" if fam == "SDE" else "")
            drop.append(r)
        else:
            keep.append(r)
    return keep, drop, intern, sa


def gate_level(records):
    keep, drop = [], []
    for r in records:
        if leadership_requirement(r.get("title") or "", r.get("jd_text") or ""):
            r["排除理由"] = "管理 / Technical Lead 职称或明确带人要求，应届无对应全职经验"
            drop.append(r)
        elif is_phd_zero_scientist(r):
            r["level_exception"] = "Principal Scientist 明确 PhD 0 路径；仅保留细判，仍需核验独立年限与职责"
            keep.append(r)
        elif r.get("level_tag") in ("staff", "principal", "director"):
            r["排除理由"] = f"职级 {r.get('level_tag')}（{r.get('level_source', '')}）"
            drop.append(r)
        else:
            keep.append(r)
    return keep, drop


def gate_stretch(records):
    keep, stretch = [], []
    for r in records:
        y = r.get("eff_min_years")
        if r.get("stretch") or (isinstance(y, (int, float)) and y >= 5):
            r["funnel_gate"] = "stretch"
            stretch.append(r)
        else:
            keep.append(r)
    return keep, stretch


def gate_visa(records, enabled):
    keep, drop = [], []
    for r in records:
        flags = set(r.get("restriction_flags") or [])
        if flags & VISA_FLAGS:
            if enabled:
                r["排除理由"] = f"JD 硬限制 {sorted(flags & VISA_FLAGS)}"
                drop.append(r)
                continue
            r["visa_flag_note"] = sorted(flags & VISA_FLAGS)
        keep.append(r)
    return keep, drop


def run(inp, visa_gate=False, export=None):
    records = read_json(inp, [])
    if not records:
        raise SystemExit(f"输入为空: {inp}")
    os.makedirs(FUNNEL_DIR, exist_ok=True)
    for f in os.listdir(FUNNEL_DIR):
        if f.startswith("drop_") or f in ("kept_for_llm.json", "stretch.json", "sa_health.json", "counts.json"):
            os.remove(os.path.join(FUNNEL_DIR, f))
    n_in = len(records)
    gates, cur = [], records

    def rec_gate(name, dropped, note="", n_in=None):
        write_json(os.path.join(FUNNEL_DIR, f"drop_{name}.json"), dropped)
        for d in dropped:
            d["excluded"] = True
            d["funnel_gate"] = name
        n_in = len(cur) if n_in is None else n_in
        gates.append({"gate": name, "in": n_in, "dropped": len(dropped), "out": n_in - len(dropped), "note": note})

    nxt, d = gate_dup(cur); rec_gate("00_dup_source", d, "同 uid / 同 JD 镜像"); cur = nxt
    nxt, d = gate_not_us(cur); rec_gate("01_not_us", d, "region 非美国"); cur = nxt
    nxt, d = gate_not_healthcare(cur); rec_gate("02_not_healthcare", d, "A 档非 health 组"); cur = nxt
    nxt, d, intern, sa = gate_role(cur)
    rec_gate("03_role_family", d, "OTHER/SDE/DATA_SCI")
    rec_gate("03b_intern", intern, "实习", n_in=len(cur) - len(d))   # 03 和 03b 同一步切出，03b 的入数要减掉 03 丢的（2026-09-30 修）
    for r in sa:
        r["funnel_gate"] = "sa_health"
    gates.append({"gate": "03c_sa_health_split", "in": len(cur) - len(d) - len(intern), "dropped": 0, "out": len(nxt), "note": f"SA_HEALTH 分流 {len(sa)} 条到 sa_health.json（照判）"})
    # SA_HEALTH 也要过职级硬门与 stretch 分流（2026-09-23 修：Staff / Sr. Forward Deployed 岗曾绕过 04 门进了 🟡）
    cur = nxt + sa
    nxt, d = gate_level(cur); rec_gate("04_level_hard", d, "管理/Tech Lead/staff/principal/director；明确 PhD 0 科学家例外（含 SA_HEALTH）"); cur = nxt
    nxt, stretch = gate_stretch(cur)
    gates.append({"gate": "05_stretch_split", "in": len(cur), "dropped": 0, "out": len(nxt), "note": f"stretch 分流 {len(stretch)} 条（保留并照判，含 SA_HEALTH）"})
    sa = [r for r in nxt if r.get("funnel_gate") == "sa_health"]
    cur = [r for r in nxt if r.get("funnel_gate") != "sa_health"]
    would = sum(1 for r in cur if set(r.get("restriction_flags") or []) & VISA_FLAGS)
    nxt, d = gate_visa(cur, visa_gate)
    rec_gate("06_visa_flags", d, f"{'已开启' if visa_gate else '默认关闭'}；若开启会丢 {would} 条"); cur = nxt
    for r in cur:
        r["funnel_gate"] = "kept"
    write_json(os.path.join(FUNNEL_DIR, "kept_for_llm.json"), cur)
    write_json(os.path.join(FUNNEL_DIR, "stretch.json"), stretch)
    write_json(os.path.join(FUNNEL_DIR, "sa_health.json"), sa)
    n_drop = sum(g["dropped"] for g in gates)
    counts = {"input": n_in, "gates": gates, "kept": len(cur), "stretch": len(stretch), "sa_health": len(sa),
              "identity_ok": n_in == n_drop + len(cur) + len(stretch) + len(sa)}
    write_json(os.path.join(FUNNEL_DIR, "counts.json"), counts)
    # 回写 jobs_all.json（excluded / 排除理由 / funnel_gate）
    write_json(inp, records)
    print("| 层 | 入 | 丢弃 | 出 | 理由 |\n|---|---|---|---|---|")
    for g in gates:
        print(f"| {g['gate']} | {g['in']} | {g['dropped']} | {g['out']} | {g['note']} |")
    print(f"\n输入 {n_in} = 丢弃 {n_drop} + 主判读 {len(cur)} + stretch {len(stretch)} + sa_health {len(sa)} → 恒等式 {'通过' if counts['identity_ok'] else '不通过'}")
    if export:
        os.makedirs(os.path.join(export, "数据"), exist_ok=True)
        slim = []
        heavy = ("jd_text", "jd_html", "sections", "extra", "restriction_evidence", "years_evidence", "list_meta", "team_filter", "region_postfix", "structured_pay")
        for r in records:
            if r.get("excluded"):
                slim.append({k: v for k, v in r.items() if k not in heavy})
            else:
                slim.append({k: v for k, v in r.items() if k not in ("jd_html", "extra", "list_meta", "team_filter", "region_postfix", "structured_pay")})
        write_json(os.path.join(export, "数据", "jobs_raw.json"), slim)
        csvp = os.path.join(OUT_DIR, "healthcare_jobs_全量.csv")
        if os.path.exists(csvp):
            shutil.copy(csvp, os.path.join(export, "数据", "healthcare_ai_jobs_全量.csv"))
        dropped = {}
        for g in gates:
            p = os.path.join(FUNNEL_DIR, f"drop_{g['gate']}.json")
            if os.path.exists(p):
                dropped[g["gate"]] = [{k: r.get(k) for k in ("uid", "company", "req_id", "title", "location", "role_family", "level_tag", "eff_min_years", "排除理由", "url")}
                                     for r in read_json(p, [])]
        write_json(os.path.join(export, "数据", "被漏斗丢弃的岗位明细.json"), dropped)
        write_json(os.path.join(export, "数据", "漏斗计数.json"), counts)
        print(f"已导出到 {export}/数据/")
    return 0 if counts["identity_ok"] else 3


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", default=os.path.join(OUT_DIR, "jobs_all.json"))
    ap.add_argument("--visa-gate", action="store_true")
    ap.add_argument("--export")
    a = ap.parse_args()
    sys.exit(run(a.inp, a.visa_gate, a.export))
