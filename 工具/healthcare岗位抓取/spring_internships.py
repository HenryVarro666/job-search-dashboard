#!/usr/bin/env python3
"""从现有全量岗位独立保留 Spring PhD 实习线索；不改全职漏斗或投递事实。"""
import argparse
from collections import Counter
import datetime
import json
from pathlib import Path
import re

from common import OUT_DIR, TOOL_DIR
from parse_jd import cut_boilerplate

POLICY_PATH = Path(TOOL_DIR) / "spring_internship_policy.json"
INTERN = re.compile(r"\bintern(?:ship)?s?\b|\bco[- ]?op\b", re.I)
PHD = re.compile(r"\bph\.?\s?d\.?\b|doctor(?:al|ate)", re.I)
TECH = re.compile(r"machine learning|deep learning|data scien\w*|artificial intelligence|computer vision|"
                  r"medical imag\w*|neuroimag\w*|\bmri\b|\bdti\b|\b(?:AI|ML|NLP)\b|"
                  r"natural language|foundation model|large language model|reinforcement learning", re.I)
SPRING = re.compile(r"\bspring\b(?!\s+(?:house|boot|framework|cloud))|\bwinter\b", re.I)
FLEXIBLE = re.compile(r"year[- ]round|off[- ]cycle|rolling start|flexible start", re.I)
OTHER_SEASON = re.compile(r"\bsummer\b|\bfall\b|\bautumn\b", re.I)
SPRING_START = re.compile(r"\b(?:start\w*|begin\w*|commenc\w*|available)[^.\n]{0,45}\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May)\b[^.\n]{0,15}\b20\d{2}\b", re.I)
SENTENCE = re.compile(r".+?(?:[.!?](?=\s|$)|\n|$)", re.S)
DURATION = re.compile(r"\b(\d{1,2})\s*(?:(?:[-–]|to)\s*(\d{1,2})\s*)?[- ]?(weeks?|months?)\b", re.I)


def load_policy(path=None):
    source = Path(path) if path is not None else POLICY_PATH
    if not source.is_file():
        raise SystemExit(f"Spring internship policy required: {source}. See docs/CRAWLER_SETUP.md.")
    return json.loads(source.read_text(encoding="utf-8"))


def evidence(text, pattern):
    rx = re.compile(pattern, re.I) if isinstance(pattern, str) else pattern
    return " | ".join(m.group(0).strip()[:300] for m in SENTENCE.finditer(text) if rx.search(m.group(0)))[:650]


