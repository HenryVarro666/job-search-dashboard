#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""healthcare 岗位 JD 解析器（纯标准库，Python 3.9+）。

把各 ATS 抓回来的 JD（HTML 或纯文本）解析成 `out/jobs_all.json` 统一记录里的解析字段：
正文、分节、年限、限制旗标、薪资区间、地点归一、职族、职级、healthcare 命中。
所有解析函数都是纯函数，不做网络与文件 I/O；只有 CLI 读文件。

微软全板解析（2026-09-12，2,277 岗）踩过的两个坑，本文件的规则由此而来：
1. ITAR 旗标必须区分大小写并带词边界：`\bITAR\b`。不区分大小写的 `itar` 会命中 EEO
   尾巴里 "military" 中间的 "itar"，几乎每个岗位都被误标成 ITAR。
2. 学位/年限的分支只按"行首大写 OR"切。微软写法是
   "Doctorate ... AND 3+ years\nOR Master's ... AND 5+ years\nOR Bachelor's ... AND 8+ years"。
   若按任意 "or" 切，"or related field" 会把一条要求切碎，年限全错。

另外两条约定：
- 年限与限制旗标只在 `core`（EEO/benefits 标记行之前）匹配；薪资在全文匹配，因为
  NVIDIA / Amazon 的薪资句在 EEO 段之后。
- 同一 bullet 内多个学位写法（Amazon "PhD, or Master's degree and 4+ years"）按逗号分组：
  没带年限的学位分组记 0 年；"MS/PhD"、"Master's or PhD" 这种只用连接词相邻的写法视为
  同一组共享后面的年限。

用法：
  python3 parse_jd.py --selftest              内置夹具回归（任一失败 exit 1）
  python3 parse_jd.py --print-regex           打印职族/排除正则（浏览器兜底时人工对照）
  python3 parse_jd.py <raw.json> [--index N]  解析一份已存的原始详情/列表 JSON
