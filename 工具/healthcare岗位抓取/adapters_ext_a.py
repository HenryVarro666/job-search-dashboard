#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standard-library extension adapters: SmartRecruiters, Phenom widgets,
SuccessFactors RSS, and tenant-specific Oracle Recruiting Cloud.

Company setup is explicit in the local registry. Supported settings:
    smartrecruiters: {company, country?}
    phenom2: {base, selected_fields?}
    sf_rss: {host, page_dates?: "us" | "all" | "none"}
    oracle_hcm2: {host, site, facet?: "auto" | id | null, url_site?}
These implement the same list_jobs / fetch_detail / normalize contract as Adapter.
"""
import html
import json
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

from adapters import Adapter, _item, _rec
from common import fetch, jitter, log, raw_dir, read_json, write_json, HTML_HEADERS, JSON_HEADERS

DEFAULT_KEYWORDS = ["machine learning", "data scientist", "artificial intelligence", "AI engineer", "applied scientist",
                    "research scientist", "deep learning", "NLP", "LLM", "computer vision", "software engineer"]


def _kw_list(ad):
    """公司自带关键词优先；没有时用本批次的 ML/SWE 默认表（不用 defaults 里的 healthcare 词表）。"""
    kws = ad.co.get("keywords") or DEFAULT_KEYWORDS
    black = {k.lower() for k in ad.co.get("keyword_blacklist", [])}
    return [k for k in kws if k.lower() not in black]


def _absorb_hit(items, jid, hit):
    if jid in items:
        if hit not in items[jid]["list_hits"]:
            items[jid]["list_hits"].append(hit)
        return True
    return False


def _date10(s):
    return str(s or "")[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", str(s or "")) else ""


_ISO_FALLBACK = {"US": "United States", "CA": "Canada", "MX": "Mexico", "GB": "United Kingdom", "IE": "Ireland",
                 "CN": "China", "JP": "Japan", "KR": "South Korea", "IN": "India", "MY": "Malaysia", "CR": "Costa Rica",
                 "DE": "Germany", "FR": "France", "NL": "Netherlands", "ES": "Spain", "IT": "Italy", "PL": "Poland",
                 "SG": "Singapore", "AU": "Australia", "BR": "Brazil", "CH": "Switzerland", "BE": "Belgium"}


def _iso_name(code):
    try:
        import parse_jd
        return parse_jd._ISO_COUNTRY.get(code) or _ISO_FALLBACK.get(code)
    except Exception:  # noqa
        return _ISO_FALLBACK.get(code)


# ---------------------------------------------------------------- SmartRecruiters

class SmartRecruitersAdapter(Adapter):
    kind = "smartrecruiters"
    workers = 4
    jitter = (0.1, 0.4)
    API = "https://api.smartrecruiters.com/v1/companies"

    def __init__(self, co, cfg):
        super().__init__(co, cfg)
        sr = co.get("smartrecruiters") or {}
        self.company = sr.get("company") or co.get("slug") or co.get("ats_hint")
        self.country = sr.get("country", "us")
        self.page_size = int(sr.get("page_size", 100))

    def _page(self, kw, offset):
        q = {"limit": self.page_size, "offset": offset}
        if kw:
            q["q"] = kw
        if self.country:
            q["country"] = self.country
        r = fetch(f"{self.API}/{urllib.parse.quote(self.company)}/postings?{urllib.parse.urlencode(q)}", headers=JSON_HEADERS)
        return r.json

    def list_jobs(self, **_):
        items, totals = {}, {}
        for kw in _kw_list(self):
            first = self._page(kw, 0)
            if not first:
                log(f"    [{self.key}] SmartRecruiters 首页失败 {kw!r}")
                totals[kw] = None
                continue
            total = int(first.get("totalFound") or 0)
            totals[kw] = total
            if total > self.max_total():
                log(f"    [{self.key}] 关键词 {kw!r} total={total} 超过 max_total_per_keyword={self.max_total()}，跳过")
                continue

            def absorb(js):
                for p in js.get("content") or []:
                    pid = str(p.get("id"))
                    if _absorb_hit(items, pid, kw):
                        continue
                    loc = p.get("location") or {}
                    items[pid] = _item(pid, p.get("refNumber") or pid, p.get("name"), loc.get("fullLocation") or "",
                                       p.get("releasedDate", ""),
                                       f"https://jobs.smartrecruiters.com/{self.company}/{pid}",
                                       detail_ref=pid, list_hits=[kw],
                                       list_meta={"uuid": p.get("uuid"), "refNumber": p.get("refNumber"),
                                                  "function": (p.get("function") or {}).get("label"),
                                                  "experienceLevel": (p.get("experienceLevel") or {}).get("label")})
            absorb(first)
            off = len(first.get("content") or [])
            while off < total:
                jitter(0.2, 0.6)
                js = self._page(kw, off)
                if not js or not js.get("content"):
                    log(f"    [{self.key}] SmartRecruiters 翻页失败/空页 offset={off} ({kw!r})")
                    break
                absorb(js)
                off += len(js["content"])
            log(f"    [{self.key}] {kw!r}: totalFound={total} 累计去重 {len(items)}")
        board = self._page("", 0)
        totals["_board_country_" + (self.country or "all")] = int((board or {}).get("totalFound") or 0) if board else None
        write_json(os.path.join(raw_dir(self.key), "_totals.json"), totals)
        log(f"    [{self.key}] 板块总数（country={self.country}）={totals['_board_country_' + (self.country or 'all')]}，关键词并集 {len(items)}")
        return list(items.values())

    def fetch_detail(self, item):
        r = fetch(f"{self.API}/{urllib.parse.quote(self.company)}/postings/{item['id']}", headers=JSON_HEADERS)
        js = r.json
        if js and (js.get("jobAd") or {}).get("sections"):
            return js
        return None

    def normalize(self, item, raw):
        j = raw or {}
        loc = j.get("location") or {}
        # remote=true 也可能表示按片区的外勤销售岗（地点仍是具体城市），所以不改写地点串，只经 workplace_type/is_remote 传
        full = loc.get("fullLocation") or ", ".join(x for x in [loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()] if x)
        secs = (j.get("jobAd") or {}).get("sections") or {}
        parts = []
        for k in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
            s = secs.get(k) or {}
            if s.get("text"):
                parts.append(f"<h3>{s.get('title') or k}</h3>{s['text']}")
        cf = {}
        for f in j.get("customField") or []:
            lab = f.get("fieldLabel") or ""
            if "hiring manager" in lab.lower():
                continue
            cf[lab] = f.get("valueLabel")
        comp = j.get("compensation") or None
        pay = None
        if comp and (comp.get("min") is not None or comp.get("max") is not None):
            pay = {"min": comp.get("min"), "max": comp.get("max"), "currency": comp.get("currency") or "USD",
                   "interval": comp.get("period") or "YEARLY"}
        wt = "remote" if loc.get("remote") else ("hybrid" if loc.get("hybrid") else "")
        dep = (j.get("department") or {}).get("label") or ""
        func = (j.get("function") or {}).get("label") or ""
        return _rec(req_id=str(j.get("refNumber") or item["req_id"]), job_id=str(j.get("id") or item["id"]),
                    title=j.get("name") or item["title"], org_unit=cf.get("Workday Job Family") or dep or func,
                    department=dep or func, locations=[full] if full else [item.get("location_raw", "")],
                    location_raw=item.get("location_raw", "") or full, posted=_date10(j.get("releasedDate")),
                    posted_raw=j.get("releasedDate", ""), time_type=(j.get("typeOfEmployment") or {}).get("label", ""),
                    can_apply=j.get("active"), url=j.get("postingUrl") or item["url"],
                    api_url=f"{self.API}/{self.company}/postings/{j.get('id') or item['id']}",
                    jd_html="".join(parts), structured_pay=pay, workplace_type=wt, is_remote=bool(loc.get("remote")),
                    countries_hint=[(loc.get("country") or "").upper()] if loc.get("country") else [],
                    extra={"applyUrl": j.get("applyUrl"), "uuid": j.get("uuid"), "company": self.company,
                           "experienceLevel": (j.get("experienceLevel") or {}).get("label"), "function": func,
                           "customField": cf})


# ---------------------------------------------------------------- Phenom (/widgets JSON)

class Phenom2Adapter(Adapter):
    kind = "phenom2"
    workers = 2
    jitter = (0.6, 1.2)

    def __init__(self, co, cfg):
        super().__init__(co, cfg)
        ph = co.get("phenom2") or co.get("phenom") or {}
        self.base = (ph.get("base") or co.get("ats_hint") or "").rstrip("/")
        pu = urllib.parse.urlparse(self.base)
        self.origin = f"{pu.scheme}://{pu.netloc}"
        self.widgets = f"{self.origin}/widgets"
        self.ref_num = ph.get("refNum")
        self.selected_fields = ph.get("selected_fields") or {}
        self.page_size = int(ph.get("page_size", 100))
        self.headers = dict(JSON_HEADERS)
        self.headers.update({"Content-Type": "application/json", "Origin": self.origin,
                             "Referer": f"{self.base}/search-results"})

    @staticmethod
    def _ddo_from_html(txt):
        """phApp.ddo = {...}; phApp.experimentData = ...  —— 用 raw_decode，不用正则找结尾。"""
        i = (txt or "").find("phApp.ddo")
        if i < 0:
            return None
        j = txt.find("{", i)
        if j < 0:
            return None
        try:
            obj, _ = json.JSONDecoder().raw_decode(txt[j:])
            return obj
        except ValueError:
            return None

    def _ensure_ref(self):
        if self.ref_num:
            return
        r = fetch(f"{self.base}/search-results", headers=HTML_HEADERS, as_json=False)
        m = re.search(r'"refNum"\s*:\s*"([A-Za-z0-9]+)"', r.text or "")
        self.ref_num = m.group(1) if m else None
        log(f"    [{self.key}] Phenom refNum={self.ref_num}")

    def _search(self, kw, offset):
        body = {"lang": "en_us", "deviceType": "desktop", "country": "us", "pageName": "search-results",
                "ddoKey": "refineSearch", "sortBy": "", "subsearch": "", "from": offset, "jobs": True, "counts": True,
                "all_fields": ["category", "country", "state", "city"], "size": self.page_size, "clearAll": False,
                "jdsource": "facets", "isSliderEnable": False, "siteType": "external", "keywords": kw,
                "global": True, "selected_fields": self.selected_fields}
        if self.ref_num:
            body["refNum"] = self.ref_num
        r = fetch(self.widgets, method="POST", body=body, headers=self.headers)
        return ((r.json or {}).get("refineSearch")) if r.json else None

    @staticmethod
    def _locs(j):
        out = []
        for l in j.get("multi_location") or []:
            if isinstance(l, dict):
                l = l.get("cityStateCountry") or l.get("location") or l.get("cityState") or ""
            if l and l not in out:
                out.append(l)
        if not out:
            l = j.get("cityStateCountry") or j.get("location") or j.get("cityState") or ""
            if l:
                out.append(l)
        return out

    def list_jobs(self, **_):
        self._ensure_ref()
        items, totals = {}, {}
        board = self._search("", 0)
        totals["_board"] = (board or {}).get("totalHits")
        for kw in _kw_list(self):
            first = self._search(kw, 0)
            if not first:
                log(f"    [{self.key}] Phenom 首页失败 {kw!r}")
                totals[kw] = None
                continue
            total = int(first.get("totalHits") or 0)
            totals[kw] = total
            if total > self.max_total():
                log(f"    [{self.key}] 关键词 {kw!r} totalHits={total} 超过 max_total_per_keyword={self.max_total()}，跳过")
                continue

            def absorb(rs):
                for j in ((rs.get("data") or {}).get("jobs")) or []:
                    jid = str(j.get("jobId") or j.get("reqId"))
                    if _absorb_hit(items, jid, kw):
                        continue
                    locs = self._locs(j)
                    items[jid] = _item(jid, j.get("reqId") or jid, j.get("title"), "; ".join(locs),
                                       j.get("postedDate", ""), f"{self.base}/job/{jid}", detail_ref=j.get("jobSeqNo"),
                                       list_hits=[kw], list_meta={"jobSeqNo": j.get("jobSeqNo"), "category": j.get("category"),
                                                                  "applyUrl": j.get("applyUrl")})
                return len(((rs.get("data") or {}).get("jobs")) or [])
            off = absorb(first)
            while off < total:
                jitter(*self.jitter)
                rs = self._search(kw, off)
                n = absorb(rs) if rs else 0
                if not n:
                    log(f"    [{self.key}] Phenom 翻页失败/空页 from={off} ({kw!r})")
                    break
                off += n
            log(f"    [{self.key}] {kw!r}: totalHits={total} 累计去重 {len(items)}")
        write_json(os.path.join(raw_dir(self.key), "_totals.json"), totals)
        log(f"    [{self.key}] 整板 totalHits={totals['_board']}，关键词并集 {len(items)}")
        return list(items.values())

    def fetch_detail(self, item):
        seq = item.get("detail_ref") or (item.get("list_meta") or {}).get("jobSeqNo")
        if seq:
            body = {"lang": "en_us", "deviceType": "desktop", "country": "us", "pageName": "job", "ddoKey": "jobDetail",
                    "jobSeqNo": seq, "siteType": "external"}
            if self.ref_num:
                body["refNum"] = self.ref_num
            h = dict(self.headers)
            h["Referer"] = item["url"]
            r = fetch(self.widgets, method="POST", body=body, headers=h)
            job = (((r.json or {}).get("jobDetail") or {}).get("data") or {}).get("job")
            if job and job.get("description"):
                return job
        # 兜底：职位页 HTML 里的 phApp.ddo
        r = fetch(item["url"], headers=HTML_HEADERS, as_json=False)
        ddo = self._ddo_from_html(r.text) if r.status == 200 else None
        job = (((ddo or {}).get("jobDetail") or {}).get("data") or {}).get("job")
        return job if job and job.get("description") else None

    def normalize(self, item, raw):
        j = raw or {}
        locs = self._locs(j) or [item.get("location_raw", "")]
        jd = j.get("description") or ""
        if j.get("externalDescription") and j.get("externalDescription") not in jd:
            jd += "<h3>Additional Information</h3>" + j["externalDescription"]
        country = j.get("country") or ""
        return _rec(req_id=str(j.get("reqId") or item["req_id"]), job_id=str(j.get("jobId") or item["id"]),
                    title=j.get("title") or item["title"], org_unit=j.get("department") or j.get("teamName") or j.get("category") or "",
                    department=j.get("category") or j.get("department") or "", locations=locs,
                    location_raw=item.get("location_raw", ""), posted=_date10(j.get("postedDate")),
                    posted_raw=j.get("postedDate", ""), time_type=j.get("type") or "", url=item["url"],
                    api_url=f"{self.widgets}#jobDetail:{j.get('jobSeqNo') or item.get('detail_ref')}", jd_html=jd,
                    countries_hint=[country] if country else [],
                    extra={"applyUrl": j.get("applyUrl") or (item.get("list_meta") or {}).get("applyUrl"),
                           "jobSeqNo": j.get("jobSeqNo"), "refNum": j.get("refNum") or self.ref_num,
                           "jobShift": j.get("jobShift"), "companyName": j.get("companyName")})


# ---------------------------------------------------------------- SuccessFactors RMK RSS（/sitemap.xml）

class SFRssAdapter(Adapter):
    kind = "sf_rss"
    workers = 2
    jitter = (0.5, 1.1)
    G = "{http://base.google.com/ns/1.0}"

    def __init__(self, co, cfg):
        super().__init__(co, cfg)
        sf = co.get("sf_rss") or co.get("successfactors") or {}
        self.host = sf.get("host") or co.get("ats_hint")
        self.feed = sf.get("feed") or f"https://{self.host}/sitemap.xml"
        self.page_dates = sf.get("page_dates", "us")

    @staticmethod
    def _loc(loc):
        """'Marlborough, MA, US, 01752' -> ('Marlborough, MA, US', 'US')；'Penang, MY' -> ('Penang, Malaysia', 'MY')。"""
        toks = [t.strip() for t in str(loc or "").split(",") if t.strip()]
        toks = [t for t in toks if not re.fullmatch(r"[A-Z0-9 -]*\d[A-Z0-9 -]*", t)]  # 邮编
        iso = None
        if any(t in ("US", "USA", "United States") for t in toks):
            iso = "US"
        elif toks and re.fullmatch(r"[A-Z]{2}", toks[-1]):
            name = _iso_name(toks[-1])
            if name:
                iso = toks[-1]
                toks[-1] = name
        return ", ".join(toks), iso

    @staticmethod
    def _extra_locs(desc_html):
        m = re.search(r"Additional Location\(s\):\s*</strong>(.*?)</p>", desc_html or "", re.S)
        if not m:
            return []
        v = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).replace("\xa0", " ").strip()
        out = []
        for part in [p.strip() for p in v.split(";") if p.strip()]:
            if part.upper() in ("N/A", "NA", "NONE"):
                continue
            # 'US-MN-Arden Hills' / 'Canada-QC-Montreal' / 'Belgium-Diegem' / 'France-Rhône-Alpes, Auvergne'
            bits = [b.strip() for b in part.split("-", 2)]
            c = bits[0]
            cname = "US" if c.upper() in ("US", "USA") else (_iso_name(c) if re.fullmatch(r"[A-Z]{2,3}", c) else None) or c
            if len(bits) == 3 and re.fullmatch(r"[A-Z]{2}", bits[1]):
                out.append(f"{bits[2]}, {bits[1]}, {cname}")
            elif len(bits) >= 2:
                out.append(f"{part.split('-', 1)[1].strip()}, {cname}")
            else:
                out.append(part)
        return out

    def list_jobs(self, **_):
        r = fetch(self.feed, headers=HTML_HEADERS, as_json=False, timeout=120)
        if r.status != 200 or not r.text.strip():
            log(f"    [{self.key}] RSS 拉取失败 status={r.status} {r.err}")
            return []
        try:
            root = ET.fromstring(r.text.encode("utf-8"))
        except ET.ParseError as e:
            log(f"    [{self.key}] RSS 解析失败: {e}")
            return []
        entries, items = [], []
        for it in root.iter("item"):
            d = {c.tag.replace(self.G, ""): (c.text or "") for c in it}
            jid = d.get("id") or d.get("guid") or re.sub(r".*/(\d+)/?$", r"\1", d.get("link", ""))
            d["id"] = jid
            entries.append(d)
            disp, iso = self._loc(d.get("location"))
            title = re.sub(r"\s*\(" + re.escape(d.get("location", "")) + r"\)\s*$", "", d.get("title", "")).strip()
            items.append(_item(jid, jid, title, disp, "", d.get("link", ""), detail_ref=jid, list_hits=["rss"],
                               list_meta={"iso": iso, "job_function": d.get("job_function")}))
        write_json(self.board_cache_path(), {"_fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": "rss",
                                             "feed": self.feed, "jobs": entries})
        n_us = sum(1 for i in items if i["list_meta"]["iso"] == "US")
        log(f"    [{self.key}] RSS 整板 {len(items)} 条（美国 {n_us}）")
        return items

    def _page_meta(self, url):
        r = fetch(url, headers=HTML_HEADERS, as_json=False)
        if r.status != 200:
            return None
        meta = {}
        for k, v in re.findall(r'itemprop="(\w+)"\s+content="([^"]*)"', r.text):
            meta.setdefault(k, html.unescape(v))
        m = re.search(r'href="(/talentcommunity/apply/[^"]+)"', r.text)
        if m:
            meta["applyPath"] = html.unescape(m.group(1))
        return meta

    def fetch_detail(self, item):
        board = read_json(self.board_cache_path(), {}) or {}
        entry = next((e for e in board.get("jobs", []) if str(e.get("id")) == item["id"]), None)
        if entry is None:
            return None
        page = None
        want = self.page_dates == "all" or (self.page_dates == "us" and (item.get("list_meta") or {}).get("iso") == "US")
        if want and entry.get("link"):
            page = self._page_meta(entry["link"])
        return {"rss": entry, "page": page}

    def normalize(self, item, raw):
        raw = raw or {}
        e = raw.get("rss") or {}
        page = raw.get("page") or {}
        desc = html.unescape(e.get("description") or "")  # CDATA 里是 &lt;p&gt;，解一次得到真 HTML
        disp, iso = self._loc(e.get("location"))
        locs = [disp] if disp else []
        for l in self._extra_locs(desc):
            if l not in locs:
                locs.append(l)
        posted, posted_raw = "", page.get("datePosted", "")
        if posted_raw:
            try:
                posted = time.strftime("%Y-%m-%d", time.strptime(posted_raw, "%a %b %d %H:%M:%S UTC %Y"))
            except ValueError:
                posted = _date10(posted_raw)
        apply_url = f"https://{self.host}{page['applyPath']}" if page.get("applyPath") else \
            f"https://{self.host}/talentcommunity/apply/{item['id']}/?locale=en_US"
        return _rec(req_id=str(e.get("id") or item["id"]), job_id=str(e.get("id") or item["id"]), title=item["title"] or e.get("title", ""),
                    org_unit=e.get("job_function", ""), department=e.get("job_function", ""), locations=locs,
                    location_raw=e.get("location", ""), posted=posted, posted_raw=posted_raw,
                    url=e.get("link") or item["url"], api_url=self.feed, jd_html=desc,
                    countries_hint=[iso] if iso else [],
                    extra={"applyUrl": apply_url, "expiration_date": e.get("expiration_date"),
                           "validThrough": page.get("validThrough"), "employer": e.get("employer"),
                           "page_fetched": bool(raw.get("page"))})


# ---------------------------------------------------------------- Oracle Recruiting Cloud（租户无关版）

class OracleHCM2Adapter(Adapter):
    kind = "oracle_hcm2"
    workers = 3
    jitter = (0.2, 0.6)

    def __init__(self, co, cfg):
        super().__init__(co, cfg)
        o = co.get("oracle_hcm2") or co.get("oracle_hcm") or {}
        self.host, self.site = o["host"], o["site"]
        self.url_site = o.get("url_site") or self.site
        self.facet = o.get("facet", "auto")
        self.page_size = int(o.get("page_size", 100))
        self.base = f"https://{self.host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        self.detail_base = f"https://{self.host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"

    def _get(self, finder, expand="requisitionList.secondaryLocations"):
        q = urllib.parse.urlencode({"onlyData": "true", "expand": expand, "finder": finder}, safe=';=,"')
        r = fetch(f"{self.base}?{q}", headers=JSON_HEADERS)
        if not r.json:
            return None
        return ((r.json.get("items") or [{}]) or [{}])[0]

    def _resolve_facet(self):
        if self.facet in (None, "", "none"):
            return None
        if self.facet != "auto":
            return str(self.facet)
        it = self._get(f"findReqs;siteNumber={self.site},facetsList=LOCATIONS,limit=1,offset=0")
        lf = (it or {}).get("locationsFacet") or []
        us = [f for f in lf if (f.get("Name") or "").strip() in ("United States", "United States of America")]
        board = (it or {}).get("TotalJobsCount")
        if us:
            log(f"    [{self.key}] Oracle 整板 {board}；美国 facet Id={us[0].get('Id')}（{us[0].get('TotalCount')} 条）")
            return str(us[0].get("Id"))
        log(f"    [{self.key}] Oracle 整板 {board}；未找到 'United States' 地点 facet，不按地点过滤")
        return None

    def _finder(self, kw, offset, facet):
        parts = [f"findReqs;siteNumber={self.site}"]
        if kw:
            parts.append(f'keyword="{kw}"')
        if facet:
            parts.append(f"selectedLocationsFacet={facet}")
        parts += [f"limit={self.page_size}", f"offset={offset}", "sortBy=POSTING_DATES_DESC"]
        return ",".join(parts)

    def job_url(self, jid):
        return f"https://{self.host}/hcmUI/CandidateExperience/en/sites/{self.url_site}/job/{jid}"

    def list_jobs(self, **_):
        facet = self._resolve_facet()
        items, totals = {}, {"_us_facet": facet}
        for kw in _kw_list(self):
            off, total = 0, None
            while True:
                it = self._get(self._finder(kw, off, facet))
                if it is None:
                    log(f"    [{self.key}] Oracle 请求失败 offset={off} ({kw!r})")
                    break
                total = int(it.get("TotalJobsCount") or 0)
                if off == 0 and total > self.max_total():
                    log(f"    [{self.key}] 关键词 {kw!r} total={total} 超过 max_total_per_keyword={self.max_total()}，跳过")
                    break
                rows = it.get("requisitionList") or []
                for j in rows:
                    jid = str(j.get("Id"))
                    if _absorb_hit(items, jid, kw):
                        continue
                    items[jid] = _item(jid, jid, j.get("Title"), j.get("PrimaryLocation") or "", j.get("PostedDate", ""),
                                       self.job_url(jid), detail_ref=jid, list_hits=[kw],
                                       list_meta={"secondary": [x.get("Name") for x in (j.get("secondaryLocations") or []) if x.get("Name")],
                                                  "PostedDate": j.get("PostedDate"), "JobFamily": j.get("JobFamily"),
                                                  "WorkplaceType": j.get("WorkplaceType")})
                off += len(rows)
                if not rows or off >= total:
                    break
                jitter(*self.jitter)
            totals[kw] = total
            log(f"    [{self.key}] {kw!r}: total={total} 累计去重 {len(items)}")
        write_json(os.path.join(raw_dir(self.key), "_totals.json"), totals)
        return list(items.values())

    def fetch_detail(self, item):
        q = urllib.parse.urlencode({"onlyData": "true", "expand": "all",
                                    "finder": f'ById;Id="{item["id"]}",siteNumber={self.site}'}, safe=';=,"')
        r = fetch(f"{self.detail_base}?{q}", headers=JSON_HEADERS)
        rows = (r.json or {}).get("items") or []
        return rows[0] if rows else None

    @staticmethod
    def _wl_str(w):
        city, st, c = w.get("TownOrCity"), w.get("Region2"), w.get("Country")
        if not city:
            return ""
        return ", ".join(x for x in [city, st, c] if x)

    def normalize(self, item, raw):
        j = raw or {}
        prim = j.get("PrimaryLocation") or item.get("location_raw", "")
        secondary = [x.get("Name") for x in (j.get("secondaryLocations") or []) if isinstance(x, dict) and x.get("Name")]
        secondary = secondary or list((item.get("list_meta") or {}).get("secondary") or [])
        wls = [self._wl_str(w) for w in (j.get("workLocation") or []) + (j.get("otherWorkLocations") or []) if isinstance(w, dict)]
        wls = [w for w in wls if w]
        locs = []
        bare_country = prim.strip().lower() in ("united states", "united states of america", "us", "usa")
        if prim and not (bare_country and wls):
            locs.append(prim)
        for l in secondary + (wls if bare_country or not prim else []):
            if l and l not in locs:
                locs.append(l)
        flex = [(f.get("Prompt") or "").strip() for f in (j.get("requisitionFlexFields") or [])]
        flex_pairs = [((f.get("Prompt") or "").strip().rstrip(":"), f.get("Value")) for f in (j.get("requisitionFlexFields") or [])
                      if f.get("Value") not in (None, "")]
        ws_hint = next((str(v) for k, v in flex_pairs if re.search(r"workplace|remote|on-?site|hybrid", k, re.I)), "")
        # 'Flexibility of both remote and on-site work' 这类同时提到两者的值按 hybrid 传，免得被判成 remote
        if re.search(r"remote", ws_hint, re.I) and re.search(r"on-?\s?site|office|in person", ws_hint, re.I) \
                and not re.search(r"hybrid", ws_hint, re.I) and not re.search(r"100%\s*remote", ws_hint, re.I):
            ws_hint = f"Hybrid ({ws_hint})"
        parts = []
        for key, head in (("ExternalDescriptionStr", "Description"), ("ExternalResponsibilitiesStr", "Responsibilities"),
                          ("ExternalQualificationsStr", "Qualifications")):
            if j.get(key):
                parts.append(f"<h3>{head}</h3>{j[key]}")
        if flex_pairs:
            parts.append("<h3>Additional Details</h3><ul>" + "".join(
                f"<li><strong>{html.escape(k)}:</strong> {html.escape(str(v))}</li>" for k, v in flex_pairs) + "</ul>")
        for key, head in (("OrganizationDescriptionStr", "About the Organization"), ("CorporateDescriptionStr", "About Us")):
            if j.get(key):
                parts.append(f"<h3>{head}</h3>{j[key]}")
        posted = _date10(j.get("ExternalPostedStartDate")) or _date10((item.get("list_meta") or {}).get("PostedDate"))
        return _rec(req_id=str(j.get("Id") or item["id"]), job_id=str(j.get("Id") or item["id"]), title=j.get("Title") or item["title"],
                    org_unit=j.get("Organization") or j.get("BusinessUnit") or j.get("Department") or "",
                    department=j.get("Department") or j.get("Category") or j.get("JobFunction") or "",
                    locations=locs or [prim], location_raw=item.get("location_raw", "") or prim, posted=posted,
                    posted_raw=j.get("ExternalPostedStartDate") or item.get("posted_raw", ""),
                    time_type=j.get("JobSchedule") or "", url=item["url"] or self.job_url(item["id"]),
                    api_url=f'{self.detail_base}?onlyData=true&expand=all&finder=ById;Id="{item["id"]}",siteNumber={self.site}',
                    jd_html="".join(parts), workplace_type=j.get("WorkplaceType") or "", work_site_hint=ws_hint,
                    countries_hint=[j.get("PrimaryLocationCountry")] if j.get("PrimaryLocationCountry") else [],
                    extra={"applyUrl": f"{self.job_url(item['id'])}/apply/email", "RequisitionId": j.get("RequisitionId"),
                           "Category": j.get("Category"), "JobFunction": j.get("JobFunction"), "LegalEmployer": j.get("LegalEmployer"),
                           "ExternalPostedEndDate": j.get("ExternalPostedEndDate"), "flex_prompts": flex,
                           "site": self.site, "host": self.host})


EXT_ADAPTERS = {
    SmartRecruitersAdapter.kind: SmartRecruitersAdapter,
    Phenom2Adapter.kind: Phenom2Adapter,
    SFRssAdapter.kind: SFRssAdapter,
    OracleHCM2Adapter.kind: OracleHCM2Adapter,
}
