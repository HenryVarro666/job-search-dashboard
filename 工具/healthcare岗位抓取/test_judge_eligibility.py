"""应届 PhD 的年限/职级回归；python3 -m unittest test_judge_eligibility。"""
import unittest
import json
import os
import tempfile
from pathlib import Path

from judge_tools import ELIGIBILITY_POLICY_VERSION, apply_eligibility, derive, load_eligibility


def judgment(years=None, phd=None, counts="SILENT", status="PASS", reach="MID_IC_REACHABLE"):
    return {
        "uid": "test:1", "title": "Machine Learning Scientist",
        "official_level": {"reachability": reach, "dual_level": False},
        "hard_gates": {
            "restrictions": {"status": "NONE"}, "degree": {"status": "PASS"},
            "role_family_fit": {"status": "PASS"}, "hard_skill_andgate": {"status": "NONE"},
            "years": {"status": status, "required_years_min": years, "phd_branch_years": phd,
                      "counts_research_as_experience": counts, "jd_quote": "NO_CLAUSE"}},
        "standout_coverage": {"n_items": 4, "n_have": 4, "n_partial": 0, "items": []},
        "domain_overlap": {"tags": [{"tag": t, "overlap": "CORE_MATCH", "jd_weight": "CORE_TO_JD"}
                                    for t in ("neuroimaging", "clinical-NLP")]},
        "domain_fit": {"status": "GENERAL_TECH", "required_domain_status": "NOT_REQUIRED"},
        "judge_meta": {"standout_source": "PREFERRED_SECTION", "jd_sections_present": ["preferred"]},
        "funnel": {"S1": {"severity": "NONE"}, "S2": {"severity": "NONE"}},
    }


def audit(verdict, raise_ok=False):
    return {"verdict": verdict, "raise_ok": raise_ok}