"""
from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# 1. HTML -> 文本
# ---------------------------------------------------------------------------

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_LI_OPEN_RE = re.compile(r"<li\b[^>]*>", re.I)
_BLOCK_CLOSE_RE = re.compile(
    r"</(?:p|li|div|h[1-6]|tr|ul|ol|table|blockquote|section|article|header|footer|pre)\s*>",
    re.I,
)
_CELL_CLOSE_RE = re.compile(r"</t[dh]\s*>", re.I)
# 2026-09-23 修：块级开标签前也要换行，否则 "achieve.<h3>Basic Qualifications</h3>" 会粘成一行、切段失败
_BLOCK_OPEN_RE = re.compile(r"<(?:h[1-6]|p|div|ul|ol|tr|section|article|header|blockquote|table)\b[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_QUOTE_MAP = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"',
    "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u2026": "...", "\u200b": "", "\ufeff": "",
}
_BULLET_LINE_RE = re.compile(r"(?m)^[ \t]*[\u2022\u00b7\u25cf\u25aa\u2043\u25e6]\s*")
_SPACES_RE = re.compile(r"[ \t\f\v]+")
_LINE_TRIM_RE = re.compile(r"(?m)^ +| +$")
_MANY_NL_RE = re.compile(r"\n{3,}")


def _norm_inline(text) -> str:
    """单行文本归一（引号/破折号/空白），用于 title 等短字段。"""
    s = str(text or "")
    for k, v in _QUOTE_MAP.items():
        s = s.replace(k, v)
    return _SPACES_RE.sub(" ", s.replace("\xa0", " ")).strip()


def html_to_text(html: str) -> str:
    """HTML 转纯文本：unescape 两次、块级标签换行、<li> 变 "- "、去标签、归一引号破折号。"""
    if not html:
        return ""
    s = str(html)
    s = _html.unescape(_html.unescape(s))
    s = _SCRIPT_STYLE_RE.sub(" ", s)
    s = _COMMENT_RE.sub(" ", s)
    s = _BR_RE.sub("\n", s)
    s = _LI_OPEN_RE.sub("\n- ", s)
    s = _BLOCK_OPEN_RE.sub("\n", s)
    s = _BLOCK_CLOSE_RE.sub("\n", s)
    s = _CELL_CLOSE_RE.sub(" ", s)
    s = _TAG_RE.sub("", s)
    s = s.replace("\xa0", " ")
    for k, v in _QUOTE_MAP.items():
        s = s.replace(k, v)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _BULLET_LINE_RE.sub("- ", s)
    s = _SPACES_RE.sub(" ", s)
    s = _LINE_TRIM_RE.sub("", s)
    # <li><p>text</p></li> 在块级开标签换行后会变成 "-\ntext"：把孤立的 "-" 与下一行合并
    s = re.sub(r"(?m)^-[ \t]*\n+(?=\S)", "- ", s)
    s = _MANY_NL_RE.sub("\n\n", s)
    s = re.sub(r"(?m)^(- [^\n]*)\n\n(?=- )", r"\1\n", s)      # 相邻列表项之间不留空行
    s = re.sub(r"(?m)^(- [^\n]*)\n\n(?=- )", r"\1\n", s)      # 再跑一次处理重叠匹配
    return s.strip()


# ---------------------------------------------------------------------------
# 2. 分节标题表（cut_boilerplate 也要用，所以先定义）
# ---------------------------------------------------------------------------

# 顺序即优先级；每行只匹配一次，先命中者赢。标注 "plan" 的是计划书原表，其余是常见变体补充。
_HEADING_TABLE = [
    ("responsibilities", "full", [
        "What you'll be doing", "What you will be doing", "Responsibilities", "Key job responsibilities",
        "About the job", "What you'll do", "What you will do", "In this role", "The Role",
        "Your responsibilities",                                              # plan
        "About the role", "What you'll be working on", "What you will be working on", "What you'll work on",
        "What you'll own", "Key responsibilities", "Role overview", "Job description", "Job Summary",
        "Position Summary", "Overview", "Job Responsibilities", "Duties and Responsibilities",
        "Description",                                                          # amazon.jobs 拼接时的合成标题
    ]),
    ("minimum", "full", [
        "What we need to see", "Basic Qualifications", "Minimum qualifications", "Required Qualifications",
        "Requirements", "Qualifications", "What you bring", "Who you are", "About you", "Must have",
        "What we're looking for", "What you'll need",                         # plan
        "Required/Minimum Qualifications", "Required Minimum Qualifications", "Other Requirements",
        "What you'll bring", "What you will bring", "What you have", "You have", "Skills and experience",
        "Skills & experience", "Your experience", "Your background", "Minimum requirements",
        "Basic requirements", "Required skills", "Required experience", "Must haves", "Must-haves",
        "Job requirements", "Required skills and experience", "Education and experience", "Education", "Experience",
        "Qualifications and skills", "Skills and qualifications", "We're looking for", "You are",
        "What we are looking for", "What you need", "What you'll need to succeed",
    ]),
    ("preferred", "full", [
        "Ways to stand out from the crowd", "Ways to stand out", "Preferred Qualifications", "Nice to have",
        "Nice-to-haves", "Bonus", "Bonus points", "What would set you apart", "Ideal candidate",
        "Preferred",                                                          # plan
        "Additional or Preferred Qualifications", "Preferred/Additional Qualifications", "Nice to haves",
        "Nice-to-have", "Bonus points if", "Preferred experience", "Preferred skills", "Extra credit",
        "It would be great if you", "You might also have", "Preferred requirements", "Desired qualifications",
        "Desired skills", "Pluses", "Even better if", "Bonus if you have", "Great to have", "Good to have",
    ]),
    ("pay", "prefix", [
        "The base salary range", "Pay Range", "Compensation", "Salary", "Pay Transparency",  # plan
        "Your base salary", "The base salary", "Base salary range", "Base pay range", "The typical base pay range",
        "The US base salary range", "The base pay for this position", "Our compensation", "Pay and benefits",
        "Compensation and benefits", "Salary range", "Pay:", "Expected salary",
    ]),
]


def _heading_pattern(phrase: str) -> str:
    esc = re.escape(phrase).replace("\\ ", " ").replace(" ", r"\s+").replace("'", "['\u2019]?")
    return esc


_HEADING_REGEXES = []
for _key, _mode, _phrases in _HEADING_TABLE:
    for _p in _phrases:
        _pat = _heading_pattern(_p)
        if _mode == "full":
            _rx = re.compile(r"^\s*[#*_]*\s*(?:" + _pat + r")\s*[:\uff1a\-]?\s*[*_#]*\s*$", re.I)
        else:
            _rx = re.compile(r"^\s*[#*_]*\s*(?:" + _pat + r")(?![A-Za-z])", re.I)
        _HEADING_REGEXES.append((_key, _mode, _p, _rx))


def _match_heading(line: str):
    """返回 (section_key, mode, phrase) 或 None。"""
    if not line or len(line) > 120 and not line.lower().lstrip("#*_ ").startswith(("the base", "your base", "pay", "compensation", "salary", "our compensation", "base ")):
        return None
    for key, mode, phrase, rx in _HEADING_REGEXES:
        if rx.match(line):
            return key, mode, phrase
    return None


# ---------------------------------------------------------------------------
# 3. 切掉 EEO / benefits 尾巴
# ---------------------------------------------------------------------------

BOILERPLATE_MARKERS = (
    "is an equal opportunity employer",
    "equal opportunity employer",
    "Microsoft is an equal opportunity employer",
    "NVIDIA is committed to fostering",
    "Amazon is committed to a diverse",
    "Google is proud to be an equal opportunity",
    "EEO is the law",
    "Reasonable accommodation",
    "Our commitment to diversity",
    "Benefits Summary",
)
_MARKERS_LOWER = tuple(m.lower() for m in BOILERPLATE_MARKERS)


def cut_boilerplate(text: str) -> tuple:
    """在第一条 EEO/benefits 标记行切成 (core, tail)。

    "Pay Transparency" 只有在已经出现过 minimum 类标题之后才算标记。
    保护：不会在最后一个资格类标题之前切（有些初创公司把 EEO 放在开头的 About 段里）；
    若切出来的 core 不足 400 字符且后面还有别的标记行，则跳过这条继续找。
    """
    if not text:
        return "", ""
    lines = text.split("\n")
    heads = [_match_heading(l) for l in lines]
    qual_idx = [i for i, h in enumerate(heads) if h and h[0] in ("minimum", "preferred", "responsibilities")]
    last_qual = max(qual_idx) if qual_idx else -1
    seen_min = False
    candidates = []
    for i, line in enumerate(lines):
        h = heads[i]
        if h and h[0] == "minimum":
            seen_min = True
        low = line.lower()
        if any(m in low for m in _MARKERS_LOWER) or ("pay transparency" in low and seen_min):
            if i > last_qual:
                candidates.append(i)
    for n, i in enumerate(candidates):
        core = "\n".join(lines[:i]).rstrip()
        if len(core) >= 400 or n == len(candidates) - 1:
            return core, "\n".join(lines[i:]).strip()
    return text, ""


# ---------------------------------------------------------------------------
# 4. 分节
# ---------------------------------------------------------------------------

SECTION_KEYS = ("responsibilities", "minimum", "preferred", "pay", "other")


def split_sections(core: str) -> tuple:
    """按行首标题切节。返回 ({responsibilities, minimum, preferred, pay, other}, sections_found)。"""
    buckets = {k: [] for k in SECTION_KEYS}
    found = []
    cur = "other"
    for line in (core or "").split("\n"):
        h = _match_heading(line)
        if h:
            cur = h[0]
            if cur not in found:
                found.append(cur)
            if h[1] == "prefix":       # 薪资一句话标题，正文就在这一行
                buckets[cur].append(line)
            continue
        buckets[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in buckets.items()}, found


# ---------------------------------------------------------------------------
# 5. 年限
# ---------------------------------------------------------------------------

_OR_SPLIT_RE = re.compile(r"(?m)^\s*(?:[-*\u2022]\s*)?OR\b")
_YEARS_NUM_RE = re.compile(
    r"(?<![\d.])(?:(\d{1,2})\s*(?:-|\u2013|to)\s*)?(\d{1,2})\)?\s*\+?\s*(?:or more\s+)?(?:years?|yrs?)\b", re.I)
_YEARS_WORD_RE = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\s*(?:\(\d{1,2}\)\s*)?\+?\s*(?:or more\s+)?(?:years?|yrs?)\b",
    re.I)
_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_YEARS_BAD_CTX_RE = re.compile(
    r"\b(?:of age|past|last|next(?!-)|ago|over the|history|warranty|within|every|thereafter|per year|each year)\b", re.I)
_DEGREE_RE = re.compile(
    r"(?i:\bph\.?\s?d\b\.?|\bdoctorate\b|\bdoctoral\b|\bd\.?phil\b|\bmaster(?:'s|s)?\b|\bbachelor(?:'s|s)?\b)"
    r"|\b(?:M\.?S|B\.?S|M\.?Sc|B\.?Sc|MEng|BEng|MD)\.?(?![A-Za-z])")
_PHD_RE = re.compile(r"(?i:\bph\.?\s?d\b|doctorate|doctoral|\bd\.?phil\b)|\bMD\b|\bM\.D\.")
_CONNECTOR_RE = re.compile(r"^\s*(?:degree\s*)?(?:/|\bor\b|\band\b|\|)?\s*(?:an?\s+)?$", re.I)
_AND_BEFORE_YEARS_RE = re.compile(r"\band\b|\bplus\b|&")
_IN_LIEU_RE = re.compile(
    r"in lieu of|may substitute|equivalent research experience|research experience (?:may|can) (?:count|substitute)", re.I)
_EQUIV_RE = re.compile(r"or equivalent (?:practical |industry |work |professional |relevant )?experience", re.I)
_NEW_GRAD_RE = re.compile(
    r"currently pursuing|new college grad|recent graduate|graduating in|university grad|new grad", re.I)
_SENT_BOUND_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")


def _sentence_at(text: str, pos: int, maxlen: int = 300) -> str:
    """取 pos 所在的句子（同一行内按句号切），超过 maxlen 时以 pos 为中心截断。"""
    if not text:
        return ""
    ls = text.rfind("\n", 0, pos) + 1
    le = text.find("\n", pos)
    le = len(text) if le < 0 else le
    line = text[ls:le]
    p = pos - ls
    bounds = [0] + [m.end() for m in _SENT_BOUND_RE.finditer(line)] + [len(line)]
    sent, off = line, 0
    for i in range(len(bounds) - 1):
        if bounds[i] <= p < bounds[i + 1] or i == len(bounds) - 2:
            sent, off = line[bounds[i]:bounds[i + 1]], bounds[i]
            break
    sent = sent.strip()
    if len(sent) > maxlen:
        c = max(0, p - off - maxlen // 2)
        sent = sent[c:c + maxlen]
    return sent


def _find_years(text: str) -> list:
    """返回 [(years, start, end)]，剔除年龄/历史等语境；按位置排序。"""
    out = []
    for m in _YEARS_NUM_RE.finditer(text or ""):
        num = int(m.group(1)) if m.group(1) else int(m.group(2))
        out.append((num, m.start(), m.end()))
    for m in _YEARS_WORD_RE.finditer(text or ""):
        out.append((_WORD_NUM[m.group(1).lower()], m.start(), m.end()))
    keep = []
    for num, s, e in sorted(out, key=lambda t: t[1]):
        ctx = text[max(0, s - 40):e + 40]
        if _YEARS_BAD_CTX_RE.search(ctx):
            continue
        if num > 30:
            continue
        keep.append((num, s, e))
    return keep


def _split_branches(text: str) -> list:
    return [b for b in _OR_SPLIT_RE.split(text or "") if b.strip()]


def _degree_paths(branch: str) -> list:
    """bullet 内按学位扫描，返回 [(is_phd, years, evidence)]。

    - 一行只有一个学位提法：年限取该提法之后到行尾的子句里的数字（没有就不算路径）。
    - 一行多个学位提法：只用连接词（/ or and）相邻的合并为一组；某组没年限而别的组有
      "and N+ years" 式年限时，记 0 年（Amazon 写法）。
    """
    paths = []
    for line in (branch or "").split("\n"):
        mentions = list(_DEGREE_RE.finditer(line))
        if not mentions:
            continue
        groups = [[0]]
        for i in range(1, len(mentions)):
            between = line[mentions[i - 1].end():mentions[i].start()]
            if _CONNECTOR_RE.match(between):
                groups[-1].append(i)
            else:
                groups.append([i])
        infos = []
        for gi, g in enumerate(groups):
            start = mentions[g[0]].start()
            end = mentions[groups[gi + 1][0]].start() if gi + 1 < len(groups) else len(line)
            clause = line[start:end]
            ys = _find_years(clause)
            years = min(y for y, _, _ in ys) if ys else None
            is_phd = any(_PHD_RE.search(mentions[k].group(0)) for k in g)
            bound = bool(ys) and bool(_AND_BEFORE_YEARS_RE.search(clause[:ys[0][1]]))
            infos.append((is_phd, years, clause.strip(), bound))
        if not any(i[1] is not None for i in infos):
            continue
        any_bound = any(i[3] for i in infos) and len(infos) >= 2
        for is_phd, years, clause, _b in infos:
            if years is None:
                if any_bound:
                    years = 0
                else:
                    continue
            paths.append((is_phd, years, clause))
    return paths


def extract_years(min_text: str, core: str) -> dict:
    """从 minimum 节（缺则整个 core）抽年限。见模块 docstring 的分支规则。"""
    text = min_text if (min_text or "").strip() else (core or "")
    entries = []
    for b in _split_branches(text):
        has_phd = bool(_PHD_RE.search(b))
        paths = _degree_paths(b)
        if paths:
            for is_phd, years, clause in paths:
                entries.append({"years": years, "is_phd": is_phd, "evidence": clause})
            continue
        ys = _find_years(b)
        if ys:
            num, s, _e = min(ys, key=lambda t: (t[0], t[1]))
            entries.append({"years": num, "is_phd": has_phd, "evidence": _sentence_at(b, s)})
        else:
            entries.append({"years": None, "is_phd": has_phd, "evidence": ""})

    valid = [e for e in entries if e["years"] is not None]
    min_entry = min(valid, key=lambda e: e["years"]) if valid else None
    min_years_required = min_entry["years"] if min_entry else None
    phd_entries = [e for e in valid if e["is_phd"]]
    phd_entry = min(phd_entries, key=lambda e: e["years"]) if phd_entries else None
    phd_branch_years = phd_entry["years"] if phd_entry else None
    other_years = [e["years"] for e in valid if not e["is_phd"]]
    phd_alt = (phd_branch_years is not None and any(o > phd_branch_years for o in other_years)) \
        or bool(_IN_LIEU_RE.search(text))
    has_equiv = bool(_EQUIV_RE.search(text))

    ng = _NEW_GRAD_RE.search(core or "") or _NEW_GRAD_RE.search(text)
    level_hint = None
    if ng:
        src = core if _NEW_GRAD_RE.search(core or "") else text
        eff, evidence, level_hint = 0, _sentence_at(src, ng.start()), "new_grad"
    elif phd_entry is not None:
        eff, evidence = phd_branch_years, phd_entry["evidence"]
    elif min_entry is not None:
        eff, evidence = min_years_required, min_entry["evidence"]
    else:
        eff, evidence = None, ""
    return {
        "min_years_required": min_years_required,
        "phd_branch_years": phd_branch_years,
        "phd_alt_path": "Y" if phd_alt else "",
        "has_equivalent_clause": has_equiv,
        "eff_min_years": eff,
        "years_evidence": evidence,
        "preferred_years": None,
        "level_hint": level_hint,
    }


def extract_preferred_years(preferred_text: str) -> Optional[int]:
    """preferred 节单独抽年限（最小值），不参与 eff。"""
    ys = _find_years(preferred_text or "")
    return min(y for y, _, _ in ys) if ys else None


# ---------------------------------------------------------------------------
# 6. 限制旗标
# ---------------------------------------------------------------------------

RESTRICTION_PATTERNS = [
    ("ITAR", [re.compile(r"\bITAR\b"), re.compile(r"International Traffic in Arms", re.I)]),
    ("EXPORT_CONTROL", [re.compile(r"export control|deemed export|export licen", re.I), re.compile(r"\bEAR\b")]),
    ("US_PERSON", [re.compile(r"U\.?S\.? [Pp]erson")]),
    ("US_CITIZEN_REQUIRED", [re.compile(
        r"(?:must be|required to be|only) (?:a )?U\.?S\.? citizen|citizenship (?:is )?required", re.I)]),
    ("CLEARANCE", [re.compile(r"security clearance|obtain and maintain .{0,40}clearance", re.I),
                   re.compile(r"\b(?:TS/SCI|Top Secret|Secret|Public Trust)(?: clearance)?\b")]),
    ("NO_SPONSOR", [re.compile(
        r"(?:(?:will|do|does|can)(?: ?not|n't)|cannot|won't)\s+(?:be able to\s+)?(?:\w+ly\s+)?"
        r"(?:sponsor|provide (?:visa )?sponsorship)|not eligible for (?:visa )?sponsorship|unable to sponsor"
        r"|no (?:visa )?sponsorship|sponsorship is not available"
        # "now or in the future" 单独出现会误报（2026-09-30 实测），必须和 sponsorship 同句
        r"|sponsorship[^.]{0,80}now or in the future|now or in the future[^.]{0,80}sponsor"
        r"|(?:are|is|am)\s+not\s+able\s+to\s+(?:\w+ly\s+)?(?:sponsor|provide (?:visa )?sponsorship)"
        r"|(?:do|does|will) not (?:currently )?offer (?:visa |immigration )?sponsorship|not (?:currently )?sponsoring"
        # Lilly 式："will not provide support for or sponsor work authorization and/or visas"
        r"|(?:will|does|do|can)(?: ?not|n't)\s+provide\s+support\s+for[^.]{0,40}(?:sponsor|visa|work authorization)"
        r"|(?:requires?|must (?:have|possess))\s+permanent\s+work\s+authorization"
        r"|(?:unable|not able) to (?:accept|consider|hire)[^.]{0,60}\b(?:OPT|CPT|F-?1)\b"
        r"|(?:not|aren't|isn't) (?:eligible|accepting)[^.]{0,40}\b(?:OPT|CPT|STEM OPT)\b", re.I)]),
    ("AUTH_NO_SPONSOR", [re.compile(
        r"(?:authori[sz]ed|eligible) to work in the (?:United States|U\.?S\.?)[^.]{0,120}without[^.]{0,60}sponsorship", re.I)]),
    ("CLOUD_BG_CHECK", [re.compile(r"Microsoft Cloud Background Check", re.I)]),
    # 只挡 STEM OPT 延期、不挡初始 OPT 和 H-1B（Mayo），单列一类，不进漏斗的 VISA_FLAGS
    ("NO_STEM_OPT", [re.compile(r"(?:does|do|will) not[^.]{0,30}participate in[^.]{0,60}STEM", re.I)]),
    # 软性措辞：不排除，只登记（Amgen "Sponsorship for this role is not guaranteed."）
    ("SPONSOR_NOT_GUARANTEED", [re.compile(
        r"sponsorship[^.]{0,60}(?:is )?not guaranteed|sponsorship (?:may|might) (?:not )?be (?:available|offered|considered)"
        r"|sponsorship[^.]{0,40}case[- ]by[- ]case", re.I)]),
]
# 签证类措辞常写在 EEO / 福利样板之后，要在全文上找；其余旗标只看正文，避免样板误报
FULLTEXT_FLAGS = {"NO_SPONSOR", "AUTH_NO_SPONSOR", "NO_STEM_OPT", "SPONSOR_NOT_GUARANTEED"}


def extract_restrictions(core: str, full: str = None) -> tuple:
    """匹配限制旗标，返回 (flags, evidence)；evidence 为 "FLAG: 命中句"（<=300 字符）。

    签证类（FULLTEXT_FLAGS）在全文 full 上找，其余只在 core 上找；不传 full 时都用 core。
    """
    flags, evidence = [], []
    for flag, regexes in RESTRICTION_PATTERNS:
        text = (full if full is not None and flag in FULLTEXT_FLAGS else core) or ""
        for rx in regexes:
            m = rx.search(text)
            if m:
                flags.append(flag)
                evidence.append(flag + ": " + _sentence_at(text, m.start()))
                break
    return flags, evidence


# ---------------------------------------------------------------------------
# 7. 薪资
# ---------------------------------------------------------------------------

_NV_PAY_RE = re.compile(r"([\d,]+)\s*USD\s*(?:-|\u2013)\s*([\d,]+)\s*USD\s*for\s*Level\s*(\d)", re.I)
_AMZ_PAY_RE = re.compile(r"from \$([\d,]+)/year in our lowest geographic market up to \$([\d,]+)/year", re.I)
_MS_PAY_RE = re.compile(r"USD \$([\d,]+) (?:-|\u2013) \$([\d,]+) per year", re.I)
_GOOG_PAY_RE = re.compile(r"base salary range .{0,60}?\$([\d,]+)(?:-|\u2013)\$([\d,]+)", re.I | re.S)
_GEN_PAY_RE = re.compile(r"\$\s?([\d,]{5,9})\s*(?:-|\u2013|to)\s*\$?\s?([\d,]{5,9})", re.I)
_K_PAY_RE = re.compile(r"(\$?)([\d.]+)\s?[Kk]\s*(?:-|\u2013|to)\s*\$?([\d.]+)\s?[Kk]\b")
_HOURLY_PAY_RE = re.compile(
    r"\$\s?(\d{2,3}(?:\.\d{1,2})?)\s*(?:-|\u2013|to)\s*\$?\s?(\d{2,3}(?:\.\d{1,2})?)\s*(?:/|per\s+)(?:hour|hr)", re.I)
_HOUR_CTX_RE = re.compile(r"/\s?hour|per hour|hourly|/\s?hr\b", re.I)
_MONTH_CTX_RE = re.compile(r"/\s?month|per month|monthly", re.I)
_PAY_CTX_RE = re.compile(r"salary|compensation|\bpay\b|base|range|total cash|\bOTE\b", re.I)
_CURRENCY_RE = re.compile(r"\b(CAD|EUR|GBP|AUD|INR|SGD|CHF|ILS|JPY|CNY|NZD)\b")


def _to_int(s) -> Optional[int]:
    try:
        v = float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return int(round(v))


def _mk_range(level, low, high, currency="USD", basis="year", evidence=""):
    if low is None and high is None:
        return None
    if low is None:
        low = high
    if high is None:
        high = low
    if low <= 0 or high <= 0 or low > high:
        return None
    return {"level": level or "", "low": int(low), "high": int(high), "currency": currency or "USD",
            "basis": basis or "year", "evidence": (evidence or "")[:300]}


def _basis_from_interval(interval) -> str:
    s = str(interval or "").lower()
    if "hour" in s:
        return "hour"
    if "month" in s:
        return "month"
    if "week" in s:
        return "week"
    if "day" in s:
        return "day"
    return "year"


def _structured_pay_ranges(sp) -> list:
    """把 Greenhouse / Ashby / Lever / JSON-LD 的结构化薪资字段转成统一区间；形状容错。"""
    out = []
    if sp is None or sp is False or sp == "" or sp == {} or sp == []:
        return out
    if isinstance(sp, list):
        for item in sp:
            out.extend(_structured_pay_ranges(item))
        return out
    if not isinstance(sp, dict):
        return out
    # Greenhouse
    if "pay_input_ranges" in sp:
        return _structured_pay_ranges(sp.get("pay_input_ranges"))
    if "min_cents" in sp or "max_cents" in sp:
        lo, hi = _to_int(sp.get("min_cents")), _to_int(sp.get("max_cents"))
        r = _mk_range(sp.get("title"), None if lo is None else lo // 100, None if hi is None else hi // 100,
                      sp.get("currency_type") or "USD", "year", "greenhouse pay_input_ranges")
        return [r] if r else []
    # Ashby
    if "compensationTiers" in sp or "summaryComponents" in sp:
        tiers = sp.get("compensationTiers") or []
        got = False
        for tier in tiers:
            comps = (tier or {}).get("components") or []
            for c in comps:
                if c.get("minValue") is None and c.get("maxValue") is None:
                    continue
                ctype = str(c.get("compensationType") or "")
                if ctype and ctype.lower() not in ("salary", "base", "basesalary", "base salary"):
                    continue
                r = _mk_range(tier.get("title") or "", _to_int(c.get("minValue")), _to_int(c.get("maxValue")),
                              c.get("currencyCode") or "USD", _basis_from_interval(c.get("interval")),
                              tier.get("tierSummary") or c.get("summary") or "ashby compensationTiers")
                if r:
                    out.append(r)
                    got = True
        if not got:
            for c in sp.get("summaryComponents") or []:
                if c.get("minValue") is None and c.get("maxValue") is None:
                    continue
                ctype = str(c.get("compensationType") or "")
                if ctype and ctype.lower() not in ("salary", "base", "basesalary", "base salary"):
                    continue
                r = _mk_range("", _to_int(c.get("minValue")), _to_int(c.get("maxValue")),
                              c.get("currencyCode") or "USD", _basis_from_interval(c.get("interval")),
                              sp.get("compensationTierSummary") or "ashby summaryComponents")
                if r:
                    out.append(r)
        return out
    # 嵌套壳
    for key in ("compensation", "salaryRange", "baseSalary", "salary", "pay"):
        if key in sp and isinstance(sp[key], (dict, list)):
            return _structured_pay_ranges(sp[key])
    # JSON-LD MonetaryAmount
    if isinstance(sp.get("value"), dict):
        v = sp["value"]
        r = _mk_range("", _to_int(v.get("minValue") or v.get("value")), _to_int(v.get("maxValue") or v.get("value")),
                      sp.get("currency") or v.get("currency") or "USD", _basis_from_interval(v.get("unitText")),
                      "json-ld baseSalary")
        return [r] if r else []
    # Lever salaryRange / 通用 {min,max}
    lo = sp.get("min", sp.get("minValue", sp.get("low", sp.get("from"))))
    hi = sp.get("max", sp.get("maxValue", sp.get("high", sp.get("to"))))
    if lo is not None or hi is not None:
        r = _mk_range(sp.get("title") or sp.get("level") or "", _to_int(lo), _to_int(hi),
                      sp.get("currency") or sp.get("currencyCode") or "USD",
                      _basis_from_interval(sp.get("interval") or sp.get("unitText")), "structured salaryRange")
        return [r] if r else []
    return out


def _text_pay_ranges(text: str) -> list:
    """按 NVIDIA -> Amazon -> Microsoft -> Google -> 通用 的顺序抽区间，命中一族即停。"""
    t = text or ""
    out = []

    def cur_at(pos):
        m = _CURRENCY_RE.search(t[pos:pos + 40])
        return m.group(1) if m else "USD"

    def basis_at(pos):
        win = t[pos:pos + 60]
        if _HOUR_CTX_RE.search(win):
            return "hour"
        if _MONTH_CTX_RE.search(win):
            return "month"
        return "year"

    for m in _NV_PAY_RE.finditer(t):
        r = _mk_range("Level " + m.group(3), _to_int(m.group(1)), _to_int(m.group(2)), "USD", "year",
                      _sentence_at(t, m.start()))
        if r:
            out.append(r)
    if out:
        return out
    m = _AMZ_PAY_RE.search(t)
    if m:
        r = _mk_range("lowest-highest geo", _to_int(m.group(1)), _to_int(m.group(2)), "USD", "year",
                      _sentence_at(t, m.start()))
        if r:
            return [r]
    ms = list(_MS_PAY_RE.finditer(t))
    if ms:
        labels = ["US", "SF Bay Area/NYC"]
        for i, m in enumerate(ms):
            r = _mk_range(labels[i] if i < 2 else "band %d" % (i + 1), _to_int(m.group(1)), _to_int(m.group(2)),
                          "USD", "year", _sentence_at(t, m.start()))
            if r and (r["low"], r["high"]) not in [(x["low"], x["high"]) for x in out]:
                out.append(r)
        if out:
            return out
    m = _GOOG_PAY_RE.search(t)
    if m:
        r = _mk_range("", _to_int(m.group(1)), _to_int(m.group(2)), "USD", "year", _sentence_at(t, m.start()))
        if r:
            return [r]
    seen = set()
    for m in _GEN_PAY_RE.finditer(t):
        lo, hi = _to_int(m.group(1)), _to_int(m.group(2))
        if lo is None or hi is None or lo < 1000:
            continue
        r = _mk_range("", lo, hi, cur_at(m.end()), basis_at(m.end()), _sentence_at(t, m.start()))
        if r and (r["low"], r["high"]) not in seen:
            seen.add((r["low"], r["high"]))
            out.append(r)
    if out:
        return out
    for m in _K_PAY_RE.finditer(t):
        if not m.group(1) and not _PAY_CTX_RE.search(t[max(0, m.start() - 80):m.start()]):
            continue
        try:
            lo, hi = int(round(float(m.group(2)) * 1000)), int(round(float(m.group(3)) * 1000))
        except ValueError:
            continue
        r = _mk_range("", lo, hi, cur_at(m.end()), basis_at(m.end()), _sentence_at(t, m.start()))
        if r and (r["low"], r["high"]) not in seen:
            seen.add((r["low"], r["high"]))
            out.append(r)
    if out:
        return out
    for m in _HOURLY_PAY_RE.finditer(t):
        r = _mk_range("", _to_int(m.group(1)), _to_int(m.group(2)), cur_at(m.end()), "hour", _sentence_at(t, m.start()))
        if r and (r["low"], r["high"]) not in seen:
            seen.add((r["low"], r["high"]))
            out.append(r)
    return out


def _annualize(v: int, basis: str) -> int:
    return {"hour": v * 2080, "month": v * 12, "week": v * 52, "day": v * 260}.get(basis, v)


def extract_pay(full_text: str, structured_pay=None) -> dict:
    """结构化字段优先，其后文本正则。pay_low/pay_high 按年化取 min/max，basis 原样保留。"""
    ranges = _structured_pay_ranges(structured_pay)
    if not ranges:
        ranges = _text_pay_ranges(full_text or "")
    parts = []
    for r in ranges:
        unit = "" if r["basis"] == "year" else "/" + r["basis"]
        s = "{:,}-{:,} {}{}".format(r["low"], r["high"], r["currency"], unit)
        if r.get("level"):
            s += " (" + str(r["level"]) + ")"
        parts.append(s)
    lows = [_annualize(r["low"], r["basis"]) for r in ranges]
    highs = [_annualize(r["high"], r["basis"]) for r in ranges]
    return {
        "pay_ranges": ranges,
        "pay_range": "; ".join(parts),
        "pay_low": min(lows) if lows else None,
        "pay_high": max(highs) if highs else None,
    }


# ---------------------------------------------------------------------------
# 8. 地点归一
# ---------------------------------------------------------------------------

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia", "PR": "Puerto Rico",
}
_STATE_BY_NAME = {v.lower(): k for k, v in US_STATES.items()}
_STATE_BY_NAME["washington d.c."] = "DC"
_STATE_BY_NAME["washington dc"] = "DC"
_STATE_BY_NAME["washington, d.c."] = "DC"
_CA_PROVINCES = {"ON", "BC", "QC", "AB", "MB", "SK", "NS", "NB", "NL", "PE", "NT", "YT", "NU"}
_COUNTRY_NAMES = {
    "us": "US", "usa": "US", "u.s.": "US", "u.s": "US", "u.s.a.": "US", "u.s.a": "US", "united states": "US",
    "united states of america": "US", "america": "US",
    "canada": "Canada", "united kingdom": "United Kingdom", "uk": "United Kingdom", "u.k.": "United Kingdom",
    "england": "United Kingdom", "scotland": "United Kingdom", "wales": "United Kingdom",
    "great britain": "United Kingdom", "germany": "Germany", "deutschland": "Germany", "france": "France",
    "india": "India", "israel": "Israel", "china": "China", "prc": "China", "taiwan": "Taiwan", "japan": "Japan",
    "korea": "South Korea", "south korea": "South Korea", "republic of korea": "South Korea",
    "singapore": "Singapore", "australia": "Australia", "netherlands": "Netherlands",
    "the netherlands": "Netherlands", "holland": "Netherlands", "ireland": "Ireland", "switzerland": "Switzerland",
    "sweden": "Sweden", "denmark": "Denmark", "finland": "Finland", "norway": "Norway", "poland": "Poland",
    "spain": "Spain", "italy": "Italy", "belgium": "Belgium", "austria": "Austria", "czech republic": "Czechia",
    "czechia": "Czechia", "brazil": "Brazil", "mexico": "Mexico", "argentina": "Argentina", "chile": "Chile",
    "colombia": "Colombia", "uae": "UAE", "united arab emirates": "UAE", "saudi arabia": "Saudi Arabia",
    "egypt": "Egypt", "south africa": "South Africa", "nigeria": "Nigeria", "kenya": "Kenya",
    "new zealand": "New Zealand", "philippines": "Philippines", "vietnam": "Vietnam", "thailand": "Thailand",
    "malaysia": "Malaysia", "indonesia": "Indonesia", "pakistan": "Pakistan", "bangladesh": "Bangladesh",
    "sri lanka": "Sri Lanka", "hong kong": "Hong Kong", "hong kong sar": "Hong Kong", "portugal": "Portugal",
    "greece": "Greece", "turkey": "Turkey", "hungary": "Hungary", "romania": "Romania", "ukraine": "Ukraine",
    "russia": "Russia", "serbia": "Serbia", "bulgaria": "Bulgaria", "croatia": "Croatia", "slovakia": "Slovakia",
    "slovenia": "Slovenia", "lithuania": "Lithuania", "latvia": "Latvia", "estonia": "Estonia",
    "luxembourg": "Luxembourg", "iceland": "Iceland", "qatar": "Qatar", "kuwait": "Kuwait", "jordan": "Jordan",
    "morocco": "Morocco", "costa rica": "Costa Rica", "peru": "Peru", "uruguay": "Uruguay", "panama": "Panama",
}
_ISO_COUNTRY = {
    "USA": "US", "CAN": "Canada", "GB": "United Kingdom", "GBR": "United Kingdom", "UK": "United Kingdom",
    "CN": "China", "CHN": "China", "JP": "Japan", "JPN": "Japan", "KR": "South Korea", "KOR": "South Korea",
    "TW": "Taiwan", "TWN": "Taiwan", "SG": "Singapore", "SGP": "Singapore", "AU": "Australia", "AUS": "Australia",
    "NZ": "New Zealand", "NZL": "New Zealand", "FR": "France", "FRA": "France", "IT": "Italy", "ITA": "Italy",
    "ES": "Spain", "ESP": "Spain", "NL": "Netherlands", "NLD": "Netherlands", "BE": "Belgium", "BEL": "Belgium",
    "CH": "Switzerland", "CHE": "Switzerland", "SE": "Sweden", "SWE": "Sweden", "DK": "Denmark", "DNK": "Denmark",
    "FI": "Finland", "FIN": "Finland", "NO": "Norway", "NOR": "Norway", "PL": "Poland", "POL": "Poland",
    "CZ": "Czechia", "CZE": "Czechia", "AT": "Austria", "AUT": "Austria", "IE": "Ireland", "IRL": "Ireland",
    "PT": "Portugal", "PRT": "Portugal", "BR": "Brazil", "BRA": "Brazil", "MX": "Mexico", "MEX": "Mexico",
    "AR": "Argentina", "ARG": "Argentina", "CL": "Chile", "CHL": "Chile", "CO": "Colombia", "COL": "Colombia",
    "AE": "UAE", "ARE": "UAE", "SA": "Saudi Arabia", "SAU": "Saudi Arabia", "EG": "Egypt", "EGY": "Egypt",
    "ZA": "South Africa", "ZAF": "South Africa", "PH": "Philippines", "PHL": "Philippines", "VN": "Vietnam",
    "VNM": "Vietnam", "TH": "Thailand", "THA": "Thailand", "MY": "Malaysia", "MYS": "Malaysia",
    "ID": "Indonesia", "IDN": "Indonesia", "PK": "Pakistan", "PAK": "Pakistan", "HK": "Hong Kong",
    "HKG": "Hong Kong", "GR": "Greece", "GRC": "Greece", "TR": "Turkey", "TUR": "Turkey", "HU": "Hungary",
    "HUN": "Hungary", "RO": "Romania", "ROU": "Romania", "UA": "Ukraine", "UKR": "Ukraine", "RU": "Russia",
    "RUS": "Russia", "RS": "Serbia", "SRB": "Serbia", "BG": "Bulgaria", "BGR": "Bulgaria", "HR": "Croatia",
    "HRV": "Croatia", "LT": "Lithuania", "LTU": "Lithuania", "LV": "Latvia", "LVA": "Latvia", "EE": "Estonia",
    "EST": "Estonia", "LU": "Luxembourg", "LUX": "Luxembourg", "QA": "Qatar", "QAT": "Qatar", "IN": "India",
    "IND": "India", "DE": "Germany", "DEU": "Germany", "IL": "Israel", "ISR": "Israel", "CA": "Canada",
}
# 常见美国城市 -> 州（用于没有州/国家标记的地点串；Cambridge 故意不收，与英国同名）
_US_CITY_STATE = {
    "seattle": "WA", "redmond": "WA", "bellevue": "WA", "kirkland": "WA", "santa clara": "CA", "san jose": "CA",
    "sunnyvale": "CA", "mountain view": "CA", "palo alto": "CA", "menlo park": "CA", "san francisco": "CA",
    "south san francisco": "CA", "oakland": "CA", "berkeley": "CA", "san mateo": "CA", "redwood city": "CA",
    "foster city": "CA", "cupertino": "CA", "los angeles": "CA", "santa monica": "CA", "irvine": "CA",
    "san diego": "CA", "la jolla": "CA", "sacramento": "CA", "milpitas": "CA", "fremont": "CA",
    "pleasanton": "CA", "emeryville": "CA", "san bruno": "CA", "portland": "OR", "hillsboro": "OR",
    "beaverton": "OR", "denver": "CO", "boulder": "CO", "austin": "TX", "dallas": "TX", "plano": "TX",
    "houston": "TX", "san antonio": "TX", "chicago": "IL", "north chicago": "IL", "lake forest": "IL",
    "deerfield": "IL", "boston": "MA", "waltham": "MA", "burlington": "MA", "andover": "MA",
    "marlborough": "MA", "framingham": "MA", "lexington": "MA", "bedford": "MA", "needham": "MA",
    "natick": "MA", "new york": "NY", "new york city": "NY", "nyc": "NY", "brooklyn": "NY", "rochester": "NY",
    "jersey city": "NJ", "newark": "NJ", "princeton": "NJ", "morristown": "NJ", "basking ridge": "NJ",
    "philadelphia": "PA", "pittsburgh": "PA", "washington": "DC", "arlington": "VA", "reston": "VA",
    "mclean": "VA", "herndon": "VA", "bethesda": "MD", "rockville": "MD", "baltimore": "MD", "atlanta": "GA",
    "miami": "FL", "orlando": "FL", "tampa": "FL", "nashville": "TN", "memphis": "TN", "raleigh": "NC",
    "durham": "NC", "chapel hill": "NC", "charlotte": "NC", "cary": "NC", "minneapolis": "MN",
    "cleveland": "OH", "columbus": "OH", "cincinnati": "OH", "detroit": "MI", "ann arbor": "MI",
    "madison": "WI", "milwaukee": "WI", "salt lake city": "UT", "phoenix": "AZ", "scottsdale": "AZ",
    "tempe": "AZ", "tucson": "AZ", "las vegas": "NV", "st. louis": "MO", "st louis": "MO", "kansas city": "MO",
    "indianapolis": "IN", "new haven": "CT", "stamford": "CT", "hartford": "CT", "providence": "RI",
    "boise": "ID", "albuquerque": "NM", "omaha": "NE", "louisville": "KY", "new orleans": "LA",
    "honolulu": "HI", "anchorage": "AK", "sf": "CA", "bay area": "CA", "sf bay area": "CA",
    "silicon valley": "CA", "dc": "DC", "la": "CA",
}
# 常见非美国科技/医疗城市 -> 国家：防止 "Bangalore, IN" / "Tel Aviv, IL" 里的 ISO 码被当成 Indiana / Illinois
_NONUS_CITY_COUNTRY = {
    "bangalore": "India", "bengaluru": "India", "hyderabad": "India", "pune": "India", "mumbai": "India",
    "chennai": "India", "delhi": "India", "new delhi": "India", "gurgaon": "India", "gurugram": "India",
    "noida": "India", "london": "United Kingdom", "manchester": "United Kingdom", "edinburgh": "United Kingdom",
    "oxford": "United Kingdom", "toronto": "Canada", "vancouver": "Canada", "montreal": "Canada",
    "ottawa": "Canada", "waterloo": "Canada", "tel aviv": "Israel", "tel aviv-yafo": "Israel", "yokneam": "Israel",
    "haifa": "Israel", "jerusalem": "Israel", "berlin": "Germany", "munich": "Germany", "hamburg": "Germany",
    "erlangen": "Germany", "paris": "France", "amsterdam": "Netherlands", "eindhoven": "Netherlands",
    "best": "Netherlands", "dublin": "Ireland", "zurich": "Switzerland", "basel": "Switzerland",
    "singapore": "Singapore", "sydney": "Australia", "melbourne": "Australia", "tokyo": "Japan",
    "shanghai": "China", "beijing": "China", "shenzhen": "China", "hangzhou": "China", "taipei": "Taiwan",
    "hsinchu": "Taiwan", "seoul": "South Korea", "hong kong": "Hong Kong", "stockholm": "Sweden",
    "copenhagen": "Denmark", "helsinki": "Finland", "warsaw": "Poland", "madrid": "Spain", "barcelona": "Spain",
    "milan": "Italy", "lisbon": "Portugal", "sao paulo": "Brazil", "mexico city": "Mexico",
}
_US_MARKER_RE = re.compile(r"(?i:\bunited states(?: of america)?\b|\busa\b|\bu\.s\.a\.?)|\bUS\b|\bU\.S\.")
_REMOTE_RE = re.compile(r"\b(?:remote|virtual|work from home|wfh|anywhere)\b", re.I)
_REMOTE_TOKEN_RE = re.compile(
    r"^(?:remote|virtual|hybrid|on-?site|remote[- ]friendly|fully remote|work from home|wfh|anywhere|flexible)$", re.I)
_OFFICE_SUFFIX_RE = re.compile(r"\s+(?:office|hq|headquarters|campus|metro(?:politan)?(?: area)?|area)$", re.I)
_MULTI_LOC_RE = re.compile(r"^\d+\s+locations?$", re.I)
_WORK_SITE_RE = re.compile(r"\b(hybrid|remote|on-?site|in[- ]office)\b", re.I)
_WORK_SITE_CTX_RE = re.compile(r"location|work model|work arrangement|workplace|work setup|work environment", re.I)


_FILLER_WORD_RE = re.compile(
    r"\b(?:preferred|onsite|on-site|hybrid|remote|only|based|or|and|the|in|near|greater)\b", re.I)


def _is_state_token(t: str) -> bool:
    return (len(t) == 2 and t.isupper() and t in US_STATES) or t.lower() in _STATE_BY_NAME


def _city_key(t: str) -> str:
    """地点 token -> 城市表键：去 office/HQ 后缀与 preferred/onsite 等填充词；
    整串不在表里时试尾部 1-3 个词（"PathAI Boston" -> boston）。"""
    s = _OFFICE_SUFFIX_RE.sub("", t.strip()).lower()
    if s in _US_CITY_STATE:
        return s
    s2 = _SPACES_RE.sub(" ", _FILLER_WORD_RE.sub(" ", s)).strip(" ,.-")
    if s2 in _US_CITY_STATE:
        return s2
    words = s2.split(" ")
    for n in (3, 2, 1):
        if len(words) >= n:
            tail = " ".join(words[-n:])
            if tail in _US_CITY_STATE:
                return tail
    return s2 or s


def _parse_one_location(raw) -> dict:
    """单个地点串 -> {raw, country, state, city, remote, multi}。"""
    s = _norm_inline(raw)
    info = {"raw": s, "country": None, "state": None, "city": None, "remote": False, "multi": False}
    if not s:
        return info
    info["remote"] = bool(_REMOTE_RE.search(s))
    if _MULTI_LOC_RE.match(s):
        info["multi"] = True
        return info
    tokens = [t.strip(" .") for t in re.split(r"[,;/|()]|\s-\s|\s+or\s+", s)]
    tokens = [t for t in tokens if t]
    body = [t for t in tokens if not _REMOTE_TOKEN_RE.fullmatch(t)]
    country = "US" if _US_MARKER_RE.search(s) else None
    if country is None:
        for t in body:
            c = _COUNTRY_NAMES.get(t.lower())
            if c:
                country = c
                break
    # "City, ST" 且 ST 是美国州代码 → 先判美国（2026-09-23 修：Dublin, OH / Waterloo, IA / Vancouver, WA 曾被外国城市表抢走）。
    # IN / TN / CA 同时是印度、泰米尔纳德邦、加拿大的常见代码，不走这条。
    if country is None and len(body) >= 2 and any(
            len(t) == 2 and t.isupper() and t in US_STATES and t not in ("IN", "TN", "CA") for t in body[1:]):
        country = "US"
    if country is None:
        for t in body:
            c = _NONUS_CITY_COUNTRY.get(_city_key(t))
            if c:
                country = c
                break
    if country is None and body:
        first = body[0]
        if re.fullmatch(r"[A-Z]{2,3}", first) and (
                len(body) >= 3 or (len(body) == 2 and not _is_state_token(body[1]) and _city_key(body[1]) not in _US_CITY_STATE)):
            if first in ("US", "USA"):
                country = "US"
            elif first in ("CA", "CAN") and len(body) >= 2 and body[1].upper() in _CA_PROVINCES:
                country = "Canada"
            elif first in _ISO_COUNTRY and _ISO_COUNTRY[first] != "US":
                country = _ISO_COUNTRY[first]
    if country is None and any(len(t) == 2 and t.isupper() and t in _CA_PROVINCES for t in body):
        country = "Canada"
    if country is None and any(_is_state_token(t) for t in body):
        country = "US"
    if country is None and any(_city_key(t) in _US_CITY_STATE for t in body):
        country = "US"
    info["country"] = country
    if country == "US":
        state, state_from_code = None, False
        for t in body:
            if len(t) == 2 and t.isupper() and t in US_STATES:
                state, state_from_code = t, True
                break
        if state is None:
            for t in body:
                c = _STATE_BY_NAME.get(t.lower())
                if c:
                    state = c
                    break
        city = None
        for t in body:
            tl = t.lower()
            if _COUNTRY_NAMES.get(tl) == "US" or t in ("US", "USA"):
                continue
            if _is_state_token(t) and not (_city_key(t) in _US_CITY_STATE and state and state != _STATE_BY_NAME.get(tl)):
                continue
            city = t
            break
        if city is not None:
            ck = _city_key(city)
            if ck in _US_CITY_STATE and (state is None or not state_from_code):
                state = _US_CITY_STATE[ck]
            if ck == "sf":
                city = "San Francisco"
            elif ck == "nyc":
                city = "New York"
            elif ck == "dc":
                city = "Washington"
            elif ck in _US_CITY_STATE and len(ck) > 2:
                city = ck.title()
            else:
                city = _OFFICE_SUFFIX_RE.sub("", city)
        info["state"], info["city"] = state, city
    else:
        for t in body:
            if _COUNTRY_NAMES.get(t.lower()) or (re.fullmatch(r"[A-Z]{2,3}", t) and t in _ISO_COUNTRY) \
                    or (len(t) == 2 and t.isupper() and t in _CA_PROVINCES):
                continue
            info["city"] = t
            break
    return info


def _display_location(p: dict) -> str:
    if p["multi"]:
        return p["raw"]
    if p["country"] == "US":
        core = ", ".join(x for x in (p["city"], p["state"]) if x) or "US"
    elif p["country"]:
        core = ", ".join(x for x in (p["city"], p["country"]) if x)
    else:
        core = p["raw"]
    if p["remote"] and core and not _REMOTE_RE.search(core):
        core = "Remote (" + core + ")"
    return core or p["raw"]


def _country_from_hint(h) -> Optional[str]:
    if not h:
        return None
    hs = _norm_inline(h)
    c = _COUNTRY_NAMES.get(hs.lower())
    if c:
        return c
    if re.fullmatch(r"[A-Z]{2,3}", hs):
        if hs in ("US", "USA"):
            return "US"
        return _ISO_COUNTRY.get(hs)
    return _parse_one_location(hs)["country"]


def _work_site_from_hint(v) -> Optional[str]:
    s = str(v or "").strip().lower()
    if not s:
        return None
    if "hybrid" in s:
        return "hybrid"
    if "remote" in s or "virtual" in s or "home" in s:
        return "remote"
    if "onsite" in s or "on-site" in s or "on site" in s or "office" in s or "in person" in s:
        return "onsite"
    return None


def normalize_location(raw_list: list, hints: Optional[dict] = None) -> dict:
    """地点归一。返回 region/state/location/remote_us/work_site/countries。

    region: 美国 / 远程-美国 / 多地含美国 / 非美国 / 未知（未知在漏斗里按放行处理）。
    """
    hints = hints or {}
    raws = [r for r in (raw_list or []) if r]
    if not raws and hints.get("location_raw"):
        raws = [hints["location_raw"]]
    parsed = [_parse_one_location(r) for r in raws]
    hint_countries = []
    hc = hints.get("countries_hint") or []
    if isinstance(hc, str):
        hc = [hc]
    for h in hc:
        c = _country_from_hint(h)
        if c and c not in hint_countries:
            hint_countries.append(c)
    if len(hint_countries) == 1:
        for p in parsed:
            if p["country"] is None and not p["multi"]:
                p["country"] = hint_countries[0]
    countries = []
    for p in parsed:
        if p["country"] and p["country"] not in countries:
            countries.append(p["country"])
    if not countries:
        countries = list(hint_countries)

    title = _norm_inline(hints.get("title") or "")
    work_site = (_work_site_from_hint(hints.get("workplace_type"))
                 or ("remote" if hints.get("is_remote") is True and not hints.get("workplace_type") else None)
                 or _work_site_from_hint(hints.get("work_site_hint")))
    if not work_site and re.search(r"\bremote\b", title, re.I):
        work_site = "remote"
    if not work_site and parsed and all(p["remote"] for p in parsed):
        work_site = "remote"
    if not work_site and hints.get("jd_core"):
        jd = hints["jd_core"]
        for m in _WORK_SITE_CTX_RE.finditer(jd):
            w = _WORK_SITE_RE.search(jd[max(0, m.start() - 80):m.end() + 80])
            if w:
                work_site = _work_site_from_hint(w.group(1))
                break
    work_site = work_site or "unknown"

    us_locs = [p for p in parsed if p["country"] == "US"]
    has_us = "US" in countries
    non_us = [c for c in countries if c != "US"]
    remote_us = has_us and (any(p["remote"] for p in us_locs) or work_site == "remote")
    if has_us and non_us:
        region = "多地含美国"
    elif has_us:
        all_remote = bool(us_locs) and all(p["remote"] for p in us_locs)
        region = "远程-美国" if (all_remote or (not us_locs and work_site == "remote")) else "美国"
    elif non_us:
        region = "非美国"
    else:
        region = "未知"
    state = next((p["state"] for p in us_locs if p["state"]), None)
    displays = []
    for p in parsed:
        d = _display_location(p)
        if d and d not in displays:
            displays.append(d)
    location = "; ".join(displays)
    return {"region": region, "state": state, "location": location, "remote_us": bool(remote_us),
            "work_site": work_site, "countries": countries}


# ---------------------------------------------------------------------------
# 9. 职族
# ---------------------------------------------------------------------------

EXCLUSION_REGEX = (
    r"sales|account (?:executive|manager|director)|marketing|product manager|program manager|project manager"
    r"|developer relations|devrel|advocate|evangelist"
    r"|clinical (?:specialist|trial|research coordinator|operations|educator|consultant|liaison|scientist)"
    r"|interpreter|translator|recruit|analyst|customer success|support engineer|technical writer|designer|\bUX\b"
    r"|counsel|finance|accountant|nurse|physician|pharmacist|field service|regulatory|compliance|help desk"
    r"|hardware|\bASIC\b|\bRTL\b|\bFPGA\b|mechanical|electrical engineer|quality engineer|manager|director"
    r"|head of|\bvp\b|chief|intern"
    # 2026-09-24 补（D 档传统医疗公司噪音）：检验、影像技师、制造与现场工程
    r"|laborator(?:y|ies) scientist|\blab scientist|med(?:ical)? lab|technologist|technician|\bextern\b|sonographer"
    r"|phlebotom|histotech|cytotech|dosimetrist|therapist|pathologist|attending|industrial engineer"
    r"|manufacturing engineer|process engineer|validation engineer|service engineer|automation engineer"
    r"|instrumentation|biomedical equipment|facilities"
)
INTERN_REGEX = r"\bintern(?:ship)?\b|\bco-?op\b"
ML_WORDS_REGEX = (r"machine learning|\bml\b|\bai\b|deep learning|llm|neural|computer vision|\bnlp\b|mlops"
                  r"|model (?:training|inference|serving|deployment)")   # 2026-09-24 收紧：去掉 model / training / imaging 泛词
SDE_TITLE_REGEX = (
    r"software (?:development )?engineer|software developer|\bsde\b|\bswe\b|backend|full[- ]stack"
    r"|platform engineer|infrastructure engineer|developer technology|devtech"
)
ROLE_FAMILY_REGEX = [
    ("SA_HEALTH", r"solutions? architect|solution engineer|customer engineer|forward deployed"),
    ("RESEARCH_SCI", r"research scientist|scientist, research|research fellow|researcher"),
    ("RESEARCH_ENG", r"research engineer"),
    ("DATA_SCI", r"data scientist"),   # 2026-09-24 前移：原先被下一行的 \bscientist\b 先抢走
    ("APPLIED_SCI", r"applied scientist|applied research(?:er)?|\bscientist\b"),
    ("MLE", r"machine learning engineer|\bml engineer|\bmle\b|deep learning engineer|computer vision engineer"
            r"|nlp engineer|llm engineer|algorithms? engineer|ai/ml engineer|perception engineer|ml scientist"
            r"|machine learning scientist"),
    ("AI_ENG", r"\bai engineer|genai|generative ai engineer|applied ai|agent engineer|member of technical staff|\bmts\b"),
    ("SDE", SDE_TITLE_REGEX),
]
ENGINEER_FALLBACK_REGEX = r"\b(?:architect|engineer|developer|programmer)\b"   # 2026-09-24 去掉 technologist（CT / MRI 技师）
# 泛科学类标题（Scientist / Research Scientist / Researcher / Research Fellow / Postdoc）要有 ML 信号才算核心职族
SCI_TITLE_ML_REGEX = (r"machine learning|\bML\b|\bAI\b|artificial intelligence|deep learning|computer vision|\bNLP\b"
                      r"|natural language|\bLLMs?\b|computational|data scien|bioinformatic|algorithm|informatics|imaging ai"
                      r"|generative|foundation model|multimodal|digital health")
SCI_JD_ML_REGEX = (r"machine learning|deep learning|artificial intelligence|neural network|computer vision"
                   r"|natural language processing|\bNLP\b|\bLLMs?\b|large language model|foundation model|transformer"
                   r"|generative AI|reinforcement learning|pytorch|tensorflow|\bAI\b|\bML\b")
CORE_FAMILIES = ("MLE", "SDE_ML", "APPLIED_SCI", "AI_ENG", "RESEARCH_ENG", "RESEARCH_SCI")

_EXCLUSION_RX = re.compile(EXCLUSION_REGEX, re.I)
_INTERN_RX = re.compile(INTERN_REGEX, re.I)
_ML_WORDS_RX = re.compile(ML_WORDS_REGEX, re.I)
_ROLE_RX = [(fam, re.compile(rx, re.I)) for fam, rx in ROLE_FAMILY_REGEX]
_SCI_TITLE_ML_RX = re.compile(SCI_TITLE_ML_REGEX, re.I)
_SCI_JD_ML_RX = re.compile(SCI_JD_ML_REGEX, re.I)
_LEADERSHIP_TITLE_RX = re.compile(
    r"\b(?:manager|mgr|director|head of|vp)\b|\b(?:technical|tech|team|engineering)\s+lead(?:er)?\b"
    r"|\blead\s+(?:(?:software|machine learning|ml|ai|data|research|applied|platform|technical)\s+){0,3}"
    r"(?:engineer|scientist|architect|developer|researcher)\b", re.I)
_PEOPLE_MANAGEMENT_RX = re.compile(
    r"\b(?:experience|proven|demonstrated|required)\b[^\n.;]{0,60}\b(?:people|line)[- ]management\b"
    r"|\b(?:experience|track record|background)\b[^\n.;]{0,60}\b(?:managing|supervising|leading)\b"
    r"[^\n.;]{0,40}\b(?:teams?|people|engineers?|scientists?)\b"
    r"|\b(?:manage|supervise)\s+(?:an?\s+)?"
    r"(?:(?:data|science|engineering|research|software|machine|learning|ml|ai|technical|cross[- ]functional)\s+){0,4}"
    r"(?:teams?\b|direct reports?\b)"
    r"|\b(?:feedback|career development|performance reviews?)\b[^\n.;]{0,40}\bdirect reports\b", re.I)
_TECH_LEAD_RESPONSIBILITY_RX = re.compile(
    r"\b(?:serve|act|work|function)\s+as\s+(?:(?:the|an?|our)\s+)?(?:technical|tech|team)\s+lead(?:er)?\b"
    r"|\byou\s+(?:will|must)\s+be\s+(?:(?:the|an?|our)\s+)?(?:technical|tech|team)\s+lead(?:er)?\b", re.I)
_OPTIONAL_MANAGEMENT_RX = re.compile(
    r"\bpreferred\b|nice to have|bonus|a plus|with or without direct reports"
    r"|\bfor (?:a|the) people(?: / program)? management position\b|\bmentoring or managing\b", re.I)
_NEGATED_MANAGEMENT_RX = re.compile(
    r"\b(?:management|teams?|people|engineers?|scientists?|lead(?:er)?)\b[^,;\n]{0,35}\bnot\s+(?:required|needed|expected)\b"
    r"|\bno\b[^,;\n]{0,45}\b(?:management|teams?|people|engineers?|scientists?)\b"
    r"|\b(?:will|must|need)\s+not\s+(?:manage|supervise|serve|act|be)\b", re.I)


def leadership_requirement(title: str, jd_core: str) -> bool:
    """管理 / Tech Lead 职称或明确带人要求；普通 lead project / experiment 不算。"""
    if _LEADERSHIP_TITLE_RX.search(title or ""):
        return True
    sections, found = split_sections(jd_core or "")
    # preferred 的带人加分项不当成硬门；未分节时只检查未标成 preferred 的行。
    text = "\n".join(sections[k] for k in ("minimum", "responsibilities")) if found else (jd_core or "")
    # 分号 / 句号隔开的 required 与 preferred 各自判断，避免一句加分项抹掉同一行的硬要求。
    for clause in re.split(r"[\n;]|(?<=[.!?])\s+", text):
        if _OPTIONAL_MANAGEMENT_RX.search(clause) or _NEGATED_MANAGEMENT_RX.search(clause):
            continue
        if _PEOPLE_MANAGEMENT_RX.search(clause) or _TECH_LEAD_RESPONSIBILITY_RX.search(clause):
            return True
    return False


_SPECIFIC_SCI_RX = re.compile(r"applied scientist|applied research", re.I)


def _sci_needs_ml(fam: str, title: str, jd_core: str, ev: str) -> tuple:
    """泛科学类标题：标题有 ML 词，或 JD core 有 ≥2 个不同的强 ML 词，才保留该职族；否则 OTHER。"""
    if _SPECIFIC_SCI_RX.search(title) or _SCI_TITLE_ML_RX.search(title):
        return fam, ev
    hits = []
    for mm in _SCI_JD_ML_RX.finditer(jd_core or ""):
        w = mm.group(0).lower()
        if w not in hits:
            hits.append(w)
    if len(hits) >= 2:
        return fam, ev + "; jd ml words: " + ", ".join(hits[:5])
    return "OTHER", ev + "; generic science title without ML signal (jd hits: " + ", ".join(hits) + ")"
_ENGINEER_FALLBACK_RX = re.compile(ENGINEER_FALLBACK_REGEX, re.I)


def _sde_or_sde_ml(title: str, department: str, jd_core: str, evidence_prefix: str) -> tuple:
    td = (title or "") + " | " + (department or "")
    m = _ML_WORDS_RX.search(td)
    if m:
        return "SDE_ML", evidence_prefix + "; ml word in title/department: '" + m.group(0) + "'"
    hits = []
    for mm in _ML_WORDS_RX.finditer(jd_core or ""):
        w = mm.group(0).lower()
        if w not in hits:
            hits.append(w)
    if len(hits) >= 2:
        return "SDE_ML", evidence_prefix + "; jd ml words: " + ", ".join(hits[:5])
    return "SDE", evidence_prefix + "; no ml words (jd hits: " + ", ".join(hits) + ")"


def classify_role_family(title: str, department: str, jd_core: str, track=None) -> tuple:
    """先排除（intern -> INTERN，其余 -> OTHER），再按 ROLE_FAMILY_REGEX 顺序命中。返回 (family, evidence)。"""
    t = _norm_inline(title)
    m = _INTERN_RX.search(t)
    if m:
        return "INTERN", "title: '" + m.group(0) + "'"
    if leadership_requirement(t, jd_core):
        return "OTHER", "leadership title or explicit people-management requirement"
    m = _EXCLUSION_RX.search(t)
    if m:
        return "OTHER", "exclusion: '" + m.group(0) + "'"
    for fam, rx in _ROLE_RX:
        m = rx.search(t)
        if not m:
            continue
        ev = fam + " title: '" + m.group(0) + "'"
        if fam == "SDE":
            return _sde_or_sde_ml(t, department, jd_core, ev)
        if fam in ("RESEARCH_SCI", "APPLIED_SCI") and str(track or "").upper() in ("C", "D"):
            # 只对传统医疗公司（C/D 档）要求 ML 信号；B 档是 AI 原生公司，A 档已有 health 门
            return _sci_needs_ml(fam, t, jd_core, ev)
        return fam, ev
    m = _ENGINEER_FALLBACK_RX.search(t)
    if m:
        return _sde_or_sde_ml(t, department, jd_core, "fallback engineer-type title: '" + m.group(0) + "'")
    return "OTHER", "no role regex matched: '" + t + "'"


def print_regex() -> None:
    """--print-regex：打印职族/排除/职级正则，供浏览器兜底时人工对照。"""
    print("# EXCLUSION (title, case-insensitive) -> OTHER; intern/co-op -> INTERN")
    print(EXCLUSION_REGEX)
    print("# INTERN")
    print(INTERN_REGEX)
    print("# ROLE FAMILIES (title, case-insensitive, first match wins)")
    for fam, rx in ROLE_FAMILY_REGEX:
        print("%-13s %s" % (fam, rx))
    print("# SDE -> SDE_ML when ML words in title/department, or >=2 distinct ML words in jd core:")
    print(ML_WORDS_REGEX)
    print("# fallback engineer-type title (goes through the SDE/SDE_ML rule):")
    print(ENGINEER_FALLBACK_REGEX)
    print("# CORE families:", ", ".join(CORE_FAMILIES))
    print("# LEVEL (title)")
    for lvl, rx in _LEVEL_PATTERNS:
        print("%-10s %s" % (lvl, rx.pattern))
    print("# HEALTHCARE terms")
    print(_HC_TERM_RE.pattern)


# ---------------------------------------------------------------------------
# 10. 职级
# ---------------------------------------------------------------------------

_LEVEL_ORDER = ["new_grad", "early", "mid", "senior", "staff", "principal", "director"]
_LEVEL_PATTERNS = [
    ("new_grad", re.compile(r"(?i:new (?:college )?grad|university grad|\bgraduate\b|\bgrad\b)")),
    ("early", re.compile(r"(?i:early career|\bentry\b|\bjunior\b|\bjr\b\.?|\bassociate\b|\bic2\b)|\bI\b|\b1\b")),
    ("mid", re.compile(r"(?i:\bmid\b|\bmid-level\b|\bic3\b)|\bII\b|\b2\b")),
    ("senior", re.compile(r"(?i:\bsenior\b|\bsr\b\.?|\bic4\b|\blead\b)|\bIII\b")),
    ("staff", re.compile(r"(?i:\bstaff\b|\bic5\b|\bic6\b)")),
    ("principal", re.compile(r"(?i:\bprincipal\b|\bprin\b|\bdistinguished\b|\bfellow\b)")),
    ("director", re.compile(r"(?i:\bdirector\b|\bhead of\b|\bvp\b)")),
]
_JUNIOR_FELLOW_RE = re.compile(r"research fellow|post-?doc|postdoctoral|clinical fellow|ai fellow", re.I)
_DUAL_SEP_RE = re.compile(r"[/&]|\bor\b", re.I)
_ALL_LEVELS_RE = re.compile(r"all levels|multiple levels|\bI/II\b|\bII/III\b|\bI-III\b|\bI-II\b", re.I)
_JD_ROLE_LEVEL_RE = re.compile(
    r"\b(?:seeking|hiring|looking for)\s+(?:an?\s+)?"
    r"(?:(?:highly|talented|motivated|experienced|exceptional|dedicated|skilled|passionate)\s+){0,4}"
    r"(senior|staff|principal|distinguished)\s+"
    r"(?:(?:research|applied|machine learning|ml|ai|data|software|platform|systems|clinical|computational)\s+){0,4}"
    r"(?:scientist|engineer|researcher|architect)\b", re.I)


def classify_level(title: str, eff_min_years, jd_core: str, level_hint=None, phd_branch_years=None) -> dict:
    """title / 明确自述职级 -> 年限回退；new_grad 提示不能抹掉明确资深职级。"""
    t = _norm_inline(title)
    # "Member of Technical Staff" 是泛称不是职级（2026-09-23 发现 Ataraxis / Kinetic / OpenEvidence 等 15 条被误丢）
    t = re.sub(r"\bmember\s+of\s+(the\s+)?technical\s+staff\b|\bMTS\b", " ", t, flags=re.I)
    matched = []
    for lvl, rx in _LEVEL_PATTERNS:
        m = rx.search(t)
        if m:
            if lvl == "principal" and m.group(0).lower() == "fellow" and _JUNIOR_FELLOW_RE.search(t):
                matched.append(("early", m.group(0)))
                continue
            matched.append((lvl, m.group(0)))
    distinct = []
    for lvl, _w in matched:
        if lvl not in distinct:
            distinct.append(lvl)
    multi_level = bool(_ALL_LEVELS_RE.search(t))
    level_tag, level_source = None, "none"
    if distinct:
        if len(distinct) >= 2 and _DUAL_SEP_RE.search(t):
            level_tag = min(distinct, key=_LEVEL_ORDER.index)
            multi_level = True
        else:
            level_tag = max(distinct, key=_LEVEL_ORDER.index)
        level_source = "title"
    body_level = _JD_ROLE_LEVEL_RE.search(jd_core or "")
    if body_level:
        body_tag = "principal" if body_level.group(1).lower() == "distinguished" else body_level.group(1).lower()
        if level_tag is None or _LEVEL_ORDER.index(body_tag) > _LEVEL_ORDER.index(level_tag):
            level_tag, level_source = body_tag, "jd_role"
    if level_hint == "new_grad" and level_tag not in ("senior", "staff", "principal", "director"):
        level_tag, level_source = "new_grad", "hint"
    if level_tag is None:
        y = eff_min_years
        if y is None:
            level_tag, level_source = "unknown", "none"
        else:
            level_tag = "early" if y <= 1 else "mid" if y <= 3 else "senior" if y <= 5 else "staff"
            level_source = "years"
    eff = eff_min_years
    if level_tag in ("staff", "principal", "director"):
        stretch = False
    else:
        stretch = ((level_tag == "senior" and eff is not None and eff <= 5)
                   or (level_tag in ("early", "mid", "unknown") and eff in (3, 4) and phd_branch_years is None)
                   or (eff is not None and eff >= 5))
    return {"level_tag": level_tag, "level_source": level_source, "multi_level": bool(multi_level),
            "stretch": bool(stretch)}


# ---------------------------------------------------------------------------
# 11. healthcare 命中
# ---------------------------------------------------------------------------

_HC_TERM_RE = re.compile(
    r"health(?:care)?|medical|clinical|clinic|patient|hospital|radiolog|patholog|imaging|\bMRI\b|\bCT\b"
    r"|ultrasound|genomic|biolog|biomed|biopharma|drug discovery|life sciences|\bEHR\b|\bEMR\b|FHIR|HIPAA|\bFDA\b"
    r"|oncolog|cardio|surgical|diagnos|MONAI|BioNeMo|Holoscan|(?<!Santa )Clara", re.I)
_CT_CTX_RE = re.compile(r"scan|imaging|radiolog", re.I)
_BENEFITS_CTX_RE = re.compile(r"dental|insurance|401\(?k|benefits? package|wellness|paid time off|\bPTO\b", re.I)


def _scan_hc(text: str, benefits_ctx: bool = True) -> list:
    # benefits_ctx 只对 JD 正文有意义；标题 / org 里的 "Health & Wellness" 是组名（2026-10-07：Alexa Health Tech 的 Applied Scientist 因此在 02 门被丢）
    out = []
    text = text or ""
    for m in _HC_TERM_RE.finditer(text):
        tok = m.group(0).lower()
        if tok == "ct" and not _CT_CTX_RE.search(text[max(0, m.start() - 80):m.end() + 80]):
            continue
        if benefits_ctx and tok in ("health", "healthcare", "medical") and _BENEFITS_CTX_RE.search(text[max(0, m.start() - 40):m.end() + 40]):
            continue
        if tok not in out:
            out.append(tok)
    return out


def healthcare_hits(title, org_unit, jd_core, track, list_hits) -> tuple:
    """返回 (distinct hits, primary source)。来源优先级 title > org > list > jd > company；B/C 档追加 company。"""
    list_text = " ".join(str(x) for x in (list_hits or []))
    sources = [("title", _scan_hc(_norm_inline(title), benefits_ctx=False)), ("org", _scan_hc(_norm_inline(org_unit), benefits_ctx=False)),
               ("list", _scan_hc(list_text)), ("jd", _scan_hc(jd_core or ""))]
    if list_hits and not sources[2][1]:
        sources[2] = ("list", [str(x).lower() for x in list_hits if str(x).strip()])
    hits, primary = [], ""
    for name, h in sources:
        if h and not primary:
            primary = name
        for tok in h:
            if tok not in hits:
                hits.append(tok)
    if str(track or "").upper() in ("B", "C", "D"):
        if "company" not in hits:
            hits.append("company")
        if not primary:
            primary = "company"
    return hits, primary


# ---------------------------------------------------------------------------
# 12. 总入口
# ---------------------------------------------------------------------------

def parse(record: dict) -> dict:
    """预解析记录 -> 补齐统一 schema 的全部解析字段（原字段保留）。"""
    rec = dict(record or {})
    jd_html = rec.get("jd_html") or ""
    jd_text = html_to_text(jd_html) if jd_html else html_to_text(rec.get("jd_text") or "")
    core, tail = cut_boilerplate(jd_text)
    sections, found = split_sections(core)
    years = extract_years(sections["minimum"], core)
    years["preferred_years"] = extract_preferred_years(sections["preferred"])
    flags, evidence = extract_restrictions(core, jd_text)
    pay = extract_pay(jd_text, rec.get("structured_pay"))
    locations = rec.get("locations") or ([rec["location_raw"]] if rec.get("location_raw") else [])
    loc = normalize_location(locations, {
        "location_raw": rec.get("location_raw"), "workplace_type": rec.get("workplace_type"),
        "is_remote": rec.get("is_remote"), "work_site_hint": rec.get("work_site_hint"),
        "countries_hint": rec.get("countries_hint"), "title": rec.get("title"), "jd_core": core,
    })
    family, fam_ev = classify_role_family(rec.get("title") or "", rec.get("department") or "", core, rec.get("track"))
    level = classify_level(rec.get("title") or "", years["eff_min_years"], core, years.get("level_hint"),
                           years["phd_branch_years"])
    org_text = " | ".join(x for x in (rec.get("org_unit"), rec.get("department")) if x)
    hits, src = healthcare_hits(rec.get("title") or "", org_text, core, rec.get("track"), rec.get("list_hits"))

    rec.update({
        "jd_text": jd_text, "jd_len": len(jd_text), "jd_core_len": len(core),
        "sections": sections, "sections_found": found,
    })
    rec.update(years)
    rec.update({"restriction_flags": flags, "restriction_evidence": evidence})
    rec.update(pay)
    rec.update(loc)
    rec.update({"role_family": family, "role_family_evidence": fam_ev, "role_core": family in CORE_FAMILIES})
    rec.update(level)
    rec.update({"healthcare_hits": hits, "healthcare_hit_source": src})
    return rec


# ---------------------------------------------------------------------------
# 13. 原始 JSON -> 预解析记录（CLI 调试用；正式路径由 adapters.normalize 生成）
# ---------------------------------------------------------------------------

_JD_KEYS = ("job_description", "jobDescription", "descriptionHtml", "content", "description", "descriptionPlain")


def _first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", False):
            return v
    return None


def record_from_item(item: dict) -> dict:
    """单条原始条目（Workday / Eightfold / Amazon / Greenhouse / Ashby / Lever / JSON-LD）-> 预解析记录。"""
    if not isinstance(item, dict):
        return {}
    if isinstance(item.get("jobPostingInfo"), dict):          # Workday 详情
        jp = item["jobPostingInfo"]
        locs = [jp.get("location")] + list(jp.get("additionalLocations") or [])
        return {
            "ats": "workday", "title": jp.get("title"), "req_id": jp.get("jobReqId"),
            "jd_html": jp.get("jobDescription"), "locations": [l for l in locs if l],
            "location_raw": jp.get("location"), "countries_hint": [((jp.get("country") or {}).get("descriptor"))],
            "work_site_hint": jp.get("remoteType"), "time_type": jp.get("timeType"), "can_apply": jp.get("canApply"),
            "posted": jp.get("startDate"),
        }
    if "job_description" in item:                                # Eightfold 详情
        return {
            "ats": "eightfold", "title": _first(item, "name", "posting_name", "title"),
            "req_id": _first(item, "display_job_id", "ats_job_id"), "jd_html": item.get("job_description"),
            "locations": item.get("locations") or ([item["location"]] if item.get("location") else []),
            "location_raw": item.get("location"), "department": item.get("department"),
            "org_unit": item.get("business_unit"),
            "work_site_hint": _first(item, "location_flexibility", "work_location_option"),
        }
    if "basic_qualifications" in item or "preferred_qualifications" in item:   # Amazon 列表元素
        parts = [item.get("description") or ""]
        if item.get("basic_qualifications"):
            parts.append("<p><b>Basic Qualifications</b></p>" + item["basic_qualifications"])
        if item.get("preferred_qualifications"):
            parts.append("<p><b>Preferred Qualifications</b></p>" + item["preferred_qualifications"])
        # Amazon 的 locations[] 是 JSON 字符串（含 location / normalizedLocation / type=ONSITE...）
        locs, site_types = [], []
        for x in (item.get("locations") or []):
            if isinstance(x, str) and x.lstrip().startswith("{"):
                try:
                    x = json.loads(x)
                except ValueError:
                    pass
            if isinstance(x, dict):
                v = x.get("location") or x.get("normalizedLocation")
                if x.get("type"):
                    site_types.append(str(x["type"]))
            else:
                v = x
            if v and v not in locs:
                locs.append(v)
        return {
            "ats": "amazon", "title": item.get("title"), "req_id": item.get("id_icims"), "jd_html": "<br/>".join(parts),
            "locations": locs or ([item["location"]] if item.get("location") else []),
            "location_raw": item.get("normalized_location") or item.get("location"),
            "countries_hint": [item.get("country_code")], "department": item.get("job_category"),
            "org_unit": item.get("business_category"), "posted": item.get("posted_date"),
            "work_site_hint": site_types[0] if len(set(site_types)) == 1 else None,
        }
    if "descriptionHtml" in item or "workplaceType" in item:      # Ashby
        locs = [item.get("location")] + [(s or {}).get("location") for s in (item.get("secondaryLocations") or [])]
        hints = []
        for addr in [item.get("address")] + [(s or {}).get("address") for s in (item.get("secondaryLocations") or [])]:
            pa = ((addr or {}).get("postalAddress") or {})
            if pa.get("addressCountry"):
                hints.append(pa["addressCountry"])
        return {
            "ats": "ashby", "title": item.get("title"), "req_id": item.get("id"),
            "jd_html": item.get("descriptionHtml") or item.get("descriptionPlain"),
            "locations": [l for l in locs if l], "location_raw": item.get("location"), "countries_hint": hints,
            "department": item.get("department"), "org_unit": item.get("team"),
            "workplace_type": item.get("workplaceType"), "is_remote": item.get("isRemote"),
            "structured_pay": item.get("compensation"), "posted": item.get("publishedAt"),
        }
    if "absolute_url" in item or "pay_input_ranges" in item:     # Greenhouse
        deps = item.get("departments") or []
        return {
            "ats": "greenhouse", "title": item.get("title"), "req_id": item.get("id"),
            "jd_html": item.get("content") or "", "locations": [((item.get("location") or {}).get("name"))],
            "location_raw": (item.get("location") or {}).get("name"),
            "department": (deps[0] or {}).get("name") if deps else None,
            "structured_pay": item.get("pay_input_ranges"), "posted": item.get("first_published"),
        }
    if "lists" in item and "categories" in item:                 # Lever
        parts = [item.get("description") or item.get("descriptionPlain") or ""]
        for l in item.get("lists") or []:
            parts.append("<p><b>" + str(l.get("text") or "") + "</b></p>" + str(l.get("content") or ""))
        parts.append(item.get("additional") or "")
        cats = item.get("categories") or {}
        return {
            "ats": "lever", "title": item.get("text"), "req_id": item.get("id"), "jd_html": "\n".join(parts),
            "locations": [cats.get("location")] + list(cats.get("allLocations") or []),
            "location_raw": cats.get("location"), "department": cats.get("team") or cats.get("department"),
            "work_site_hint": item.get("workplaceType"), "structured_pay": item.get("salaryRange"),
        }
    if item.get("@type") == "JobPosting" or "jobLocation" in item:   # Google JSON-LD
        jl = item.get("jobLocation") or []
        if isinstance(jl, dict):
            jl = [jl]
        locs, hints = [], []
        for x in jl:
            a = (x or {}).get("address") or {}
            locs.append(", ".join(v for v in (a.get("addressLocality"), a.get("addressRegion"), a.get("addressCountry")) if v))
            if a.get("addressCountry"):
                hints.append(a["addressCountry"])
        return {
            "ats": "google", "title": item.get("title"), "jd_html": item.get("description"), "locations": locs,
            "countries_hint": hints, "structured_pay": item.get("baseSalary"), "posted": item.get("datePosted"),
            "work_site_hint": item.get("jobLocationType"),
        }
    jd = _first(item, *_JD_KEYS)
    return {"title": _first(item, "title", "name", "text"), "jd_html": jd,
            "locations": item.get("locations") or ([item["location"]] if isinstance(item.get("location"), str) else []),
            "location_raw": item.get("location") if isinstance(item.get("location"), str) else None,
            "department": item.get("department") if isinstance(item.get("department"), str) else None}


def records_from_raw(raw) -> list:
    """整份原始 JSON -> 预解析记录列表（详情文件 1 条；列表/整板文件 N 条）。"""
    if isinstance(raw, list):
        return [record_from_item(x) for x in raw if isinstance(x, dict)]
    if not isinstance(raw, dict):
        return []
    for key in ("jobs", "jobPostings", "results", "data"):
        v = raw.get(key)
        if isinstance(v, list) and v and isinstance(v[0], dict) and "jobPostingInfo" not in raw:
            return [record_from_item(x) for x in v]
    return [record_from_item(raw)]


def _summary_line(r: dict) -> str:
    return " | ".join(str(x) for x in (
        r.get("req_id") or "", (r.get("title") or "")[:60], r.get("role_family"), r.get("level_tag"),
        "eff=%s" % r.get("eff_min_years"), "phd=%s" % r.get("phd_branch_years"), r.get("region"),
        r.get("pay_range") or "-", ",".join(r.get("restriction_flags") or []) or "-",
        ",".join(r.get("healthcare_hits") or [])[:60] or "-",
    ))


# ---------------------------------------------------------------------------
# 14. 内置夹具
# ---------------------------------------------------------------------------

_MS_FIXTURE = (
    "Required Qualifications:\n"
    "- Doctorate in Computer Science, or related technical field AND 3+ years technical engineering experience "
    "with coding in languages including, but not limited to, C, C++, C#, Java, JavaScript, or Python\n"
    "OR Master's Degree in Computer Science or related technical field AND 5+ years technical engineering experience\n"
    "OR Bachelor's Degree in Computer Science or related technical field AND 8+ years technical engineering experience\n"
    "OR equivalent experience.\n"
    "Other Requirements:\n"
    "- Microsoft Cloud Background Check: This position will be required to pass the Microsoft Cloud background check "
    "upon hire/transfer and every two years thereafter.\n"
)
_NV_FIXTURE_HTML = (
    "<p><b>What we need to see:</b></p><ul><li><p>PhD in Computer Science, Machine Learning, or a related quantitative "
    "field (or equivalent experience).</p></li><li><p>8&#43; years of relevant industry experience focusing on medical "
    "AI research.</p></li></ul><p><b>Ways to stand out from the crowd:</b></p><ul><li><p>Experience developing "
    "foundation models; 3+ years with distributed GPU training.</p></li></ul>"
    "<p></p>Your base salary will be determined based on your location. The base salary range is 184,000 USD - 287,500 USD "
    "for Level 4, and 224,000 USD - 356,500 USD for Level 5.<p></p>NVIDIA is committed to fostering an inclusive work "
    "environment and proud to be an equal opportunity employer. We do not discriminate on the basis of veteran status, "
    "military status or disability. ITAR does not apply here.<p></p>"
)


def _selftest() -> int:
    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    # 1 微软 OR 分支
    secs, found = split_sections(_MS_FIXTURE)
    y = extract_years(secs["minimum"], _MS_FIXTURE)
    check("01 microsoft OR branches -> phd 3 / eff 3 / min 3",
          y["phd_branch_years"] == 3 and y["eff_min_years"] == 3 and y["min_years_required"] == 3
          and y["phd_alt_path"] == "Y" and y["has_equivalent_clause"] is True, json.dumps(y, ensure_ascii=False))
    # 2 小写 or 不切分支
    t2 = "- PhD in Computer Science or related field and 2+ years of experience\n- 5+ years of Python"
    y2 = extract_years(t2, t2)
    check("02 lowercase 'or related field' keeps one branch",
          len(_split_branches(t2)) == 1 and y2["phd_branch_years"] == 2 and y2["eff_min_years"] == 2,
          "branches=%d %s" % (len(_split_branches(t2)), json.dumps(y2)))
    # 3 ITAR vs military
    full3 = ("What we need to see:\n- Must comply with ITAR regulations.\n"
             "NVIDIA is committed to fostering an inclusive work environment; veteran status, military status.")
    core3, tail3 = cut_boilerplate(full3)
    f3a, _ = extract_restrictions(core3)
    f3b, _ = extract_restrictions("veteran status, military status, disability; no other text")
    f3c, _ = extract_restrictions(tail3.replace("ITAR", ""))
    check("03 ITAR fires on core 'ITAR', not on 'military' in tail",
          "ITAR" in f3a and "ITAR" not in f3b and "ITAR" not in f3c, "core=%r tail=%r" % (f3a, f3c))
    # 4 NVIDIA 双 Level
    p4 = extract_pay("The base salary range is 184,000 USD - 287,500 USD for Level 4, and 224,000 USD - 356,500 USD for Level 5.", None)
    check("04 NVIDIA two levels -> 184000 / 356500",
          len(p4["pay_ranges"]) == 2 and p4["pay_low"] == 184000 and p4["pay_high"] == 356500
          and p4["pay_range"] == "184,000-287,500 USD (Level 4); 224,000-356,500 USD (Level 5)", p4["pay_range"])
    # 5 Amazon
    p5 = extract_pay("The base pay for this position ranges from $136,000/year in our lowest geographic market up to $223,400/year in our highest geographic market.", None)
    check("05 Amazon geo range -> 136000 / 223400", p5["pay_low"] == 136000 and p5["pay_high"] == 223400, p5["pay_range"])
    # 6 微软双 band
    p6 = extract_pay("The typical base pay range for this role across the U.S. is USD $117,200 - $229,200 per year. "
                     "There is a different range applicable to specific work locations, within the San Francisco Bay area "
                     "and New York City metropolitan area, and the base pay range for this role in those locations is "
                     "USD $153,600 - $250,200 per year.", None)
    check("06 Microsoft double band -> 2 ranges 117200 / 250200",
          len(p6["pay_ranges"]) == 2 and p6["pay_low"] == 117200 and p6["pay_high"] == 250200, p6["pay_range"])
    # 7 缺第二个 $
    p7 = extract_pay("The salary range for this role is $161,300-274,200 plus equity.", None)
    check("07 missing second dollar -> 161300 / 274200", p7["pay_low"] == 161300 and p7["pay_high"] == 274200, p7["pay_range"])
    # 8 双转义 nbsp
    check("08 &amp;nbsp; -> space", html_to_text("A&amp;nbsp;B") == "A B", repr(html_to_text("A&amp;nbsp;B")))
    # 9 Amazon PhD 0 年
    t9 = "- PhD, or Master's degree and 4+ years of CS, CE, ML or related field experience\n- Experience programming in Python"
    y9 = extract_years(t9, t9)
    check("09 Amazon 'PhD, or Master's and 4+ years' -> phd 0 / eff 0 / alt Y",
          y9["phd_branch_years"] == 0 and y9["eff_min_years"] == 0 and y9["phd_alt_path"] == "Y", json.dumps(y9))
    # 10 在读
    t10 = "- Currently pursuing a PhD in Computer Science or related field\n- Strong Python"
    y10 = extract_years(t10, t10)
    check("10 'Currently pursuing a PhD' -> eff 0 / new_grad", y10["eff_min_years"] == 0 and y10["level_hint"] == "new_grad", json.dumps(y10))
    # 11 双级 title
    l11 = classify_level("Applied Scientist II / Senior Applied Scientist", 3, "")
    check("11 dual title -> mid, multi_level", l11["level_tag"] == "mid" and l11["multi_level"] is True, json.dumps(l11))
    # 12 职族
    r12 = [classify_role_family("Senior Solutions Architect - Healthcare", "", "")[0],
           classify_role_family("Software Engineer, Healthcare AI Platform", "", "")[0],
           classify_role_family("Account Manager - Digital Health", "", "")[0],
           classify_role_family("Machine Learning Engineer Intern", "", "")[0]]
    check("12 role families SA_HEALTH / SDE_ML / OTHER / INTERN", r12 == ["SA_HEALTH", "SDE_ML", "OTHER", "INTERN"], str(r12))
    # 13 地点
    la = normalize_location(["US, CA, Santa Clara"], {})
    lb = normalize_location(["Remote - US"], {})
    lc = normalize_location(["London, UK"], {})
    ld = normalize_location(["US, WA, Seattle", "Canada, Toronto"], {})
    check("13 locations 美国/CA, 远程-美国, 非美国, 多地含美国",
          la["region"] == "美国" and la["state"] == "CA" and lb["region"] == "远程-美国" and lb["remote_us"] is True
          and lc["region"] == "非美国" and ld["region"] == "多地含美国",
          json.dumps([la, lb, lc, ld], ensure_ascii=False))
    # 14 Clara
    h14a, _ = healthcare_hits("Santa Clara", "", "Located in Santa Clara, CA", "A", [])
    h14b, _ = healthcare_hits("", "", "Build on MONAI and NVIDIA Clara Holoscan.", "A", [])
    check("14 'Santa Clara' no clara hit; MONAI/Clara/Holoscan hit",
          "clara" not in h14a and {"monai", "clara", "holoscan"} <= set(h14b), "%s %s" % (h14a, h14b))
    # 15 CT
    h15a, _ = healthcare_hits("", "", "Experience with CT scan segmentation", "A", [])
    h15b, _ = healthcare_hits("", "", "Office located in CT, Hartford", "A", [])
    check("15 CT needs scan/imaging context", "ct" in h15a and "ct" not in h15b, "%s %s" % (h15a, h15b))
    # 16 NVIDIA 式 "PhD (or equivalent)" + 单独 "8+ years" bullet，端到端
    r16 = parse({"title": "Senior Applied Research Scientist, Multimodal Foundation Models \u2013 Healthcare",
                 "jd_html": _NV_FIXTURE_HTML, "locations": ["US, CA, Santa Clara"], "track": "A",
                 "org_unit": "Engineer, Sys SW"})
    check("16 NVIDIA-style end-to-end: min 8 / eff 8 / senior / stretch / RESEARCH_SCI / 2 pay / no flags / preferred 3",
          r16["min_years_required"] == 8 and r16["eff_min_years"] == 8 and r16["phd_branch_years"] in (None, 8)
          and r16["level_tag"] == "senior" and r16["stretch"] is True and r16["role_family"] == "RESEARCH_SCI"
          and len(r16["pay_ranges"]) == 2 and r16["restriction_flags"] == [] and r16["preferred_years"] == 3
          and r16["sections_found"] == ["minimum", "preferred", "pay"] and "medical" in r16["healthcare_hits"]
          and r16["healthcare_hit_source"] == "title" and r16["has_equivalent_clause"] is True,
          _summary_line(r16) + " | sections=%s pref=%s equiv=%s" % (r16["sections_found"], r16["preferred_years"], r16["has_equivalent_clause"]))
    # 17 结构化薪资形状
    gh = extract_pay("", [{"min_cents": 15000000, "max_cents": 20000000, "currency_type": "USD", "title": "L4"}])
    ab = extract_pay("", {"compensationTiers": [{"title": None, "components": [
        {"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "USD", "minValue": 205000, "maxValue": 300000}]}]})
    lv = extract_pay("", {"min": 50, "max": 70, "currency": "USD", "interval": "per-hour-wage"})
    check("17 structured pay greenhouse/ashby/lever(hourly annualized)",
          gh["pay_low"] == 150000 and ab["pay_high"] == 300000 and lv["pay_ranges"][0]["basis"] == "hour"
          and lv["pay_low"] == 50 * 2080, "%s | %s | %s" % (gh["pay_range"], ab["pay_range"], lv["pay_range"]))
    # 18 微软夹具的 Cloud BG check 与 "every two years" 不污染年限
    f18, _ = extract_restrictions(_MS_FIXTURE)
    check("18 CLOUD_BG_CHECK flag; 'every two years' ignored", "CLOUD_BG_CHECK" in f18 and y["min_years_required"] == 3, str(f18))

    # 19 块级开标签前换行：标题不能粘在上一句末尾（2026-09-23 核验发现 amazon.jobs 大量漏切）
    t19 = html_to_text("<h3>Description</h3>We help teams achieve.<h3>Basic Qualifications</h3>- PhD<br/>"
                       "<h3>Preferred Qualifications</h3>- MICCAI papers")
    _, found19 = split_sections(cut_boilerplate(t19)[0])
    check("19 block-open newline: Description/Basic/Preferred all split", found19 == ["responsibilities", "minimum", "preferred"], str(found19))

    # 20 <li><p> 嵌套不能把列表符和正文拆成两行
    t20 = html_to_text("<ul><li><p>Foundation models that read a CT study</p></li><li><p>VLM learning</p></li></ul>")
    lv21 = classify_level("Member of Technical Staff, Research Engineer", None, "")["level_tag"]
    lv21b = classify_level("Member of Technical Staff, Principal Biostatistician", None, "")["level_tag"]
    check("21 'Member of Technical Staff' is not a staff level", lv21 != "staff" and lv21b == "principal", f"{lv21} / {lv21b}")

    f22, _ = extract_restrictions("We are not able to sponsor visas at this time. We do not currently offer visa sponsorship.")
    check("22 'are not able to sponsor' / 'do not currently offer sponsorship' flagged", "NO_SPONSOR" in f22, str(f22))

    r23 = [_parse_one_location(x)["country"] for x in ("Dublin, OH", "Waterloo, IA", "Vancouver, WA", "Vancouver, BC", "Dublin, Ireland", "Bangalore, IN", "Chennai, TN", "London, ON")]
    check("23 US state code beats non-US city table (except IN/TN/CA)",
          r23[:3] == ["US", "US", "US"] and all(c and c != "US" for c in r23[3:]), str(r23))
    f24, _ = extract_restrictions("Eligible to work in the United States without visa sponsorship (TN visa holders may apply).")
    check("24 'eligible to work ... without visa sponsorship' flagged", "AUTH_NO_SPONSOR" in f24, str(f24))
    f24b = [extract_restrictions(x)[0] for x in (
        "Lilly will not provide support for or sponsor work authorization and/or visas for this role.",
        "This position requires permanent work authorization in the United States.",
        "We are unable to accept candidates on OPT or CPT.",
        "Mayo Clinic does not participate in the F-1 OPT STEM extension program.",
        "We are building the care platform patients need now or in the future.",
        "Sponsorship for this role is not guaranteed.")]
    check("24b sponsor wording: Lilly / permanent auth / OPT-CPT / STEM flagged; bare 'now or in the future' not; 'not guaranteed' soft",
          all("NO_SPONSOR" in f for f in f24b[:3]) and f24b[3] == ["NO_STEM_OPT"] and f24b[4] == [] and f24b[5] == ["SPONSOR_NOT_GUARANTEED"], str(f24b))
    f24c, _ = extract_restrictions("Build models.", "Build models.\nEqual Opportunity Employer.\nWe do not offer visa sponsorship.")
    check("24c sponsor wording after EEO boilerplate found on full text", "NO_SPONSOR" in f24c, str(f24c))
    _, found25 = split_sections("About the role\nEDUCATION:\nRequired: Bachelor's degree\nEXPERIENCE:\nRequired: Two years")
    check("25 MD Anderson EDUCATION: / EXPERIENCE: headings -> minimum", "minimum" in found25, str(found25))

    rf26 = [classify_role_family(tt, "", jd, "D")[0] for tt, jd in (
        ("Medical Laboratory Scientist II", "Perform testing on patient specimens."),
        ("CT Technologist", "Operate imaging equipment; model safety training."),
        ("Senior Scientist", "Develop cell culture assays and purification processes."),
        ("Senior Scientist", "Build deep learning models in PyTorch for machine learning on imaging."),
        ("Postdoctoral Researcher", "Study cardiac inflammation in mouse models."),
        ("Research Scientist, Computational Pathology", ""),
        ("Data Scientist II", ""),
        ("Applied Scientist", ""))]
    check("26 D-track noise: lab scientist / technologist / wet-lab scientist / bio postdoc -> OTHER; ML ones kept",
          rf26 == ["OTHER", "OTHER", "OTHER", "APPLIED_SCI", "OTHER", "RESEARCH_SCI", "DATA_SCI", "APPLIED_SCI"]
          and classify_role_family("Research Scientist", "", "We build AI.", "B")[0] == "RESEARCH_SCI", str(rf26))

    check("20 li>p keeps bullet and text on one line", t20 == "- Foundation models that read a CT study\n- VLM learning", repr(t20))

    # 27 福利段语境只过滤 JD 正文：标题里的 "Health & Wellness" 是组名，正文里的 "health and wellness benefits" 仍不算
    h27t, s27t = healthcare_hits("Applied Scientist, Health & Wellness (Health Tech)", "team-alexa-echo-devices", "", "A", [])
    h27j, _ = healthcare_hits("Software Engineer", "", "We offer health and wellness benefits, dental and 401(k).", "A", [])
    check("27 benefits context skipped for title, kept for JD body", "health" in h27t and s27t == "title" and h27j == [], f"{h27t} {s27t} | {h27j}")

    failed = 0
    for name, ok, detail in results:
        print(("PASS  " if ok else "FAIL  ") + name)
        if not ok:
            failed += 1
            print("      " + detail[:600])
    print("%d/%d passed" % (len(results) - failed, len(results)))
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# 15. CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="healthcare 岗位 JD 解析器")
    ap.add_argument("raw", nargs="?", help="已存的原始详情/列表 JSON")
    ap.add_argument("--selftest", action="store_true", help="跑内置夹具")
    ap.add_argument("--print-regex", action="store_true", help="打印职族/排除正则")
    ap.add_argument("--index", type=int, default=None, help="列表文件里只解析第 N 条并完整打印")
    ap.add_argument("--track", default="A", help="A/B/C，影响 healthcare_hits 的 company 来源")
    ap.add_argument("--with-sections", action="store_true", help="完整打印时保留 sections 全文")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.print_regex:
        print_regex()
        return 0
    if not args.raw:
        ap.print_help()
        return 2
    with open(args.raw, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    recs = records_from_raw(raw)
    if not recs:
        print("no job records found in", args.raw, file=sys.stderr)
        return 2
    if args.index is not None:
        recs = [recs[args.index]]
    parsed = []
    for r in recs:
        r["track"] = r.get("track") or args.track
        parsed.append(parse(r))
    if len(parsed) == 1:
        out = dict(parsed[0])
        out.pop("jd_text", None)
        out.pop("jd_html", None)
        if not args.with_sections:
            out["sections"] = {k: (v[:160] + ("..." if len(v) > 160 else "")) for k, v in out["sections"].items()}
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    else:
        for r in parsed:
            print(_summary_line(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