def assess(record, policy=None):
    policy = policy or load_policy()
    title = record.get("title") or ""
    jd = record.get("jd_text") or ""
    core, _ = cut_boilerplate(jd)
    text = title + "\n" + core
    result = {k: record.get(k) for k in ("uid", "company", "company_key", "req_id", "title", "url", "location", "region")}
    result.update(status="EXCLUDED", reason="非实习职位")
    if not INTERN.search(title):
        return result
    result["reason"] = "非目标研究/ML/AI/DS/医学影像方向"
    hits = {m.group(0).lower() for m in TECH.finditer(core)}
    if not TECH.search(title) and len(hits) < 2 and not (hits and re.search(r"research|applied scien", title, re.I) and PHD.search(text)):
        return result
    if record.get("can_apply") is False:
        result["reason"] = "缓存官方记录 can_apply=false"
        return result
    if record.get("region") == "非美国":
        result["reason"] = "非美国"
        return result
    if re.search(r"only (?:undergraduate|bachelor'?s?)\b|undergraduate (?:students? )?only|(?:bachelor'?s?|undergraduate)[^.\n]{0,40}only", text, re.I):
        result["reason"] = "明确仅本科生"
        return result
    if re.search(r"\bundergrad(?:uate)?\b", title, re.I) and not (PHD.search(text) or re.search(r"\bmaster'?s?\b|graduate (?:students?|degree|program)", text, re.I)):
        result["reason"] = "标题为本科实习项目，未见博士或研究生入口"
        return result
    title_years = set(re.findall(r"\b20\d{2}\b", title))
    target = str(policy["target_year"])
    if title_years and target not in title_years:
        result["reason"] = "标题明确其他项目年份"
        return result
    # Only narrow, explicit mandatory clauses are auto-excluded here. The daily
    # official-source review handles ambiguous wording and application questions.
    if policy.get("graduation_preference", {}).get("exclude_fall_2027_or_later"):
        clauses = (clause.strip() for m in SENTENCE.finditer(core) for clause in re.split(r"[;\n]", m.group(0)))
        for sentence in clauses:
            if re.search(r"\bpreferred\b|\bnot required\b|\bno requirement\b", sentence, re.I):
                continue
            mandatory = re.search(r"\b(?:must|required)\b", sentence, re.I)
            if not mandatory:
                continue
            clause = sentence[mandatory.start():]
            fall_return = re.search(r"\breturn(?:ing)?\b.{0,120}\b(?:fall|autumn)\b", clause, re.I)
            late_grad = re.search(r"\bgraduat(?:e|ing|ion)(?:\s+date)?\s+(?:in|after|not before|no earlier than|on or after)\s+"
                                  r"(?:(?P<season>[A-Za-z]+)\s+)?(?P<year>20\d{2})\b", clause, re.I)
            after_window = bool(late_grad and (int(late_grad["year"]) > int(target)
                                or (late_grad["year"] == target and (late_grad["season"] or "").lower() in ("fall", "autumn"))))
            if fall_return or after_window:
                result.update(reason="已配置毕业偏好排除：明确秋季返校或更晚毕业要求",
                              graduation_exclusion_evidence=sentence[:650])
                return result
    # 正文仅收实习时间句；清楚的多季节说明可扩展标题，不能从工作内容猜春季。
    term_core = "\n".join(s.group(0) for s in SENTENCE.finditer(core) if not re.search(
        r"graduation|graduating|graduate (?:in|by|between|after|before|date|from)|return.{0,35}(?:school|universit|studies|degree|program)", s.group(0), re.I))
    body_terms = evidence(
        term_core, r"(?:spring(?!\s+(?:house|boot|framework|cloud))|winter|summer|fall|autumn)[^.\n]{0,35}(?:20\d{2}|intern|term|start)|"
              r"(?:intern|start|term)[^.\n]{0,35}(?:spring(?!\s+(?:house|boot|framework|cloud))|winter|summer|fall|autumn)|"
              r"year[- ]round|off[- ]cycle|flexible start|rolling start|" + SPRING_START.pattern)
    title_terms = title if (SPRING.search(title) or OTHER_SEASON.search(title) or FLEXIBLE.search(title)) else ""
    term_text = " | ".join(filter(None, (title_terms, body_terms)))
    dated_terms = set(re.findall(r"(?:spring|winter|summer|fall|autumn)[ /\w-]{0,18}?\b(20\d{2})\b", term_text, re.I))
    if dated_terms and target not in dated_terms and target not in title_years:
        result["reason"] = "正文实习时间明确其他年份"
        return result
    spring_start = SPRING_START.search(term_text)
    spring_years = set(re.findall(r"(?:spring|winter)(?:(?!spring|winter|summer|fall|autumn).){0,20}?\b(20\d{2})\b", term_text, re.I))
    spring_years.update(re.findall(r"spring\s*/\s*summer\s+(20\d{2})", term_text, re.I))
    conflict = bool(spring_years and target not in spring_years)
    is_spring = (SPRING.search(term_text) and not conflict) or (spring_start and target in spring_start.group(0) and not OTHER_SEASON.search(term_text))
    season = "SPRING" if is_spring else ("FLEXIBLE" if FLEXIBLE.search(term_text) else "UNVERIFIED")
    if season == "UNVERIFIED" and OTHER_SEASON.search(term_text) and not SPRING.search(term_text):
        result["reason"] = "仅见 summer/fall 实习窗口"
        return result
    flags = list(record.get("restriction_flags") or [])
    restrictions = list(record.get("restriction_evidence") or [])
    hard = set(flags) & {"US_CITIZEN_REQUIRED", "US_PERSON", "ITAR", "CLEARANCE"}
    cpt_ban = evidence(jd, r"(?:do not|cannot|unable to|not able to|will not)[^.\n]{0,60}(?:accept|hire|consider|support)[^.\n]{0,50}\b(?:CPT|F-?1)\b")
    if hard or cpt_ban:
        result.update(reason="明确身份限制：" + (", ".join(sorted(hard)) or cpt_ban), restriction_evidence=restrictions or [cpt_ban])
        return result
    duration_sentences = []
    duration = None
    for sentence in (s.group(0).strip() for s in SENTENCE.finditer(core)):
        if re.search(r"\bOPT\b|visa|work authorization|benefit|insurance", sentence, re.I):
            continue
        for m in DURATION.finditer(sentence):
            before, after = sentence[:m.start()], sentence[m.end():]
            if re.match(r"\s*(?:of\s+)?(?:(?:prior|professional|work|research|relevant)\s+)?experience", after, re.I):
                continue
            if not (re.search(r"intern|co[- ]?op|duration|commitment|position|available|placement|lasts?", before + " " + after, re.I)
                    or re.search(r"\bfor\s*$", before, re.I)):
                continue
            factor = 52 / 12 if m.group(3).lower().startswith("month") else 1
            duration = [round(int(m.group(1)) * factor, 1), round(int(m.group(2) or m.group(1)) * factor, 1)]
            duration_sentences.append(sentence[:400])
            break
        if duration:
            break
    duration_text = " | ".join(duration_sentences)
    minimum, maximum = policy["candidate_weeks"]
    if duration and (duration[0] > maximum or duration[1] < minimum):
        result["reason"] = f"明确时长不在配置的 {minimum}–{maximum} 周候选范围"
        return result
    checks = ["投前当天核实 ATS 在招、具体春季起止日期和实习时长",
              "由学校和雇主确认在读、返校安排及适用工作授权；时间偏好不等于已有资格"]
    if policy.get("graduation_preference", {}).get("exclude_fall_2027_or_later"):
        checks.append(f"按已配置毕业偏好核实实习结束可否直接毕业；需拖至 {target} Fall 或更晚则排除，强制返校安排未清则暂缓，未披露不等于允许")
    if not PHD.search(text):
        checks.append("JD 未明确 PhD 入口，确认是否接收博士生")
    if season != "SPRING":
        checks.append(f"尚无明确春季窗口，确认可在 {target} 年春季开始")
    if conflict:
        checks.append(f"春季与项目年份有冲突，不能视为 {target} 春季机会")
    if not duration:
        checks.append(f"未明确时长，确认是否符合配置的 {minimum}–{maximum} 周范围")
    if record.get("region") not in ("美国", "远程-美国", "多地含美国"):
        checks.append("工作国家/地点待核实")
    if flags:
        checks.append("逐句核雇主身份限制；无 sponsor 声明不自动等同拒绝 CPT，未来 sponsorship 另判")
    result.update(status="CONDITIONAL", reason="独立实习条件线索，未确认个人资格", season_status=season,
                  phd_status="EXPLICIT" if PHD.search(text) else "UNVERIFIED", duration_weeks=duration,
                  duration_evidence=duration_text, start_evidence=term_text[:650],
                  enrolled_evidence=evidence(core, r"enroll|pursuing|current.{0,25}(?:student|PhD|degree)"),
                  return_to_school_evidence=evidence(core, r"return.{0,30}(?:school|universit|studies|degree|program)"),
                  graduation_window_evidence=evidence(core, r"graduat|expected.{0,20}(?:completion|degree)"),
                  pay_evidence=evidence(jd, r"\$|salary|compensation|hourly|\bpaid\b"),
                  role_evidence=evidence(text, TECH), restriction_flags=flags, restriction_evidence=restrictions,
                  source_dates={k: record[k] for k in ("_fetched_at", "fetched_at", "_listed_at", "checked_at", "posted", "date_posted") if record.get(k)}, checks=checks)
    return result


