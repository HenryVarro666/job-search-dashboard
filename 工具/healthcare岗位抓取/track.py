#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""healthcare 岗位抓取 —— 增量追踪（纯 Python 3 标准库，不调用 LLM）。

用法：
    python3 track.py fetch  [--company KEY ...]          # 重新枚举 + 补抓新 JD + build + funnel（已抓过的 JD 自动跳过）
    python3 track.py delta  --batch-dir <批次目录>        # 与上次终评对比，写 数据/追踪/delta_<stamp>.json，追加 追踪记录.md
    python3 track.py stage  --batch-dir <批次目录> [--include-changed]
                                                         # 只为新岗（可选含 JD 变更岗）出判读批 inc<stamp>_NN.json
    python3 track.py run    --batch-dir <批次目录> [--notify]
                                                         # fetch + delta + stage 一条龙；--notify 有变化时发 macOS 通知

对比口径（"抓不到"绝不报成"已下线"，与 工具/岗位有效性检查 同一原则）：
    新岗      现在进了判读池（kept / stretch / sa_health）但从未判读过的 uid
    已下线    上次判读为 🥇/🟢/🟡/stretch，本次枚举后取得的当日官方详情明确 canApply=false
    无法核实  规范化记录缺失、枚举不完整或关闭字段缺少本次详情证据；缺失本身不判下线
    JD 变更   uid 还在，正文哈希与判读时不同（按规范化文本比）
    事实表漂移 批次内可选 career-archive.md 的 md5 与 候选人事实表.md 记录的快照不同 → 提示重判

下一步：stage 只把外部审阅参数写到 数据/追踪/next_judge_args.json。
本仓库不附带 LLM 或 workflow runner；需人工或经授权的 agent 结合事实表、提示词和 schema 审阅。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

from common import OUT_DIR, TOOL_DIR, load_companies, raw_dir, read_json, write_json
from adapters import Adapter

VAULT = os.path.dirname(os.path.dirname(TOOL_DIR))
KEEP_TIERS = ("🥇 投", "🟢 投", "🟡 条件投", "stretch")
STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M")
TODAY = datetime.date.today().isoformat()


def norm_hash(text):
    """去掉全部空白后再哈希：解析器换行策略变化（如 2026-09-23 块级开标签换行修复）不能被当成 JD 变更。"""
    t = re.sub(r"\s+", "", (text or "")).lower()
    return hashlib.md5(t.encode("utf-8")).hexdigest() if t else ""


def latest(path_dir, prefix):
    if not os.path.isdir(path_dir):
        return None
    fs = [f for f in os.listdir(path_dir) if f.startswith(prefix) and f.endswith(".json")]
    if not fs:
        return None
    fs.sort(key=lambda f: os.path.getmtime(os.path.join(path_dir, f)))
    return os.path.join(path_dir, fs[-1])


def run_cmd(args):
    print("$", " ".join(args), flush=True)
    return subprocess.call([sys.executable] + args, cwd=TOOL_DIR)


# ---------------------------------------------------------------- fetch

def cmd_fetch(companies):
    extra = []
    for c in companies or []:
        extra += ["--company", c]
    rc = run_cmd(["fetch_ats.py", "list"] + extra)      # rc=2 只是 must_include 回归提示，不中断
    run_cmd(["fetch_ats.py", "jd"] + extra)
    built = run_cmd(["fetch_ats.py", "build"])
    if built == 0 and os.path.isfile(os.path.join(TOOL_DIR, "spring_internship_policy.json")):
        run_cmd(["spring_internships.py"])
    elif built == 0:
        print("未配置 spring_internship_policy.json；跳过可选实习分支。", flush=True)
    else:
        print("build 失败，本轮不刷新春季实习候选。", flush=True)
    run_cmd(["funnel.py"])
    return rc


# ---------------------------------------------------------------- delta

def company_health(cfg):
    """枚举新鲜且未骤降；Workday 查询失败不能被新增实习的数量掩盖。"""
    out = {}
    for co in cfg["companies"]:
        if co.get("skip") or co.get("display_layer_of"):
            continue
        d = raw_dir(co["key"])
        cur = read_json(os.path.join(d, "_list.json"), None)
        prev = read_json(os.path.join(d, "_list_prev.json"), None)
        if not cur:
            out[co["key"]] = {"ok": False, "why": "无 _list.json"}
            continue
        n, pn = len(cur.get("items", [])), len((prev or {}).get("items", []))
        fresh = str(cur.get("_listed_at", ""))[:10] == TODAY
        ok = fresh and n > 0 and not (pn and n < 0.5 * pn)
        why = "" if ok else ("枚举不是今天的" if not fresh else ("0 条" if n == 0 else f"条数骤降 {pn}→{n}"))
        if ok and cur.get("adapter", co.get("ats")) == "workday":
            totals = read_json(os.path.join(d, "_totals.json"), {}) or {}
            expected = Adapter(co, cfg).keywords() + [f"facet:{name}" for name in co.get("applied_facets", {})]
            failed = [kw for kw in expected if totals.get(kw) is None]
            if failed:
                ok = False
                why = "Workday 查询失败或未核实：" + ", ".join(failed)
        out[co["key"]] = {"ok": ok, "n": n, "prev": pn, "why": why,
                          "listed_at": cur.get("_listed_at")}
    return out


