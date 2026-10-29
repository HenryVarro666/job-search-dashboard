#!/usr/bin/env python3
"""生成 LinkedIn 招聘帖检索计划；不联网、不读取个人文件、不执行搜索。

native_posts 需在本人可用会话中手动核实 Posts 筛选；public_web 只是公开索引补充。
生成计划不代表已搜索，也不证明岗位在招或符合全职/实习资格。
"""
import argparse
import datetime as dt
import json
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo


CHICAGO = ZoneInfo("America/Chicago")
FAMILIES = (
    ("medical_imaging", '"medical imaging"', '"medical imaging"'),
    ("tech_fintech_ds", '"data scientist"', '"data scientist"'),
    ("applied_research", '"research scientist"',
     '("research scientist" OR "applied scientist")'),
    ("phd_mle", '"machine learning"', '"machine learning"'),
)
PUBLIC_POSTS = "(site:linkedin.com/posts/ OR site:linkedin.com/feed/update/)"


def chicago_today():
    return dt.datetime.now(CHICAGO).date()


def _query(source, scope, family, expression, window, day):
    recent = window == "recent"
    time_filter = "past_week" if recent else "none"
    native = source == "native_posts"
    if native:
        query = expression
        search_url = "https://www.linkedin.com/search/results/content/?" + urlencode(
            {"keywords": query}
        )
        surface = "Posts"
        filters = {"surface": surface, "sort": "latest", "date": time_filter}
        recency_basis = "post_time"
    else:
        query = f"{PUBLIC_POSTS} AND {expression}"
        params = {}
        if recent:
            query += " after:" + (day - dt.timedelta(days=7)).isoformat()
            params["tbs"] = "qdr:w"
        params["q"] = query
        search_url = "https://www.google.com/search?" + urlencode(params)
        surface = "indexed_linkedin_posts"
        filters = {"surface": surface, "date": time_filter}
        recency_basis = "search_engine_approximate"
    result = {
        "id": f"{source}:{scope}:{family}:{window}",
        "source": source,
        "provider": "linkedin_manual" if native else "public_web",
        "scope": scope,
        "family": family,
        "query": query,
        "window": window,
        "time_filter": time_filter,
        "recency_days": 7 if recent else None,
        "search_url": search_url,
        "requested_surface": surface,
        "requested_filters": filters,
        "filters_verified": False,
        "manual_session_required": native,
        "recency": {
            "basis": recency_basis,
            "verified": False,
            "note": (
                "仅指帖子发布时间；不是岗位官方 posted。日期筛选须在实际 UI 核实。"
                if native else
                "搜索引擎估计的页面发布/更新时间；不是原生 Posts 覆盖或官方 posted。"
            ),
        },
        "status": "not_run",
    }
    if not native:
        result["google_url"] = search_url
    return result


def build_plan(scope="fulltime", day=None):
    if scope not in ("fulltime", "internship", "all"):
        raise ValueError("scope must be fulltime, internship or all")
    day = day if day is not None else chicago_today()
    rotated = FAMILIES[day.toordinal() % len(FAMILIES)]
    queries = []
    for source in ("native_posts", "public_web"):
        if scope in ("fulltime", "all"):
            for family, target, _ in FAMILIES:
                queries.append(_query(source, "fulltime", family,
                                      f'"join our team" AND {target}', "recent", day))
            family, _, recall_target = rotated
            queries.append(_query(source, "fulltime", family,
                                  f"(hiring OR openings) AND {recall_target}", "recall", day))
        if scope in ("internship", "all"):
            queries.append(_query(source, "internship", "phd_spring",
                                  '"join our team" AND intern', "recent", day))
            queries.append(_query(source, "internship", "phd_spring",
                                  "hiring AND (intern OR internship)", "recall", day))
    coverage = {
        "queries": len(queries),
        "recent": sum(q["window"] == "recent" for q in queries),
        "recall": sum(q["window"] == "recall" for q in queries),
        "fulltime_recall_family": rotated[0] if scope != "internship" else None,
        "fulltime_recall_cycle_days": 4 if scope != "internship" else None,
    }
    for source in ("native_posts", "public_web"):
        selected = [q for q in queries if q["source"] == source]
        coverage[source] = {
            "queries": len(selected),
            "recent": sum(q["window"] == "recent" for q in selected),
            "recall": sum(q["window"] == "recall" for q in selected),
        }
    return {
        "schema_version": 1,
        "date": day.isoformat(),
        "timezone": "America/Chicago",
        "scope": scope,
        "status": "planned_not_executed",
        "coverage": coverage,
        "instructions": [
            "逐条保存实际执行状态和证据；执行记录 status 仅 completed / failed / skipped；正常完成但零结果用 completed 与空 result_urls，未执行项不记零结果。",
            "native_posts 需可用的本人会话；手动选择 Posts、latest 和所需日期，在实际 UI 核实后才可标记筛选已验证。URL 只携带关键词。",
            "Google 的公开索引结果仅作补充；public_web 完成不代表已读取原生 Posts。两种来源分别记录覆盖。",
            "recent 请求过去一周；recall 不限日期。原生日期指帖子时间，公开搜索日期近似，两者都不是岗位官方 posted。",
            "scope / family 表示搜索意图，不是资格证明；先过职级、年限和 Required 硬门，进入推荐前核官方 ATS/正式招聘页、美国地点、领域及身份；实习另审开始时间和返校要求。",
            "只读发现线索；不发消息、不加好友或发请求，不自动登录、不抓取 LinkedIn。登录或权限不可用记 skipped，执行出错记 failed，原因写入 error。",
        ],
        "queries": queries,
    }


def _parse_day(value):
    day = dt.date.fromisoformat(value)
    if day.isoformat() != value:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD")
    return day


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("fulltime", "internship", "all"), default="fulltime")
    parser.add_argument("--date", type=_parse_day, help="计划日期 YYYY-MM-DD，默认美中今天")
    parser.add_argument("--output", type=Path, help="保存 JSON；省略则输出到 stdout")
    args = parser.parse_args(argv)
    text = json.dumps(build_plan(args.scope, args.date), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
