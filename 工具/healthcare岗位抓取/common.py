#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""healthcare 岗位抓取 —— HTTP 核心与公共工具（纯 Python 3 标准库）。

从 工具/微软岗位抓取/fetch_microsoft_jobs.py 搬来的：UA、429 指数退避、404 哨兵、
线程池 + 抖动、按文件存在续跑。从 工具/岗位有效性检查/check_jobs.py 搬来的：gzip/deflate
解码、(status, text, final_url, err) 返回形。本文件新增：POST JSON、空 200 视为可重试失败、
证书校验失败自动转 /usr/bin/curl 兜底（--curl 强制；--insecure 只允许显式开）。

任何适配器都只通过 fetch() 出网；任何原始响应都通过 save_raw() 落盘且永不删除。
"""
import datetime
import gzip
import json
import os
import random
import re
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import namedtuple

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(TOOL_DIR, "raw")
OUT_DIR = os.path.join(TOOL_DIR, "out")
LOG_DIR = os.path.join(TOOL_DIR, "logs")
MANUAL_DIR = os.path.join(TOOL_DIR, "manual")
COMPANIES_PATH = os.path.join(TOOL_DIR, "companies.json")
for _d in (RAW_DIR, OUT_DIR, LOG_DIR, MANUAL_DIR):
    os.makedirs(_d, exist_ok=True)

# NVIDIA 的 Eightfold 边缘（CloudFront）对 Chrome/140 + Referer 返回 403，Chrome/128 不带 Referer 正常。
UA_CHROME128 = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
UA_CHROME140 = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
UA = UA_CHROME128

JSON_HEADERS = {"Accept": "application/json", "Accept-Language": "en-US,en;q=0.9"}
HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
    "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"macOS"',
}

Resp = namedtuple("Resp", "status text json final_url err")

_lock = threading.Lock()
STATS = {"ok": 0, "429": 0, "err": 0, "empty": 0, "curl": 0, "404": 0}
FLAGS = {"force_curl": False, "insecure": False}
_run_stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
LOG_PATH = os.path.join(LOG_DIR, f"run_{_run_stamp}.log")


def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def log(msg):
    line = f"[{now_iso()}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def jitter(lo=0.0, hi=0.6):
    time.sleep(lo + random.random() * (hi - lo))


def safe_id(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(s))[:150]


# ---------------------------------------------------------------- HTTP

def _decode(body, encoding, charset):
    if encoding in ("gzip", "x-gzip"):
        body = gzip.decompress(body)
    elif encoding == "deflate":
        try:
            body = zlib.decompress(body)
        except zlib.error:
            body = zlib.decompress(body, -zlib.MAX_WBITS)
    elif encoding == "br":
        raise ValueError("brotli 响应无法解码（本工具不发 Accept-Encoding，服务器仍强推 br）")
    return body.decode(charset or "utf-8", errors="replace")


def _curl(url, method, headers, body, timeout):
    """证书校验失败或 --curl 时走系统 curl。返回 (status, text, final_url, err)。"""
    cmd = ["/usr/bin/curl", "-sS", "-L", "--max-time", str(int(timeout)), "-X", method,
           "-o", "-", "-w", "\n__CURL_META__%{http_code}\t%{url_effective}"]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    if body is not None:
        cmd += ["--data-binary", body if isinstance(body, str) else body.decode("utf-8")]
    cmd.append(url)
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
    except Exception as e:  # noqa
        return None, "", url, f"curl: {type(e).__name__}: {e}"
    out = p.stdout.decode("utf-8", "replace")
    if "__CURL_META__" not in out:
        return None, "", url, f"curl exit {p.returncode}: {p.stderr.decode('utf-8', 'replace')[:200]}"
    text, meta = out.rsplit("\n__CURL_META__", 1)
    code, _, final = meta.partition("\t")
    with _lock:
        STATS["curl"] += 1
    try:
        return int(code), text, final.strip() or url, None
    except ValueError:
        return None, text, url, "curl: bad status"


def _once(url, method, headers, body, timeout):
    """单次请求。返回 (status, text, final_url, err, http_error_flag)。"""
    if FLAGS["force_curl"]:
        s, t, f, e = _curl(url, method, headers, body, timeout)
        return s, t, f, e
    data = None
    if body is not None:
        data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    ctx = ssl._create_unverified_context() if FLAGS["insecure"] else None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            raw = r.read()
            text = _decode(raw, r.headers.get("Content-Encoding", ""), r.headers.get_content_charset())
            return r.status, text, r.geturl(), None
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
            text = _decode(raw, e.headers.get("Content-Encoding", "") if e.headers else "",
                           e.headers.get_content_charset() if e.headers else None)
        except Exception:  # noqa
            text = ""
        return e.code, text, getattr(e, "url", url), None
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e))
        if "CERTIFICATE_VERIFY_FAILED" in reason or isinstance(getattr(e, "reason", None), ssl.SSLError):
            log(f"    证书校验失败，转 curl: {url[:100]}")
            return _curl(url, method, headers, body, timeout)
        return None, "", url, f"URLError: {reason}"
    except ssl.SSLError as e:
        log(f"    SSL 错误，转 curl: {url[:100]}")
        return _curl(url, method, headers, body, timeout)
    except Exception as e:  # noqa
        return None, "", url, f"{type(e).__name__}: {e}"


def fetch(url, *, method="GET", body=None, headers=None, as_json=True, tries=6,
          timeout=45, allow_empty=False, ua=None):
    """带退避的请求。

    返回 Resp(status, text, json, final_url, err)：
      - 404 立即返回（status=404, json=None），调用方自己判"不存在"
      - 429 指数退避重试；5xx 线性退避重试
      - 200 但 body 为空（Eightfold 限流的典型表现）视为可重试失败，除非 allow_empty
      - as_json 且解析失败 → 视为可重试失败
      - 用尽 tries → Resp(None, "", None, url, err)
    """
    hdrs = {"User-Agent": ua or UA}
    hdrs.update(headers or {})
    if body is not None and isinstance(body, (dict, list)):
        body = json.dumps(body)
        hdrs.setdefault("Content-Type", "application/json")
    last_err = None
    for i in range(tries):
        status, text, final, err = _once(url, method, hdrs, body, timeout)
        if status == 404:
            with _lock:
                STATS["404"] += 1
            return Resp(404, text, None, final, None)
        if status == 429:
            with _lock:
                STATS["429"] += 1
            time.sleep((2 ** i) * 1.5 + random.random() * 2)
            last_err = "HTTP 429"
            continue
        if status is None:
            last_err = err
            time.sleep(1.5 * (i + 1))
            continue
        if status >= 500 or status in (403, 401, 406):
            last_err = f"HTTP {status}"
            if status in (403, 401, 406):
                # 鉴权/边缘拦截类：重试意义不大，退避两次后放弃
                if i >= 1:
                    return Resp(status, text, None, final, last_err)
            time.sleep(1.5 * (i + 1))
            continue
        if status >= 400:
            return Resp(status, text, None, final, f"HTTP {status}")
        if not text.strip() and not allow_empty:
            with _lock:
                STATS["empty"] += 1
            last_err = "empty body"
            time.sleep((2 ** i) * 1.0 + random.random())
            continue
        if as_json:
            try:
                js = json.loads(text)
            except ValueError:
                last_err = "bad json"
                time.sleep(1.5 * (i + 1))
                continue
            with _lock:
                STATS["ok"] += 1
            return Resp(status, text, js, final, None)
        with _lock:
            STATS["ok"] += 1
        return Resp(status, text, None, final, None)
    with _lock:
        STATS["err"] += 1
    return Resp(None, "", None, url, last_err)


# ---------------------------------------------------------------- raw 落盘

def raw_dir(company_key):
    d = os.path.join(RAW_DIR, safe_id(company_key))
    os.makedirs(d, exist_ok=True)
    return d


def raw_path(company_key, id_):
    return os.path.join(raw_dir(company_key), safe_id(id_) + ".json")


def save_raw(company_key, id_, body, adapter, url, list_item=None):
    rec = {"_adapter": adapter, "_fetched_at": now_iso(), "_url": url,
           "_list_item": list_item, "body": body}
    p = raw_path(company_key, id_)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    return p


def load_raw(company_key, id_):
    p = raw_path(company_key, id_)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_companies(path=None):
    cfg = read_json(path or COMPANIES_PATH)
    if not cfg:
        raise SystemExit(f"找不到 companies.json: {path or COMPANIES_PATH}")
    cfg.setdefault("defaults", {})
    cfg["defaults"].setdefault("keywords", ["healthcare", "medical", "clinical"])
    cfg["defaults"].setdefault("max_total_per_keyword", 400)
    cfg["defaults"].setdefault("workers", 4)
    return cfg


def select_companies(cfg, keys=None, tracks=None, ats=None):
    out = []
    for co in cfg["companies"]:
        if keys and co["key"] not in keys:
            continue
        if tracks and co.get("track") not in tracks:
            continue
        if ats and co.get("ats") not in ats:
            continue
        out.append(co)
    return out