def judged_state(bdir):
    ddir = os.path.join(bdir, "数据")
    p = latest(ddir, "终评_") or latest(ddir, "LLM判读结果_")
    judged = {j["uid"]: j for j in (read_json(p, []) or [])} if p else {}
    # 判读时的 JD 哈希：从所有判读输入批里取（新文件覆盖旧文件）
    jd_hash = {}
    idir = os.path.join(ddir, "判读输入")
    if os.path.isdir(idir):
        fs = [f for f in os.listdir(idir) if f.endswith(".json") and not f.startswith("_")]
        fs.sort(key=lambda f: os.path.getmtime(os.path.join(idir, f)))
        for f in fs:
            for r in (read_json(os.path.join(idir, f), {}) or {}).get("records", []):
                jd_hash[r["uid"]] = norm_hash(r.get("jd_text"))
    return judged, jd_hash, p


def fact_sheet_drift(bdir):
    fs = os.path.join(bdir, "候选人事实表.md")
    arc = os.path.join(bdir, "career-archive.md")
    if not (os.path.exists(fs) and os.path.exists(arc)):
        return None
    m = re.search(r"career-archive\.md md5=([0-9a-f]{32})", open(fs, encoding="utf-8").read())
    now = hashlib.md5(open(arc, "rb").read()).hexdigest()
    return {"snapshot": m.group(1) if m else None, "now": now, "drift": bool(m and m.group(1) != now)}


def current_primary_detail(record, company_status):
    """A primary detail fetched today at/after today's official enumeration."""
    try:
        fetched = datetime.datetime.fromisoformat(str(record.get("fetched_at", "")).replace("Z", "+00:00"))
        listed = datetime.datetime.fromisoformat(str(company_status.get("listed_at", "")).replace("Z", "+00:00"))
        return (record.get("source_authority") == "primary" and fetched.date().isoformat() == TODAY
                and listed.date().isoformat() == TODAY and fetched >= listed)
    except (ValueError, TypeError):
        return False