class QualificationTests(unittest.TestCase):
    def test_verified_candidate_histories_produce_different_experience_outcomes(self):
        # Same employer requirement, two fictional reviewed candidate histories.
        for years, evidence, expected in ((1.5, "E-FICTIONAL-SHORT", "🔴 不投"),
                                          (6.0, "E-FICTIONAL-LONG", "🟢 投")):
            with self.subTest(verified_research_years=years):
                j = judgment(4, counts="YES_EXPLICIT")
                j["hard_gates"]["years"].update(verified_research_years=years,
                                               research_evidence_ids=[evidence])
                self.assertEqual(derive(j)[0], expected)

    def test_unknown_or_unsupported_candidate_duration_remains_conditional(self):
        for proof in ({}, {"verified_research_years": 6},
                      {"research_evidence_ids": ["E-FICTIONAL-RESEARCH"]},
                      {"verified_research_years": 6, "research_evidence_ids": []},
                      {"verified_research_years": 6, "research_evidence_ids": [""]},
                      {"verified_research_years": 6, "research_evidence_ids": "E-FICTIONAL-RESEARCH"}):
            with self.subTest(proof=proof):
                j = judgment(8, counts="YES_EXPLICIT")
                j["hard_gates"]["years"].update(proof)
                self.assertEqual(derive(j)[0], "🟡 条件投")

    def test_invalid_duration_is_unknown_evidence_instead_of_candidate_failure(self):
        for years in (None, -1, True, "6", float("nan"), float("inf")):
            with self.subTest(verified_research_years=years):
                j = judgment(4, counts="YES_EXPLICIT")
                j["hard_gates"]["years"].update(verified_research_years=years,
                                               research_evidence_ids=["E-FICTIONAL-RESEARCH"])
                self.assertEqual(derive(j)[0], "🟡 条件投")

    def test_auditor_confirming_employer_research_acceptance_does_not_supply_candidate_duration(self):
        j = judgment(3, status="FAIL")
        e = dict(audit("ELIGIBLE_PHD_COUNTS", True), policy_version=ELIGIBILITY_POLICY_VERSION,
                 decisive_quote="3 years of experience including graduate research", qualifying_years_min=3,
                 counts_research_as_experience="YES_EXPLICIT")
        self.assertEqual(apply_eligibility(j, e, "🔴 不投")[0], "🟡 条件投")

    def test_strong_skills_do_not_rescue_failed_years(self):
        j = judgment(8, status="FAIL", reach="SENIOR_NO_BRANCH")
        self.assertEqual(derive(j)[0], "🔴 不投")

    def test_staff_is_not_a_skill_based_stretch(self):
        self.assertEqual(derive(judgment(reach="STAFF_PLUS"))[0], "🔴 不投")

    def test_unstated_level_cannot_become_green_from_skill_match(self):
        self.assertEqual(derive(judgment(reach="UNKNOWN"))[0], "🟡 条件投")

    def test_one_or_two_silent_years_are_conditional(self):
        for n in (1, 2):
            with self.subTest(years=n):
                self.assertEqual(derive(judgment(n))[0], "🟡 条件投")

    def test_phd_plus_one_year_is_not_automatic_pass(self):
        j = judgment(8, phd=1, status="PASS_VIA_PHD_BRANCH", reach="SENIOR_WITH_PHD_BRANCH")
        self.assertEqual(derive(j)[0], "🟡 条件投")

    def test_extra_professional_year_is_not_waived(self):
        j = judgment(5, phd=1, counts="NO_EXPLICIT", status="PASS_VIA_PHD_BRANCH")
        self.assertEqual(derive(j)[0], "🔴 不投")

    def test_generic_equivalence_does_not_waive_four_years(self):
        self.assertEqual(derive(judgment(4, status="PASS_VIA_EQUIVALENCE_CLAUSE"))[0], "🔴 不投")

    def test_phd_zero_year_alternative_survives(self):
        j = judgment(5, phd=0, counts="NO_EXPLICIT", status="PASS_VIA_PHD_BRANCH")
        self.assertEqual(derive(j)[0], "🟢 投")

    def test_explicit_verified_research_and_no_years_survive(self):
        j = judgment(3, counts="YES_EXPLICIT")
        j["hard_gates"]["years"].update(verified_research_years=3,
                                       research_evidence_ids=["E-FICTIONAL-THREE-YEARS"])
        self.assertEqual(derive(j)[0], "🟢 投")
        self.assertEqual(derive(judgment())[0], "🟢 投")

    def test_conditional_audit_never_promotes_to_green(self):
        j = judgment(1)
        tier, _ = apply_eligibility(j, audit("CONDITIONAL_RESEARCH_YEARS", True), "🟢 投")
        self.assertEqual(tier, "🟡 条件投")

    def test_conditional_audit_cannot_relabel_senior_or_staff(self):
        for reach, expected in (("SENIOR_NO_BRANCH", "stretch"), ("STAFF_PLUS", "🔴 不投")):
            with self.subTest(reach=reach):
                tier, updated = apply_eligibility(judgment(1, reach=reach), audit("CONDITIONAL_RESEARCH_YEARS"), expected)
                self.assertEqual(tier, expected)
                self.assertEqual(updated["official_level"]["reachability"], reach)

    def test_single_auditor_cannot_waive_a_hard_failure(self):
        j = judgment(1, counts="NO_EXPLICIT", status="FAIL")
        tier, _ = apply_eligibility(j, audit("ELIGIBLE_PHD_COUNTS"), "🔴 不投")
        self.assertEqual(tier, "🔴 不投")

    def test_unclear_audit_does_not_restore_old_green(self):
        j = judgment(4, status="PASS_VIA_EQUIVALENCE_CLAUSE")
        tier, _ = apply_eligibility(j, audit("UNCLEAR"), "🟢 投")
        self.assertEqual(tier, "🔴 不投")

    def test_confirmed_phd_zero_can_correct_old_years(self):
        j = judgment(5, counts="NO_EXPLICIT", status="FAIL", reach="SENIOR_NO_BRANCH")
        e = dict(audit("ELIGIBLE_PHD_COUNTS", True), policy_version=ELIGIBILITY_POLICY_VERSION,
                 decisive_quote="PhD with 0 years of experience", qualifying_years_min=0,
                 counts_research_as_experience="SILENT")
        tier, updated = apply_eligibility(j, e, "🔴 不投")
        self.assertEqual(tier, "🟢 投")
        self.assertEqual(updated["hard_gates"]["years"]["phd_branch_years"], 0)

    def test_confirmed_research_acceptance_can_correct_old_years(self):
        j = judgment(3, status="FAIL")
        j["hard_gates"]["years"].update(verified_research_years=3,
                                       research_evidence_ids=["E-FICTIONAL-THREE-YEARS"])
        e = dict(audit("ELIGIBLE_PHD_COUNTS", True), policy_version=ELIGIBILITY_POLICY_VERSION,
                 decisive_quote="3 years of experience including graduate research", qualifying_years_min=3,
                 counts_research_as_experience="YES_EXPLICIT")
        self.assertEqual(apply_eligibility(j, e, "🔴 不投")[0], "🟢 投")

    def test_research_acceptance_still_requires_enough_research_time(self):
        j = judgment(8, counts="YES_EXPLICIT")
        j["hard_gates"]["years"].update(verified_research_years=3,
                                       research_evidence_ids=["E-FICTIONAL-THREE-YEARS"])
        self.assertEqual(derive(j)[0], "🔴 不投")

    def test_old_policy_cannot_rescue_failed_years(self):
        j = judgment(5, status="FAIL", reach="SENIOR_NO_BRANCH")
        e = dict(audit("ELIGIBLE_PHD_COUNTS", True), policy_version="2026-09-23a",
                 decisive_quote="PhD with 0 years of experience", qualifying_years_min=0,
                 counts_research_as_experience="SILENT")
        self.assertEqual(apply_eligibility(j, e, "🔴 不投")[0], "🔴 不投")

    def test_eligible_label_without_branch_details_cannot_rescue(self):
        j = judgment(status="FAIL")
        e = dict(audit("ELIGIBLE_NEW_GRAD", True), policy_version=ELIGIBILITY_POLICY_VERSION,
                 decisive_quote="Early career role")
        self.assertEqual(apply_eligibility(j, e, "🔴 不投")[0], "🔴 不投")

    def test_audit_identified_manager_is_rejected(self):
        e = dict(audit("LEVEL_MISMATCH"), level_mismatch_kind="STAFF_PLUS")
        self.assertEqual(apply_eligibility(judgment(), e, "🟢 投")[0], "🔴 不投")

    def test_audit_identified_ordinary_senior_is_observation_only(self):
        e = dict(audit("LEVEL_MISMATCH"), level_mismatch_kind="SENIOR_NO_BRANCH")
        self.assertEqual(apply_eligibility(judgment(), e, "🟢 投")[0], "stretch")

    def test_new_audit_wins_over_older_zfix_filename(self):
        with tempfile.TemporaryDirectory() as root:
            d = Path(root) / "数据" / "资格审计输出"
            d.mkdir(parents=True)
            old = dict(audit("NOT_ELIGIBLE"), uid="test:1", as_of_date="2026-09-24")
            new = dict(audit("ELIGIBLE_PHD_COUNTS"), uid="test:1", as_of_date="2026-10-17",
                       policy_version=ELIGIBILITY_POLICY_VERSION)
            for name, record, stamp in (("elig_zfix20260924_01.json", old, 1), ("elig_20261017_01.json", new, 2)):
                p = d / name
                p.write_text(json.dumps([record]))
                os.utime(p, (stamp, stamp))
            self.assertEqual(load_eligibility(root)["test:1"]["verdict"], "ELIGIBLE_PHD_COUNTS")

    def test_current_double_audit_can_restore_silent_two_year_attempt(self):
        j = judgment(2, status="FAIL")
        e = dict(audit("CONDITIONAL_RESEARCH_YEARS", True), policy_version=ELIGIBILITY_POLICY_VERSION,
                 qualifying_years_min=2, counts_research_as_experience="SILENT",
                 decisive_quote="2 years of relevant experience")
        self.assertEqual(apply_eligibility(j, e, "🔴 不投")[0], "🟡 条件投")

    def test_stale_challenge_cannot_override_current_audit(self):
        with tempfile.TemporaryDirectory() as root:
            d = Path(root) / "数据" / "资格审计输出"
            d.mkdir(parents=True)
            a = dict(audit("ELIGIBLE_PHD_COUNTS"), uid="test:1", as_of_date="2026-10-17",
                     policy_version=ELIGIBILITY_POLICY_VERSION)
            c = dict(uid="test:1", upheld=False, challenger_verdict="NOT_ELIGIBLE", as_of_date="2026-09-24")
            (d / "elig_01.json").write_text(json.dumps([a]))
            (d / "elig_01_复核.json").write_text(json.dumps([c]))
            e = load_eligibility(root)["test:1"]
            self.assertEqual(e["verdict"], "ELIGIBLE_PHD_COUNTS")
            self.assertFalse(e["policy_current"])
            self.assertFalse(e["raise_ok"])


if __name__ == "__main__":
    unittest.main()
