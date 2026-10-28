#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""岗位有效性检查工具。

核查 vault（求职机会/）里归档岗位的官方渠道是否仍然在线。
只读 vault、不修改任何档案；结论写入本目录 reports/ 下的报告。

用法：
    python3 check_jobs.py               # 核查所有未关闭岗位
    python3 check_jobs.py --all         # 连档案里已关闭的也查（看是否重新上线）
    python3 check_jobs.py --job affirm  # 只查名称/路径含关键字的岗位
    python3 check_jobs.py --scan        # 找出 vault 里有档案但未登记进 jobs.json 的岗位
    python3 check_jobs.py --json        # 以 JSON 输出（不写报告加 --no-report）

结论分档（宁可报"无法核实"，不把抓取失败当"已关闭"）：
    有效 / 已关闭 / 疑似关闭 / 无法核实 / 请求失败
"""

import argparse
import concurrent.futures
import datetime
import gzip
import json
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY = TOOL_DIR / "jobs.json"
REPORT_DIR = TOOL_DIR / "reports"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

OPEN, CLOSED, LIKELY, UNKNOWN, ERROR = "有效", "已关闭", "疑似关闭", "无法核实", "请求失败"
VERDICT_EMOJI = {OPEN: "✅", CLOSED: "❌", LIKELY: "⚠️", UNKNOWN: "❓", ERROR: "🔌"}
CANON_STATUS = ("待准备", "已投递", "面试中", "已关闭")

_LI_LOCK = threading.Lock()          # LinkedIn 请求串行化 + 限速
_BOARD_CACHE: dict = {}              # Ashby/Breezy 整板列表缓存
_BOARD_LOCK = threading.Lock()


# ---------------------------------------------------------------- HTTP 基础

def _decode(body: bytes, encoding: str, charset) -> str:
    """解码响应体。解压失败抛 ValueError——绝不把压缩字节当乱码文本返回，
    否则关键词判定会把在招页面误报成疑似关闭。"""
    if encoding in ("gzip", "x-gzip"):
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError) as e:
            raise ValueError(f"gzip 解压失败（响应可能被截断）: {e}")
    elif encoding == "deflate":
        try:
            body = zlib.decompress(body)
        except zlib.error:
            try:
                body = zlib.decompress(body, -zlib.MAX_WBITS)
            except zlib.error as e:
                raise ValueError(f"deflate 解压失败: {e}")
    return body.decode(charset or "utf-8", errors="replace")


def _off_path(orig: str, final: str) -> bool:
    """最终落地 URL 是否偏离原 URL（忽略 www 前缀、末尾斜杠、query/fragment）。
    被撤下的页面常被 302 到栏目首页再返回 200——那不是'在线'。"""
    if not final or final == orig:
        return False
    try:
        o, f = urllib.parse.urlsplit(orig), urllib.parse.urlsplit(final)
    except ValueError:
        return False

    def norm(u):
        host = u.netloc.lower()
        host = host[4:] if host.startswith("www.") else host
        return host, (u.path.rstrip("/") or "/")

    return norm(o) != norm(f)


def _diff_host(orig: str, final: str) -> bool:
    try:
        o, f = urllib.parse.urlsplit(orig), urllib.parse.urlsplit(final)
    except ValueError:
        return False
    strip = lambda h: h.lower()[4:] if h.lower().startswith("www.") else h.lower()
    return strip(o.netloc) != strip(f.netloc)


def fetch(url, headers=None, timeout=25, retries=2, max_bytes=3_000_000):
    """GET url。返回 (status, text, final_url, content_type, err)。

    网络层失败时 status=None、err 为原因；HTTP 4xx/5xx 正常返回状态码与 body。
    """
    hdrs = dict(BROWSER_HEADERS)
    if headers:
        hdrs.update(headers)
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(max_bytes)
                try:
                    text = _decode(body, resp.headers.get("Content-Encoding", ""),
                                   resp.headers.get_content_charset())
                except ValueError as de:
                    last_err = str(de)
                    if attempt < retries:
                        time.sleep(1.0 * (attempt + 1))
                        continue
                    return None, "", url, "", last_err
                return (resp.status, text, resp.geturl(),
                        resp.headers.get("Content-Type", ""), None)
        except urllib.error.HTTPError as e:
            try:
                body = e.read(max_bytes)
            except Exception:
                body = b""
            enc = e.headers.get("Content-Encoding", "") if e.headers else ""
            charset = e.headers.get_content_charset() if e.headers else None
            try:
                text = _decode(body, enc, charset)
            except ValueError:
                text = ""
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                last_err = f"HTTP {e.code}"
                time.sleep(1.5 * (attempt + 1))
                continue
            ctype = e.headers.get("Content-Type", "") if e.headers else ""
            return e.code, text, getattr(e, "url", url), ctype, None
        except Exception as e:  # URLError / timeout / SSL 等
            last_err = f"{type(e).__name__}: {e}"
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
    return None, "", url, "", last_err


# ---------------------------------------------------------------- 各 ATS 检查器
# 每个检查器返回 dict：label / url / verdict / confidence(high|medium|low) / detail

def _res(src, verdict, detail, confidence="high", url=None):
    return {
        "label": src.get("label") or src.get("type", "?"),
        "url": url or src.get("url", ""),
        "verdict": verdict,
        "confidence": confidence,
        "detail": detail,
        "authority": src.get("authority", "primary"),
    }


def check_greenhouse(src):
    m = re.search(r"greenhouse\.io/([^/]+)/jobs/(\d+)", src["url"])
    if not m:
        return _res(src, ERROR, f"无法从 URL 解析 Greenhouse board/req：{src['url']}")
    board, jid = m.groups()
    api = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid}"
    status, text, _, _, err = fetch(api, headers={"Accept": "application/json"})
    if status is None:
        return _res(src, ERROR, f"Greenhouse API 请求失败：{err}", url=api)
    if status == 200:
        try:
            data = json.loads(text)
            title = data.get("title", "?")
            updated = (data.get("updated_at") or "")[:10]
            return _res(src, OPEN, f"在招：{title}（更新于 {updated}）", url=api)
        except json.JSONDecodeError:
            return _res(src, ERROR, "Greenhouse API 返回非 JSON", url=api)
    if status == 404:
        # 404 分不清"req 下线"和"board 改名/停用公开 API"——探一下 board 根再定
        bstatus, _, _, _, _ = fetch(f"https://boards-api.greenhouse.io/v1/boards/{board}",
                                    headers={"Accept": "application/json"})
        if bstatus == 200:
            return _res(src, CLOSED,
                        "Greenhouse API 404（board 本身正常）—— 该 req 已从公开板下线", url=api)
        return _res(src, UNKNOWN,
                    f"req 404 且 board '{board}' 也不可达（HTTP {bstatus}）"
                    "——可能是 board 改名/停用公开 API，不能据此判关闭",
                    confidence="low", url=api)
    return _res(src, ERROR, f"Greenhouse API HTTP {status}", url=api)


def check_workday(src):
    url = src["url"].split("?")[0].rstrip("/")
    if url.endswith("/apply"):
        url = url[: -len("/apply")]
    m = re.match(r"https://([^/]+)/(.+)$", url)
    if not m:
        return _res(src, ERROR, f"无法解析 Workday URL：{url}")
    host, path = m.groups()
    parts = path.split("/")
    if parts and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]):  # 可选 locale 段
        parts = parts[1:]
    if len(parts) < 3 or "job" not in parts:
        return _res(src, ERROR, f"Workday URL 结构异常：{url}")
    tenant = host.split(".")[0]
    site = parts[0]
    rest = "/".join(parts[1:])
    cxs = f"https://{host}/wday/cxs/{tenant}/{site}/{rest}"
    status, text, _, _, err = fetch(cxs, headers={"Accept": "application/json"})
    if status is None:
        return _res(src, ERROR, f"Workday CXS 请求失败：{err}", url=cxs)
    if status == 200:
        try:
            info = json.loads(text).get("jobPostingInfo")
        except json.JSONDecodeError:
            return _res(src, ERROR, "Workday CXS 返回非 JSON", url=cxs)
        if not info:
            return _res(src, LIKELY, "CXS 200 但无 jobPostingInfo（可能已撤帖）",
                        confidence="medium", url=cxs)
        title = info.get("title", "?")
        req_id = info.get("jobReqId", "")
        posted_on = info.get("postedOn") or info.get("startDate") or ""
        if info.get("canApply") is False:
            return _res(src, LIKELY, f"帖子在线但 canApply=false：{title} {req_id}",
                        confidence="medium", url=cxs)
        return _res(src, OPEN, f"在招：{title}（{req_id}，{posted_on}）", url=cxs)
    if status == 404:
        site_url = f"https://{host}/{site}"
        sstatus, _, _, _, _ = fetch(site_url)
        if sstatus == 200:
            return _res(src, CLOSED, "Workday CXS 404（招聘站点本身正常）—— 该 req 已撤下", url=cxs)
        return _res(src, UNKNOWN,
                    f"CXS 404 且站点 {site_url} 也不可达（HTTP {sstatus}）"
                    "——可能 tenant/site 已改名，不能据此判关闭",
                    confidence="low", url=cxs)
    if status == 403 and _json_field(text, "errorCode") == "S22":
        return _workday_s22(src, host, tenant, site, rest, cxs)
    return _res(src, ERROR, f"Workday CXS HTTP {status}", url=cxs)


def _json_field(text, key):
    try:
        v = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return v.get(key) if isinstance(v, dict) else None


def _post_json(url, payload, timeout=25):
    """POST JSON，返回 (status, 解析后的 JSON 或 None, err)。只给 Workday 站内搜索用。"""
    hdrs = dict(BROWSER_HEADERS)
    hdrs.update({"Accept": "application/json", "Content-Type": "application/json"})
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = _decode(resp.read(3_000_000), resp.headers.get("Content-Encoding", ""),
                           resp.headers.get_content_charset())
            return resp.status, json.loads(text), None
    except urllib.error.HTTPError as e:
        return e.code, None, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001  网络、解压、非 JSON
        return None, None, f"{type(e).__name__}: {e}"


def _workday_s22(src, host, tenant, site, rest, cxs):
    """CXS 详情 403 + errorCode S22（"permission denied"，本站不可见）时，按 req 号在同一站内搜索。

    2026-10-08 起加（之前一律报"请求失败"，下线的岗进不了下线表）：Dexcom JR117507、Tempus JR202600508、
    J&J R-095856、Amgen R-250809 / R-253108 都是 S22 且站内搜不到，而同站编号相邻的岗都还在；阳性对照
    Amgen R-256345、Caris JR104962 按 req 号都能搜到。S22 也可能是转成了内部岗，所以只判"疑似关闭"。
    """
    rid = rest.rsplit("_", 1)[-1]
    rid0 = re.sub(r"-\d{1,2}$", "", rid)  # 去掉重发后缀 -1 / -2
    search = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    st, data, err = _post_json(search, {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": rid0})
    if not isinstance(data, dict) or not isinstance(data.get("jobPostings"), list):
        return _res(src, UNKNOWN, f"CXS 403 S22（本站不可见），站内搜索也没拿到结果（{err or st}），不判关闭",
                    confidence="low", url=cxs)
    hits = [j.get("externalPath", "") for j in data["jobPostings"] if rid0.lower() in (j.get("externalPath") or "").lower()]
    if hits:
        return _res(src, OPEN, f"原链接 CXS 403 S22，但站内按 {rid0} 搜到：{hits[0]}（换了 posting，投递用新链接）",
                    confidence="medium", url=f"https://{host}/{site}{hits[0]}")
    st0, d0, _ = _post_json(search, {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
    total = d0.get("total") if isinstance(d0, dict) else None
    if not total:
        return _res(src, UNKNOWN, f"CXS 403 S22，站内搜不到 {rid0}，但整站空查询也没返回岗位数，不判关闭",
                    confidence="low", url=cxs)
    return _res(src, LIKELY, f"CXS 403 S22（本站不可见）且站内按 {rid0} 搜不到（全站 {total} 条在招）"
                "—— 已从公开站撤下，也可能只转成内部岗", confidence="medium", url=cxs)


def check_tiktok(src):
    """lifeattiktok 公开详情接口（2026-10-08 加）。条目写 job_post_id；只在内推门户可见的岗加 "portal_only": true。

    判活签名（调研方法 §1 TikTok 一节）：data.job_post_detail.channel_online_status，1 = 公开站在线。
    不存在的 id 返回 job_post_detail = null。2026-10-08 测 9/6 抓的 51 条校招帖仍全部为 1，暂无"已撤帖"
    阴性对照，所以 null 与公开岗变 0 都只判"疑似关闭"。门户专属岗（Future Talent USDS 等）的 online 常态就是 0，
    接口还能返回只说明帖子没删，在不在招要登录内推门户看，报"无法核实"。
    """
    pid = str(src.get("job_post_id") or "").strip()
    if not pid:
        m = re.search(r"/(\d{15,})", src.get("url", ""))
        pid = m.group(1) if m else ""
    if not pid:
        return _res(src, ERROR, "TikTok 条目缺 job_post_id，URL 里也没有")
    api = f"https://lifeattiktok.com/api/v1/public/supplier/job/posts/{pid}"
    hdrs = dict(BROWSER_HEADERS)
    hdrs.update({"Accept": "application/json", "Content-Type": "application/json",
                 "website-path": "tiktok", "portal-channel": "tiktok", "portal-platform": "PC"})
    req = urllib.request.Request(api, data=json.dumps({"job_post_id": pid}).encode(), headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            d = json.loads(_decode(resp.read(3_000_000), resp.headers.get("Content-Encoding", ""),
                                   resp.headers.get_content_charset()))
    except Exception as e:  # noqa: BLE001  缺头时常返回空 body 或非 JSON，不能当下线
        return _res(src, ERROR, f"TikTok 详情接口失败：{type(e).__name__}: {e}", url=api)
    if not isinstance(d, dict) or d.get("code") != 0:
        return _res(src, ERROR, f"TikTok 详情接口返回异常：{str(d)[:120]}", url=api)
    det = (d.get("data") or {}).get("job_post_detail")
    if not det:
        return _res(src, LIKELY, f"详情接口对 {pid} 返回空（帖子已撤或 id 失效）", confidence="medium", url=api)
    name = f"{det.get('code') or ''} {det.get('title') or ''}".strip()
    online = det.get("channel_online_status")
    if online == 1:
        return _res(src, OPEN, f"公开站在线：{name}", url=api)
    if src.get("portal_only"):
        return _res(src, UNKNOWN, f"门户专属岗，详情接口仍返回（{name}，online={online} 是常态）；在不在招要登录内推门户看",
                    confidence="low", url=api)
    return _res(src, LIKELY, f"公开站已下线（channel_online_status={online}）：{name}", confidence="medium", url=api)


def _get_board(kind, org, url):
    key = (kind, org)
    with _BOARD_LOCK:
        if key in _BOARD_CACHE:
            return _BOARD_CACHE[key]
    # 整板 JSON 可能远超默认的 3MB 上限（Snowflake Ashby 板约 4.5MB，2026-10-04），截断后会被误报成非 JSON
    limit = 30_000_000
    status, text, _, _, err = fetch(url, headers={"Accept": "application/json"}, max_bytes=limit)
    data = None
    if status == 200:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            err = ("整板列表疑似被截断（达到读取上限）" if len(text) >= limit * 0.99
                   else "整板列表返回非 JSON")
    elif err is None:
        err = f"HTTP {status}"
    if data is not None:  # 失败不入缓存——瞬时抖动不该拖垮同板所有渠道
        with _BOARD_LOCK:
            _BOARD_CACHE[key] = (data, None)
    return data, err


_ASHBY_URL_RE = re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})")


def check_ashby(src):
    # 条目里显式写的 org / posting_id 优先；缺哪个就从 URL jobs.ashbyhq.com/{org}/{uuid} 补
    org = src.get("org")
    pid = src.get("posting_id")
    if not (org and pid):
        m = _ASHBY_URL_RE.search(src.get("url", ""))
        if m:
            org, pid = org or m.group(1), pid or m.group(2)
    if not (org and pid):
        return _res(src, UNKNOWN,
                    "Ashby 条目缺 org/posting_id，且 URL 里没有 posting UUID"
                    "（整板 URL 只能人工对照，脚本不能自动判定）",
                    confidence="low")
    api = f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(org)}"
    data, err = _get_board("ashby", org, api)
    if data is None:
        return _res(src, ERROR, f"Ashby 整板列表请求失败：{err}", url=api)
    jobs = data.get("jobs")
    if not jobs:
        return _res(src, UNKNOWN, "Ashby 整板列表为空或结构异常，不能据此判关闭",
                    confidence="low", url=api)
    hit = next((j for j in jobs if j.get("id") == pid), None)
    if hit:
        title = hit.get("title", "?")
        loc = hit.get("location", "")
        if hit.get("isListed") is False:
            return _res(src, LIKELY, f"帖子存在但 isListed=false：{title}",
                        confidence="medium", url=api)
        return _res(src, OPEN, f"在招：{title} @ {loc}", url=api)
    return _res(src, CLOSED,
                f"已从 Ashby 公开 board 下线（板上现有 {len(jobs)} 个在招岗位；"
                "unlisted 直链岗不在该列表，如握有内推直链可人工复核）", url=api)


_LEVER_URL_RE = re.compile(r"jobs\.lever\.co/([^/?#]+)/([0-9a-f-]{36})")


def check_lever(src):
    """Lever Postings API（公开、无需鉴权）。判活签名 2026-09-23 实测：
    在招 → 200 + 含 id 的 JSON 对象；posting 下线 → 404 + {"ok": false, "error": "Document not found"}；
    org 不存在时 posting 与整板都回同一个 404 报文，所以 404 后必须再探整板才能区分。"""
    org = src.get("org")
    pid = src.get("posting_id")
    if not (org and pid):
        m = _LEVER_URL_RE.search(src.get("url", ""))
        if m:
            org, pid = org or m.group(1), pid or m.group(2)
    if not (org and pid):
        return _res(src, ERROR,
                    f"Lever 需要 org + posting_id（或含 posting UUID 的 URL）：{src.get('url', '')}")
    base = f"https://api.lever.co/v0/postings/{urllib.parse.quote(org)}"
    api = f"{base}/{pid}?mode=json"
    status, text, _, _, err = fetch(api, headers={"Accept": "application/json"})
    if status is None:
        return _res(src, ERROR, f"Lever API 请求失败：{err}", url=api)
    if status == 200:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return _res(src, ERROR, "Lever API 返回非 JSON", url=api)
        if not isinstance(data, dict) or not data.get("id"):
            return _res(src, UNKNOWN, "Lever API 200 但无 id 字段（结构异常），不能据此判定",
                        confidence="low", url=api)
        title = data.get("text", "?")
        loc = (data.get("categories") or {}).get("location") or ""
        created = data.get("createdAt")
        created = (time.strftime("%Y-%m-%d", time.gmtime(created / 1000))
                   if isinstance(created, (int, float)) else "")
        return _res(src, OPEN, f"在招：{title} @ {loc}（发布 {created}）", url=api)
    if status == 404:
        # 404 分不清"posting 下线"和"org 改名/不存在"（报文相同）——探整板再定
        bstatus, btext, _, _, _ = fetch(f"{base}?mode=json&limit=1",
                                        headers={"Accept": "application/json"})
        board = None
        if bstatus == 200:
            try:
                board = json.loads(btext)
            except json.JSONDecodeError:
                board = None
        if isinstance(board, list):
            if board:
                return _res(src, CLOSED,
                            "Lever API 404（board 本身正常）—— 该 posting 已下线", url=api)
            return _res(src, CLOSED,
                        "Lever API 404 且 board 现为空板（org 存在但零在招）—— 该 posting 已下线；"
                        "整板是否迁走建议人工看一眼", confidence="medium", url=api)
        return _res(src, UNKNOWN,
                    f"posting 404 且 board '{org}' 也不可达（HTTP {bstatus}）"
                    "——可能是 org 改名/停用公开 API，不能据此判关闭",
                    confidence="low", url=api)
    return _res(src, ERROR, f"Lever API HTTP {status}", url=api)


def check_smartrecruiters(src):
    company = src.get("company")
    pid = src.get("posting_id")
    if not (company and pid):
        m = re.search(r"smartrecruiters\.com/v1/companies/([^/]+)/postings/(\d+)",
                      src.get("url", ""))
        if m:
            company, pid = m.groups()
        else:
            return _res(src, ERROR, "SmartRecruiters 需要 company + posting_id")
    api = f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{pid}"
    status, text, _, _, err = fetch(api, headers={"Accept": "application/json"})
    if status is None:
        return _res(src, ERROR, f"SmartRecruiters API 请求失败：{err}", url=api)
    if status == 200:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return _res(src, ERROR, "SmartRecruiters API 返回非 JSON", url=api)
        title = data.get("name", "?")
        released = (data.get("releasedDate") or "")[:10]
        if data.get("active") is False:
            return _res(src, LIKELY, f"帖子存在但 active=false：{title}",
                        confidence="medium", url=api)
        return _res(src, OPEN, f"在招：{title}（发布 {released}）", url=api)
    if status == 404:
        cstatus, _, _, _, _ = fetch(
            f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=1",
            headers={"Accept": "application/json"})
        if cstatus == 200:
            return _res(src, CLOSED, "SmartRecruiters API 404（公司板正常）—— 帖子已取消发布", url=api)
        return _res(src, UNKNOWN,
                    f"帖子 404 且公司板 '{company}' 也不可达（HTTP {cstatus}），不能据此判关闭",
                    confidence="low", url=api)
    return _res(src, ERROR, f"SmartRecruiters API HTTP {status}", url=api)


def check_breezy(src):
    org = src["org"]
    pos = src["position"]
    api = f"https://{org}.breezy.hr/json"
    data, err = _get_board("breezy", org, api)
    if data is None:
        return _res(src, ERROR, f"Breezy 整板列表请求失败：{err}", url=api)
    if not isinstance(data, list):
        return _res(src, ERROR, "Breezy 整板列表结构异常（非数组）", url=api)
    if not data:
        return _res(src, UNKNOWN, "Breezy 整板列表为空，不能据此判关闭",
                    confidence="low", url=api)
    hit = next(
        (p for p in data
         if f"/p/{pos}" in (p.get("url") or "") or str(p.get("id", "")).startswith(pos)),
        None,
    )
    if hit:
        title = hit.get("name", "?")
        loc = ((hit.get("location") or {}).get("name")
               if isinstance(hit.get("location"), dict) else "") or ""
        return _res(src, OPEN, f"在招：{title} @ {loc}", url=api)
    return _res(src, CLOSED,
                f"已不在 Breezy 公开列表（现有 {len(data)} 个在招岗位）", url=api)


_LI_CLOSED_MARKERS = (
    "no longer accepting applications",
    "this job is no longer available",
    "job is closed",
)


def check_linkedin(src):
    m = re.search(r"(\d{8,})", src["url"])
    if not m:
        return _res(src, ERROR, f"无法从 URL 解析 LinkedIn job id：{src['url']}")
    jid = m.group(1)
    api = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}"
    with _LI_LOCK:
        time.sleep(1.5)
        status, text, final, _, err = fetch(api)
    if status is None:
        return _res(src, ERROR, f"LinkedIn 请求失败：{err}", url=api)
    if status in (403, 429, 999) or "authwall" in final or "authwall" in text[:3000]:
        return _res(src, UNKNOWN,
                    f"被 LinkedIn 反爬拦截（HTTP {status}），需人工打开页面确认",
                    confidence="low", url=src["url"])
    if status == 404:
        return _res(src, CLOSED, "guest 接口 404 —— 帖子大概率已删除（建议人工复核）",
                    confidence="medium", url=api)
    if status == 200:
        low = text.lower()
        for marker in _LI_CLOSED_MARKERS:
            if marker in low:
                return _res(src, CLOSED, f"页面明示关闭：'{marker}'", url=api)
        tm = re.search(r"top-card-layout__title[^>]*>\s*([^<]+)<", text)
        title = tm.group(1).strip() if tm else None
        if title or "top-card" in text or "num-applicants" in text:
            return _res(src, OPEN,
                        f"guest 页面仍在线{'：' + title if title else ''}（LinkedIn 判定为中置信度）",
                        confidence="medium", url=api)
        return _res(src, UNKNOWN, "guest 接口返回 200 但内容无法判定，需人工确认",
                    confidence="low", url=api)
    return _res(src, ERROR, f"LinkedIn guest 接口 HTTP {status}", url=api)


def check_page(src):
    status, text, final, _, err = fetch(src["url"])
    if status is None:
        return _res(src, ERROR, f"页面请求失败：{err}")
    if status == 404:
        return _res(src, LIKELY, "页面 404（岗位页可能已撤下）", confidence="medium")
    if status == 403:
        return _res(src, UNKNOWN, "被 WAF/反爬拦截（HTTP 403），需人工确认", confidence="low")
    if status != 200:
        return _res(src, ERROR, f"页面 HTTP {status}")
    moved = _off_path(src["url"], final)
    move_note = f"（已被重定向到 {final}）" if moved else ""
    low = text.lower()
    for marker in ("no longer posted", *src.get("closed_markers", [])):
        if marker.lower() in low:
            return _res(src, CLOSED, f"页面命中关闭标记：'{marker}'{move_note}")
    must = src.get("must_contain", [])
    if must:
        missing = [k for k in must if k.lower() not in low]
        if not missing:
            if moved and _diff_host(src["url"], final):
                return _res(src, UNKNOWN,
                            f"被重定向到其他站点 {final}，关键词命中不可信，需人工确认",
                            confidence="low")
            return _res(src, OPEN, f"页面在线且包含关键词 {must}{move_note}",
                        confidence="medium")
        if src.get("spa"):
            return _res(src, UNKNOWN,
                        f"页面为 JS 渲染（SPA），静态 HTML 未见 {missing}，需人工确认",
                        confidence="low")
        return _res(src, LIKELY,
                    f"页面在线但未找到关键词 {missing}{move_note}", confidence="medium")
    return _res(src, OPEN, f"页面在线（未配置判定关键词）{move_note}", confidence="low")


def check_eightfold(src):
    """Eightfold PCSX（Microsoft 等）。详情 API 的判活签名实测于 2026-09-12：
    在招 → 200 + 完整 JSON；已下线 → 404 + {"message": "Job with ID <id> not found"}；
    限流 → 429 或空 body（不能判关闭）。"""
    m = re.search(r"/careers/job/(\d+)", src["url"]) or re.search(r"/jobs/(\d+)", src["url"])
    if not m:
        return _res(src, ERROR, f"无法从 URL 解析 Eightfold job id：{src['url']}")
    jid = m.group(1)
    host = re.match(r"https://([^/]+)/", src["url"])
    host = host.group(1) if host else "apply.careers.microsoft.com"
    # domain 从条目读：Microsoft 条目 "microsoft.com"；NVIDIA 条目必须写 "nvidia.com"，
    # URL 形如 https://jobs.nvidia.com/careers/job/<12 位数字>。
    # 本请求不带 Referer：2026-09-23 有过带 Referer 被 NVIDIA 的 CloudFront 边缘回 403 的记录
    #（同日补丁复测 5 种 Referer 值均 200，疑与边缘节点/时段有关）；fetch() 默认头里没有
    # Referer，这里也不要加。
    domain = src.get("domain") or "microsoft.com"
    api = f"https://{host}/api/apply/v2/jobs/{jid}?domain={domain}"
    status, text, _, _, err = fetch(api, headers={"Accept": "application/json"})
    if status is None:
        return _res(src, ERROR, f"Eightfold API 请求失败：{err}", url=api)
    if status == 200:
        if not text.strip():
            return _res(src, UNKNOWN, "API 200 但 body 为空（多半是限流，不可判关闭）",
                        confidence="low", url=api)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return _res(src, ERROR, "Eightfold API 返回非 JSON", url=api)
        if not data.get("job_description"):
            return _res(src, LIKELY, "API 200 但无 job_description（可能已撤帖）",
                        confidence="medium", url=api)
        title = data.get("name", "?")
        req = data.get("display_job_id") or data.get("ats_job_id") or ""
        upd = data.get("t_update")
        upd = time.strftime("%Y-%m-%d", time.gmtime(upd)) if isinstance(upd, int) else ""
        return _res(src, OPEN, f"在招：{title}（{req}，更新于 {upd}）", url=api)
    if status == 404:
        if "not found" in text.lower():
            return _res(src, CLOSED, f"Eightfold API 404（{text.strip()[:80]}）—— 该 req 已下线", url=api)
        return _res(src, UNKNOWN, "HTTP 404 但无 not-found 报文，可能是路径变更",
                    confidence="low", url=api)
    if status in (403, 429):
        return _res(src, UNKNOWN, f"HTTP {status}（限流或反爬，不可判关闭）", confidence="low", url=api)
    return _res(src, ERROR, f"Eightfold API HTTP {status}", url=api)


def check_http_ok(src):
    status, _, final, ctype, err = fetch(src["url"])
    if status is None:
        return _res(src, ERROR, f"请求失败：{err}")
    if status == 200:
        base_ct = (ctype.split(";")[0] or "").strip().lower()
        if _off_path(src["url"], final):
            return _res(src, LIKELY,
                        f"被重定向到 {final}（原文件可能已撤下，200 来自落地页）",
                        confidence="medium")
        expect = src.get("expect_content_type")
        if expect and base_ct != expect.lower():
            return _res(src, LIKELY,
                        f"内容类型 {base_ct or '未知'} 与预期 {expect} 不符（可能是软 404 页）",
                        confidence="medium")
        return _res(src, OPEN, f"文件/页面在线（{base_ct or '未知类型'}）")
    if status == 404:
        return _res(src, CLOSED, "已下线（HTTP 404）")
    return _res(src, ERROR, f"HTTP {status}")


CHECKERS = {
    "greenhouse": check_greenhouse,
    "workday": check_workday,
    "ashby": check_ashby,
    "lever": check_lever,
    "smartrecruiters": check_smartrecruiters,
    "breezy": check_breezy,
    "linkedin": check_linkedin,
    "eightfold": check_eightfold,
    "tiktok": check_tiktok,
    "page": check_page,
    "http_ok": check_http_ok,
}


# ---------------------------------------------------------------- vault 状态解析

_STATUS_CACHE: dict = {}


def read_vault_status(vault_root: Path, folder: str):
    """从 岗位信息.md 的 `- **状态**：xxx` 行取第一个规范状态值。"""
    if folder in _STATUS_CACHE:
        return _STATUS_CACHE[folder]
    path = vault_root / folder / "岗位信息.md"
    status = "未知"
    try:
        text = path.read_text(encoding="utf-8")
        m = re.search(r"\*\*状态\*\*\s*[:：]\s*([^\n]+)", text)
        if m:
            sm = re.search("|".join(CANON_STATUS), m.group(1))
            if sm:
                status = sm.group(0)
    except OSError:
        status = "档案缺失"
    _STATUS_CACHE[folder] = status
    return status


# ---------------------------------------------------------------- 汇总与输出

def aggregate(results):
    """把一个岗位多渠道结果并成单一结论。"""
    if not results:
        return UNKNOWN, "未登记任何官方渠道，无法自动核查"
    prim = [r for r in results if r["authority"] == "primary"] or results
    opens_strong = [r for r in prim
                    if r["verdict"] == OPEN and r["confidence"] in ("high", "medium")]
    opens_weak = [r for r in prim
                  if r["verdict"] == OPEN and r["confidence"] == "low"]
    closed_hi = [r for r in prim if r["verdict"] == CLOSED and r["confidence"] == "high"]
    closed_any = [r for r in prim if r["verdict"] == CLOSED]
    likely = [r for r in prim if r["verdict"] == LIKELY]
    errors = [r for r in prim if r["verdict"] == ERROR]
    if opens_strong:
        n_down = len(closed_any) + len(likely)
        if n_down:
            return OPEN, (f"{len(opens_strong)}/{len(prim)} 个一手渠道在招"
                          f"（另有 {n_down} 个渠道已下线）")
        return OPEN, f"{len(opens_strong)}/{len(prim)} 个一手渠道确认在招"
    if closed_hi:
        return CLOSED, f"{len(closed_any)}/{len(prim)} 个一手渠道确认下线"
    if closed_any or likely:
        return LIKELY, "一手渠道显示疑似下线（无高置信关闭证据，建议人工复核）"
    if opens_weak:
        return UNKNOWN, "仅有低置信在线信号（如页面 200 但无判定关键词），需人工确认"
    if errors and len(errors) == len(prim):
        return ERROR, "所有渠道请求失败（网络/接口问题，非岗位关闭）"
    return UNKNOWN, "现有渠道均无法自动判定，需人工确认"


def mismatch_flag(vault_status, verdict, reopen_ok=True):
    if vault_status in ("待准备", "已投递", "面试中") and verdict == CLOSED:
        return f"⚠️ 档案状态仍为「{vault_status}」但官方渠道已下线 → 建议把档案状态改为「已关闭」并同步 00_岗位总览"
    if vault_status in ("待准备", "已投递", "面试中") and verdict == LIKELY:
        return f"🟠 档案状态为「{vault_status}」但渠道疑似下线 → 建议人工打开链接确认"
    if vault_status == "已关闭" and verdict == OPEN and reopen_ok:
        return "🔄 档案已关闭但官方渠道仍在线/重新上线 → 如有兴趣可重开调研"
    return None


def disp_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s: str, w: int) -> str:
    return s + " " * max(0, w - disp_width(s))


def check_job(job, vault_root):
    status = read_vault_status(vault_root, job["folder"])
    results = []
    for src in job.get("sources", []):
        checker = CHECKERS.get(src.get("type"))
        if checker is None:
            results.append(_res(src, ERROR, f"未知渠道类型：{src.get('type')}"))
            continue
        try:
            results.append(checker(src))
        except Exception as e:  # 单渠道崩溃不拖垮整轮
            results.append(_res(src, ERROR, f"检查器异常：{type(e).__name__}: {e}"))
    verdict, summary = aggregate(results)
    return {
        "name": job["name"],
        "folder": job["folder"],
        "vault_status": status,
        "verdict": verdict,
        "summary": summary,
        "flag": mismatch_flag(status, verdict, job.get("reopen_flag", True)),
        "notes": job.get("notes"),
        "sources": results,
        "skipped": False,
    }


def build_report(rows, checked_at):
    lines = [
        "# 岗位有效性报告",
        "",
        f"> 生成时间：{checked_at}。工具：`工具/岗位有效性检查/check_jobs.py`。",
        "> 本报告只回答一件事：**官方渠道上的帖子是否还在线**。它不改写任何档案；",
        "> 岗位质量与签证结论以各岗位 `调研报告.md` 与 `00_岗位总览` 为准。",
        "",
        "## 结论速览",
        "",
        "| 岗位 | 档案状态 | 核查结论 | 摘要 |",
        "|---|---|---|---|",
    ]
    for r in rows:
        verdict = ("跳过" if r["skipped"]
                   else f"{VERDICT_EMOJI[r['verdict']]} {r['verdict']}")
        summary = "档案已关闭，默认不核查（--all 可查）" if r["skipped"] else r["summary"]
        lines.append(f"| {r['name']} | {r['vault_status']} | {verdict} | {summary} |")
    flags = [r for r in rows if not r["skipped"] and r["flag"]]
    lines += ["", "## 需要行动", ""]
    if flags:
        for r in flags:
            lines.append(f"- **{r['name']}**：{r['flag']}")
    else:
        lines.append("- 无。核查结论与档案状态一致。")
    lines += ["", "## 渠道明细", ""]
    for r in rows:
        if r["skipped"]:
            continue
        lines.append(f"### {r['name']}")
        lines.append("")
        lines.append(f"- 档案：`{r['folder']}`（状态：{r['vault_status']}）")
        lines.append(f"- 结论：{VERDICT_EMOJI[r['verdict']]} **{r['verdict']}** —— {r['summary']}")
        if r["notes"]:
            lines.append(f"- 注：{r['notes']}")
        for s in r["sources"]:
            lines.append(
                f"  - [{s['verdict']}·{s['confidence']}] {s['label']}：{s['detail']}"
                f"（[链接]({s['url']})）"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="核查归档岗位的官方渠道是否仍然在线")
    ap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    ap.add_argument("--all", action="store_true", help="连档案已关闭的岗位也核查")
    ap.add_argument("--job", action="append", default=[],
                    help="只查名称/路径含该关键字的岗位（可重复）")
    ap.add_argument("--scan", action="store_true",
                    help="找出 vault 里有档案但未登记进 jobs.json 的岗位")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    ap.add_argument("--no-report", action="store_true", help="不写报告文件")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)

    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    vault_root = (args.registry.parent / registry.get("vault_root", "../..")).resolve()
    jobs = registry["jobs"]

    if args.scan:
        registered = {j["folder"] for j in jobs}
        on_disk = {
            str(p.parent.relative_to(vault_root))
            for p in (vault_root / "求职机会").glob("*/*/岗位信息.md")
        }
        missing = sorted(on_disk - registered)
        stale = sorted(registered - on_disk)
        if missing:
            print("vault 里有档案但未登记进 jobs.json（建议补登）：")
            for f in missing:
                print(f"  - {f}")
        if stale:
            print("jobs.json 里登记了但 vault 已无档案：")
            for f in stale:
                print(f"  - {f}")
        if not missing and not stale:
            print("jobs.json 与 vault 档案一致。")
        return 0

    if args.job:
        keys = [k.lower() for k in args.job]
        jobs = [j for j in jobs
                if any(k in (j["name"] + j["folder"]).lower() for k in keys)]
        if not jobs:
            print(f"没有岗位匹配 {args.job}", file=sys.stderr)
            return 1

    rows = []
    to_check = []
    for job in jobs:
        status = read_vault_status(vault_root, job["folder"])
        if status == "已关闭" and not args.all:
            rows.append({
                "name": job["name"], "folder": job["folder"],
                "vault_status": status, "verdict": UNKNOWN,
                "summary": "档案已关闭，默认跳过", "flag": None,
                "notes": job.get("notes"), "sources": [], "skipped": True,
            })
        else:
            rows.append(None)
            to_check.append((len(rows) - 1, job))

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(check_job, job, vault_root): idx
                   for idx, job in to_check}
        for fut in concurrent.futures.as_completed(futures):
            rows[futures[fut]] = fut.result()

    checked_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    if args.json:
        print(json.dumps({"checked_at": checked_at, "jobs": rows},
                         ensure_ascii=False, indent=2))
    else:
        w1 = max(disp_width(r["name"]) for r in rows) + 2
        print(f"\n岗位有效性核查 @ {checked_at}\n")
        for r in rows:
            verdict = ("跳过" if r["skipped"]
                       else f"{VERDICT_EMOJI[r['verdict']]} {r['verdict']}")
            summary = "档案已关闭（--all 可查）" if r["skipped"] else r["summary"]
            print(f"  {pad(r['name'], w1)} {pad(r['vault_status'], 8)} "
                  f"{pad(verdict, 10)} {summary}")
        flags = [r for r in rows if not r["skipped"] and r["flag"]]
        if flags:
            print("\n需要行动：")
            for r in flags:
                print(f"  - {r['name']}：{r['flag']}")

    if not args.no_report:
        REPORT_DIR.mkdir(exist_ok=True)
        report = build_report(rows, checked_at)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
        dated = REPORT_DIR / f"岗位有效性报告_{stamp}.md"
        dated.write_text(report, encoding="utf-8")
        (TOOL_DIR / "最新报告.md").write_text(report, encoding="utf-8")
        if not args.json:
            print(f"\n报告已写入：{dated}\n           （{TOOL_DIR / '最新报告.md'} 为最新一份的副本）")

    return 2 if any(not r["skipped"] and r["flag"] for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