def cmd_delta(bdir):
    cfg = load_companies()
    health = company_health(cfg)
    judged, jd_hash, judged_path = judged_state(bdir)
    jobs = {r["uid"]: r for r in (read_json(os.path.join(OUT_DIR, "jobs_all.json"), []) or [])}
    fd = os.path.join(OUT_DIR, "funnel")
    pool = {}
    for name, tag in (("kept_for_llm.json", "kept"), ("stretch.json", "stretch"), ("sa_health.json", "sa_health")):
        for r in read_json(os.path.join(fd, name), []) or []:
            pool[r["uid"]] = (tag, r)

    def brief(r, extra=None):
        d = {"uid": r["uid"], "company": r.get("company"), "req_id": r.get("req_id"), "title": r.get("title"),
             "location": r.get("location"), "level_tag": r.get("level_tag"), "url": r.get("url")}
        d.update(extra or {})
        return d

    new = [brief(r, {"pool": tag, "eff_min_years": r.get("eff_min_years")}) for uid, (tag, r) in pool.items() if uid not in judged]
    closed, unverifiable, changed = [], [], []
    for uid, j in judged.items():
        tier = j.get("final_tier") or j.get("script_tier")
        ck = uid.split(":")[0]
        rec = jobs.get(uid)
        if rec is None:
            if tier not in KEEP_TIERS:
                continue
            h = health.get(ck, {"ok": False, "why": "公司不在注册表"})
            item = {"uid": uid, "company": j.get("company"), "req_id": j.get("req_id"), "title": j.get("title"), "tier": tier}
            reason = "本次规范化记录缺失；列表缺失或详情/构建失败均不能证明下线"
            if not h.get("ok"):
                reason += f"；公司枚举未核实：{h.get('why')}"
            unverifiable.append(dict(item, reason=reason))
            continue
        if rec.get("can_apply") is False and tier in KEEP_TIERS:
            current = current_primary_detail(rec, health.get(ck, {}))
            item = {"uid": uid, "company": j.get("company"), "req_id": j.get("req_id"), "title": j.get("title"), "tier": tier}
            if current:
                closed.append(dict(item, reason="本次官方详情 canApply=false", fetched_at=rec["fetched_at"]))
            else:
                unverifiable.append(dict(item, reason="canApply=false 缺少 primary 来源或本次枚举后的当日官方详情证据"))
            continue
        old = jd_hash.get(uid)
        now = norm_hash(rec.get("jd_text"))
        if old and now and old != now:
            changed.append(brief(rec, {"tier": tier}))
    drift = fact_sheet_drift(bdir)
    delta = {"stamp": STAMP, "date": TODAY, "judged_from": os.path.basename(judged_path or ""), "n_judged": len(judged),
             "n_pool": len(pool), "new": new, "closed": closed, "unverifiable": unverifiable, "changed": changed,
             "fact_sheet": drift, "company_health": health}
    tdir = os.path.join(bdir, "数据", "追踪")
    # 累积下线表（render_notes 读它给笔记和 hub 打 ❌）
    cp = os.path.join(tdir, "closed.json")
    acc = read_json(cp, {}) or {}
    for c in closed:
        acc.setdefault(c["uid"], {"date": TODAY, "reason": c["reason"], "title": c["title"], "company": c["company"], "source": "track"})
    for uid in list(acc):
        # apply_links.py 的在招核验判关闭的不在这里撤销：SmartRecruiters 过期页、Workday 撤下的 req
        # 在 ATS 列表里照样挂着（HTTP 200 ≠ 在招），按枚举结果撤销会把它们又放回待投（2026-09-30 修）
        by_check = acc[uid].get("source") == "apply_links" or str(acc[uid].get("reason", "")).startswith("在招核验")
        if by_check:
            continue
        rec = jobs.get(uid) or {}
        current = current_primary_detail(rec, health.get(uid.split(":")[0], {}))
        if rec.get("can_apply") is True and current:
            acc.pop(uid)          # 本次官方详情明确重新开放
        elif not (rec.get("can_apply") is False and current) and not any(item["uid"] == uid for item in unverifiable):
            unverifiable.append({"uid": uid, "company": acc[uid].get("company"), "title": acc[uid].get("title"),
                                 "reason": "保留既有关闭记录；缺少本次 primary 官方详情明确 canApply=true 的复开证据"})
    write_json(cp, acc)
    write_json(os.path.join(tdir, f"delta_{STAMP}.json"), delta)
    # 漏斗计数随增量刷新到批次目录（以前只有带 --export 的全量运行才写，一直停在首轮）
    fc = os.path.join(OUT_DIR, "funnel", "counts.json")
    if os.path.exists(fc):
        write_json(os.path.join(bdir, "数据", "漏斗计数.json"), read_json(fc, {}))
    append_log(bdir, delta)
    print(f"新岗 {len(new)} ／ 已下线 {len(closed)} ／ 无法核实 {len(unverifiable)} ／ JD 变更 {len(changed)}"
          f"｜事实表漂移 {bool(drift and drift['drift'])}｜枚举异常公司 {[k for k, v in health.items() if not v['ok']]}")
    return delta


def append_log(bdir, d):
    p = os.path.join(bdir, "追踪记录.md")
    rel = os.path.relpath(bdir, os.path.join(VAULT, "求职机会"))
    if not os.path.exists(p):
        head = ["---", "tags:", "  - 域/求职", "  - 类型/追踪记录", "  - 状态/活跃", "---", "",
                "# Healthcare-AI 扫描批次 · 增量追踪记录", "",
                "> 每次 `python3 工具/healthcare岗位抓取/track.py run` 追加一节。新岗需要人工或经授权的 agent 完成判读后才会出现在岗位清单里。",
                "> 口径：缺失的 req 一律记为\"无法核实\"；只有本次枚举后取得的当日官方详情 canApply=false 才记\"已下线\"。", ""]
        foot = ["", "---", "", f"相关：[[{rel}/岗位信息|批次归档入口]]｜[[00_岗位总览]]"]
        open(p, "w", encoding="utf-8").write("\n".join(head + ["<!-- 追踪记录插入点 -->"] + foot) + "\n")
    bad = [k for k, v in d["company_health"].items() if not v["ok"]]
    lines = [f"## {d['date']}（{d['stamp']}）", "",
             f"- 判读池 {d['n_pool']} 条，已判读 {d['n_judged']} 条（基准 `{d['judged_from']}`）",
             f"- **新岗 {len(d['new'])}**｜已下线 {len(d['closed'])}｜无法核实 {len(d['unverifiable'])}｜JD 变更 {len(d['changed'])}",
             f"- 枚举异常公司：{', '.join(bad) if bad else '无'}",
             f"- 事实表漂移：{'**是，建议全量重判**' if d['fact_sheet'] and d['fact_sheet']['drift'] else '否'}", ""]
    if d["new"]:
        lines += ["| 新岗 | 公司 | 职位 | 地点 | 级别 | 池 | 原帖 |", "|---|---|---|---|---|---|---|"]
        for n in d["new"][:60]:
            lines.append(f"| {n['req_id']} | {n['company']} | {(n['title'] or '')[:60]} | {n.get('location') or ''} | {n.get('level_tag')} | {n['pool']} | [原帖]({n['url']}) |")
        if len(d["new"]) > 60:
            lines.append(f"| … | 另有 {len(d['new']) - 60} 条见 `数据/追踪/delta_{d['stamp']}.json` | | | | | |")
        lines.append("")
    for key, title in (("closed", "已下线"), ("unverifiable", "无法核实"), ("changed", "JD 变更")):
        if d[key]:
            lines += [f"**{title}**：" + "；".join(f"{x['company']} {x['req_id']}（{x.get('tier', '')}）" for x in d[key][:30]), ""]
    s = open(p, encoding="utf-8").read()
    s = s.replace("<!-- 追踪记录插入点 -->", "<!-- 追踪记录插入点 -->\n\n" + "\n".join(lines), 1)
    open(p, "w", encoding="utf-8").write(s)


