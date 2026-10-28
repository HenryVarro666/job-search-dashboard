#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""healthcare 岗位抓取 —— 票数与签证信号（2026-09-30 本人要求前置，纯脚本，不用 LLM）。

给每条入围 req 算三件事，供 board.html、pool_snapshot.json、求职进度面板显示和排序用。
**只用于显示和同档内排序，不改档位**（本人 2026-09-30 定）。

1. wage level：薪资区间 × 工作地 × SOC → OEWS 四档线 → 加权抽签票数（Level I=1 … IV=4），
   以及建议填的期望薪资（刚过区间内够得着的最高一档线）。
2. 雇主 LCA 摘要：ref/lca_summary.json（build_lca_summary.py 生成）。
3. 雇主政策：ref/employer_visa.json（手工登记 sponsor-check 结论，如 Mayo 不参加 STEM OPT）。

口径与边界（写给下次改这个文件的人）：
- 工资表是 OFLC WY2026-27（ref/oflc_wy2627/，时薪 ×2080）。工资年每年 7/1 换表，
  2027-03 registration 用的仍是这张；2027-07 之后要换目录重算。
- 这里算的是「按 offered salary 判的抽签档」，不是 LCA 上雇主报的 PW_WAGE_LEVEL。
- SOC 取保守值：同一岗位可能报的几个 SOC 里，取 L4 线最高的那个。雇主实际报什么要 offer 阶段问。
  「可能报的」以 DOL 实报份额为准，不按名字猜（2026-10-07 修）：FY2026 Q1–Q3 Certified LCA 里，
  标题含 "data scien" 的 8,523 条 87.3% 报 15-2051、只有 0.9% 报 15-1252；scientist / researcher 类
  12,399 条 15-1221 占 26.7%、15-2051 占 5.5%、15-1252 只占 1.0%。此前把 15-1252 放进候选，
  它的 L4 线在多数地区最高，于是这两类标题大多按 15-1252 判档，少算一票左右（Caris JR104962 调研时发现）。
  现在份额约 1% 的 SOC 只放进 soc_alts 展示，不参与取最高。
- 远程岗按配置的默认区域 DFW（area 19100）算；写着「Remote (PA)」这类限定州的远程岗也按 DFW 算，
  并在 note 里标出「住德州能否做待确认」。
- 城市 → 县 的映射是手写表（CITY_COUNTY），只覆盖池里出现过的城市；映射不上就不算，不猜。

用法：
    python3 visa_signals.py --selftest      # 对 2026-09-30 手算过的几条岗位回归
