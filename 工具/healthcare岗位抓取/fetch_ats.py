#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""healthcare 岗位抓取 —— 主 CLI（纯 Python 3 标准库）。

用法：
    python3 fetch_ats.py list   [--company KEY ...] [--track A|B|C ...] [--ats workday ...] [--allow-broad]
    python3 fetch_ats.py jd     [--company ...] [--workers N] [--force] [--curl] [--max-details N]
    python3 fetch_ats.py build  [--company ...]          # raw → out/jobs_all.json + out/healthcare_jobs_全量.csv
    python3 fetch_ats.py status                          # 每家：ats/status/listed/fetched/missing
    python3 fetch_ats.py all    [--company ...]          # list + jd + build
    python3 fetch_ats.py full-board --company example  # Eightfold 整板枚举（一次性兜底）

产物：
    raw/<key>/_list.json        本次枚举（旧的挪到 _list_prev.json，打印 新增/消失）
    raw/<key>/<id>.json         逐 req 原始响应（整板 ATS 内容变化时更新；其他已存在则跳过，--force 重抓）
    out/jobs_all.json           统一 schema（含 excluded/排除理由/funnel_gate 三个占位字段，由 funnel.py 回写）
    out/healthcare_jobs_全量.csv 前 14 列与 微软全量 CSV 逐字一致
    logs/run_<stamp>.log

