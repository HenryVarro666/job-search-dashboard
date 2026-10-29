#!/usr/bin/env python3
"""生成公开 ATS 检索清单；不联网、不读取个人材料、不改岗位或申请状态。

搜索时间只是发现线索，不能作为官方 posted；生成清单也不代表执行过搜索。
执行者须逐条记录完成/失败，核验官方原帖后再走现有面板同步流程。
"""
import argparse
import datetime as dt
import json
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo


DOMAINS = (
    "jobs.ashbyhq.com", "job-boards.greenhouse.io", "boards.greenhouse.io",
    "jobs.lever.co", "myworkdayjobs.com", "jobs.smartrecruiters.com",
)
FAMILIES = (
    ("medical_imaging", '("Research Scientist" OR "Machine Learning Engineer" OR "Research Engineer") '
     '("medical imaging" OR radiology)',
     '(imaging OR radiology OR MRI) (learning OR scientist OR engineer)'),
    ("tech_fintech_ds", '("Data Scientist" OR "Decision Scientist" OR "Applied Scientist")',
     '(data OR decision) (scientist OR science)'),
    ("applied_research", '("Applied Scientist" OR "Research Scientist" OR "Machine Learning Scientist")',
     '(applied OR research) scientist'),
    ("phd_mle", '("Machine Learning Engineer" OR "ML Engineer" OR "Research Engineer") '
     '(PhD OR "new grad" OR "early career")',
     'machine learning (engineer OR scientist)'),
)
INTERN_RECENT = (
    '("Research Intern" OR "Machine Learning Intern" OR "Data Science Intern") '
    'PhD (spring OR "off-cycle" OR "year-round")'
)
INTERN_RECALL = '(research OR learning OR data) (intern OR internship OR co-op)'


def chicago_today():
    return dt.datetime.now(ZoneInfo("America/Chicago")).date()


def _query(scope, family, domain, words, window, day):
    query = f"site:{domain} {words}"
    params = {}
    if window == "recent":
        query += " after:" + (day - dt.timedelta(days=7)).isoformat()
        params["tbs"] = "qdr:w"
    # 32 词只是本工具的可读性预算，不声称是 Google 的硬限制。
    if len(query.split()) >= 32:
        raise ValueError(f"query exceeds local readability budget: {query}")
    params["q"] = query
    return {
        "id": f"{scope}:{family}:{domain}:{window}",
        "scope": scope,
        "family": family,
        "domain": domain,
        "query": query,
        "window": window,
        "time_filter": "past_week" if window == "recent" else "none",
        "recency_days": 7 if window == "recent" else None,
        "google_url": "https://www.google.com/search?" + urlencode(params),
        "status": "not_run",
    }


def build_plan(scope="fulltime", day=None):
    if scope not in ("fulltime", "internship", "all"):
        raise ValueError("scope must be fulltime, internship or all")
    day = day or chicago_today()
    queries = []
    rotated = FAMILIES[day.toordinal() % len(FAMILIES)]
    if scope in ("fulltime", "all"):
        for domain in DOMAINS:
            for family, recent, _ in FAMILIES:
                queries.append(_query("fulltime", family, domain, recent, "recent", day))
            family, _, recall = rotated
            queries.append(_query("fulltime", family, domain, recall, "recall", day))
    if scope in ("internship", "all"):
        for domain in DOMAINS:
            queries.append(_query("internship", "phd_spring", domain, INTERN_RECENT, "recent", day))
            queries.append(_query("internship", "phd_spring", domain, INTERN_RECALL, "recall", day))
    return {
        "schema_version": 1,
        "date": day.isoformat(),
        "timezone": "America/Chicago",
        "scope": scope,
        "status": "planned_not_executed",
        "coverage": {
            "domains": len(DOMAINS),
            "queries": len(queries),
            "recent": sum(q["window"] == "recent" for q in queries),
            "recall": sum(q["window"] == "recall" for q in queries),
            "fulltime_recall_family": rotated[0] if scope != "internship" else None,
            "fulltime_recall_cycle_days": 4 if scope != "internship" else None,
        },
        "instructions": [
            "使用可用搜索工具执行 query，或手动打开 google_url；脚本不会抓取 Google。逐条记录实际完成、失败及来源，不将未执行项报成零结果。",
            "recent 只限定搜索引擎估计的页面发布/更新时间；recall 不限时间并放宽标题。两者都不是官方新发布证明，也不能用于判关闭。",
            "官方发布日期、最近重新发布、页面更新时间、本地首次发现及本次核验时间分开保存；同公司同 req 不重置首次发现。",
            "先核实公司官网与具体 ATS 原帖/API，再按美国工作地、职级、真实年限、领域和身份规则筛选；Remote 不自动等于美国。",
            "实习另核冬春开始、时长、在读/返校和毕业窗口；全职与实习分开合并，保留人工暂停与排除。",
            "已知公司继续走 ATS 增量枚举，校招另查官网外链入口；搜索零结果不证明没有岗位。仅官方核验后使用现有 opportunity_updates.py / internship_updates.py 同步。",
        ],
        "queries": queries,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("fulltime", "internship", "all"), default="fulltime")
    parser.add_argument("--date", type=dt.date.fromisoformat, help="计划日期 YYYY-MM-DD，默认美中今天")
    parser.add_argument("--output", type=Path, help="保存 JSON；省略则输出到 stdout")
    args = parser.parse_args(argv)
    text = json.dumps(build_plan(args.scope, args.date), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
