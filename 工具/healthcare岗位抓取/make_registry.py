#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""healthcare 岗位抓取 —— 把入围 req 生成 工具/岗位有效性检查/jobs.json 片段。

用法：
    python3 make_registry.py --in <终评或 kept json> [--uids nvidia:JR2022219 ...] --folder "求职机会/科技公司-医疗AI/Healthcare-AI-扫描-2026-09" [--merge]

type 映射：workday / greenhouse / ashby(+org,posting_id) / lever / eightfold(+domain) / 其余 page（must_contain + closed_markers + spa）。
--merge 只把 folder 在 vault 里已存在的条目追加进 jobs.json（写前备份 jobs.json.bak）；加 --replace 时同名条目整条覆盖。
--in 可以是终评文件：缺 ats / url 的记录按 uid 回查 out/jobs_all.json 补全，查不到的（已下线）跳过。
"""
import argparse
import json
import os
import re
import shutil

from common import OUT_DIR, TOOL_DIR, read_json, write_json

VAULT = os.path.dirname(os.path.dirname(TOOL_DIR))
REGISTRY = os.path.join(VAULT, "工具", "岗位有效性检查", "jobs.json")
CLOSED = ["no longer available", "no longer accepting applications", "not available at this time", "position has been filled",
          "job you are looking for", "job has expired", "this job is expired", "posting has expired", "no longer open",
          "requisition is closed", "job is closed", "no longer posted"]


def entry(r, folder):
    name = f"{r['company']} — {r['title']} ({r['req_id']})"
    ats = r.get("ats")
    src = {"url": r.get("url", ""), "authority": "primary", "label": f"{ats} req {r['req_id']}"}
    if ats == "workday":
        src["type"] = "workday"
    elif ats == "greenhouse":
        src["type"] = "greenhouse"
        slug = (r.get("extra") or {}).get("board")
        if slug and "greenhouse.io" not in src["url"]:
            src["url"] = f"https://job-boards.greenhouse.io/{slug}/jobs/{r['req_id']}"
    elif ats == "ashby":
        src["type"] = "ashby"
        src["org"] = (r.get("extra") or {}).get("org")
        src["posting_id"] = (r.get("extra") or {}).get("posting_id")
    elif ats == "lever":
        src["type"] = "lever"
    elif ats == "smartrecruiters":
        # 2026-09-29：原先落到通用 page 类型，过期页照样 200、关键词里也没有 "has expired"，AbbVie 🥇 岗过期后漏判。
        # check_jobs.py 有专用检查，读公开 postings API 的 active 字段。
        m = re.search(r"smartrecruiters\.com/([^/]+)/(\d+)", src["url"])
        src["type"] = "smartrecruiters"
        if m:
            src["company"], src["posting_id"] = m.group(1), m.group(2)
    elif ats == "eightfold":
        src["type"] = "eightfold"
        src["domain"] = "nvidia.com" if "nvidia" in src["url"] else "microsoft.com"
    else:
        src["type"] = "page"
        src["must_contain"] = [r["title"]]
        src["closed_markers"] = CLOSED
        src["spa"] = True
    return {"name": name, "folder": folder, "sources": [src],
            "notes": "批次 folder 共用状态行；未投 req 的状态列不可按字面采信（Microsoft 先例）"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--uids", nargs="*")
    ap.add_argument("--folder", required=True)
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--replace", action="store_true", help="与 --merge 同用：同名条目用新生成的覆盖（修登记错误时用）")
    a = ap.parse_args()
    recs = read_json(a.inp, [])
    if isinstance(recs, dict):
        recs = recs.get("records") or recs.get("items") or []
    if a.uids:
        recs = [r for r in recs if r.get("uid") in set(a.uids)]
    # 终评记录没有 ats / url / location 等抓取字段（2026-09-29 曾因此登记出 8 条空 URL），按 uid 回查 jobs_all 补全
    jobs = {r["uid"]: r for r in (read_json(os.path.join(OUT_DIR, "jobs_all.json"), []) or [])}
    fixed = []
    for r in recs:
        if not r.get("ats") or not r.get("url"):
            j = jobs.get(r.get("uid"))
            if not j:
                print(f"跳过（jobs_all 里没有，可能已下线）：{r.get('uid')}")
                continue
            r = dict(j, **{k: r[k] for k in ("company", "title", "req_id") if r.get(k)})
        fixed.append(r)
    recs = fixed
    frag = [entry(r, a.folder) for r in recs]
    out = os.path.join(OUT_DIR, "jobs_registry_fragment.json")
    write_json(out, frag)
    print(f"{len(frag)} 条 → {out}")
    if a.merge:
        if not os.path.isdir(os.path.join(VAULT, a.folder)):
            raise SystemExit(f"folder 不存在，拒绝合并: {a.folder}")
        reg = read_json(REGISTRY)
        names = {j["name"] for j in reg["jobs"]}
        shutil.copy(REGISTRY, REGISTRY + ".bak")
        added = 0
        idx = {j["name"]: i for i, j in enumerate(reg["jobs"])}
        replaced = 0
        for e in frag:
            if e["name"] not in names:
                reg["jobs"].append(e)
                added += 1
            elif a.replace:
                reg["jobs"][idx[e["name"]]] = e
                replaced += 1
        with open(REGISTRY, "w", encoding="utf-8") as f:
            json.dump(reg, f, ensure_ascii=False, indent=1)
        print(f"已合并 {added} 条、替换 {replaced} 条进 {REGISTRY}（备份 .bak）")


if __name__ == "__main__":
    main()
