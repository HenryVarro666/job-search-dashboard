#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""healthcare 岗位抓取 —— 申请入口（直接提交链接）生成与核验。

用法：
    python3 apply_links.py verify --batch-dir <批次目录>     # 对终评里 🥇/🟢/🟡/stretch 的 req 生成申请链接并 HTTP 实测，写 数据/申请链接核验.json

各 ATS 的申请入口（2026-09-23 实测，浏览器确认过前端渲染的三类）：
    ashby       <岗位页>/application                          打开即申请表
    lever       <岗位页>/apply                                打开即申请表
    greenhouse  job-boards.greenhouse.io/embed/job_app?for=<board>&token=<id>   打开即申请表（公司自有 careers 页会接管普通链接，所以用 embed 表单）
    workday     <岗位页>/apply                                "Start Your Application"，需登录该公司 Workday 账号
    gem         岗位页本身                                     页面下方就是申请表（Apply and save）
    workable    apply.workable.com/<account>/j/<shortcode>/apply/   打开即申请表
    amazon      岗位页                                         点 Apply now，登录 Amazon 账号后提交（/apply 路径会跳回岗位页）
    eightfold   NVIDIA 用 apply_redirect_url 里的 Workday 申请页；Microsoft 用岗位页
    其余        岗位页
render_notes.py 通过 apply_url(rec) 取链接，通过 数据/申请链接核验.json 取实测状态。

2026-09-29 起 verify 分两层：
    1. 申请链接 HTTP 可达（原逻辑）
    2. 在招核验：按 make_registry.entry 生成的核查源，直接调用 工具/岗位有效性检查/check_jobs.py 的对应检查器
       （Workday CXS / Greenhouse / Ashby board / SmartRecruiters active 字段 / 通用页关闭关键词）。
       起因：SmartRecruiters 过期页、Workday 撤下的 req 都照样返回 HTTP 200，只看状态码会把已过期岗位报成"在线"。
    在招核验判"已关闭 / 疑似关闭"的 req 自动写进 <批次>/数据/追踪/closed.json（与 track.py 同一累计表），
    "请求失败 / 无法核实"只打印提醒，不当下线处理。
"""
import argparse
import glob
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from common import HTML_HEADERS, OUT_DIR, TOOL_DIR, fetch, now_iso, read_json, write_json

sys.path.insert(0, os.path.join(os.path.dirname(TOOL_DIR), "岗位有效性检查"))
import check_jobs  # noqa: E402  库里唯一的岗位有效性核查实现，这里只复用它的检查器
from make_registry import entry as registry_entry  # noqa: E402

LIVE_CLOSED = (check_jobs.CLOSED, check_jobs.LIKELY)


def apply_url(rec):
    """返回 (url, 提交方式说明)。"""
    ats, url = rec.get("ats"), rec.get("url") or ""
    ex = rec.get("extra") or {}
    if ats == "ashby":
        return (ex.get("applyUrl") or url.rstrip("/") + "/application"), "打开即申请表"
    if ats == "lever":
        return (ex.get("applyUrl") or url.rstrip("/") + "/apply"), "打开即申请表"
    if ats == "greenhouse":
        board, jid = ex.get("board"), rec.get("job_id") or rec.get("req_id")
        if board and jid:
            return f"https://job-boards.greenhouse.io/embed/job_app?for={board}&token={jid}", "打开即申请表"
        return url, "岗位页"
    if ats == "workday":
        return url.rstrip("/") + "/apply", "需登录该公司 Workday 账号"
    if ats == "gem":
        return url, "岗位页下方即申请表"
    if ats == "workable":
        m = re.search(r"accounts/([^/?]+)", rec.get("api_url") or "")
        if m:
            return f"https://apply.workable.com/{m.group(1)}/j/{rec.get('req_id')}/apply/", "打开即申请表"
        return url, "岗位页"
    if ats == "amazon":
        return url, "岗位页点 Apply now，需登录 Amazon 账号"
    if ats == "eightfold":
        red = ex.get("apply_redirect_url")
        if red:
            return red, "需登录 NVIDIA Workday 账号"
        return url, "岗位页点 Apply"
    return url, "岗位页"


MARKERS = {"greenhouse": ("Submit application", "application--form"), "lever": ("application-form",),
           "workable": ("application-form", "/apply")}


def check(rec):
    u, how = apply_url(rec)
    r = fetch(u, headers=HTML_HEADERS, as_json=False, tries=3, timeout=30)
    ok = r.status == 200
    marker = None
    if ok and rec.get("ats") in MARKERS:
        marker = any(m in (r.text or "") for m in MARKERS[rec["ats"]])
        ok = ok and marker
    live, live_detail = None, ""
    try:
        src = registry_entry(rec, "")["sources"][0]
        fn = check_jobs.CHECKERS.get(src.get("type"))
        if fn:
            lr = fn(src)
            live, live_detail = lr.get("verdict"), lr.get("detail", "")
    except Exception as e:  # noqa: BLE001
        live, live_detail = check_jobs.ERROR, f"在招核验异常：{e}"
    if live in LIVE_CLOSED:
        ok = False
    return {"uid": rec["uid"], "apply_url": u, "how": how, "status": r.status, "final_url": r.final_url,
            "form_marker": marker, "live": live, "live_detail": live_detail, "ok": ok, "checked_at": now_iso()}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["verify"])
    ap.add_argument("--batch-dir", required=True)
    a = ap.parse_args()
    ddir = os.path.join(a.batch_dir, "数据")
    ff = sorted(glob.glob(os.path.join(ddir, "终评_*.json")), key=os.path.getmtime)
    allj = read_json(ff[-1], []) if ff else []
    keep = {j["uid"] for j in allj if (j.get("final_tier") or j.get("script_tier")) in ("🥇 投", "🟢 投", "🟡 条件投", "stretch")}
    recs = [r for r in read_json(os.path.join(OUT_DIR, "jobs_all.json"), []) if r["uid"] in keep]
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(check, recs))
    write_json(os.path.join(ddir, "申请链接核验.json"), {r["uid"]: r for r in res})
    bad = [r for r in res if not r["ok"]]
    print(f"{len(res)} 条申请链接：通过 {len(res) - len(bad)}，未通过 {len(bad)}")
    for r in bad:
        print("  ✗", r["uid"], r["status"], r.get("live") or "", r.get("live_detail", "")[:80], r["apply_url"])
    # 在招核验判关闭的写进累计下线表（render_notes / render_progress / render_board 都读它）
    cpath = os.path.join(ddir, "追踪", "closed.json")
    closed = read_json(cpath, {}) or {}
    byuid = {r["uid"]: r for r in recs}
    newly = []
    for r in res:
        if r.get("live") in LIVE_CLOSED and r["uid"] not in closed:
            j = byuid.get(r["uid"], {})
            closed[r["uid"]] = {"date": r["checked_at"][:10], "reason": f"在招核验（check_jobs）{r['live']}：{r.get('live_detail', '')}",
                                "title": j.get("title"), "company": j.get("company"), "source": "apply_links"}
            newly.append(r["uid"])
    if newly:
        write_json(cpath, closed)
        print(f"新增下线 {len(newly)} 条 → {cpath}：" + "、".join(newly))
    shaky = [r for r in res if r.get("live") in (check_jobs.ERROR, check_jobs.UNKNOWN)]
    for r in shaky:
        print("  ? 在招无法确认", r["uid"], r.get("live"), r.get("live_detail", "")[:80])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