def build_report(records, policy=None, input_path=""):
    policy = policy or load_policy()
    counts = Counter(input=0, duplicates=0, candidates=0)
    candidates, excluded, seen = [], [], set()
    for record in records:
        counts["input"] += 1
        uid = record.get("uid") or (record.get("company"), record.get("req_id"), record.get("url"))
        if uid in seen:
            counts["duplicates"] += 1
            continue
        seen.add(uid)
        result = assess(record, policy)
        if result["status"] == "CONDITIONAL":
            candidates.append(result)
            counts["candidates"] += 1
        else:
            counts["excluded: " + result["reason"]] += 1
            if INTERN.search(record.get("title") or ""):
                excluded.append(result)
    candidates.sort(key=lambda r: (r["season_status"] != "SPRING", r["phd_status"] != "EXPLICIT", r.get("company") or "", r.get("title") or ""))
    return {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "input_path": str(input_path),
            "live_verification": False, "policy": policy, "counts": dict(counts), "candidates": candidates, "excluded_internships": excluded}


def render_markdown(report):
    def cell(value):
        return str(value or "待核实").replace("|", " / ").replace("\n", " ")
    lines = ["# 春季 PhD 实习候选线索", "", "> 从本地抓取缓存筛选，未重新联网判活；所有条目均为条件线索，个人时间与资格须另行确认。", "",
             report["policy"]["school_status"], "", f"候选 {len(report['candidates'])} 条；生成时间 {report['generated_at']}。", "",
             "| 公司 / 岗位 | 时间 / 时长 | 在读 / 返校 / 毕业要求 | 待确认 |", "|---|---|---|---|"]
    for r in report["candidates"]:
        title = cell(r.get("company")) + " · " + cell(r.get("title"))
        link = f"[{title}]({r['url']})" if r.get("url") else title
        school = " / ".join(filter(None, (r["enrolled_evidence"], r["return_to_school_evidence"], r["graduation_window_evidence"])))
        lines.append(f"| {link} | {cell(r['season_status'])} · {cell(r['start_evidence'])} / {cell(r['duration_evidence'])} | {cell(school)} | {cell('；'.join(r['checks']))} |")
    if not report["candidates"]:
        lines += ["", "当前缓存未找到候选，不代表官方没有此类项目。"]
    lines += ["", "来源核验日期沿用 JSON 中 source_dates；生成日期不代表岗位重新核验。", "",
              "相关：[[工具/healthcare岗位抓取/README|抓取工具说明]]"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(Path(OUT_DIR) / "jobs_all.json"))
    parser.add_argument("--output-dir", default=str(Path(OUT_DIR) / "spring_internships"))
    parser.add_argument("--policy", type=Path, help="显式本地政策 JSON；默认工具目录 spring_internship_policy.json")
    args = parser.parse_args()
    policy = load_policy(args.policy)
    if not policy.get("enabled", True):
        print("春季实习候选分支已关闭")
        return
    records = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = build_report(records, policy, args.input)
    dest = Path(args.output_dir)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "candidates.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (dest / "候选线索.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"春季实习条件线索 {len(report['candidates'])} 条 → {dest}")


if __name__ == "__main__":
    main()
