#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""healthcare 岗位抓取 —— 逐 ATS 适配器。

统一契约（fetch_ats.py 只认这三个方法，不认任何 vendor 细节）：
    list_jobs()            -> [ListItem]   ListItem = {id, req_id, title, location_raw, posted_raw,
                                            url, detail_ref, list_hits[], list_meta{}}
    fetch_detail(item)     -> raw          原始响应（dict / str），由 fetch_ats 通过 save_raw 落盘
    normalize(item, raw)   -> record       预解析记录，交给 parse_jd.parse()：
        {req_id, job_id, title, org_unit, department, locations[], location_raw, posted, posted_raw,
         time_type, can_apply, url, api_url, jd_html | jd_text, structured_pay, work_site_hint,
         workplace_type, is_remote, countries_hint[], extra{}}

整板类（Greenhouse / Ashby / Lever / SuccessFactors）把整板缓存成 raw/<key>/_board.json，
fetch_detail 直接返回缓存元素，这样 raw 文件约定对所有 vendor 一致。

请求头预设（应按当前端点行为验证）：
    workday      Accept: application/json + Referer https://{host}/{site}；POST 带 Content-Type/Origin
    eightfold    微软带 Referer；NVIDIA 绝不带 Referer（CloudFront 403）
    amazon / greenhouse / ashby / lever   只要 Accept: application/json
    google / successfactors / phenom       全套 Chrome 头，单线程慢抓
    任何地方不主动发 Accept-Encoding: gzip
