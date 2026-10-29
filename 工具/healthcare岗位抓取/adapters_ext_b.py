#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standard-library extension adapters: TalentBrew, Taleo and Avature.

Registry settings:
    talentbrew: {host, company_id, country_facet_id?, records_per_page?}
    taleo: {host, careersection, portal, default_state?,
            mcloud?: {company, exclude_portals}}
    avature: {base, page_size?, max_pages?}
TalentBrew search uses keyword OR matching. Taleo fields vary by tenant.
Avature enumerates pages without keyword filtering. No registry is bundled.
"""
import functools
import html
import json
import os
import re
import time
import urllib.parse

from adapters import Adapter, _item, _rec
from common import fetch, jitter, log, raw_dir, read_json, write_json, HTML_HEADERS, JSON_HEADERS

DEFAULT_KEYWORDS = ["machine learning", "data scientist", "artificial intelligence", "AI engineer",
                    "applied scientist", "research scientist", "deep learning", "NLP", "LLM",
                    "computer vision", "software engineer"]

US_STATE_CODES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "puerto rico": "PR",
}
_US_CODES = set(US_STATE_CODES.values())
_US_COUNTRY = {"us", "usa", "united states", "united states of america"}

_LDJSON_RE = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _kw_list(ad):
    kws = ad.co.get("keywords") or DEFAULT_KEYWORDS
    black = {k.lower() for k in ad.co.get("keyword_blacklist", [])}
    return [k for k in kws if k.lower() not in black]


def _plain(s):
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", s or ""))).strip()


def _state_code(s):
    s = (s or "").strip()
    if s.upper() in _US_CODES and len(s) == 2:
        return s.upper()
    return US_STATE_CODES.get(s.lower())


@functools.lru_cache(maxsize=4096)
def _us_city_st(city, st):
    """'Dallas, TX'；若 parse_jd 的海外城市表会把它判成非美国（'Vancouver, WA' -> Canada），补 ', US' 标记。"""
    s = f"{city}, {st}"
    try:
        import parse_jd
        if parse_jd.normalize_location([s], {}).get("region") != "美国":
            s += ", US"
    except Exception:  # noqa  parse_jd 不可用时保持原样
        pass
    return s


def _fmt_loc(city="", region="", country=""):
    """地址三元组 -> 'Dallas, TX' / 'WA, US' / 'United States' / 'Gurgaon, Haryana, India'。"""
    city, region, country = (city or "").strip(), (region or "").strip(), (country or "").strip()
    is_us = country.lower() in _US_COUNTRY or (not country and _state_code(region))
    if is_us:
        st = _state_code(region)
        if city and st:
            return _us_city_st(city, st)
        if st:
            return f"{st}, US"
        if city:
            return f"{city}, US"
        return "United States"
    return ", ".join(x for x in (city, region, country) if x)


def _iso_date(s):
    """'2026-6-18' / '06/18/2026' / 'Sep 9, 2026, 10:13 AM' / '2026-05-08T18:13:07' -> 'YYYY-MM-DD'。"""
    s = (s or "").strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.match(r"^([A-Z][a-z]{2})[a-z]*\.? (\d{1,2}), (\d{4})", s)
    if m:
        try:
            return time.strftime("%Y-%m-%d", time.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y"))
        except ValueError:
            return ""
    return ""


def _pay_from_text(s):
    """'$166100 - $214940 / year' / '$45.10 - $60.20 / hour' -> {min, max, currency, interval}。"""
    m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)\s*(?:-|–|to)\s*\$\s*([\d,]+(?:\.\d+)?)\s*(?:/|per)?\s*(year|yr|annual|hour|hr|month|week)?",
                  s or "", re.I)
    if not m:
        return None
    lo, hi = float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
    unit = (m.group(3) or "year").lower()
    unit = {"yr": "year", "annual": "year", "hr": "hour"}.get(unit, unit)
    return {"min": int(round(lo)), "max": int(round(hi)), "currency": "USD", "interval": unit, "raw": m.group(0)}


# ================================================================ Radancy / TalentBrew

class TalentBrewAdapter(Adapter):
    kind = "talentbrew"
    workers = 3
    jitter = (0.3, 0.9)

    def __init__(self, co, cfg):
        super().__init__(co, cfg)
        tb = co["talentbrew"]
        self.host = tb["host"]
        self.company_id = str(tb["company_id"])
        self.country_facet = str(tb.get("country_facet_id", "6252001"))   # GeoNames United States
        self.rpp = int(tb.get("records_per_page", 100))
        self.base = f"https://{self.host}"
        self.headers = dict(JSON_HEADERS)
        self.headers["Referer"] = f"{self.base}/search-jobs"
        self.headers["X-Requested-With"] = "XMLHttpRequest"

    def keywords(self):
        return _kw_list(self)

    def _search_url(self, kw, page):
        q = [("ActiveFacetID", "0"), ("CurrentPage", str(page)), ("RecordsPerPage", str(self.rpp)),
             ("Distance", "50"), ("RadiusUnitType", "0"), ("Keywords", kw), ("Location", ""),
             ("ShowRadius", "False"), ("IsPagination", "False" if page == 1 else "True"), ("CustomFacetName", ""),
             ("FacetTerm", ""), ("FacetType", "0")]
        if self.country_facet:
            q += [("FacetFilters[0].ID", self.country_facet), ("FacetFilters[0].FacetType", "2"),
                  ("FacetFilters[0].Count", "0"), ("FacetFilters[0].Display", "United States"),
                  ("FacetFilters[0].IsApplied", "true"), ("FacetFilters[0].FieldName", "")]
        q += [("SearchResultsModuleName", "Search Results"), ("SearchFiltersModuleName", "Search Filters"),
              ("SortCriteria", "0"), ("SortDirection", "0"), ("SearchType", "5")]
        return f"{self.base}/search-jobs/results?{urllib.parse.urlencode(q)}"

    def _page(self, kw, page):
        r = fetch(self._search_url(kw, page), headers=self.headers)
        js = r.json
        if not isinstance(js, dict) or "results" not in js:
            return None
        res = js.get("results") or ""
        tot = re.search(r'data-total-results="(\d+)"', res)
        pages = re.search(r'data-total-pages="(\d+)"', res)
        rows = []
        for m in re.finditer(r'<a\b([^>]*\bhref="(/job/[^"]+)"[^>]*)>(.*?)</a>', res, re.S):
            attrs, href, inner = m.group(1), m.group(2), m.group(3)
            jid = re.search(r'data-job-id="(\d+)"', attrs)
            jid = jid.group(1) if jid else re.sub(r".*/(\d+)/?$", r"\1", href)
            t = re.search(r"<h2[^>]*>(.*?)</h2>", inner, re.S)
            req = re.search(r'<span[^>]*class="[^"]*\bjob-id\b[^"]*"[^>]*>(.*?)</span>', inner, re.S)
            loc = re.search(r'<span[^>]*class="[^"]*\bjob-location\b[^"]*"[^>]*>(.*?)</span>', inner, re.S)
            dt = re.search(r'<span[^>]*class="[^"]*\bjob-date\b[^"]*"[^>]*>(.*?)</span>', inner, re.S)
            rows.append({"id": jid, "href": href, "title": _plain(t.group(1)) if t else "",
                         "req": _plain(req.group(1)) if req else "", "loc": _plain(loc.group(1)) if loc else "",
                         "date": _plain(dt.group(1)) if dt else ""})
        return {"total": int(tot.group(1)) if tot else None, "pages": int(pages.group(1)) if pages else None,
                "rows": rows}

    def list_jobs(self, allow_broad=False, **_):
        items, totals = {}, {}
        for kw in self.keywords():
            first = self._page(kw, 1)
            if not first:
                log(f"    [{self.key}] TalentBrew 首页失败 {kw!r}")
                totals[kw] = None
                continue
            total = first["total"] or 0
            totals[kw] = total
            if total > self.max_total() and not allow_broad:
                log(f"    [{self.key}] 关键词 {kw!r} total={total} 超过 max_total_per_keyword={self.max_total()}，跳过")
                continue
            pages = first["pages"] or max(1, -(-total // self.rpp))

            def absorb(rows):
                for r in rows:
                    if r["id"] in items:
                        if kw not in items[r["id"]]["list_hits"]:
                            items[r["id"]]["list_hits"].append(kw)
                        continue
                    items[r["id"]] = _item(r["id"], r["req"] or r["id"], r["title"], r["loc"], r["date"],
                                           self.base + r["href"], detail_ref=r["href"], list_hits=[kw],
                                           list_meta={"talentbrew_id": r["id"], "company_id": self.company_id})
            absorb(first["rows"])
            got = len(first["rows"])
            for p in range(2, pages + 1):
                jitter(*self.jitter)
                d = self._page(kw, p)
                if not d:
                    log(f"    [{self.key}] TalentBrew 翻页失败 {kw!r} page={p}")
                    break
                if not d["rows"]:
                    break
                absorb(d["rows"])
                got += len(d["rows"])
            log(f"    [{self.key}] {kw!r}: total={total} 翻到 {got} 行 / {pages} 页，累计去重 {len(items)}")
        write_json(os.path.join(raw_dir(self.key), "_totals.json"), totals)
        # TalentBrew 关键词是 OR 匹配，宽词（'data scientist' = data OR scientist）会带进大量无关岗。按命中关键词里
        # total 最小者升序排（越窄越靠前，同档内保留站点相关度顺序），这样 jd --max-details N 截断时先抓最相关的。
        order = {id_: n for n, id_ in enumerate(items)}
        def spec(it):
            ts = [totals.get(k) for k in it["list_hits"] if totals.get(k)]
            return (min(ts) if ts else 10 ** 9, order[it["id"]])
        return sorted(items.values(), key=spec)

    def fetch_detail(self, item):
        url = self.base + (item.get("detail_ref") or "")
        r = fetch(url, headers=HTML_HEADERS, as_json=False)
        if r.status != 200 or not r.text:
            return None
        txt = r.text
        ld = None
        for m in _LDJSON_RE.finditer(txt):
            try:
                js = json.loads(m.group(1), strict=False)
            except ValueError:
                continue
            for c in (js if isinstance(js, list) else [js]):
                if isinstance(c, dict) and c.get("@type") == "JobPosting":
                    ld = c
                    break
            if ld:
                break
        info = {}
        for m in re.finditer(r'<span[^>]*class="[^"]*\bjob-(?:info|id|date|location)\b[^"]*"[^>]*>\s*<(b|strong)>(.*?)</\1>(.*?)</span>',
                             txt, re.S):
            k, v = _plain(m.group(2)).rstrip(":").strip(), _plain(m.group(3))
            if k and v and k not in info:
                info[k] = v
        add_locs = []
        m = re.search(r'class="[^"]*additional-locations[^"]*"[^>]*>(.*?)</div>', txt, re.S)
        if m:
            add_locs = [_plain(x) for x in re.findall(r"<li[^>]*>(.*?)</li>", m.group(1), re.S) if _plain(x)]
        apply_url = re.search(r'data-apply-url="([^"]+)"', txt)
        desc_html = ""
        if not ld or not ld.get("description"):
            m = re.search(r'<div class="ats-description[^"]*">(.*?)</section>', txt, re.S)
            desc_html = m.group(1) if m else ""
        if not ld and not desc_html:
            log(f"    [{self.key}] {item['id']} 详情页无 JSON-LD 也无正文（可能已下架）final={r.final_url}")
            return None
        return {"ld": ld, "info": info, "additional_locations": add_locs,
                "apply_url": html.unescape(apply_url.group(1)) if apply_url else "",
                "desc_html": desc_html, "final_url": r.final_url, "page_bytes": len(txt)}

    def normalize(self, item, raw):
        raw = raw or {}
        ld = raw.get("ld") or {}
        info = raw.get("info") or {}
        locs, countries = [], []
        jl = ld.get("jobLocation") or []
        for l in (jl if isinstance(jl, list) else [jl]):
            a = (l or {}).get("address") or {}
            s = _fmt_loc(a.get("addressLocality"), a.get("addressRegion"), a.get("addressCountry"))
            if s and s not in locs:
                locs.append(s)
            c = (a.get("addressCountry") or "").strip()
            c = "US" if c.lower() in _US_COUNTRY else c
            if c and c not in countries:
                countries.append(c)
        if not locs:
            for s in raw.get("additional_locations") or []:
                if s not in locs:
                    locs.append(s)
        if not locs and item.get("location_raw"):
            locs = [item["location_raw"]]
        parts = []
        if info:
            parts.append("<h3>Job Details</h3><ul>" + "".join(
                f"<li>{html.escape(k)}: {html.escape(v)}</li>" for k, v in info.items()) + "</ul>")
        if ld.get("description"):
            parts.append(ld["description"])
        elif raw.get("desc_html"):
            parts.append(raw["desc_html"])
        if ld.get("qualifications"):
            parts.append("<h3>Qualifications</h3>" + ld["qualifications"])
        if ld.get("responsibilities"):
            parts.append("<h3>Responsibilities</h3>" + ld["responsibilities"])
        pay_src = info.get("Pay Range") or info.get("Salary") or info.get("Salary Range") or ""
        pay = _pay_from_text(pay_src)
        if not pay:
            bs = ld.get("baseSalary") or {}
            v = (bs.get("value") or {}) if isinstance(bs, dict) else {}
            if v.get("minValue") or v.get("maxValue"):
                pay = bs
        posted_raw = ld.get("datePosted") or info.get("Date posted") or info.get("Date Posted") or item.get("posted_raw", "")
        identifier = ld.get("identifier")
        if isinstance(identifier, dict):
            identifier = identifier.get("value")
        req = str(identifier or info.get("Requisition number") or info.get("Job Number") or item["req_id"])
        dept = info.get("Department") or info.get("Job category") or ""
        return _rec(req_id=req, job_id=item["id"], title=_plain(ld.get("title")) or item["title"],
                    org_unit=(ld.get("hiringOrganization") or {}).get("name", "") or self.co.get("name", ""),
                    department=dept, locations=locs, location_raw=item.get("location_raw", ""),
                    posted=_iso_date(posted_raw), posted_raw=posted_raw,
                    time_type=info.get("Job Schedule") or info.get("Schedule") or "",
                    url=ld.get("url") or item["url"], api_url=raw.get("final_url") or item["url"],
                    jd_html="".join(parts), structured_pay=pay, work_site_hint=info.get("Worker Location") or "",
                    countries_hint=countries,
                    extra={"applyUrl": raw.get("apply_url") or "", "talentbrew_id": item["id"],
                           "company_id": self.company_id, "info": info,
                           "additional_locations": raw.get("additional_locations") or [],
                           "employmentType": ld.get("employmentType"), "industry": ld.get("industry")})


# ================================================================ Oracle Taleo（+ 可选 m-cloud 补充源）

class TaleoAdapter(Adapter):
    kind = "taleo"
    workers = 2
    jitter = (0.5, 1.2)
    PAGE = 25

    def __init__(self, co, cfg):
        super().__init__(co, cfg)
        t = co["taleo"]
        self.host, self.cs, self.portal = t["host"], str(t["careersection"]), str(t["portal"])
        self.default_state = t.get("default_state") or ""
        self.mcloud = t.get("mcloud") or None
        self.base = f"https://{self.host}"
        self.search_url = f"{self.base}/careersection/rest/jobboard/searchjobs?lang=en&portal={self.portal}"
        self.post_headers = {"Accept": "application/json, text/javascript, */*; q=0.01",
                             "Accept-Language": "en-US,en;q=0.9", "Content-Type": "application/json",
                             "X-Requested-With": "XMLHttpRequest", "tz": "GMT-05:00", "Origin": self.base,
                             "Referer": f"{self.base}/careersection/{self.cs}/jobsearch.ftl?lang=en"}

    def keywords(self):
        return _kw_list(self)

    def job_url(self, contest):
        return f"{self.base}/careersection/{self.cs}/jobdetail.ftl?job={urllib.parse.quote(str(contest))}&lang=en"

    def apply_url(self, contest):
        return f"{self.base}/careersection/{self.cs}/jobapply.ftl?job={urllib.parse.quote(str(contest))}&lang=en"

    # ---- Taleo REST
    def _search(self, kw, page):
        body = {"multilineEnabled": False,
                "sortingSelection": {"sortBySelectionParam": "3", "ascendingSortingOrder": "false"},
                "fieldData": {"fields": {"KEYWORD": kw, "LOCATION": ""}, "valid": True},
                "filterSelectionParam": {"searchFilterSelections": []},
                "advancedSearchFiltersSelectionParam": {"searchFilterSelections": []},
                "pageNo": page}
        r = fetch(self.search_url, method="POST", body=body, headers=self.post_headers)
        js = r.json
        if not isinstance(js, dict) or "requisitionList" not in js:
            return None
        return js

    def _absorb_taleo(self, items, js, kw):
        n = 0
        for req in js.get("requisitionList") or []:
            contest = str(req.get("contestNo") or req.get("jobId"))
            cols = req.get("column") or []
            lc = req.get("linkedColumn") or 0
            title = cols[lc] if len(cols) > lc else ""
            locs = []
            for i in req.get("locationsColumns") or []:
                if len(cols) > i and cols[i]:
                    try:
                        v = json.loads(cols[i])
                        locs += v if isinstance(v, list) else [str(v)]
                    except ValueError:
                        locs.append(cols[i])
            date = next((c for c in cols if isinstance(c, str) and _iso_date(c)), "")
            other = [c for i, c in enumerate(cols) if i != lc and i not in (req.get("locationsColumns") or []) and c != date]
            n += 1
            if contest in items:
                if kw not in items[contest]["list_hits"]:
                    items[contest]["list_hits"].append(kw)
                continue
            items[contest] = _item(contest, contest, (title or "").strip(), "; ".join(locs), date,
                                   self.job_url(contest), detail_ref=contest, list_hits=[kw],
                                   list_meta={"source": "taleo", "jobId": req.get("jobId"), "locations": locs,
                                              "columns_other": other})
        return n

    # ---- m-cloud（镜像站的 Google Talent 搜索），只收 Taleo 以外的来源
    def _mcloud_page(self, kw, offset, size=100):
        q = urllib.parse.urlencode({"companyName": self.mcloud["company"], "query": kw, "pageSize": size, "offset": offset})
        r = fetch(f"https://jobsapi-google.m-cloud.io/api/job/search?{q}", headers=JSON_HEADERS)
        return r.json if isinstance(r.json, dict) else None

    def _mcloud_enumerate(self, items, kw):
        excl = {x.lower() for x in (self.mcloud.get("exclude_portals") or [])}
        off, total, kept, seen = 0, None, 0, 0
        while True:
            js = self._mcloud_page(kw, off)
            if not js:
                log(f"    [{self.key}] m-cloud 失败 {kw!r} offset={off}")
                break
            total = js.get("totalHits") if total is None else total
            rows = js.get("searchResults") or []
            for x in rows:
                j = x.get("job") or {}
                seen += 1
                if str(j.get("ats_portalid") or "").lower() in excl:
                    # Taleo 本身已覆盖；只记下镜像站独有的字段（salary 单值、镜像页 URL），list 结束后并进 Taleo 条目
                    cid = str(j.get("clientid") or j.get("ref") or "")
                    if cid:
                        self._mc_taleo[cid] = {"salary_field": j.get("salary"), "mirror_url": j.get("url"),
                                               "mcloud_id": j.get("id"), "open_date": j.get("open_date")}
                    continue
                mid = f"mc-{j.get('id')}"
                if mid in items:
                    if kw not in items[mid]["list_hits"]:
                        items[mid]["list_hits"].append(kw)
                    continue
                gl = (j.get("google_locations") or [{}])[0] or {}
                loc = _fmt_loc(gl.get("city") or j.get("primary_city"), gl.get("state") or j.get("primary_state"),
                               gl.get("country") or j.get("primary_country"))
                items[mid] = _item(mid, j.get("clientid") or j.get("ref") or mid, j.get("title"), loc,
                                   j.get("open_date") or "", j.get("url") or j.get("seo_url") or "",
                                   detail_ref={"_src": "mcloud", "job": j}, list_hits=[kw],
                                   list_meta={"source": "mcloud", "ats_portalid": j.get("ats_portalid"),
                                              "mcloud_id": j.get("id")})
                kept += 1
            off += len(rows)
            if not rows or (total is not None and off >= int(total)):
                break
            jitter(*self.jitter)
        return total, seen, kept

    def list_jobs(self, **_):
        items, totals = {}, {}
        self._mc_taleo = {}
        for kw in self.keywords():
            first = self._search(kw, 1)
            if not first:
                log(f"    [{self.key}] Taleo 首页失败 {kw!r}")
                totals[kw] = None
                continue
            pg = first.get("pagingData") or {}
            total = int(pg.get("totalCount") or 0)
            size = int(pg.get("pageSize") or self.PAGE)
            totals[kw] = total
            got = self._absorb_taleo(items, first, kw)
            page = 1
            while got < total:
                page += 1
                if page > 200:
                    break
                jitter(*self.jitter)
                js = self._search(kw, page)
                if not js:
                    log(f"    [{self.key}] Taleo 翻页失败 {kw!r} page={page}")
                    break
                n = self._absorb_taleo(items, js, kw)
                if not n:
                    break
                got += n
            log(f"    [{self.key}] Taleo {kw!r}: total={total} 翻到 {got}（每页 {size}），累计去重 {len(items)}")
            if self.mcloud:
                mt, seen, kept = self._mcloud_enumerate(items, kw)
                totals[f"mcloud:{kw}"] = mt
                log(f"    [{self.key}] m-cloud {kw!r}: total={mt} 看过 {seen}，新收非 Taleo {kept}，累计去重 {len(items)}")
        n_mc = 0
        for cid, meta in self._mc_taleo.items():
            if cid in items:
                items[cid]["list_meta"]["mcloud"] = meta
                n_mc += 1
        if self.mcloud:
            log(f"    [{self.key}] m-cloud 镜像字段并入 Taleo 条目 {n_mc} 条（salary 单值语义未核实，只放 extra）")
        write_json(os.path.join(raw_dir(self.key), "_totals.json"), totals)
        return list(items.values())

    def fetch_detail(self, item):
        if isinstance(item.get("detail_ref"), dict):          # m-cloud 条目：列表元素自带全文
            return item["detail_ref"]
        r = fetch(self.job_url(item["id"]), headers=HTML_HEADERS, as_json=False)
        if r.status != 200 or not r.text:
            return None
        m = re.search(r'id="initialHistory" value="([^"]*)"', r.text)
        if not m:
            log(f"    [{self.key}] {item['id']} 详情页无 initialHistory")
            return None
        parts = []
        for p in m.group(1).split("!|!"):
            s = urllib.parse.unquote(html.unescape(p))
            if s.startswith("true!$!ftlerrors") or s == "ftlerrors":
                break
            parts.append(s)
        if not any(p.startswith("!*!") and len(p) > 40 for p in parts):
            log(f"    [{self.key}] {item['id']} 详情无正文段（可能已下架）")
            return None
        return {"_src": "taleo", "parts": parts, "final_url": r.final_url}

    # ---- normalize
    @staticmethod
    def _unesc(s):
        return re.sub(r"\\(.)", r"\1", s or "")

    def _taleo_loc(self, s):
        """'Texas-Dallas-5323 Harry Hines Blvd' -> 'Dallas, TX'；'Arlington' + default_state -> 'Arlington, TX'。"""
        s = (s or "").strip()
        if not s:
            return ""
        toks = [t.strip() for t in s.split("-") if t.strip()]
        if len(toks) >= 2:
            st = _state_code(toks[0])
            if st:
                return _us_city_st(toks[1], st)
            if toks[0].lower() in _US_COUNTRY and len(toks) >= 3 and _state_code(toks[1]):
                return _us_city_st(toks[2], _state_code(toks[1]))
        if "," in s:
            return s
        if self.default_state:
            return _us_city_st(s, self.default_state)
        return s

    def normalize(self, item, raw):
        raw = raw or {}
        if raw.get("_src") == "mcloud":
            return self._normalize_mcloud(item, raw.get("job") or {})
        parts = [self._unesc(p) for p in raw.get("parts") or []]
        descs = []
        for p in parts:
            if p.startswith("!*!"):
                body = p[3:]
                if body and body not in descs:
                    descs.append(body)
        posted_raw = next((p for p in parts if re.match(r"^[A-Z][a-z]{2} \d{1,2}, \d{4}", p)), "") or item.get("posted_raw", "")
        tt = next((p for p in parts if re.match(r"^(Full|Part)[- ]?[Tt]ime\b|^PRN$|^Per Diem", p)), "")
        fields = []
        for p in parts[12:]:
            if (p and len(p) < 160 and not p.startswith("!*!") and p not in fields and p not in ("true", "false")
                    and not re.fullmatch(r"\d+", p) and "Submission for the position" not in p and "<" not in p):
                fields.append(p)
        lm = item.get("list_meta") or {}
        locs = [self._taleo_loc(l) for l in (lm.get("locations") or [])] or [self._taleo_loc(item.get("location_raw"))]
        locs = [l for l in dict.fromkeys(locs) if l]
        other = lm.get("columns_other") or []
        dept = next((c for c in other if c), "")
        return _rec(req_id=item["id"], job_id=str(lm.get("jobId") or item["id"]), title=item["title"],
                    org_unit=self.co.get("name", ""), department=dept, locations=locs,
                    location_raw=item.get("location_raw", ""), posted=_iso_date(posted_raw), posted_raw=posted_raw,
                    time_type=tt, url=self.job_url(item["id"]), api_url=self.search_url,
                    jd_html="<hr/>".join(descs),
                    countries_hint=[self.co.get("default_country", "US")] if self.co.get("default_country", "US") == "US" else [],
                    extra={"applyUrl": self.apply_url(item["id"]), "source": "taleo", "taleo_jobId": lm.get("jobId"),
                           "fields": fields[:30], "mcloud_mirror": lm.get("mcloud")})

    def _normalize_mcloud(self, item, j):
        locs = []
        for gl in j.get("google_locations") or []:
            s = _fmt_loc(gl.get("city"), gl.get("state"), gl.get("country"))
            if s and s not in locs:
                locs.append(s)
        for al in j.get("addtnl_locations") or []:
            if isinstance(al, dict):
                s = _fmt_loc(al.get("city"), al.get("state"), al.get("country"))
                if s and s not in locs:
                    locs.append(s)
        if not locs:
            locs = [item.get("location_raw") or ""]
        pay = _pay_from_text(str(j.get("salary") or "")) if j.get("salary") else None
        return _rec(req_id=str(j.get("clientid") or j.get("ref") or item["id"]), job_id=item["id"], title=j.get("title") or item["title"],
                    org_unit=self.co.get("name", ""), department=j.get("department") or j.get("primary_category") or "",
                    locations=[l for l in locs if l], location_raw=item.get("location_raw", ""),
                    posted=_iso_date(j.get("open_date") or ""), posted_raw=j.get("open_date") or "",
                    time_type=j.get("employment_type") or "", url=j.get("url") or j.get("seo_url") or item["url"],
                    api_url=f"https://jobsapi-google.m-cloud.io/api/job/search?companyName={self.mcloud['company'] if self.mcloud else ''}",
                    jd_html=j.get("description") or "", structured_pay=pay,
                    countries_hint=[gl.get("country") for gl in (j.get("google_locations") or []) if gl.get("country")][:1],
                    extra={"applyUrl": j.get("url") or j.get("seo_url") or "", "source": "mcloud",
                           "ats_portalid": j.get("ats_portalid"), "mcloud_id": j.get("id"),
                           "primary_category": j.get("primary_category"), "close_date": j.get("close_date")})


# ================================================================ Avature（Epic）

class AvatureAdapter(Adapter):
    kind = "avature"
    workers = 1
    jitter = (0.8, 1.5)

    def __init__(self, co, cfg):
        super().__init__(co, cfg)
        av = co["avature"]
        self.base = av["base"].rstrip("/")
        self.page_size = int(av.get("page_size", 10))
        self.max_pages = int(av.get("max_pages", 60))

    def list_jobs(self, **_):
        items = {}
        pages = 0
        for p in range(self.max_pages):
            off = p * self.page_size
            r = fetch(f"{self.base}/SearchJobs/?folderOffset={off}", headers=HTML_HEADERS, as_json=False)
            if r.status != 200 or not r.text:
                log(f"    [{self.key}] Avature 列表失败 offset={off} status={r.status} {r.err}")
                break
            blocks = re.findall(r'<li[^>]*class="[^"]*jobResultItem[^"]*"[^>]*>(.*?)</li>', r.text, re.S)
            new = 0
            for b in blocks:
                m = re.search(r'href="([^"]*/FolderDetail/([^"/]+)/(\d+))"[^>]*>(.*?)</a>', b, re.S)
                if not m:
                    continue
                url, slug, fid, title = html.unescape(m.group(1)), m.group(2), m.group(3), _plain(m.group(4))
                if fid in items:
                    continue
                bg = re.search(r"<p>\s*(Background:.*?)</p>", b, re.S)
                summ = re.search(r'class="jobResultItemDescription"[^>]*>(.*?)</div>', b, re.S)
                items[fid] = _item(fid, fid, title, self._slug_loc(slug), "", url, detail_ref=url, list_hits=["board"],
                                   list_meta={"slug": slug, "background": _plain(bg.group(1)) if bg else "",
                                              "summary": _plain(summ.group(1)) if summ else ""})
                new += 1
            pages += 1
            if not blocks or not new:
                break
            jitter(*self.jitter)
        log(f"    [{self.key}] Avature 全板：{pages} 页，{len(items)} 条")
        write_json(os.path.join(raw_dir(self.key), "_totals.json"), {"board": len(items), "pages": pages})
        return list(items.values())

    @staticmethod
    def _slug_loc(slug):
        """'Verona-Wisconsin-United-States-Software-Developer' -> 'Verona, WI'。"""
        m = re.match(r"^(.*?)-United-States-", slug or "")
        if not m:
            # 非美国站点：slug 形如 '5216-PP-s-Hertogenbosch-Netherlands-<title>'，取国家名及其前缀
            for c in ("Netherlands", "United-Kingdom", "Denmark", "Finland", "Sweden", "Norway", "Ireland", "Australia",
                      "New-Zealand", "Canada", "Singapore", "Saudi-Arabia", "United-Arab-Emirates", "Germany", "France",
                      "Spain", "Italy", "India", "Switzerland", "Belgium"):
                m2 = re.match(rf"^(.*?)-{c}-", slug or "")
                if m2:
                    return f"{m2.group(1).replace('-', ' ')}, {c.replace('-', ' ')}"
            return ""
        toks = m.group(1).split("-")
        for n in (3, 2, 1):
            if len(toks) > n:
                st = _state_code(" ".join(toks[-n:]))
                if st:
                    return _us_city_st(" ".join(toks[:-n]), st)
        return " ".join(toks) + ", US"

    def fetch_detail(self, item):
        r = fetch(item["detail_ref"], headers=HTML_HEADERS, as_json=False)
        if r.status != 200 or not r.text:
            return None
        txt = r.text
        t = re.search(r'itemprop="title"[^>]*>(.*?)</h2>', txt, re.S)
        secs = []
        for m in re.finditer(r'<h3>(.*?)</h3>\s*<div itemprop="description" class="jobDetailDescription">(.*?)</div>\s*(?=<h3>|<div class="tPad1|\s*<div class="tPad1)',
                             txt, re.S):
            secs.append({"heading": _plain(m.group(1)), "html": m.group(2)})
        if not secs:   # 退路：jobDetail 块起点到 footer 之前整段收下
            blk = re.search(r'<div class="jobDetail[^"]*"[^>]*>(.*)', txt, re.S)
            if blk and "jobDetailDescription" in blk.group(1):
                body = blk.group(1)
                k = body.find('id="footer"')
                secs.append({"heading": "", "html": body[:k] if k > 0 else body})
        if not secs:
            log(f"    [{self.key}] {item['id']} 详情页无正文（可能已下架）")
            return None
        ap = re.search(r'href="([^"]*ApplyFlowCheck\?[^"]*folderId=\d+[^"]*)"', txt)
        return {"title": _plain(t.group(1)) if t else "", "sections": secs,
                "apply_url": html.unescape(ap.group(1)) if ap else "", "final_url": r.final_url}

    def normalize(self, item, raw):
        raw = raw or {}
        lm = item.get("list_meta") or {}
        jd = "".join((f"<h3>{html.escape(s['heading'])}</h3>" if s.get("heading") else "") + s.get("html", "")
                     for s in raw.get("sections") or [])
        if lm.get("background"):
            jd = f"<p>{html.escape(lm['background'])}</p>" + jd
        loc = self._slug_loc(lm.get("slug")) or item.get("location_raw") or ""
        return _rec(req_id=item["id"], job_id=item["id"], title=raw.get("title") or item["title"],
                    org_unit=self.co.get("name", ""), locations=[loc] if loc else [], location_raw=loc,
                    url=item["url"], api_url=f"{self.base}/SearchJobs/?folderOffset=0", jd_html=jd,
                    countries_hint=["US"] if "-United-States-" in (lm.get("slug") or "") else [],
                    extra={"applyUrl": raw.get("apply_url") or f"{self.base}/ApplyFlowCheck?folderId={item['id']}",
                           "folderId": item["id"], "summary": lm.get("summary"), "slug": lm.get("slug")})


EXT_ADAPTERS = {TalentBrewAdapter.kind: TalentBrewAdapter, TaleoAdapter.kind: TaleoAdapter,
                AvatureAdapter.kind: AvatureAdapter}