"""
import csv
import json
import math
import os
import re
import sys

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(TOOL_DIR, "ref")
OFLC_DIR = os.path.join(REF, "oflc_wy2627")
WAGE_YEAR = "WY2026-27"
HOME_AREA = ("Tarrant", "TX")  # Configurable default wage region

# 城市（小写）→（Geography.csv 的 CountyTownName 关键字, 州）
CITY_COUNTY = {
    "san francisco": ("San Francisco", "CA"), "south san francisco": ("San Mateo", "CA"), "menlo park": ("San Mateo", "CA"),
    "redwood city": ("San Mateo", "CA"), "mountain view": ("Santa Clara", "CA"), "sunnyvale": ("Santa Clara", "CA"),
    "santa clara": ("Santa Clara", "CA"), "thousand oaks": ("Ventura", "CA"), "los angeles": ("Los Angeles", "CA"),
    "san diego": ("San Diego", "CA"),
    "rochester": ("Olmsted", "MN"), "eden prairie": ("Hennepin", "MN"), "minnetonka": ("Hennepin", "MN"),
    "minneapolis": ("Hennepin", "MN"), "arden hills": ("Ramsey", "MN"),
    "dallas": ("Dallas", "TX"), "irving": ("Dallas", "TX"), "richardson": ("Dallas", "TX"), "plano": ("Collin", "TX"),
    "frisco": ("Collin", "TX"), "fort worth": ("Tarrant", "TX"), "arlington": ("Tarrant", "TX"),
    "houston": ("Harris", "TX"), "austin": ("Travis", "TX"),
    "new york": ("New York", "NY"), "new york city": ("New York", "NY"),
    "boston": ("Suffolk", "MA"), "cambridge": ("Middlesex", "MA"), "marlborough": ("Middlesex", "MA"),
    "burlington": ("Middlesex", "MA"), "worcester": ("Worcester", "MA"),
    "chicago": ("Cook", "IL"), "north chicago": ("Lake", "IL"),
    "tampa": ("Hillsborough", "FL"), "miami": ("Miami-Dade", "FL"),
    "princeton": ("Mercer", "NJ"), "new brunswick": ("Middlesex", "NJ"), "basking ridge": ("Somerset", "NJ"),
    "raritan": ("Somerset", "NJ"), "titusville": ("Mercer", "NJ"),
    "spring house": ("Montgomery", "PA"), "conshohocken": ("Montgomery", "PA"),
    "seattle": ("King", "WA"), "denver": ("Denver", "CO"), "tempe": ("Maricopa", "AZ"), "lehi": ("Utah", "UT"),
    # 2026-10-06 统一面板接入手工待投岗时补（此前 Bellevue / San Jose 一直映射不上）
    "san jose": ("Santa Clara", "CA"), "palo alto": ("Santa Clara", "CA"), "cupertino": ("Santa Clara", "CA"),
    "san mateo": ("San Mateo", "CA"), "foster city": ("San Mateo", "CA"), "oakland": ("Alameda", "CA"),
    "bellevue": ("King", "WA"), "redmond": ("King", "WA"), "kirkland": ("King", "WA"),
    "las vegas": ("Clark", "NV"), "atlanta": ("Fulton", "GA"), "indianapolis": ("Marion", "IN"),
}
# 只给了州、没给城市时用的代表县（目前只有 NY：Mount Sinai / NYU 这类写法）
STATE_ONLY = {"NY": ("New York", "NY")}
STATE_RE = r"(?:A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])"

# 岗位标题 → (可能报的 SOC：取 L4 线最高的为主口径, 只展示的少见 SOC)。份额依据见文件头。
SOC_BY_TITLE = [
    (re.compile(r"post-?doc|research fellow", re.I), ["19-1042"], []),
    (re.compile(r"data scien", re.I), ["15-2051"], ["15-1252"]),
    (re.compile(r"scientist|researcher", re.I), ["15-1221", "15-2051"], ["15-1252"]),
]
SOC_DEFAULT = (["15-1252"], [])
SOC_NAME = {"15-1252": "Software Developers", "15-2051": "Data Scientists", "15-1221": "Computer & Info Research Scientists",
            "19-1042": "Medical Scientists"}

_cache = {}


def _load_oflc():
    if "oflc" in _cache:
        return _cache["oflc"]
    county, levels = {}, {}
    try:
        with open(os.path.join(OFLC_DIR, "Geography.csv"), encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                county[(r["CountyTownName"].replace(" County", "").strip().lower(), r["StateAb"])] = (r["Area"], r["AreaName"])
        want = {s for _, main, alts in SOC_BY_TITLE + [(None,) + SOC_DEFAULT] for s in main + alts}
        with open(os.path.join(OFLC_DIR, "ALC_Export.csv"), encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r["SocCode"] in want:
                    try:
                        levels[(r["Area"], r["SocCode"])] = [round(float(r[k]) * 2080) for k in ("Level1", "Level2", "Level3", "Level4")]
                    except ValueError:
                        pass
    except OSError:
        pass
    _cache["oflc"] = (county, levels)
    return _cache["oflc"]


def _load_json(name):
    if name not in _cache:
        try:
            with open(os.path.join(REF, name), encoding="utf-8") as f:
                _cache[name] = json.load(f)
        except (OSError, ValueError):
            _cache[name] = {}
    return _cache[name]


def area_of(county_kw, state):
    county, _ = _load_oflc()
    return county.get((county_kw.lower(), state))


def resolve_location(loc):
    """地点字符串 → (area, area_name, 用的是哪一段, remote_home, note)。映射不上返回 None。

    先后顺序：不限州的远程 → 本人住址；否则取第一个能映射的城市；限定州的远程放最后兜底。
    """
    loc = loc or ""
    segs = [s.strip() for s in re.split(r";", loc) if s.strip()]
    home = area_of(*HOME_AREA)
    state_remote = None
    for s in segs:
        m = re.match(r"^(?:US Remote|Remote(?: \((US)\))?|Remote \(([A-Z]{2})\))$", s)
        if m:
            if m.group(2):
                state_remote = state_remote or m.group(2)
                continue
            return home + (s, True, "") if home else None
    for s in segs:
        if re.match(r"^Remote", s) or "International" in s:
            continue
        m = re.match(rf"^(.*?)[,\-\s]+({STATE_RE})$", s)
        city = (m.group(1) if m else s).strip().lower()
        st = m.group(2) if m else None
        hit = CITY_COUNTY.get(city)
        if hit and (st is None or st == hit[1]):
            a = area_of(*hit)
            if a:
                return a + (s, False, "")
        if re.fullmatch(STATE_RE, s) and s in STATE_ONLY:
            a = area_of(*STATE_ONLY[s])
            if a:
                return a + (s, False, "只写了州，按 " + STATE_ONLY[s][0] + " 县算")
    if state_remote and home:
        return home + (f"Remote ({state_remote})", True, f"岗位写的是 {state_remote} 远程，住德州能否做待确认")
    return None


def level_of(amount, lv):
    """年薪 → 档（0 = 低于 L1 线；1–4）。"""
    return sum(1 for x in lv if amount >= x)


def suggest_salary(lo, hi, lv):
    """建议期望薪资：区间内够得着的最高一档线，往上取整到 5000（超出上限就取整到 1000）。

    区间里一条档线都没有时：整段都在 L4 以上就给区间 1/4 处；否则填区间上限往下取整到 1000（再高也换不来票）。
    """
    inside = [(i + 1, x) for i, x in enumerate(lv) if lo <= x <= hi]
    if not inside and lo >= lv[3]:  # 整个区间都在 L4 线以上：填多少都是 4 票，给区间 1/4 处
        amt = int(round((lo + 0.25 * (hi - lo)) / 5000) * 5000)
        return {"amount": amt, "level": 4, "line": None, "pct": 0.25}
    if not inside:
        return {"amount": int(hi // 1000 * 1000), "level": level_of(hi // 1000 * 1000, lv), "line": None}
    k, line = inside[-1]
    amt = math.ceil(line / 5000) * 5000
    if amt > hi:
        amt = math.ceil(line / 1000) * 1000
        if amt - line < 500 and amt + 1000 <= hi:
            amt += 1000
    if amt > hi:
        amt = int(hi)
    out = {"amount": int(amt), "level": level_of(amt, lv), "line": line}
    pct = (amt - lo) / (hi - lo) if hi > lo else 1.0
    out["pct"] = round(pct, 2)
    if pct > 0.9 and len(inside) > 1:  # 顶格才够得着：同时给低一档的稳妥数
        k2, line2 = inside[-2]
        a2 = math.ceil(line2 / 5000) * 5000
        out["alt"] = {"amount": int(a2), "level": level_of(a2, lv)}
    return out


def soc_candidates(title):
    """→ (参与取最高的 SOC, 只展示的 SOC)。"""
    for rx, main, alts in SOC_BY_TITLE:
        if rx.search(title or ""):
            return main, alts
    return SOC_DEFAULT


def wage_signal(title, loc, pay_lo, pay_hi):
    """一条 req 的票数信号。工作地或工资表对不上时返回带 note 的空壳，不猜。"""
    _, levels = _load_oflc()
    r = resolve_location(loc)
    if not r:
        return {"ok": False, "note": "工作地映射不到 OFLC 区域"}
    area, area_name, seg, remote_home, note = r
    main, alts = soc_candidates(title)
    cands = [(s, levels.get((area, s))) for s in main]
    cands = [(s, lv) for s, lv in cands if lv]
    if not cands:
        return {"ok": False, "note": "该区域没有对应 SOC 的工资数据"}
    soc, lv = max(cands, key=lambda c: c[1][3])  # 保守：L4 线最高的
    shown = [(s, l) for s, l in cands if s != soc] + [(s, levels.get((area, s))) for s in alts if levels.get((area, s))]
    out = {"ok": True, "wage_year": WAGE_YEAR, "area": area, "area_name": area_name, "loc_used": seg, "remote_home": remote_home,
           "soc": soc, "soc_name": SOC_NAME.get(soc, ""), "levels": lv, "note": note,
           "soc_alts": [{"soc": s, "levels": l, "tix_hi": level_of(pay_hi, l) if pay_hi else None} for s, l in shown]}
    if pay_lo and pay_hi:
        mid = (pay_lo + pay_hi) / 2
        out.update({"tix_lo": level_of(pay_lo, lv), "tix_mid": level_of(mid, lv), "tix_hi": level_of(pay_hi, lv),
                    "suggest": suggest_salary(pay_lo, pay_hi, lv)})
    return out


def employer_signal(company_key):
    """雇主级：LCA 摘要 + 手工登记的政策。"""
    lca = (_load_json("lca_summary.json").get("companies") or {}).get(company_key)
    pol = (_load_json("employer_visa.json").get("employers") or {}).get(company_key)
    return {"lca": lca, "policy": pol, "lca_coverage": _load_json("lca_summary.json").get("coverage", "")}


def signals(uid, title, loc, pay_lo, pay_hi):
    key = (uid or "").split(":")[0]
    out = {"wage": wage_signal(title, loc, pay_lo, pay_hi)}
    out.update(employer_signal(key))
    return out


def _selftest():
    cases = [  # (说明, title, loc, lo, hi, 期望：tix_hi, 建议数额)
        # 2026-10-07 改：data scien 标题主口径由 15-1252 改为 15-2051。35620 区 15-2051 L1 = 41.95×2080 = 87,256，
        # 落在区间内 → 建议 90,000（L1）。旧值 94,000 是 15-1252 口径下整段低于 L1 时的区间上限取整。
        ("BMS R1606730", "Data Scientist, Sr. Specialist", "New Brunswick, NJ", 77950, 94451, 1, 90000),
        # 19100 区 15-2051 L4 = 70.44×2080 = 146,515；旧口径（15-1252）会给 tix_hi 3。区间是调研报告推断的 $132K–$150K
        ("Caris JR104962", "Data Scientist - Innovation - PhD", "Irving, TX", 132000, 150000, 4, 150000),
        ("CVS R1038482", "Software Development Engineer", "Remote (PA)", 72100, 144200, 3, 140000),
        ("CVS R1051951", "Software Development Engineer", "Remote (IL)", 79310, 158620, 3, 140000),
        ("Amgen R-250799", "Senior Forward Deployed Engineer, AI Studio", "Remote (US)", 156190, 211316, 4, 165000),
        ("Amgen R-250796", "Machine Learning Engineer, AI Studio", "Remote (US)", 129655, 175415, 4, 165000),
    ]
    ok = True
    for name, t, loc, lo, hi, tix_hi, amt in cases:
        w = wage_signal(t, loc, lo, hi)
        good = w.get("ok") and (tix_hi is None or w["tix_hi"] == tix_hi) and w["suggest"]["amount"] == amt
        ok = ok and good
        print(("✅" if good else "❌"), name, "|", w.get("area"), w.get("soc"), w.get("levels"), "| 票 低/中/高",
              w.get("tix_lo"), w.get("tix_mid"), w.get("tix_hi"), "| 建议", w.get("suggest"), "|", w.get("note"))
    for loc in ("HQ", "San Francisco, CA; Miami, FL", "NY", "Boston-MA", "US Remote; Austin, TX", "Seattle, WA; Truveta Main"):
        print("   ", loc, "→", resolve_location(loc))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else 0)