must_include 回归：companies.json 里登记的 id 若不在本次列表里，打 [regression] 并以 exit 2 结束。
"""
import argparse
import csv
import datetime
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import common
from common import (OUT_DIR, STATS, FLAGS, load_companies, load_raw, log, now_iso, raw_dir, read_json,
                    save_raw, select_companies, write_json)
from adapters import make_adapter

CSV_COLUMNS = [
    "req_id", "title", "region", "location", "department", "work_site", "eff_min_years", "min_years_required",
    "phd_alt_path", "restriction_flags", "pay_range", "posted", "url", "job_id",
    "company", "track", "role_family", "level_tag", "source_authority", "fetched_at",
    "uid", "ats", "org_unit", "state", "remote_us", "can_apply", "pay_low", "pay_high", "healthcare_hits",
    "list_hits", "stretch", "funnel_gate", "excluded", "排除理由", "jd_len",
]


def _list_path(key):
    return os.path.join(raw_dir(key), "_list.json")


def cmd_list(cfg, companies, allow_broad=False):
    regressions = []
    for co in companies:
        ad = make_adapter(co, cfg)
        if not ad:
            log(f"[list] {co['key']}: ats={co.get('ats')} 无适配器，跳过（请检查本地 registry 的 ats 配置）")
            continue
        log(f"[list] {co['key']} ({ad.kind}) ...")
        try:
            items = ad.list_jobs(allow_broad=allow_broad) if ad.kind in ("workday",) else ad.list_jobs()
        except Exception as e:  # noqa
            log(f"[list] {co['key']} 失败: {type(e).__name__}: {e}")
            continue
        p = _list_path(co["key"])
        prev = read_json(p, None)
        if prev:
            try:
                os.replace(p, p.replace("_list.json", "_list_prev.json"))
            except OSError:
                pass
            old_ids = {i["id"] for i in prev.get("items", [])}
            new_ids = {i["id"] for i in items}
            log(f"[list] {co['key']}: 新增 {len(new_ids - old_ids)} / 消失 {len(old_ids - new_ids)} / 现有 {len(new_ids)}")
        else:
            log(f"[list] {co['key']}: 首次枚举 {len(items)} 条")
        write_json(p, {"_listed_at": now_iso(), "adapter": ad.kind, "company": co["key"], "items": items})
        ids = {i["id"] for i in items} | {i["req_id"] for i in items}
        for mi in co.get("must_include", []):
            if str(mi) not in ids:
                log(f"[regression] {co['key']} 列表缺少 must_include id {mi}")
                regressions.append((co["key"], mi))
    return regressions


def _board_timestamp(value):
    """Board caches use local ISO timestamps; preserve them instead of dating cache reads."""
    try:
        if "T" not in str(value):
            return None
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _refresh_board_details(ad, items, max_details=None, force=False):
    """Greenhouse/Ashby/Lever already fetched the full body during list_jobs()."""
    try:
        board = read_json(ad.board_cache_path(), {})
        if not isinstance(board, dict) or not isinstance(board.get("jobs"), list):
            raise ValueError("整板缓存格式无效")
        fetched_at = board.get("_fetched_at")
        fetched = _board_timestamp(fetched_at)
        if fetched is None:
            raise ValueError("整板缓存缺少有效抓取时间")
        by_id = {str(row["id"]): row for row in board["jobs"]
                 if isinstance(row, dict) and row.get("id") is not None}
    except (OSError, ValueError, TypeError) as e:
        log(f"[jd] {ad.key}: 保留旧 raw，无法读取整板缓存：{e}")
        return
    changed = unchanged = unavailable = detail_requests = 0
    for item in items:
        raw = by_id.get(item["id"])
        title_key = "text" if ad.kind == "lever" else "title"
        body_keys = {"greenhouse": ("content",), "ashby": ("descriptionHtml", "descriptionPlain"),
                     "lever": ("description", "descriptionPlain", "lists")}[ad.kind]
        if raw is None or not raw.get(title_key) or not any(raw.get(key) for key in body_keys):
            unavailable += 1
            continue
        try:
            path = common.raw_path(ad.key, item["id"])
            old_bytes = None
            if os.path.exists(path):
                with open(path, "rb") as f:
                    old_bytes = f.read()
            try:
                old = json.loads(old_bytes) if old_bytes is not None else None
                if old_bytes is not None and (not isinstance(old, dict) or not isinstance(old.get("body"), dict)):
                    raise ValueError("已有 raw 格式无效")
            except (ValueError, UnicodeError):
                if not force:
                    raise
                old = None
            previous = _board_timestamp((old or {}).get("_fetched_at"))
            if old and ((previous is None and not force) or (previous is not None and fetched < previous)):
                unavailable += 1
                continue
            old_body = (old or {}).get("body") or {}
            # A Greenhouse board omits fields exposed only by its detail endpoint.
            # Compare the board's fields so an unchanged enriched response does not trigger a request.
            detail_fields = set(old_body) - set(raw) if ad.kind == "greenhouse" else set()
            same_body = old_body == raw or (detail_fields and all(k in old_body and old_body[k] == v for k, v in raw.items()))
            if old and previous is not None and same_body:
                unchanged += 1
                continue
            if max_details and changed >= max_details:
                break
            record_fetched_at = fetched_at
            if detail_fields:
                detail_requests += 1
                slug = urllib.parse.quote(ad.co["slug"], safe="")
                job_id = urllib.parse.quote(item["id"], safe="")
                response = common.fetch(
                    f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}?pay_transparency=true&questions=true",
                    headers=common.JSON_HEADERS)
                detail = response.json
                if (response.status != 200 or not isinstance(detail, dict)
                        or str(detail.get("id")) != item["id"] or not detail.get("title")
                        or not detail.get("content") or not detail_fields.issubset(detail)):
                    raise ValueError("Greenhouse 详情未成功返回全部既有详情字段")
                raw = detail
                record_fetched_at = now_iso()
            record = {"_adapter": ad.kind, "_fetched_at": record_fetched_at,
                      "_url": item.get("url", ""), "_list_item": item, "body": raw}
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(path),
                                                 prefix=".board-refresh-", suffix=".tmp", delete=False) as f:
                    temp_path = f.name
                    json.dump(record, f, ensure_ascii=False)
                if old_bytes is not None:
                    history = os.path.join(os.path.dirname(path), "_history")
                    os.makedirs(history, exist_ok=True)
                    digest = hashlib.sha256(old_bytes).hexdigest()
                    backup = os.path.join(history, f"{os.path.splitext(os.path.basename(path))[0]}_{digest}.json")
                    try:
                        with open(backup, "xb") as f:
                            f.write(old_bytes)
                    except FileExistsError:
                        with open(backup, "rb") as f:
                            if f.read() != old_bytes:
                                raise ValueError("已有历史备份内容不匹配")
                os.replace(temp_path, path)
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.unlink(temp_path)
            changed += 1
        except (OSError, ValueError, TypeError) as e:
            unavailable += 1
            log(f"    [{ad.key}] {item['id']} 保留旧 raw，整板刷新失败：{e}")
    log(f"[jd] {ad.key}: 整板新增/变更 {changed}，未变 {unchanged}，无法刷新 {unavailable}，Greenhouse 详情请求 {detail_requests}")


def cmd_jd(cfg, companies, workers=None, force=False, max_details=None):
    for co in companies:
        ad = make_adapter(co, cfg)
        if not ad:
            continue
        lst = read_json(_list_path(co["key"]), None)
        if not lst:
            log(f"[jd] {co['key']}: 无 _list.json，先跑 list")
            continue
        items = lst["items"]
        if ad.kind in ("greenhouse", "ashby", "lever"):
            # --force may repair invalid old raw; valid newer evidence is still protected.
            _refresh_board_details(ad, items, max_details=max_details, force=force)
            continue
        todo = [i for i in items if force or not os.path.exists(common.raw_path(co["key"], i["id"]))]
        if max_details:
            todo = todo[:max_details]
        log(f"[jd] {co['key']}: 待抓 {len(todo)}（已有 {len(items) - len(todo)}）")
        w = min(ad.workers, workers or ad.workers)
        ok = fail = 0

        def grab(item):
            common.jitter(*ad.jitter)
            try:
                raw = ad.fetch_detail(item)
            except Exception as e:  # noqa
                log(f"    [{co['key']}] {item['id']} 异常 {type(e).__name__}: {e}")
                return False
            if raw is None or raw == "":
                return False
            save_raw(co["key"], item["id"], raw, ad.kind, item.get("url", ""), item)
            return True

        with ThreadPoolExecutor(max(1, w)) as ex:
            futs = [ex.submit(grab, i) for i in todo]
            for n, f in enumerate(as_completed(futs), 1):
                if f.result():
                    ok += 1
                else:
                    fail += 1
                if n % 50 == 0:
                    log(f"    {co['key']} {n}/{len(todo)} ok={ok} fail={fail} {STATS}")
        log(f"[jd] {co['key']}: ok={ok} fail={fail}（失败的重跑同命令续抓）")



NONUS_CITIES = {"london", "calgary", "toronto", "vancouver", "montreal", "munich", "berlin", "paris", "amsterdam", "eindhoven", "zurich",
                "basel", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai", "haifa", "tel aviv", "yokneam", "raanana", "shanghai",
                "beijing", "shenzhen", "taipei", "hsinchu", "tokyo", "seoul", "sydney", "melbourne", "dublin", "galway", "zipf",
                "ho chi minh", "singapore", "warsaw", "krakow", "madrid", "barcelona", "milan", "stockholm", "copenhagen", "oslo",
                "helsinki", "vienna", "prague", "budapest", "bucharest", "lisbon", "brussels", "manchester", "cambridge, uk", "oxford",
                "edinburgh", "sao paulo", "mexico city", "buenos aires", "cairo", "dubai", "riyadh", "tokyo", "osaka", "manila", "jakarta",
                "kuala lumpur", "bangkok", "hanoi", "chennai", "noida", "gurgaon", "gurugram", "kolkata", "ahmedabad", "wuxi", "suzhou",
                "lausanne", "geneva", "shenyang", "suzhou", "guangzhou", "chengdu", "hangzhou", "nanjing", "xian", "tianjin", "hong kong", "incheon", "pune", "best", "hamburg", "erlangen", "forchheim", "kemnath", "suki india", "remote - international", "remote international", "remote - emea", "remote - apac", "remote - uk", "remote - canada"}
US_CITIES_KNOWN = {"research triangle park", "ambry headquarters", "ny office", "sf office", "boston office", "us office", "hq", "headquarters", "silicon valley", "bay area", "new york city", "nyc", "san francisco bay area"}
US_STATES = {"AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT",
             "NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC"}
ISO3_US = {"USA"}


def postfix_region(rec, co):
    """parse_jd 判不出的地点（裸城市名、GE 站点代码、裸 Remote）按公司 default_country 与站点代码补判。"""
    if rec.get("region") != "未知":
        return
    locs = [str(x) for x in (rec.get("locations") or []) if x] or [str(rec.get("location_raw") or "")]
    verdicts = []
    for l in locs:
        ll = l.strip().lower()
        m = re.match(r"^([A-Z]{2,3})\d{2}-\d{2}-", l.strip())
        if m:
            code = m.group(1)
            if code in ISO3_US or code in US_STATES:
                verdicts.append("US")
                if code in US_STATES and not rec.get("state"):
                    rec["state"] = code
            else:
                verdicts.append("NONUS")
            continue
        if ll in ("remote", "remote (us)", "us remote", "remote us", "virtual", "anywhere"):
            verdicts.append("REMOTE_US" if co.get("default_country", "US") == "US" else "NONUS")
            continue
        if any(ll == c or ll.startswith(c + ",") or ll.startswith(c + " ") for c in NONUS_CITIES):
            verdicts.append("NONUS")
            continue
        if any(ll == c or ll.startswith(c + ",") for c in US_CITIES_KNOWN):
            verdicts.append("US")
            continue
        verdicts.append("UNK")
    if not verdicts:
        return
    if all(v == "NONUS" for v in verdicts):
        rec["region"] = "非美国"
    elif all(v in ("US", "REMOTE_US") for v in verdicts):
        rec["region"] = "远程-美国" if all(v == "REMOTE_US" for v in verdicts) else "美国"
        rec["remote_us"] = all(v == "REMOTE_US" for v in verdicts) or rec.get("remote_us")
    elif any(v in ("US", "REMOTE_US") for v in verdicts):
        rec["region"] = "多地含美国"
    elif all(v == "UNK" for v in verdicts) and co.get("default_country", "US") != "US":
        rec["region"] = "非美国"
    rec["region_postfix"] = verdicts


def _merge_display_layer(records):
    """NVIDIA Eightfold 展示层记录按 apply_redirect_url 里的 JR 号并进 Workday 主记录，不另起一行。"""
    import re
    by_req = {}
    for r in records:
        if r.get("source_authority") == "primary":
            by_req[(r.get("display_layer_of") or r["company_key"], r["req_id"])] = r
    kept = []
    for r in records:
        if r.get("source_authority") != "display":
            kept.append(r)
            continue
        m = re.search(r"_(JR\d+)", str(r.get("extra", {}).get("apply_redirect_url") or ""))
        jr = m.group(1) if m else None
        target = by_req.get((r.get("display_layer_of"), jr)) if jr else None
        if target:
            target.setdefault("extra", {})["eightfold"] = {
                "id": r["job_id"], "t_create_posted": r.get("posted"), "url": r.get("url"),
                "apply_redirect_url": r.get("extra", {}).get("apply_redirect_url")}
            if not target.get("jd_text") and r.get("jd_text"):
                target["jd_text"], target["jd_len"] = r["jd_text"], r["jd_len"]
        else:
            r["排除理由"] = "展示层记录未匹配到主记录（JR 号缺失或主记录未抓）"
            kept.append(r)
    return kept


def cmd_build(cfg, companies):
    try:
        import parse_jd
    except ImportError as e:
        log(f"[build] 无法导入 parse_jd.py: {e}")
        return []
    records = []
    for co in companies:
        ad = make_adapter(co, cfg)
        if not ad:
            continue
        lst = read_json(_list_path(co["key"]), None)
        if not lst:
            continue
        n_ok = n_missing = 0
        for item in lst["items"]:
            raw_rec = load_raw(co["key"], item["id"])
            if raw_rec is None:
                n_missing += 1
                continue
            try:
                pre = ad.normalize(item, raw_rec["body"])
            except Exception as e:  # noqa
                log(f"    [{co['key']}] normalize 失败 {item['id']}: {type(e).__name__}: {e}")
                n_missing += 1
                continue
            pre.update({
                "company": co["name"], "company_key": co["key"], "track": co.get("track", ""), "ats": ad.kind,
                "source_authority": ("display" if co.get("display_layer_of") else ("manual" if ad.kind == "manual" else "primary")),
                "display_layer_of": co.get("display_layer_of"),
                "fetched_at": raw_rec.get("_fetched_at", ""), "list_hits": item.get("list_hits", []),
                "team_filter": co.get("team_filter", []), "list_meta": item.get("list_meta", {}),
            })
            pre["uid"] = f"{co['key']}:{pre.get('req_id') or item['id']}"
            try:
                rec = parse_jd.parse(pre)
            except Exception as e:  # noqa
                log(f"    [{co['key']}] parse 失败 {item['id']}: {type(e).__name__}: {e}")
                rec = pre
            postfix_region(rec, co)
            rec.setdefault("excluded", False)
            rec.setdefault("排除理由", "")
            rec.setdefault("funnel_gate", "")
            records.append(rec)
            n_ok += 1
        log(f"[build] {co['key']}: 记录 {n_ok}，缺 raw {n_missing}")
    records = _merge_display_layer(records)
    write_json(os.path.join(OUT_DIR, "jobs_all.json"), records)
    with open(os.path.join(OUT_DIR, "healthcare_jobs_全量.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in records:
            row = {k: r.get(k, "") for k in CSV_COLUMNS}
            for k in ("restriction_flags", "healthcare_hits", "list_hits"):
                if isinstance(row.get(k), list):
                    row[k] = ";".join(map(str, row[k]))
            w.writerow(row)
    log(f"[build] 共 {len(records)} 条 → out/jobs_all.json, out/healthcare_jobs_全量.csv")
    return records


def cmd_status(cfg, companies):
    print(f"{'key':22} {'ats':14} {'status':9} {'listed':>7} {'fetched':>8} {'missing':>8}  listed_at")
    for co in companies:
        lst = read_json(_list_path(co["key"]), None)
        listed = len(lst["items"]) if lst else 0
        fetched = sum(1 for i in (lst["items"] if lst else []) if os.path.exists(common.raw_path(co["key"], i["id"])))
        print(f"{co['key']:22} {co.get('ats', '?'):14} {co.get('status', '?'):9} {listed:>7} {fetched:>8} {listed - fetched:>8}  {lst['_listed_at'] if lst else '-'}")


def cmd_full_board(cfg, companies):
    for co in companies:
        if co.get("ats") != "eightfold":
            log(f"[full-board] {co['key']} 不是 eightfold，跳过")
            continue
        ad = make_adapter(co, cfg)
        items = ad.list_jobs(full_board=True)
        write_json(os.path.join(raw_dir(co["key"]), "_fullboard.json"), {"_listed_at": now_iso(), "items": items})
        log(f"[full-board] {co['key']}: {len(items)} 条 → raw/{co['key']}/_fullboard.json")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["list", "jd", "build", "status", "all", "full-board"])
    ap.add_argument("--company", action="append", help="companies.json 里的 key，可重复")
    ap.add_argument("--track", action="append", choices=["A", "B", "C", "D"])
    ap.add_argument("--ats", action="append")
    ap.add_argument("--allow-broad", action="store_true", help="Workday 关键词 total 超阈值也照拉")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--force", action="store_true", help="jd: 已有 raw 也重抓")
    ap.add_argument("--max-details", type=int, help="jd: 每家最多抓 N 条（调试用）")
    ap.add_argument("--curl", action="store_true", help="全部请求走 /usr/bin/curl")
    ap.add_argument("--insecure", action="store_true", help="urllib 关闭证书校验（只在明确需要时用）")
    ap.add_argument("--registry", help="companies.json 路径")
    a = ap.parse_args(argv)
    FLAGS["force_curl"] = a.curl
    FLAGS["insecure"] = a.insecure
    cfg = load_companies(a.registry)
    companies = [c for c in select_companies(cfg, a.company, a.track, a.ats) if not c.get("skip")]
    if not companies:
        raise SystemExit("No matching companies. Configure the local registry or adjust --company/--track/--ats; see docs/CRAWLER_SETUP.md.")
    rc = 0
    if a.cmd in ("list", "all"):
        if cmd_list(cfg, companies, a.allow_broad):
            rc = 2
    if a.cmd in ("jd", "all"):
        cmd_jd(cfg, companies, a.workers, a.force, a.max_details)
    if a.cmd in ("build", "all"):
        cmd_build(cfg, companies)
    if a.cmd == "status":
        cmd_status(cfg, companies)
    if a.cmd == "full-board":
        cmd_full_board(cfg, companies)
    log(f"HTTP 统计 {STATS}")
    sys.exit(rc)


if __name__ == "__main__":
    main()