"""
import html
import json
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

from common import (HTML_HEADERS, JSON_HEADERS, MANUAL_DIR, TOOL_DIR, UA_CHROME128, UA_CHROME140,
                    fetch, jitter, log, raw_dir, read_json, write_json)


def _item(id_, req_id, title, location_raw="", posted_raw="", url="", detail_ref=None,
          list_hits=None, list_meta=None):
    return {"id": str(id_), "req_id": str(req_id or id_), "title": title or "",
            "location_raw": location_raw or "", "posted_raw": posted_raw or "", "url": url or "",
            "detail_ref": detail_ref, "list_hits": list(list_hits or []), "list_meta": list_meta or {}}


def _rec(**kw):
    base = {"req_id": "", "job_id": "", "title": "", "org_unit": "", "department": "",
            "locations": [], "location_raw": "", "posted": "", "posted_raw": "", "time_type": "",
            "can_apply": None, "url": "", "api_url": "", "jd_html": "", "jd_text": "",
            "structured_pay": None, "work_site_hint": "", "workplace_type": "", "is_remote": None,
            "countries_hint": [], "extra": {}}
    base.update(kw)
    return base


class Adapter:
    kind = "base"
    workers = 3
    jitter = (0.2, 0.8)

    def __init__(self, co, cfg):
        self.co, self.cfg, self.key = co, cfg, co["key"]
        self.defaults = cfg.get("defaults", {})

    # -- helpers
    def keywords(self):
        kws = list(self.co.get("keywords") or self.defaults.get("keywords", []))
        policy = read_json(os.path.join(TOOL_DIR, "spring_internship_policy.json"), {})
        if policy.get("enabled"):
            kws.extend(policy.get("search_keywords", []))
        black = {k.lower() for k in self.co.get("keyword_blacklist", [])}
        seen = set(black)
        result = []
        for k in kws:
            if k.lower() not in seen:
                seen.add(k.lower())
                result.append(k)
        return result

    def max_total(self):
        return int(self.co.get("max_total_per_keyword") or self.defaults.get("max_total_per_keyword", 400))

    def board_cache_path(self):
        return os.path.join(raw_dir(self.key), "_board.json")

    def list_jobs(self):
        raise NotImplementedError

    def fetch_detail(self, item):
        raise NotImplementedError

    def normalize(self, item, raw):
        raise NotImplementedError


# ---------------------------------------------------------------- Workday CXS

class WorkdayAdapter(Adapter):
    kind = "workday"
    workers = 3

    def __init__(self, co, cfg):
        super().__init__(co, cfg)
        wd = co["workday"]
        self.host, self.tenant, self.site = wd["host"], wd["tenant"], wd["site"]
        self.base = f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"
        self.headers = dict(JSON_HEADERS)
        self.headers["Referer"] = f"https://{self.host}/{self.site}"
        self.post_headers = dict(self.headers)
        self.post_headers["Content-Type"] = "application/json"
        self.post_headers["Origin"] = f"https://{self.host}"

    def _page(self, search_text, offset, limit=20, facets=None):
        body = {"appliedFacets": facets or {}, "limit": limit, "offset": offset, "searchText": search_text}
        r = fetch(f"{self.base}/jobs", method="POST", body=body, headers=self.post_headers)
        return r.json if r.json else None

    def _enumerate(self, search_text, facets=None, tag=None, allow_broad=False, items=None):
        items = items if items is not None else {}
        first = self._page(search_text, 0, facets=facets)
        if not first:
            log(f"    [{self.key}] Workday 首页失败: {search_text!r}")
            return items, None
        total = int(first.get("total") or 0)
        if not facets and search_text and total > self.max_total() and not allow_broad:
            log(f"    [{self.key}] 关键词 {search_text!r} total={total} 过宽，跳过（--allow-broad 可强制）")
            # The query was not enumerated; use the existing incomplete marker
            # so other successful queries cannot certify company-wide coverage.
            return items, None, first
        hit = tag or search_text or "board"
        def absorb(js):
            for jp in js.get("jobPostings", []):
                bf = jp.get("bulletFields") or []
                req = bf[0] if bf else None
                path = jp.get("externalPath") or ""
                if not req:
                    m = re.search(r"_([A-Z]{1,3}\d{4,})$", path)
                    req = m.group(1) if m else path
                if req in items:
                    if hit not in items[req]["list_hits"]:
                        items[req]["list_hits"].append(hit)
                    continue
                items[req] = _item(req, req, jp.get("title"), jp.get("locationsText"), jp.get("postedOn"),
                                   f"https://{self.host}/en-US/{self.site}{path}", detail_ref=path,
                                   list_hits=[hit], list_meta={"bulletFields": bf})
        absorb(first)
        off = 20
        while off < total:
            jitter(*self.jitter)
            js = self._page(search_text, off, facets=facets)
            if not js:
                log(f"    [{self.key}] Workday 翻页失败 offset={off} ({search_text!r})")
                return items, None, first
            absorb(js)
            off += 20
        return items, total, first

    def list_jobs(self, allow_broad=False):
        items, facets_dump = {}, None
        totals = {}
        for kw in self.keywords():
            res = self._enumerate(kw, allow_broad=allow_broad, items=items)
            items = res[0]
            totals[kw] = res[1]
            if facets_dump is None and len(res) > 2 and res[2]:
                facets_dump = res[2].get("facets")
            log(f"    [{self.key}] {kw!r}: total={res[1]} 累计去重 {len(items)}")
        # 可选：按 facet 分片拉整板（绕过 total 2000 封顶）
        for name, af in (self.co.get("applied_facets") or {}).items():
            res = self._enumerate("", facets=af, tag=f"facet:{name}", allow_broad=True, items=items)
            items = res[0]
            totals[f"facet:{name}"] = res[1]
            log(f"    [{self.key}] facet {name}: total={res[1]} 累计去重 {len(items)}")
        if facets_dump is not None:
            write_json(os.path.join(raw_dir(self.key), "_facets.json"), facets_dump)
        write_json(os.path.join(raw_dir(self.key), "_totals.json"), totals)
        return list(items.values())

    def fetch_detail(self, item):
        path = item.get("detail_ref") or ""
        r = fetch(f"{self.base}{path}", headers=self.headers)
        return r.json if r.json else None

    def normalize(self, item, raw):
        jp = (raw or {}).get("jobPostingInfo") or {}
        org = ((raw or {}).get("hiringOrganization") or {}).get("name", "")
        locs = [jp.get("location")] if jp.get("location") else []
        extra_locs = jp.get("additionalLocations") or []
        if isinstance(extra_locs, str):
            extra_locs = [extra_locs]
        locs += [l for l in extra_locs if l]
        return _rec(req_id=jp.get("jobReqId") or item["req_id"], job_id=jp.get("jobReqId") or item["id"],
                    title=jp.get("title") or item["title"], org_unit=org, department=org,
                    locations=locs or [item.get("location_raw", "")], location_raw=item.get("location_raw", ""),
                    posted=jp.get("startDate") or "", posted_raw=jp.get("postedOn") or item.get("posted_raw", ""),
                    time_type=jp.get("timeType") or "", can_apply=jp.get("canApply"),
                    url=jp.get("externalUrl") or item["url"], api_url=f"{self.base}{item.get('detail_ref') or ''}",
                    jd_html=jp.get("jobDescription") or "", workplace_type=jp.get("remoteType") or "",
                    extra={"similarJobs": [s.get("title") for s in (raw or {}).get("similarJobs", [])][:8],
                           "jobPostingSiteId": jp.get("jobPostingSiteId")})


# ---------------------------------------------------------------- Greenhouse

class GreenhouseAdapter(Adapter):
    kind = "greenhouse"
    workers = 1

    def list_jobs(self, **_):
        slug = self.co["slug"]
        r = fetch(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", headers=JSON_HEADERS)
        if not r.json:
            log(f"    [{self.key}] Greenhouse 板拉取失败: {r.err} status={r.status}")
            return []
        jobs = r.json.get("jobs", [])
        write_json(self.board_cache_path(), {"_fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "jobs": jobs})
        out = []
        for j in jobs:
            out.append(_item(j["id"], j["id"], j.get("title"), (j.get("location") or {}).get("name", ""),
                             j.get("first_published") or "", j.get("absolute_url", ""),
                             detail_ref=j["id"], list_hits=["board"],
                             list_meta={"updated_at": j.get("updated_at") or ""}))
        return out

    def fetch_detail(self, item):
        board = read_json(self.board_cache_path(), {}) or {}
        for j in board.get("jobs", []):
            if str(j.get("id")) == item["id"]:
                return j
        slug = self.co["slug"]
        r = fetch(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{item['id']}", headers=JSON_HEADERS)
        return r.json

    def normalize(self, item, raw):
        j = raw or {}
        deps = [d.get("name") for d in j.get("departments", []) if d.get("name")]
        offices = [o.get("name") for o in j.get("offices", []) if o.get("name")]
        loc = (j.get("location") or {}).get("name", "")
        pay = j.get("pay_input_ranges") or None
        return _rec(req_id=str(j.get("id", item["id"])), job_id=str(j.get("id", item["id"])),
                    title=j.get("title", item["title"]), org_unit=(deps[0] if deps else ""),
                    department="; ".join(deps), locations=[loc] + [o for o in offices if o != loc],
                    location_raw=loc, posted=(j.get("first_published") or "")[:10],
                    posted_raw=j.get("first_published") or "",
                    posted_basis="greenhouse.first_published" if j.get("first_published") else "unknown",
                    updated_at=j.get("updated_at") or "", url=j.get("absolute_url", item["url"]),
                    api_url=f"https://boards-api.greenhouse.io/v1/boards/{self.co['slug']}/jobs/{j.get('id')}",
                    jd_html=j.get("content", ""), structured_pay=pay,
                    extra={"metadata": j.get("metadata"), "board": self.co["slug"]})


# ---------------------------------------------------------------- Ashby

class AshbyAdapter(Adapter):
    kind = "ashby"
    workers = 1

    def list_jobs(self, **_):
        org = self.co["slug"]
        r = fetch(f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(org)}?includeCompensation=true", headers=JSON_HEADERS)
        if not r.json:
            log(f"    [{self.key}] Ashby 板拉取失败: {r.err} status={r.status}")
            return []
        jobs = r.json.get("jobs", [])
        write_json(self.board_cache_path(), {"_fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "jobs": jobs})
        return [_item(j["id"], j["id"], j.get("title"), j.get("location", ""), j.get("publishedAt", ""),
                      j.get("jobUrl", ""), detail_ref=j["id"], list_hits=["board"]) for j in jobs]

    def fetch_detail(self, item):
        board = read_json(self.board_cache_path(), {}) or {}
        for j in board.get("jobs", []):
            if str(j.get("id")) == item["id"]:
                return j
        return None

    def normalize(self, item, raw):
        j = raw or {}
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations", []) or []]
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        return _rec(req_id=str(j.get("id", item["id"])), job_id=str(j.get("id", item["id"])),
                    title=j.get("title", item["title"]), org_unit=j.get("team") or j.get("department") or "",
                    department=j.get("department") or "", locations=[l for l in locs if l],
                    location_raw=j.get("location", ""), posted=(j.get("publishedAt") or "")[:10],
                    posted_raw=j.get("publishedAt", ""), time_type=j.get("employmentType", ""),
                    posted_basis="ashby.publishedAt.latest_publication" if j.get("publishedAt") else "unknown",
                    can_apply=j.get("isListed"), url=j.get("jobUrl", item["url"]),
                    api_url=f"https://api.ashbyhq.com/posting-api/job-board/{self.co['slug']}",
                    jd_html=j.get("descriptionHtml") or "", jd_text=j.get("descriptionPlain") or "",
                    structured_pay=j.get("compensation"), is_remote=j.get("isRemote"),
                    countries_hint=[addr.get("addressCountry")] if addr.get("addressCountry") else [],
                    extra={"org": self.co["slug"], "posting_id": j.get("id"), "applyUrl": j.get("applyUrl"),
                           "addressRegion": addr.get("addressRegion")})


# ---------------------------------------------------------------- Lever

class LeverAdapter(Adapter):
    kind = "lever"
    workers = 1

    def list_jobs(self, **_):
        slug = self.co["slug"]
        r = fetch(f"https://api.lever.co/v0/postings/{slug}?mode=json", headers=JSON_HEADERS)
        if not isinstance(r.json, list):
            log(f"    [{self.key}] Lever 板拉取失败: {r.err} status={r.status}")
            return []
        write_json(self.board_cache_path(), {"_fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "jobs": r.json})
        out = []
        for j in r.json:
            cat = j.get("categories") or {}
            out.append(_item(j["id"], j["id"], j.get("text"), cat.get("location", ""),
                             str(j.get("createdAt", "")), j.get("hostedUrl", ""), detail_ref=j["id"], list_hits=["board"]))
        return out

    def fetch_detail(self, item):
        board = read_json(self.board_cache_path(), {}) or {}
        for j in board.get("jobs", []):
            if str(j.get("id")) == item["id"]:
                return j
        return None

    def normalize(self, item, raw):
        j = raw or {}
        cat = j.get("categories") or {}
        parts = [j.get("description") or ""]
        for l in j.get("lists", []) or []:
            parts.append(f"<h3>{l.get('text', '')}</h3>{l.get('content', '')}")
        parts.append(j.get("additional") or "")
        created = j.get("createdAt")
        posted = time.strftime("%Y-%m-%d", time.gmtime(created / 1000)) if isinstance(created, (int, float)) else ""
        locs = [cat.get("location", "")] + list(cat.get("allLocations") or [])
        return _rec(req_id=str(j.get("id", item["id"])), job_id=str(j.get("id", item["id"])),
                    title=j.get("text", item["title"]), org_unit=cat.get("team") or cat.get("department") or "",
                    department=cat.get("department") or "", locations=[l for l in dict.fromkeys(locs) if l],
                    location_raw=cat.get("location", ""), posted=posted, posted_raw=str(created or ""),
                    time_type=cat.get("commitment", ""), url=j.get("hostedUrl", item["url"]),
                    api_url=f"https://api.lever.co/v0/postings/{self.co['slug']}/{j.get('id')}?mode=json",
                    jd_html="".join(parts), jd_text=j.get("descriptionPlain") or "",
                    structured_pay=j.get("salaryRange"), workplace_type=j.get("workplaceType") or "",
                    extra={"applyUrl": j.get("applyUrl")})


# ---------------------------------------------------------------- amazon.jobs

class AmazonAdapter(Adapter):
    kind = "amazon"
    workers = 2

    def _page(self, kw, offset, limit):
        q = urllib.parse.urlencode({"base_query": kw, "loc_query": "United States", "country": "USA",
                                    "result_limit": limit, "offset": offset, "sort": "recent"})
        h = dict(JSON_HEADERS)
        h["Referer"] = "https://www.amazon.jobs/en/search"
        r = fetch(f"https://www.amazon.jobs/en/search.json?{q}", headers=h)
        return r.json

    def list_jobs(self, **_):
        items = {}
        for kw in self.keywords():
            first = self._page(kw, 0, 100)
            if not first:
                log(f"    [{self.key}] amazon 首页失败 {kw!r}")
                continue
            hits = int(first.get("hits") or 0)
            limit = 100
            if len(first.get("jobs", [])) != min(100, hits):
                limit = 10
                first = self._page(kw, 0, 10) or first
            def absorb(js):
                for j in js.get("jobs", []):
                    jid = str(j.get("id_icims") or j.get("id"))
                    if jid in items:
                        if kw not in items[jid]["list_hits"]:
                            items[jid]["list_hits"].append(kw)
                        continue
                    team = j.get("team")
                    team = team.get("label") if isinstance(team, dict) else (team or "")
                    items[jid] = _item(jid, jid, j.get("title"), j.get("location") or j.get("normalized_location", ""),
                                       j.get("posted_date", ""), "https://www.amazon.jobs" + (j.get("job_path") or ""),
                                       detail_ref=j, list_hits=[kw], list_meta={"team": team})
            absorb(first)
            off = limit
            while off < hits:
                jitter(*self.jitter)
                js = self._page(kw, off, limit)
                if not js:
                    break
                absorb(js)
                off += limit
            log(f"    [{self.key}] {kw!r}: hits={hits} 累计去重 {len(items)}")
        # detail_ref 是整条列表元素；落盘前 fetch_detail 直接返回它
        return list(items.values())

    def fetch_detail(self, item):
        return item.get("detail_ref")

    def normalize(self, item, raw):
        j = raw or {}
        team = j.get("team")
        team = team.get("label") if isinstance(team, dict) else (team or item["list_meta"].get("team", ""))
        jd = (f"<h3>Description</h3>{j.get('description') or ''}"
              f"<h3>Basic Qualifications</h3>{j.get('basic_qualifications') or ''}"
              f"<h3>Preferred Qualifications</h3>{j.get('preferred_qualifications') or ''}")
        posted = j.get("posted_date", "")
        try:
            posted_iso = time.strftime("%Y-%m-%d", time.strptime(re.sub(r"\s+", " ", posted).strip(), "%B %d, %Y"))
        except ValueError:
            posted_iso = ""
        return _rec(req_id=str(j.get("id_icims") or item["id"]), job_id=str(j.get("id") or item["id"]),
                    title=j.get("title", item["title"]), org_unit=team, department=j.get("business_category") or team,
                    locations=[j.get("location") or j.get("normalized_location") or ""],
                    location_raw=j.get("location", ""), posted=posted_iso, posted_raw=posted,
                    time_type=j.get("job_schedule_type", ""), url=item["url"],
                    api_url="https://www.amazon.jobs/en/search.json?base_query=" + urllib.parse.quote(str(j.get("id_icims") or "")),
                    jd_html=jd, extra={"job_category": j.get("job_category"), "company_name": j.get("company_name")})


# ---------------------------------------------------------------- Google careers (HTML)

class GoogleAdapter(Adapter):
    kind = "google"
    workers = 1
    jitter = (1.2, 2.0)
    BASE = "https://www.google.com/about/careers/applications/jobs/results/"

    def _search(self, kw, page):
        q = urllib.parse.urlencode({"q": kw, "location": "United States", "page": page})
        r = fetch(f"{self.BASE}?{q}", headers=HTML_HEADERS, as_json=False, ua=UA_CHROME128)
        if r.final_url and "google.com/sorry" in r.final_url:
            log(f"    [{self.key}] 触发 google.com/sorry，暂停 60 s")
            time.sleep(60)
            return None
        return r.text if r.status == 200 else None

    def list_jobs(self, **_):
        items = {}
        pag_ok = True
        for kw in self.keywords():
            seen_pages = []
            for page in range(1, 41):
                txt = self._search(kw, page)
                if not txt:
                    break
                found = re.findall(r"jobs/results/(\d+)-([a-z0-9-]+)", txt)
                ids = {f[0] for f in found}
                if not ids or (seen_pages and ids == seen_pages[-1]):
                    if seen_pages and ids == seen_pages[-1] and page == 2:
                        pag_ok = False
                    break
                seen_pages.append(ids)
                for jid, slug in dict.fromkeys(found):
                    if jid in items:
                        if kw not in items[jid]["list_hits"]:
                            items[jid]["list_hits"].append(kw)
                        continue
                    title = slug.replace("-", " ")
                    items[jid] = _item(jid, jid, title, "", "", f"{self.BASE}{jid}-{slug}",
                                       detail_ref=f"{jid}-{slug}", list_hits=[kw])
                jitter(*self.jitter)
            log(f"    [{self.key}] {kw!r}: 页数={len(seen_pages)} 累计去重 {len(items)}")
        write_json(os.path.join(raw_dir(self.key), "_pagination.json"), {"pagination_ok": pag_ok})
        return list(items.values())

    def fetch_detail(self, item):
        r = fetch(f"{self.BASE}{item['detail_ref']}", headers=HTML_HEADERS, as_json=False, ua=UA_CHROME128)
        return r.text if r.status == 200 else None

    def normalize(self, item, raw):
        txt = raw or ""
        ld = None
        for m in re.finditer(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', txt, re.S):
            try:
                js = json.loads(html.unescape(m.group(1)))
            except ValueError:
                continue
            cands = js if isinstance(js, list) else [js]
            for c in cands:
                if isinstance(c, dict) and c.get("@type") == "JobPosting":
                    ld = c
                    break
            if ld:
                break
        if ld:
            locs = []
            jl = ld.get("jobLocation") or []
            for l in (jl if isinstance(jl, list) else [jl]):
                a = (l or {}).get("address") or {}
                locs.append(", ".join(x for x in [a.get("addressLocality"), a.get("addressRegion"), a.get("addressCountry")] if x))
            return _rec(req_id=item["id"], job_id=item["id"], title=ld.get("title") or item["title"],
                        org_unit=(ld.get("hiringOrganization") or {}).get("name", ""), locations=locs,
                        location_raw="; ".join(locs), posted=(ld.get("datePosted") or "")[:10],
                        url=item["url"], api_url=item["url"], jd_html=ld.get("description") or "",
                        structured_pay=ld.get("baseSalary"), extra={"validThrough": ld.get("validThrough")})
        # 无 JSON-LD 时回退：<title> 去掉 " — Google Careers"；正文取
        # "Minimum qualifications" 之前 ~2500 字符到 EEO 段之前；地点取 "preferred working location from the following: ..." 列表
        # 或 "City, ST, USA" 模式。
        t = re.search(r"<title>(.*?)</title>", txt, re.S)
        title = html.unescape(t.group(1)) if t else item["title"]
        title = re.sub(r"\s*(—|-|\|)\s*Google Careers.*$", "", title).strip()
        i = txt.find("Minimum qualifications")
        if i < 0:
            i = txt.find("About the job")
        start = max(0, i - 2500) if i >= 0 else 0
        end = len(txt)
        for marker in ("Google is proud to be an equal opportunity", "Google is an equal opportunity", "Equal Opportunity Employer"):
            k = txt.find(marker, i if i >= 0 else 0)
            if k > 0:
                end = min(end, k + 400)
        seg = txt[start:end] if i >= 0 else txt[-60000:]
        plain = html.unescape(re.sub(r"<[^>]+>", " ", seg))
        locs = []
        m = re.search(r"preferred working location from the following:\s*(.+?)\s*\.\s*(?:Minimum|Note|About)", plain, re.S)
        if m:
            locs = [x.strip() for x in re.split(r";", m.group(1)) if x.strip()]
        if not locs:
            wide = html.unescape(re.sub(r"<[^>]+>", " ", txt[max(0, start - 40000):end]))
            locs = list(dict.fromkeys(re.findall(r"([A-Z][A-Za-z .'\-]{2,40}, [A-Z]{2}, (?:USA|United States|Canada|UK|Germany|India|Australia|Israel|Japan|Singapore|Ireland|Switzerland|Poland|Brazil|Taiwan|Korea|Mexico))", wide)))
            locs = [l for l in locs if not l.lower().startswith(("apply", "email", "share"))][:6]
        if not locs and re.search(r"\bRemote\b", plain[:3000]):
            locs = ["Remote"]
        jd_len_ok = i >= 0
        return _rec(req_id=item["id"], job_id=item["id"], title=title, org_unit="Google", url=item["url"], api_url=item["url"],
                    locations=locs, location_raw="; ".join(locs), jd_html=seg if jd_len_ok else "",
                    extra={"ldjson": False, "page_bytes": len(txt), "jd_located": jd_len_ok})


# ---------------------------------------------------------------- Eightfold PCSX

class EightfoldAdapter(Adapter):
    kind = "eightfold"
    workers = 5

    def __init__(self, co, cfg):
        super().__init__(co, cfg)
        ef = co["eightfold"]
        self.host, self.domain = ef["host"], ef["domain"]
        self.base = f"https://{self.host}"
        self.headers = dict(JSON_HEADERS)
        self.ua = UA_CHROME140 if ef.get("referer", True) else UA_CHROME128
        if ef.get("referer", True):
            self.headers["Referer"] = f"{self.base}/careers?domain={self.domain}"

    def _search(self, query, start, num=10):
        q = {"domain": self.domain, "start": start, "num": num, "sort_by": "timestamp"}
        if query:
            q["query"] = query
        r = fetch(f"{self.base}/api/pcsx/search?{urllib.parse.urlencode(q)}", headers=self.headers, ua=self.ua)
        return (r.json or {}).get("data") if r.json else None

    def list_jobs(self, full_board=False, **_):
        items = {}
        queries = [""] if full_board else self.keywords()
        for kw in queries:
            first = self._search(kw, 0)
            if not first:
                log(f"    [{self.key}] Eightfold 首页失败 {kw!r}")
                continue
            total = int(first.get("count") or 0)
            hit = kw or "board"
            def absorb(d):
                for p in d.get("positions", []):
                    pid = str(p.get("id"))
                    if pid in items:
                        if hit not in items[pid]["list_hits"]:
                            items[pid]["list_hits"].append(hit)
                        continue
                    locs = p.get("locations") or []
                    items[pid] = _item(pid, p.get("displayJobId") or pid, p.get("name"), "; ".join(locs),
                                       str(p.get("postedTs") or ""), p.get("positionUrl") or f"{self.base}/careers/job/{pid}",
                                       detail_ref=pid, list_hits=[hit],
                                       list_meta={"department": p.get("department"), "standardizedLocations": p.get("standardizedLocations")})
            absorb(first)
            for s in range(10, total + 10, 10):
                jitter(0.1, 0.5)
                d = self._search(kw, s)
                if not d:
                    log(f"    [{self.key}] Eightfold 翻页失败 start={s}")
                    break
                absorb(d)
            log(f"    [{self.key}] {kw!r}: count={total} 累计去重 {len(items)}")
        return list(items.values())

    def fetch_detail(self, item):
        r = fetch(f"{self.base}/api/apply/v2/jobs/{item['id']}?domain={self.domain}", headers=self.headers, ua=self.ua)
        js = r.json
        if js and js.get("job_description"):
            return js
        return None

    def normalize(self, item, raw):
        j = raw or {}
        ts = j.get("t_create")
        posted = time.strftime("%Y-%m-%d", time.gmtime(ts)) if isinstance(ts, (int, float)) and ts > 0 else ""
        tt = ((j.get("custom_JD") or {}).get("data_fields") or {}).get("timeType") or []
        return _rec(req_id=j.get("display_job_id") or item["req_id"], job_id=str(j.get("id") or item["id"]),
                    title=j.get("name") or item["title"], org_unit=j.get("business_unit") or j.get("department") or "",
                    department=j.get("department") or "", locations=list(j.get("locations") or []),
                    location_raw=j.get("location", ""), posted=posted, posted_raw=str(ts or ""),
                    time_type=tt[0] if tt else "", url=j.get("canonicalPositionUrl") or item["url"],
                    api_url=f"{self.base}/api/apply/v2/jobs/{item['id']}?domain={self.domain}",
                    jd_html=j.get("job_description") or "", workplace_type=j.get("work_location_option") or "",
                    extra={"apply_redirect_url": j.get("apply_redirect_url"), "ats_job_id": j.get("ats_job_id")})


# ---------------------------------------------------------------- SAP SuccessFactors（RSS / urlset sitemap）

class SuccessFactorsAdapter(Adapter):
    kind = "successfactors"
    workers = 1
    jitter = (0.8, 1.5)
    NS = {"g": "http://base.google.com/ns/1.0", "sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    def __init__(self, co, cfg):
        super().__init__(co, cfg)
        self.host = co["successfactors"]["host"]

    def list_jobs(self, **_):
        r = fetch(f"https://{self.host}/sitemap.xml", headers=HTML_HEADERS, as_json=False)
        if r.status != 200 or not r.text.strip():
            log(f"    [{self.key}] sitemap 拉取失败 status={r.status} {r.err}")
            return []
        with open(os.path.join(raw_dir(self.key), "_rss.xml"), "w", encoding="utf-8") as f:
            f.write(r.text)
        try:
            root = ET.fromstring(r.text.encode("utf-8"))
        except ET.ParseError as e:
            log(f"    [{self.key}] sitemap 解析失败: {e}")
            return []
        items = []
        if root.tag.lower().endswith("rss"):
            entries = []
            for it in root.iter("item"):
                d = {c.tag.split('}')[-1]: (c.text or "") for c in it}
                entries.append(d)
                jid = d.get("id") or re.sub(r".*/(\d+)/?$", r"\1", d.get("link", ""))
                items.append(_item(jid, jid, d.get("title"), d.get("location", ""), d.get("pubDate", ""),
                                   d.get("link", ""), detail_ref=jid, list_hits=["rss"]))
            write_json(self.board_cache_path(), {"kind": "rss", "jobs": entries})
        else:
            urls = [u.text for u in root.iter() if u.tag.endswith("loc") and u.text and "/job/" in u.text]
            for u in urls:
                jid = re.sub(r".*/(\d+)/?$", r"\1", u.rstrip("/"))
                items.append(_item(jid, jid, "", "", "", u, detail_ref=u, list_hits=["urlset"]))
            write_json(self.board_cache_path(), {"kind": "urlset", "jobs": [{"link": u} for u in urls]})
        return items

    def fetch_detail(self, item):
        board = read_json(self.board_cache_path(), {}) or {}
        if board.get("kind") == "rss":
            for e in board.get("jobs", []):
                if (e.get("id") or re.sub(r".*/(\d+)/?$", r"\1", e.get("link", ""))) == item["id"]:
                    return e
            return None
        r = fetch(item["detail_ref"], headers=HTML_HEADERS, as_json=False)
        return r.text if r.status == 200 else None

    def normalize(self, item, raw):
        if isinstance(raw, dict):
            return _rec(req_id=item["id"], job_id=item["id"], title=raw.get("title", item["title"]),
                        org_unit=raw.get("job_function", ""), department=raw.get("job_function", ""),
                        locations=[raw.get("location", "")], location_raw=raw.get("location", ""),
                        posted=raw.get("pubDate", "")[:16], url=raw.get("link", item["url"]), api_url=f"https://{self.host}/sitemap.xml",
                        jd_html=raw.get("description", ""), extra={"expiration": raw.get("expiration_date")})
        txt = raw or ""
        t = re.search(r"<title>(.*?)</title>", txt, re.S)
        m = re.search(r'class="jobdescription"[^>]*>(.*?)</div>\s*</div>', txt, re.S)
        return _rec(req_id=item["id"], job_id=item["id"], title=html.unescape(t.group(1)).strip() if t else item["title"],
                    url=item["url"], api_url=item["url"], jd_html=m.group(1) if m else txt)


# ---------------------------------------------------------------- Phenom（GE HealthCare / J&J 若确认）

class PhenomAdapter(Adapter):
    kind = "phenom"
    workers = 1
    jitter = (0.8, 1.5)

    def __init__(self, co, cfg):
        super().__init__(co, cfg)
        self.base = co["phenom"]["base"].rstrip("/")

    def _ddo(self, url):
        r = fetch(url, headers=HTML_HEADERS, as_json=False)
        if r.status != 200:
            return None
        m = re.search(r"phApp\.ddo\s*=\s*(\{.*?\});\s*\n", r.text, re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except ValueError:
            return None

    def list_jobs(self, **_):
        items = {}
        for kw in self.keywords():
            off = 0
            while True:
                q = urllib.parse.urlencode({"keywords": kw, "from": off, "s": 1})
                ddo = self._ddo(f"{self.base}/search-results?{q}")
                data = ((ddo or {}).get("eagerLoadRefineSearch") or {}).get("data") or {}
                jobs = data.get("jobs") or []
                if not jobs:
                    break
                for j in jobs:
                    jid = str(j.get("jobId"))
                    if jid in items:
                        if kw not in items[jid]["list_hits"]:
                            items[jid]["list_hits"].append(kw)
                        continue
                    slug = j.get("jobSeqNo") or jid
                    items[jid] = _item(jid, jid, j.get("title"), j.get("cityState") or j.get("location", ""),
                                       j.get("postedDate", ""), f"{self.base}/job/{jid}/{slug}",
                                       detail_ref=f"{self.base}/job/{jid}/{slug}", list_hits=[kw])
                total = int(data.get("totalHits") or 0)
                off += len(jobs)
                if off >= total:
                    break
                jitter(*self.jitter)
            log(f"    [{self.key}] {kw!r}: 累计去重 {len(items)}")
        return list(items.values())

    def fetch_detail(self, item):
        ddo = self._ddo(item["detail_ref"])
        return ((ddo or {}).get("jobDetail") or {}).get("data", {}).get("job")

    def normalize(self, item, raw):
        j = raw or {}
        return _rec(req_id=str(j.get("reqId") or j.get("jobId") or item["id"]), job_id=str(j.get("jobId") or item["id"]),
                    title=j.get("title", item["title"]), org_unit=j.get("category") or "", department=j.get("category") or "",
                    locations=[j.get("cityState") or j.get("location") or item["location_raw"]],
                    location_raw=item["location_raw"], posted=(j.get("postedDate") or "")[:10], url=item["url"],
                    api_url=item["detail_ref"], jd_html=j.get("description") or "")


# ---------------------------------------------------------------- Workable（v1 widget GET；v3 POST 会被 Cloudflare 429）

class WorkableAdapter(Adapter):
    kind = "workable"
    workers = 1

    def list_jobs(self, **_):
        slug = self.co["slug"]
        r = fetch(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true", headers=JSON_HEADERS)
        if not r.json:
            log(f"    [{self.key}] Workable 拉取失败: {r.err} status={r.status}")
            return []
        jobs = r.json.get("jobs", [])
        write_json(self.board_cache_path(), {"_fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "jobs": jobs})
        return [_item(j.get("shortcode") or j.get("id"), j.get("shortcode") or j.get("id"), j.get("title"),
                      ", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x), j.get("published_on", ""),
                      j.get("url") or j.get("application_url", ""), detail_ref=j.get("shortcode"), list_hits=["board"]) for j in jobs]

    def fetch_detail(self, item):
        board = read_json(self.board_cache_path(), {}) or {}
        for j in board.get("jobs", []):
            if str(j.get("shortcode") or j.get("id")) == item["id"]:
                return j
        return None

    def normalize(self, item, raw):
        j = raw or {}
        loc = ", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x)
        jd = (j.get("description") or "") + "<h3>Requirements</h3>" + (j.get("requirements") or "") + "<h3>Benefits</h3>" + (j.get("benefits") or "")
        return _rec(req_id=str(j.get("shortcode") or item["id"]), job_id=str(j.get("shortcode") or item["id"]),
                    title=j.get("title", item["title"]), org_unit=j.get("department") or "", department=j.get("department") or "",
                    locations=[loc], location_raw=loc, posted=(j.get("published_on") or "")[:10], time_type=j.get("employment_type", ""),
                    url=j.get("url") or item["url"], api_url=f"https://apply.workable.com/api/v1/widget/accounts/{self.co['slug']}?details=true",
                    jd_html=jd, workplace_type="remote" if j.get("telecommuting") else "", countries_hint=[j.get("country_code")] if j.get("country_code") else [])


# ---------------------------------------------------------------- Apple（jobs.apple.com /api/v1/search；body 必须带 format 对象，否则 totalRecords=0）

class AppleAdapter(Adapter):
    kind = "apple_api"
    workers = 2
    BASE = "https://jobs.apple.com"

    def _search(self, kw, page):
        body = {"query": kw, "filters": {"locations": ["postLocation-USA"]}, "page": page, "locale": "en-us", "sort": "newest",
                "format": {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}}
        h = dict(JSON_HEADERS)
        h["Content-Type"] = "application/json"
        h["Referer"] = f"{self.BASE}/en-us/search"
        r = fetch(f"{self.BASE}/api/v1/search", method="POST", body=body, headers=h)
        return r.json

    def list_jobs(self, **_):
        items = {}
        for kw in self.keywords():
            page, total = 1, None
            while True:
                js = self._search(kw, page)
                if not js:
                    break
                res = js.get("res") or js
                jobs = res.get("searchResults") or res.get("jobs") or []
                total = res.get("totalRecords") or total
                if not jobs:
                    break
                for j in jobs:
                    jid = str(j.get("positionId") or j.get("id"))
                    if jid in items:
                        if kw not in items[jid]["list_hits"]:
                            items[jid]["list_hits"].append(kw)
                        continue
                    locs = j.get("locations") or []
                    loc = "; ".join((l.get("name") or "") for l in locs) if isinstance(locs, list) else str(locs)
                    items[jid] = _item(jid, j.get("reqId") or jid, j.get("postingTitle") or j.get("title"), loc, j.get("postingDate", ""),
                                       f"{self.BASE}/en-us/details/{jid}", detail_ref=jid, list_hits=[kw], list_meta={"team": j.get("team")})
                if total and page * 20 >= int(total):
                    break
                page += 1
                if page > 60:
                    break
                jitter(*self.jitter)
            log(f"    [{self.key}] {kw!r}: total={total} 累计去重 {len(items)}")
        return list(items.values())

    def fetch_detail(self, item):
        h = dict(JSON_HEADERS)
        h["Referer"] = f"{self.BASE}/en-us/details/{item['id']}"
        r = fetch(f"{self.BASE}/api/v1/jobDetails/{item['id']}?locale=en-us", headers=h)
        return r.json

    def normalize(self, item, raw):
        j = (raw or {}).get("res") or raw or {}
        locs = j.get("locations") or []
        locs = [(l.get("name") or "") for l in locs] if isinstance(locs, list) else [str(locs)]
        team = j.get("team") or item["list_meta"].get("team") or {}
        team = team.get("teamName") if isinstance(team, dict) else str(team)
        parts = []
        for k, h in (("jobSummary", "Summary"), ("description", "Description"), ("keyQualifications", "Key Qualifications"),
                     ("minimumQualifications", "Minimum Qualifications"), ("preferredQualifications", "Preferred Qualifications"),
                     ("educationAndExperience", "Education & Experience"), ("additionalRequirements", "Additional Requirements")):
            v = j.get(k)
            if isinstance(v, list):
                v = "<ul>" + "".join(f"<li>{x}</li>" for x in v) + "</ul>"
            if v:
                parts.append(f"<h3>{h}</h3>{v}")
        return _rec(req_id=str(j.get("reqId") or item["req_id"]), job_id=str(j.get("positionId") or item["id"]),
                    title=j.get("postingTitle") or item["title"], org_unit=team, department=team,
                    locations=locs or [item["location_raw"]], location_raw=item["location_raw"], posted=(j.get("postingDate") or "")[:10],
                    time_type=j.get("employmentType", ""), url=item["url"], api_url=f"{self.BASE}/api/v1/jobDetails/{item['id']}",
                    jd_html="".join(parts), workplace_type=j.get("homeOffice") and "remote" or "", countries_hint=["US"])


# ---------------------------------------------------------------- Oracle Cloud HCM（careers.oracle.com；expand=requisitionList 必带否则无行）

class OracleHCMAdapter(Adapter):
    kind = "oracle_hcm"
    workers = 2

    def __init__(self, co, cfg):
        super().__init__(co, cfg)
        o = co["oracle_hcm"]
        self.host, self.site = o["host"], o["site"]
        self.base = f"https://{self.host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        self.loc_facet = o.get("us_location_facet", "300000000149325")

    def _page(self, kw, offset, limit=100):
        finder = f'findReqs;siteNumber={self.site},keyword="{kw}",selectedLocationsFacet={self.loc_facet},limit={limit},offset={offset},sortBy=POSTING_DATES_DESC'
        q = urllib.parse.urlencode({"onlyData": "true", "expand": "requisitionList.secondaryLocations,flexFieldsFacet.values", "finder": finder}, safe=';=,"')
        r = fetch(f"{self.base}?{q}", headers=JSON_HEADERS)
        return r.json

    def list_jobs(self, **_):
        items = {}
        for kw in self.keywords():
            off = 0
            while True:
                js = self._page(kw, off)
                rows = ((js or {}).get("items") or [{}])[0].get("requisitionList") or []
                total = ((js or {}).get("items") or [{}])[0].get("TotalJobsCount") or 0
                for j in rows:
                    jid = str(j.get("Id"))
                    if jid in items:
                        if kw not in items[jid]["list_hits"]:
                            items[jid]["list_hits"].append(kw)
                        continue
                    items[jid] = _item(jid, jid, j.get("Title"), j.get("PrimaryLocation") or "", j.get("PostedDate", ""),
                                       f"https://careers.oracle.com/jobs/#en/sites/jobsearch/job/{jid}", detail_ref=jid, list_hits=[kw],
                                       list_meta={"secondary": [x.get("Name") for x in (j.get("secondaryLocations") or [])]})
                off += len(rows)
                if not rows or off >= int(total):
                    break
                jitter(*self.jitter)
            log(f"    [{self.key}] {kw!r}: total={total} 累计去重 {len(items)}")
        return list(items.values())

    def fetch_detail(self, item):
        q = urllib.parse.urlencode({"onlyData": "true", "expand": "all", "finder": f"ById;Id=\"{item['id']}\",siteNumber={self.site}"}, safe=';=,"')
        r = fetch(f"https://{self.host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?{q}", headers=JSON_HEADERS)
        items = ((r.json or {}).get("items") or [])
        return items[0] if items else None

    def normalize(self, item, raw):
        j = raw or {}
        locs = [j.get("PrimaryLocation") or item["location_raw"]] + list(item["list_meta"].get("secondary") or [])
        jd = (j.get("ExternalDescriptionStr") or j.get("ExternalDescription") or "") + "<h3>Qualifications</h3>" + (j.get("ExternalQualificationsStr") or j.get("ExternalQualifications") or "") + "<h3>Responsibilities</h3>" + (j.get("ExternalResponsibilitiesStr") or j.get("ExternalResponsibilities") or "")
        return _rec(req_id=str(j.get("Id") or item["id"]), job_id=str(j.get("Id") or item["id"]), title=j.get("Title") or item["title"],
                    org_unit=j.get("Organization") or j.get("JobFamily") or "", department=j.get("JobFamily") or "",
                    locations=[l for l in locs if l], location_raw=item["location_raw"], posted=(j.get("PostedDate") or "")[:10],
                    time_type=j.get("JobSchedule", ""), url=item["url"], api_url=self.base, jd_html=jd,
                    workplace_type=j.get("WorkplaceType") or "", countries_hint=["US"])


# ---------------------------------------------------------------- Gem（api.gem.com/job_board/v0/<slug>/job_posts/，字段与 Greenhouse 近似）

class GemAdapter(Adapter):
    kind = "gem"
    workers = 1

    def list_jobs(self, **_):
        slug = self.co["slug"]
        r = fetch(f"https://api.gem.com/job_board/v0/{urllib.parse.quote(slug)}/job_posts/", headers=JSON_HEADERS)
        if not isinstance(r.json, list):
            log(f"    [{self.key}] Gem 板拉取失败: {r.err} status={r.status}")
            return []
        write_json(self.board_cache_path(), {"_fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "jobs": r.json})
        return [_item(j["id"], j.get("requisition_id") or j["id"], j.get("title"), (j.get("location") or {}).get("name", ""),
                      j.get("first_published_at", ""), j.get("absolute_url", ""), detail_ref=j["id"], list_hits=["board"]) for j in r.json]

    def fetch_detail(self, item):
        board = read_json(self.board_cache_path(), {}) or {}
        for j in board.get("jobs", []):
            if str(j.get("id")) == item["id"]:
                return j
        return None

    def normalize(self, item, raw):
        j = raw or {}
        deps = [d.get("name") for d in (j.get("departments") or []) if isinstance(d, dict) and d.get("name")]
        loc = (j.get("location") or {}).get("name", "")
        offices = [((o.get("location") or {}).get("name") or o.get("name")) for o in (j.get("offices") or []) if isinstance(o, dict)]
        return _rec(req_id=str(j.get("requisition_id") or item["id"]), job_id=str(j.get("id") or item["id"]),
                    title=j.get("title", item["title"]), org_unit=(deps[0] if deps else ""), department="; ".join(deps),
                    locations=[x for x in dict.fromkeys([loc] + offices) if x], location_raw=loc,
                    posted=(j.get("first_published_at") or "")[:10], posted_raw=j.get("updated_at", ""),
                    time_type=j.get("employment_type", ""), url=j.get("absolute_url", item["url"]),
                    api_url=f"https://api.gem.com/job_board/v0/{self.co['slug']}/job_posts/",
                    jd_html=j.get("content") or "", jd_text=j.get("content_plain") or "",
                    workplace_type=j.get("location_type") or "", is_remote=(j.get("location_type") == "remote"))


# ---------------------------------------------------------------- 浏览器兜底记录

class ManualAdapter(Adapter):
    kind = "manual"
    workers = 1

    def _file(self):
        return os.path.join(MANUAL_DIR, f"{self.key}.json")

    def list_jobs(self, **_):
        d = read_json(self._file())
        if not d:
            log(f"    [{self.key}] 无 manual 记录: {self._file()}")
            return []
        out = []
        for j in d.get("jobs", []):
            jid = j.get("req_id") or j.get("url")
            out.append(_item(jid, jid, j.get("title"), j.get("location_raw", ""), j.get("posted_raw", ""),
                             j.get("url", ""), detail_ref=j, list_hits=["manual"],
                             list_meta={"list_total_seen": d.get("list_total_seen")}))
        return out

    def fetch_detail(self, item):
        return item.get("detail_ref")

    def normalize(self, item, raw):
        j = raw or {}
        if not j.get("jd_text"):
            log(f"    [{self.key}] manual 记录缺 jd_text: {item['id']}")
        return _rec(req_id=str(j.get("req_id") or item["id"]), job_id=str(j.get("req_id") or item["id"]),
                    title=j.get("title", item["title"]), org_unit=j.get("department", ""), department=j.get("department", ""),
                    locations=[j.get("location_raw", "")], location_raw=j.get("location_raw", ""),
                    posted=j.get("posted", ""), posted_raw=j.get("posted_raw", ""), url=j.get("url", ""),
                    api_url=j.get("apply_url", ""), jd_text=j.get("jd_text", ""), structured_pay=None,
                    extra={"pay_raw": j.get("pay_raw"), "notes": j.get("notes")})


ADAPTERS = {
    "workday": WorkdayAdapter, "greenhouse": GreenhouseAdapter, "ashby": AshbyAdapter, "lever": LeverAdapter,
    "amazon": AmazonAdapter, "google": GoogleAdapter, "eightfold": EightfoldAdapter,
    "successfactors": SuccessFactorsAdapter, "phenom": PhenomAdapter, "manual": ManualAdapter,
    "workable": WorkableAdapter, "apple_api": AppleAdapter, "oracle_hcm": OracleHCMAdapter, "gem": GemAdapter,
}


EXT_MODULES = ("adapters_ext_a", "adapters_ext_b", "adapters_ext_c")   # 各扩展模块暴露 EXT_ADAPTERS = {kind: cls}


def _load_ext():
    import importlib
    for m in EXT_MODULES:
        try:
            mod = importlib.import_module(m)
        except ImportError:
            continue
        for k, v in getattr(mod, "EXT_ADAPTERS", {}).items():
            ADAPTERS.setdefault(k, v)


def make_adapter(co, cfg):
    _load_ext()
    kind = co.get("ats")
    if co.get("enumeration") == "manual" or co.get("browser_fallback") and kind in (None, "unknown"):
        kind = "manual"
    cls = ADAPTERS.get(kind)
    if not cls:
        return None
    return cls(co, cfg)
