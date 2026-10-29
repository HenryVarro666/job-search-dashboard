#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standard-library extension adapters: Symphony Talent CWS, Activate,
and Radancy TalentBrew. Each tenant must be configured in the local registry.

CWS caches board descriptions and uses one-based offsets. Activate may return
JSON encoded inside a JSON string. Radancy details combine JobPosting description
and qualifications to preserve restrictions and pay disclosures. A successful
HTTP response alone does not establish complete coverage or an open requisition.
"""
import html
import json
import os
import re
import time
import urllib.parse

from adapters import Adapter, _item, _rec
from common import fetch, jitter, log, raw_dir, read_json, write_json, HTML_HEADERS, JSON_HEADERS

DEFAULT_KEYWORDS = ["machine learning", "data scientist", "artificial intelligence", "AI engineer", "applied scientist",
                    "research scientist", "deep learning", "NLP", "LLM", "computer vision", "software engineer"]

US_STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC", "puerto rico": "PR",
}


# ---------------------------------------------------------------- 小工具

def _kws(ad):
    return ad.keywords() or list(DEFAULT_KEYWORDS)


def _strip(s):
    """HTML 片段 -> 单行纯文本。"""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _state_abbr(s):
    s = (s or "").strip()
    if re.fullmatch(r"[A-Za-z]{2}", s):
        return s.upper()
    return US_STATE_ABBR.get(s.lower(), s)


def _city_state(city, state):
    city = (city or "").strip()
    if city.isupper() and len(city) > 3:
        city = city.title()
    st = _state_abbr(state)
    return ", ".join(x for x in (city, st) if x)


def _ld_jobposting(txt):
    """页面里第一个 @type=JobPosting 的 JSON-LD（容忍 @graph 与 list 外壳）。"""
    for m in re.finditer(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', txt or "", re.S):
        raw = m.group(1).strip()
        try:
            js = json.loads(raw)
        except ValueError:
            try:
                js = json.loads(html.unescape(raw))
            except ValueError:
                continue
        cands = js if isinstance(js, list) else (js.get("@graph") if isinstance(js, dict) and js.get("@graph") else [js])
        for c in cands:
            if isinstance(c, dict) and c.get("@type") == "JobPosting":
                return c
    return None


def _div_inner(txt, open_re):
    """按 div 深度配平，取第一个匹配 open_re 的 <div ...> 的内部 HTML；找不到返回 ""。"""
    m = re.search(open_re, txt or "", re.S)
    if not m:
        return ""
    depth, seg = 1, txt[m.end():]
    for t in re.finditer(r"<(/?)div\b", seg, re.I):
        depth += -1 if t.group(1) else 1
        if depth == 0:
            return seg[:t.start()]
    return seg


def _mdy_to_iso(s):
    """'9/23/2026' 或 '2026-9-17' 或 ISO 时间戳 -> 'YYYY-MM-DD'；解析不了返回 ""。"""
    s = (s or "").strip()
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


_NUM = r"\d[\d,]*(?:\.\d+)?"


def _num(s):
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _pay(lo, hi, basis_hint=""):
    """结构化薪资 {min,max,currency,interval}，交给 parse_jd 的通用 {min,max} 分支。"""
    lo, hi = _num(lo), _num(hi)
    if lo is None and hi is None:
        return None
    b = (basis_hint or "").lower()
    if "hour" in b:
        interval = "hour"
    elif "salar" in b or "year" in b or "annual" in b:
        interval = "year"
    else:
        interval = "hour" if max(x for x in (lo, hi) if x is not None) < 500 else "year"
    return {"min": lo, "max": hi, "currency": "USD", "interval": interval}


_UNIT = r"(?:per\s+(?:hour|year|annum)|/\s*[A-Za-z]+|hourly|annually|salaried)"


def _pay_after(plain, leads):
    """在引导语之后找 "$X [单位] - $Y [单位]"；leads 按优先级尝试。"""
    for lead in leads:
        m = re.search(lead + rf"\s*:?\s*\$*\s*({_NUM})\s*({_UNIT})?\s*(?:-|–|to)\s*\$*\s*({_NUM})\s*({_UNIT})?",
                      plain or "", re.I)
        if m:
            return _pay(m.group(1), m.group(3), f"{m.group(2) or ''} {m.group(4) or ''}")
    return None


# ---------------------------------------------------------------- Symphony Talent CWS（m-cloud）

class MCloudCWSAdapter(Adapter):
    """co["mcloud"] = {"org": "1637", "site": "https://jobs.nyulangone.org",
                       "mode": "boolean" | "phrase" | "fuzzy", "country": "US", "api": 可选}"""
    kind = "mcloud_cws"
    workers = 2
    jitter = (0.0, 0.05)          # 详情来自列表缓存，基本不出网
    PAGE = 100

    def __init__(self, co, cfg):
        super().__init__(co, cfg)
        mc = co.get("mcloud") or {}
        self.org = str(mc.get("org") or "")
        self.api = (mc.get("api") or "https://jobsapi-internal.m-cloud.io/api/job").rstrip("/")
        self.site = (mc.get("site") or "").rstrip("/")
        self.mode = mc.get("mode") or "boolean"
        self.country = mc.get("country", "US")
        self.headers = dict(JSON_HEADERS)
        if self.site:
            self.headers["Referer"] = self.site + "/"
            self.headers["Origin"] = self.site
        self._board = None

    def _params(self, kw, offset, limit):
        p = [("Organization", self.org), ("offset", offset), ("Limit", limit),
             ("sortfield", "open_date"), ("sortorder", "descending")]
        if kw:
            p.append(("SearchText", kw))
            if self.mode in ("boolean", "phrase"):
                p.append(("fuzzy", "false"))
            if self.mode == "boolean":
                p.append(("useBooleanKeywordSearch", "true"))
        if self.country:
            p.append(("facet", f"primary_country:{self.country}"))
        return p

    def _page(self, kw, offset, limit=PAGE):
        r = fetch(f"{self.api}?{urllib.parse.urlencode(self._params(kw, offset, limit))}", headers=self.headers)
        js = r.json
        if not isinstance(js, dict) or not isinstance(js.get("queryResult"), list):
            return None
        return js

    def list_jobs(self, allow_broad=False, **_):
        items, board, totals = {}, {}, {}
        for kw in _kws(self):
            first = self._page(kw, 1)
            if not first:
                log(f"    [{self.key}] m-cloud 首页失败 {kw!r}")
                continue
            total = int(first.get("totalHits") or 0)
            totals[kw] = total
            if total > self.max_total() and not allow_broad:
                log(f"    [{self.key}] 关键词 {kw!r} total={total} 过宽，跳过")
                continue

            def absorb(js):
                for j in js.get("queryResult") or []:
                    jid = str(j.get("id") or "")
                    if not jid:
                        continue
                    board[jid] = j
                    if jid in items:
                        if kw not in items[jid]["list_hits"]:
                            items[jid]["list_hits"].append(kw)
                        continue
                    items[jid] = _item(jid, j.get("ref") or j.get("clientid") or jid, j.get("title"),
                                       _city_state(j.get("primary_city"), j.get("primary_state")),
                                       j.get("open_date") or "", self._public_url(j), detail_ref=jid, list_hits=[kw],
                                       list_meta={"ats_portalid": j.get("ats_portalid"), "category": j.get("primary_category"),
                                                  "department": j.get("department"), "function": j.get("function")})
            absorb(first)
            got, off = len(first.get("queryResult") or []), 1 + self.PAGE
            while off <= total:
                jitter(0.2, 0.6)
                js = self._page(kw, off)
                if not js:
                    log(f"    [{self.key}] m-cloud 翻页失败 offset={off} ({kw!r})")
                    break
                rows = js.get("queryResult") or []
                if not rows:
                    break
                absorb(js)
                got += len(rows)
                off += self.PAGE
            if got < total:
                log(f"    [{self.key}] {kw!r}: 只拿到 {got}/{total}（翻页中断）")
            log(f"    [{self.key}] {kw!r}: total={total} 累计去重 {len(items)}")
        write_json(self.board_cache_path(), {"_fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": self.mode,
                                             "jobs": list(board.values())})
        write_json(os.path.join(raw_dir(self.key), "_totals.json"), totals)
        self._board = board
        log(f"    [{self.key}] m-cloud 合计去重 {len(items)}（mode={self.mode}, country={self.country}）")
        return list(items.values())

    def _public_url(self, j):
        u = j.get("url") or ""
        if u.startswith("http://"):
            u = "https://" + u[len("http://"):]
        if not u and self.site:
            u = f"{self.site}/job/{j.get('id')}/"
        return u

    def _load_board(self):
        if self._board is None:
            b = read_json(self.board_cache_path(), {}) or {}
            self._board = {str(j.get("id")): j for j in b.get("jobs", [])}
        return self._board

    def fetch_detail(self, item):
        j = self._load_board().get(item["id"])
        if j and j.get("description"):
            return j
        p = [("Organization", self.org), ("facet", f"id:{item['id']}")]
        r = fetch(f"{self.api}?{urllib.parse.urlencode(p)}", headers=self.headers)
        rows = (r.json or {}).get("queryResult") or [] if isinstance(r.json, dict) else []
        return rows[0] if rows and rows[0].get("description") else None

    def normalize(self, item, raw):
        j = raw or {}
        locs = [_city_state(j.get("primary_city"), j.get("primary_state"))]
        for a in j.get("addtnl_locations") or []:
            if isinstance(a, dict):
                locs.append(_city_state(a.get("city"), a.get("state")))
            elif a:
                locs.append(str(a))
        locs = [l for l in dict.fromkeys(locs) if l] or [item.get("location_raw") or ""]
        # 薪资：JD 正文里的纽约州薪资透明句优先（页面实际展示、法定披露）；API salary 字段兜底。
        # 正文与结构化字段可能冲突；保留两者及字段来源供核验。
        text_pay = _pay_after(_strip(j.get("description")), [r"salary range for the role is", r"contractual rate for the role is"])
        field_pay = None
        m = re.search(rf"\$?\s*({_NUM})\s*-\s*\$?\s*({_NUM})\s*(?:/\s*(\w+))?", j.get("salary") or "")
        if m:
            field_pay = _pay(m.group(1), m.group(2), m.group(3) or "")
        pay = text_pay or field_pay
        conflict = bool(text_pay and field_pay and (round(text_pay["min"]), round(text_pay["max"])) !=
                        (round(field_pay["min"]), round(field_pay["max"])))
        apply_url = j.get("seo_url") or j.get("is_internal") or ""
        return _rec(req_id=str(j.get("ref") or j.get("clientid") or item["req_id"]), job_id=str(j.get("id") or item["id"]),
                    title=j.get("title") or item["title"], org_unit=j.get("department") or j.get("brand") or "",
                    department="; ".join(x for x in [j.get("primary_category")] + list(j.get("addtnl_categories") or []) if x),
                    locations=locs, location_raw="; ".join(locs), posted=(j.get("open_date") or "")[:10],
                    posted_raw=j.get("open_date") or "", time_type=j.get("employment_type") or "",
                    can_apply=(j.get("entity_status") == "Open") if j.get("entity_status") else None,
                    url=self._public_url(j) or item["url"],
                    api_url=f"{self.api}?Organization={self.org}&facet=id:{j.get('id') or item['id']}",
                    jd_html=j.get("description") or "", structured_pay=pay,
                    workplace_type=j.get("location_type") or "",
                    countries_hint=[j["primary_country"]] if j.get("primary_country") else [],
                    extra={"applyUrl": apply_url, "ats_portalid": j.get("ats_portalid"), "salary_raw": j.get("salary"),
                           "salary_field_conflict": conflict,
                           "function": j.get("function"), "brand": j.get("brand"), "schedule": j.get("schedule"),
                           "update_date": j.get("update_date"), "company_name": j.get("company_name")})


# ---------------------------------------------------------------- Activate（MD Anderson）

class ActivateAdapter(Adapter):
    """co["activate"] = {"host": "jobs.mdanderson.org", "phrase": true}"""
    kind = "activate"
    workers = 2
    jitter = (0.3, 0.9)
    PAGE = 100

    def __init__(self, co, cfg):
        super().__init__(co, cfg)
        a = co.get("activate") or {}
        self.host = a["host"]
        self.base = f"https://{self.host}"
        self.phrase = a.get("phrase", True)
        self.headers = dict(JSON_HEADERS)
        self.headers["X-Requested-With"] = "XMLHttpRequest"
        self.headers["Referer"] = f"{self.base}/search/searchjobs"

    def _page(self, kw, start, size=PAGE):
        q = {"jtStartIndex": start, "jtPageSize": size}
        if kw:
            q["Keyword"] = f'"{kw}"' if self.phrase and not kw.startswith('"') else kw
        r = fetch(f"{self.base}/Search/SearchResults?{urllib.parse.urlencode(q)}", headers=self.headers)
        js = r.json
        if isinstance(js, str):             # 二次编码
            try:
                js = json.loads(js)
            except ValueError:
                return None
        if not isinstance(js, dict) or js.get("Result") != "OK":
            return None
        return js

    @staticmethod
    def _slug(title):
        return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (title or "").lower())).strip("-") or "job"

    def list_jobs(self, allow_broad=False, **_):
        items, totals = {}, {}
        for kw in _kws(self):
            first = self._page(kw, 0)
            if not first:
                log(f"    [{self.key}] Activate 首页失败 {kw!r}")
                continue
            total = int(first.get("TotalRecordCount") or 0)
            totals[kw] = total
            if total > self.max_total() and not allow_broad:
                log(f"    [{self.key}] 关键词 {kw!r} total={total} 过宽，跳过")
                continue

            def absorb(js):
                for rec in js.get("Records") or []:
                    gid = str(rec.get("ID") or "")
                    if not gid:
                        continue
                    if gid in items:
                        if kw not in items[gid]["list_hits"]:
                            items[gid]["list_hits"].append(kw)
                        continue
                    tr = rec.get("TrackingObject") or {}
                    title = tr.get("TitleJson") or _strip(rec.get("Title"))
                    locs = [l for l in (tr.get("CityStatesDataAbbrevJson") or []) if l] or [_strip(rec.get("CityStateDataAbbrev"))]
                    items[gid] = _item(gid, tr.get("ReferenceNumberJson") or _strip(rec.get("ReferenceNumber")) or gid, title,
                                       "; ".join(locs), rec.get("PostedDateRaw") or _strip(rec.get("PostedDate")),
                                       f"{self.base}/search/jobdetails/{self._slug(title)}/{gid}", detail_ref=gid,
                                       list_hits=[kw],
                                       list_meta={"locations": locs, "countries": tr.get("CountryNamesJson") or [_strip(rec.get("CountryName"))],
                                                  "department": _strip(rec.get("DepartmentName")), "schedule": _strip(rec.get("ScheduleName")),
                                                  "type": _strip(rec.get("TypeName")), "is_remote": rec.get("IsRemote"),
                                                  "category": tr.get("AtsCategoryNamesJson")})
            absorb(first)
            got, start = len(first.get("Records") or []), self.PAGE
            while start < total:
                jitter(*self.jitter)
                js = self._page(kw, start)
                if not js or not js.get("Records"):
                    log(f"    [{self.key}] Activate 翻页失败 start={start} ({kw!r})")
                    break
                absorb(js)
                got += len(js["Records"])
                start += self.PAGE
            if got < total:
                log(f"    [{self.key}] {kw!r}: 只拿到 {got}/{total}（翻页中断）")
            log(f"    [{self.key}] {kw!r}: total={total} 累计去重 {len(items)}")
        write_json(os.path.join(raw_dir(self.key), "_totals.json"), totals)
        log(f"    [{self.key}] Activate 合计去重 {len(items)}（phrase={self.phrase}）")
        return list(items.values())

    def fetch_detail(self, item):
        r = fetch(item["url"], headers=HTML_HEADERS, as_json=False)
        if r.status != 200 or "JobPosting" not in (r.text or ""):
            return None
        return r.text

    def normalize(self, item, raw):
        txt = raw or ""
        ld = _ld_jobposting(txt) or {}
        meta = item.get("list_meta") or {}
        # 地点：优先列表里的 "City, ST"（支持多地点），其次 JSON-LD
        locs = [l for l in (meta.get("locations") or []) if l]
        if not locs:
            jl = ld.get("jobLocation") or []
            for l in (jl if isinstance(jl, list) else [jl]):
                a = (l or {}).get("address") or {}
                locs.append(_city_state(a.get("addressLocality"), a.get("addressRegion")))
        locs = [l for l in dict.fromkeys(locs) if l]
        desc = _div_inner(txt, r'<div class="Description"[^>]*>') or ld.get("description") or ""
        plain = _strip(desc)
        pay = None
        m = re.search(rf"Min:\s*[^;]*?({_NUM})\s*;\s*Max:\s*[^;]*?({_NUM})", str(ld.get("baseSalary") or ""))
        if m:
            pay = _pay(m.group(1), m.group(2))
        else:
            lo = re.search(rf"Minimum Salary:[^0-9]*({_NUM})", plain)
            hi = re.search(rf"Maximum Salary\s*:[^0-9]*({_NUM})", plain)
            if lo or hi:
                pay = _pay(lo.group(1) if lo else None, hi.group(1) if hi else None)
        wl = re.search(r"Work Location:\s*([^:]+?)\s+(?:Pivotal Position|Referral Bonus|Relocation|$)", plain)
        # 申请按钮：staff 岗走 Taleo（mdanderson.taleo.net/.../jobapply.ftl?job=N），博后/学员岗走 trainees.mdanderson.org/register
        apply_m = (re.search(r'<a class="Action Apply[^"]*"\s+href="([^"]+)"', txt)
                   or re.search(r'href="(https://[^"]*taleo\.net/careersection/[^"]*jobapply\.ftl\?job=\d+)"', txt))
        canon = re.search(r'<meta property="og:url" content="([^"]+)"', txt)
        req = re.search(r"Requisition ID:\s*(\d+)", plain)
        unit = (ld.get("employmentUnit") or {}).get("name") if isinstance(ld.get("employmentUnit"), dict) else ""
        return _rec(req_id=str(item.get("req_id") or (req.group(1) if req else item["id"])), job_id=item["id"],
                    title=html.unescape(ld.get("title") or item["title"]), org_unit=unit or meta.get("department") or "",
                    department=meta.get("department") or unit or "", locations=locs or [item.get("location_raw") or ""],
                    location_raw=item.get("location_raw") or "",
                    posted=_mdy_to_iso(item.get("posted_raw")) or _mdy_to_iso(ld.get("datePosted")),
                    posted_raw=ld.get("datePosted") or item.get("posted_raw") or "",
                    time_type=ld.get("employmentType") or meta.get("type") or "",
                    url=html.unescape(canon.group(1)) if canon else item["url"],
                    api_url=f"{self.base}/Search/SearchResults?Keyword={urllib.parse.quote(str(item.get('req_id') or ''))}&jtStartIndex=0&jtPageSize=10",
                    jd_html=desc, structured_pay=pay,
                    workplace_type=(wl.group(1).strip() if wl else (ld.get("workHours") or meta.get("schedule") or "")),
                    is_remote=meta.get("is_remote"),
                    countries_hint=["US"] if "United States" in (meta.get("countries") or []) else [],
                    extra={"applyUrl": html.unescape(apply_m.group(1)) if apply_m else "",
                           "baseSalary_raw": ld.get("baseSalary"), "occupationalCategory": ld.get("occupationalCategory"),
                           "workHours": ld.get("workHours"), "category": meta.get("category")})


# ---------------------------------------------------------------- Radancy TalentBrew（Walgreens）

class RadancyAdapter(Adapter):
    """co["radancy"] = {"host": "jobs.walgreens.com", "lang": "en",
                        "country_facet": {"id": "6252001", "type": 2, "display": "United States"}}（null 关闭国家过滤）"""
    kind = "radancy"
    workers = 3
    jitter = (0.3, 0.9)
    PAGE = 100

    def __init__(self, co, cfg):
        super().__init__(co, cfg)
        rd = co.get("radancy") or {}
        self.host = rd["host"]
        self.lang = rd.get("lang", "en")
        self.base = f"https://{self.host}"
        self.country = rd.get("country_facet", {"id": "6252001", "type": 2, "display": "United States"})
        self.headers = {"Accept": "application/json, text/javascript, */*; q=0.01", "Accept-Language": "en-US,en;q=0.9",
                        "X-Requested-With": "XMLHttpRequest", "Referer": f"{self.base}/{self.lang}/search-jobs"}

    def _page(self, kw, page, per=PAGE):
        p = [("ActiveFacetID", 0), ("CurrentPage", page), ("RecordsPerPage", per), ("Distance", 50),
             ("RadiusUnitType", 0), ("Keywords", kw), ("Location", ""), ("ShowRadius", "False"),
             ("IsPagination", "True" if page > 1 else "False"), ("FacetType", 0),
             ("SearchResultsModuleName", "Search Results"), ("SearchFiltersModuleName", "Search Filters"),
             ("SortCriteria", 0), ("SortDirection", 0), ("SearchType", 5)]
        if self.country:
            c = self.country
            p += [("FacetFilters[0].ID", c["id"]), ("FacetFilters[0].FacetType", c.get("type", 2)),
                  ("FacetFilters[0].Count", 0), ("FacetFilters[0].Display", c.get("display", "")),
                  ("FacetFilters[0].IsApplied", "true"), ("FacetFilters[0].FieldName", "")]
        r = fetch(f"{self.base}/{self.lang}/search-jobs/results?{urllib.parse.urlencode(p)}", headers=self.headers)
        js = r.json
        if not isinstance(js, dict) or "results" not in js:
            return None
        res = js.get("results") or ""
        tot = re.search(r'data-total-results="(\d+)"', res)
        pages = re.search(r'data-total-pages="(\d+)"', res)
        jobs = []
        for m in re.finditer(r'<a\s+href="([^"]*/job/[^"]+)"\s+data-job-id="(\d+)"[^>]*>(.*?)</a>', res, re.S):
            inner = m.group(3)
            t = re.search(r"<h2[^>]*>(.*?)</h2>", inner, re.S)
            loc = re.search(r'<span class="job-location"[^>]*>(.*?)</span>', inner, re.S)
            dt = re.search(r'<span class="job-date-posted"[^>]*>(.*?)</span>', inner, re.S)
            jobs.append({"href": html.unescape(m.group(1)), "id": m.group(2),
                         "title": _strip(t.group(1)) if t else _strip(inner),
                         "location": _strip(loc.group(1)) if loc else "", "posted": _strip(dt.group(1)) if dt else ""})
        return {"total": int(tot.group(1)) if tot else None, "pages": int(pages.group(1)) if pages else None, "jobs": jobs}

    def list_jobs(self, allow_broad=False, **_):
        items, totals = {}, {}
        for kw in _kws(self):
            first = self._page(kw, 1)
            if not first or first["total"] is None:
                log(f"    [{self.key}] Radancy 首页失败 {kw!r}")
                continue
            total, pages = first["total"], first["pages"] or 1
            totals[kw] = total
            if total > self.max_total() and not allow_broad:
                log(f"    [{self.key}] 关键词 {kw!r} total={total} 过宽，跳过")
                continue

            def absorb(pg):
                for j in pg["jobs"]:
                    jid = j["id"]
                    if jid in items:
                        if kw not in items[jid]["list_hits"]:
                            items[jid]["list_hits"].append(kw)
                        continue
                    url = j["href"] if j["href"].startswith("http") else self.base + j["href"]
                    items[jid] = _item(jid, jid, j["title"], j["location"], j["posted"], url, detail_ref=url,
                                       list_hits=[kw], list_meta={"list_location": j["location"]})
            absorb(first)
            got = len(first["jobs"])
            for page in range(2, pages + 1):
                jitter(*self.jitter)
                pg = self._page(kw, page)
                if not pg or not pg["jobs"]:
                    log(f"    [{self.key}] Radancy 翻页失败 page={page} ({kw!r})")
                    break
                absorb(pg)
                got += len(pg["jobs"])
            if got < total:
                log(f"    [{self.key}] {kw!r}: 只拿到 {got}/{total}（翻页中断）")
            log(f"    [{self.key}] {kw!r}: total={total} pages={pages} 累计去重 {len(items)}")
        write_json(os.path.join(raw_dir(self.key), "_totals.json"), totals)
        log(f"    [{self.key}] Radancy 合计去重 {len(items)}（country={(self.country or {}).get('display')}）")
        return list(items.values())

    def fetch_detail(self, item):
        r = fetch(item.get("detail_ref") or item["url"], headers=HTML_HEADERS, as_json=False)
        if r.status != 200 or "JobPosting" not in (r.text or ""):
            return None
        return r.text

    def normalize(self, item, raw):
        txt = raw or ""
        ld = _ld_jobposting(txt) or {}
        desc = ld.get("description") or ""
        quals = ld.get("qualifications") or ""
        if quals and _strip(quals)[:200] not in _strip(desc):
            desc = desc + "<h3>Qualifications</h3>" + quals
        if not desc:
            desc = _div_inner(txt, r'<div class="ats-description[^"]*"[^>]*>')
        locs = []
        jl = ld.get("jobLocation") or []
        for l in (jl if isinstance(jl, list) else [jl]):
            a = (l or {}).get("address") or {}
            locs.append(_city_state(a.get("addressLocality"), a.get("addressRegion")))
        locs = [l for l in dict.fromkeys(locs) if l]
        if not locs and item.get("location_raw"):
            p = [x.strip() for x in item["location_raw"].split(",")]
            locs = [_city_state(p[0], p[1]) if len(p) >= 2 else item["location_raw"]]
        plain = _strip(desc)
        # 支持的两种写法："Salary Range: $100000 - $$175,000.00 / Salaried"（企业岗）与
        # "The current salary range for this position is $24.04 per hour - $43.27 per hour."（门店/药房岗）
        pay = _pay_after(plain, [r"Salary Range", r"salary range for this position is"])
        apply_m = re.search(r'<meta name="search-job-apply-url" content="([^"]*)"', txt)
        apply_url = html.unescape(apply_m.group(1)) if apply_m else ""
        br = re.search(r"[?&]jobid=(\d+)", apply_url)
        wa = re.search(r"Work Arrangement:\s*(.+?)(?:\s+Work Visa Sponsorship:|\s+Job Responsibilities|\s+Job Summary|$)", plain)
        in_us = bool(self.country and str(self.country.get("id")) == "6252001")
        identifier = ld.get("identifier")
        if isinstance(identifier, dict):
            identifier = identifier.get("value")
        return _rec(req_id=str(identifier or item["req_id"]), job_id=item["id"],
                    title=html.unescape(ld.get("title") or item["title"]), org_unit=ld.get("workHours") or "",
                    department=ld.get("workHours") or "", locations=locs, location_raw=item.get("location_raw") or "; ".join(locs),
                    posted=_mdy_to_iso(ld.get("datePosted")), posted_raw=ld.get("datePosted") or "",
                    time_type=ld.get("employmentType") or "", url=ld.get("url") or item["url"], api_url=item["url"],
                    jd_html=desc, structured_pay=pay, workplace_type=wa.group(1).strip()[:80] if wa else "",
                    countries_hint=["US"] if in_us else [],
                    extra={"applyUrl": apply_url, "brassring_jobid": br.group(1) if br else "",
                           "directApply": ld.get("directApply"), "list_location": item.get("location_raw")})


EXT_ADAPTERS = {
    MCloudCWSAdapter.kind: MCloudCWSAdapter,
    ActivateAdapter.kind: ActivateAdapter,
    RadancyAdapter.kind: RadancyAdapter,
}