# ---------------------------------------------------------------- stage

def cmd_stage(bdir, include_changed=False, delta=None):
    tdir = os.path.join(bdir, "数据", "追踪")
    if delta is None:
        p = latest(tdir, "delta_")
        if not p:
            raise SystemExit("先跑 delta")
        delta = read_json(p)
    uids = [n["uid"] for n in delta["new"]] + ([c["uid"] for c in delta["changed"]] if include_changed else [])
    if not uids:
        print("没有需要判读的岗位")
        write_json(os.path.join(tdir, "next_judge_args.json"), {"batches": []})
        return []
    prefix = f"inc{delta['stamp'].replace('_', '')}"
    up = os.path.join(tdir, f"uids_{prefix}.json")
    write_json(up, uids)
    import judge_tools
    manifest = judge_tools.make_batches(bdir, 9, uids, prefix) or []
    ddir = os.path.join(bdir, "数据")
    args = {"batchDir": bdir, "batches": [m["batch_id"] for m in manifest],
            "promptPath": os.path.join(ddir, "判读提示词_v1.md"), "factSheetPath": os.path.join(bdir, "候选人事实表.md"),
            "schemaPath": os.path.join(ddir, "判读schema_v1.json"), "uidsFile": up, "prefix": prefix}
    write_json(os.path.join(tdir, "next_judge_args.json"), args)
    print(f"{len(uids)} 条 → {len(manifest)} 批（前缀 {prefix}）；外部审阅参数：{os.path.join(tdir, 'next_judge_args.json')}")
    return manifest


def notify(delta):
    n, c = len(delta["new"]), len(delta["closed"])
    if not (n or c):
        return
    msg = f"新岗 {n}，已下线 {c}。请审阅批次内的 next_judge_args.json。"
    try:
        subprocess.call(["/usr/bin/osascript", "-e", f'display notification "{msg}" with title "Healthcare-AI 岗位追踪"'])
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fetch", "delta", "stage", "run"])
    ap.add_argument("--batch-dir", help="delta/stage/run 必填；显式本地批次目录")
    ap.add_argument("--company", action="append")
    ap.add_argument("--include-changed", action="store_true")
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args()
    if a.cmd in ("delta", "stage", "run") and not a.batch_dir:
        ap.error("--batch-dir is required for delta, stage and run; see docs/CRAWLER_SETUP.md")
    if a.cmd in ("stage", "run"):
        required = [os.path.join(a.batch_dir, "候选人事实表.md"),
                    os.path.join(a.batch_dir, "数据", "判读提示词_v1.md"),
                    os.path.join(a.batch_dir, "数据", "判读schema_v1.json")]
        missing = [path for path in required if not os.path.isfile(path)]
        if missing:
            ap.error("Local review inputs required: " + ", ".join(missing) + "; see docs/CRAWLER_SETUP.md")
    if a.cmd in ("fetch", "delta", "run"):
        cfg = load_companies()  # Fail before work starts if local setup is missing.
        if not cfg.get("companies"):
            ap.error("Local company registry is empty; configure companies.json before crawling (docs/CRAWLER_SETUP.md)")
    if a.cmd == "fetch":
        cmd_fetch(a.company)
    elif a.cmd == "delta":
        cmd_delta(a.batch_dir)
    elif a.cmd == "stage":
        cmd_stage(a.batch_dir, a.include_changed)
    elif a.cmd == "run":
        cmd_fetch(a.company)
        d = cmd_delta(a.batch_dir)
        cmd_stage(a.batch_dir, a.include_changed, d)
        if a.notify:
            notify(d)


if __name__ == "__main__":
    main()
