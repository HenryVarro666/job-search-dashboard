#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""healthcare 岗位抓取 —— 从求职进度面板的事件日志取投递状态（2026-09-30 起）。

不同页面必须从同一份事件日志派生申请状态，避免旧快照覆盖新结果。

规则：applications.json 是投递动静的唯一事件日志（CLAUDE.md 规定投递、确认、拒信、面试都记这里），
能匹配上的岗位一律以它推出的阶段为准；投递状态.json 只补它没有的（例如"不投"、手工记的已关闭）。
阶段推算直接复用 make_dashboard.resolve_stage，与求职进度面板同一口径。
"""
import json
import os
import re
import sys

from common import TOOL_DIR

PANEL_DIR = os.path.join(os.path.dirname(TOOL_DIR), "求职进度面板")
sys.path.insert(0, PANEL_DIR)
import make_dashboard as md  # noqa: E402

LABEL = {"referral_pending": "内推请求中", "referred": "内推已到·待投", "applied": "已投递", "screening": "已联系·待下一步",
         "assessment": "测评中", "interview": "面试中", "offer": "Offer", "rejected": "已拒", "closed": "已关闭", "withdrawn": "已撤回"}
ACTIVE = {"已投递", "已联系·待下一步", "测评中", "面试中", "Offer", "内推请求中", "内推已到·待投"}


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def load(applications=None):
    """可传同一份原始 applications 列表，避免 --data 主面板与待投池读取不同来源。"""
    if applications is None:
        path = os.path.join(PANEL_DIR, "applications.json")
        try:
            with open(path, encoding="utf-8") as f:
                applications = json.load(f).get("applications", [])
        except (OSError, ValueError):
            return []
    out = []
    for a in applications:
        ev = md.ordered_events(a.get("events") or [])
        if not ev:
            continue
        stage = md.resolve_stage(a, ev)
        last, last_any, outcome = md.status_events(stage, ev)
        submission = md.submission_evidence(a, ev)
        folder = a.get("folder") or ""
        out.append({
            "id": a.get("id"), "req": str(a.get("req") or ""), "company": a.get("company", ""),
            "title": a.get("title") or "", "location": a.get("location") or "",
            "note_url": md.obsidian_url(folder + "/岗位信息") if folder else None,
            "submission": submission,
            "stage": stage, "label": LABEL.get(stage, stage), "active": LABEL.get(stage, stage) in ACTIVE,
            **md.lifecycle_fields(stage, ev),
            "event_order_uncertain": md.event_order_uncertain(ev),
            "date": last.get("date", ""), "time": last.get("time") or "",
            "event": md.EVENT_TYPES.get(last.get("type"), last.get("type")), "summary": last.get("summary", ""),
            "last_any": md.event_snapshot(last_any),
            "outcome": outcome,
        })
    return out


def match(apps, company, req_id=None, text=""):
    """同公司 + req 编号相等 / 前缀相等（a2z 只记了 UUID 前 8 位）/ 出现在链接或标题里（至少 6 位、按词边界）。"""
    req_id, text, co = str(req_id or ""), text or "", _norm(company)
    for a in apps:
        r = a["req"]
        if not r or not co:
            continue
        ac = _norm(a["company"])
        if not (ac and (ac in co or co in ac)):
            continue
        if req_id and (req_id == r or (len(r) >= 6 and req_id.startswith(r))):
            return a
        if len(r) >= 6 and re.search(rf"(?<![0-9A-Za-z]){re.escape(r)}(?![0-9A-Za-z])", text):
            return a
    return None


def note_of(a):
    t = f" {a['time']}" if a.get("time") else ""
    return f"{a['date']}{t} {a['event']}：{a['summary']}"
