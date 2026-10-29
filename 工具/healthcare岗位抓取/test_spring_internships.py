import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.find_spec("spring_internships")
if SPEC:
    from spring_internships import assess, build_report
else:
    assess = build_report = None


# Synthetic policy is intentionally explicit and never reads a private local config.
POLICY = {"enabled": True, "target_year": 2027, "candidate_weeks": [8, 26],
          "search_keywords": ["research intern"], "school_status": "Fictional test policy; eligibility unconfirmed.",
          "graduation_preference": {"exclude_fall_2027_or_later": True}}


def job(title="Machine Learning PhD Intern - Spring 2027", **changes):
    record = {"uid": "example:123", "company": "Example", "req_id": "123",
              "title": title, "jd_text": "Currently enrolled in a PhD program. "
              "Build machine learning models for 12 weeks. Return to school after the internship.",
              "region": "美国", "location": "Seattle, WA", "url": "https://example.org/job/123",
              "restriction_flags": [], "restriction_evidence": [], "_fetched_at": "2026-10-01"}
    record.update(changes)
    return record


class SpringInternshipsTest(unittest.TestCase):
    def setUp(self):
        policy = patch("spring_internships.load_policy", return_value=copy.deepcopy(POLICY))
        policy.start()
        self.addCleanup(policy.stop)

    def test_policy_year_controls_later_graduation_exclusions_and_review_checks(self):
        policy = dict(POLICY, target_year=2031)
        for year, status in ((2030, "CONDITIONAL"), (2031, "EXCLUDED"), (2032, "EXCLUDED")):
            result = assess(job("ML PhD Intern - Spring 2031", jd_text=
                f"PhD machine learning internship for 12 weeks. Must graduate in Fall {year}."), policy)
            self.assertEqual(result["status"], status)
        result = assess(job("ML PhD Intern", jd_text="PhD machine learning internship for 12 weeks."), policy)
        self.assertTrue(any("2031" in check for check in result["checks"]))
        self.assertNotIn("2027", " ".join(result["checks"]))

    def check_assess(self, record):
        self.assertIsNotNone(assess, "spring internship selector is missing")
        return assess(record)

    def test_spring_candidate_keeps_enrollment_and_cached_date(self):
        result = self.check_assess(job())
        self.assertEqual(result["status"], "CONDITIONAL")
        self.assertEqual(result["season_status"], "SPRING")
        self.assertEqual(result["duration_weeks"], [12, 12])
        self.assertIn("Return to school", result["return_to_school_evidence"])
        self.assertEqual(result["source_dates"]["_fetched_at"], "2026-10-01")
        self.assertTrue(result["checks"])

    def test_summer_only_and_old_title_are_excluded(self):
        for title in ("ML PhD Intern - Summer 2027", "ML PhD Intern - Spring 2026"):
            with self.subTest(title=title):
                self.assertEqual(self.check_assess(job(title))["status"], "EXCLUDED")

    def test_multiple_seasons_and_unknown_season_are_not_lost(self):
        for title, expected in (("ML PhD Intern - Spring/Summer 2027", "SPRING"),
                                ("ML PhD Intern - Year-round 2027", "FLEXIBLE"),
                                ("ML PhD Intern", "UNVERIFIED")):
            with self.subTest(title=title):
                result = self.check_assess(job(title))
                self.assertEqual(result["status"], "CONDITIONAL")
                self.assertEqual(result["season_status"], expected)

    def test_body_can_explicitly_expand_summer_title_to_spring(self):
        result = self.check_assess(job("ML PhD Intern - Summer 2027", jd_text=
            "PhD students build machine learning models. We accept interns year-round, including Spring 2027 for 12 weeks."))
        self.assertEqual(result["status"], "CONDITIONAL")
        self.assertEqual(result["season_status"], "SPRING")

    def test_summer_may_start_does_not_create_spring_window(self):
        result = self.check_assess(job("ML PhD Intern - Summer 2027", jd_text=
            "PhD students build machine learning models. The summer internship lasts 12 weeks beginning in May or June 2027."))
        self.assertEqual(result["status"], "EXCLUDED")

    def test_graduation_and_return_seasons_are_not_internship_windows(self):
        for clause in ("Anticipated graduation date of Winter 2027, Spring 2028 or Fall 2028.",
                       "Must return to school in Spring 2027 after graduation."):
            result = self.check_assess(job("ML PhD Intern - Summer 2027", jd_text=
                "PhD students build machine learning models. " + clause))
            self.assertEqual(result["status"], "EXCLUDED")
        result = self.check_assess(job("ML PhD Intern", jd_text=
            "PhD students build machine learning models. Anticipated graduation date of Spring 2028."))
        self.assertEqual(result["season_status"], "UNVERIFIED")
        self.assertIn("Spring 2028", result["graduation_window_evidence"])

    def test_spring_house_and_spring_boot_are_not_term_evidence(self):
        for title, jd in (("ML PhD Intern - Spring House, PA", "PhD machine learning research."),
                          ("ML PhD Intern", "PhD research in machine learning and Spring Boot.")):
            result = self.check_assess(job(title, jd_text=jd))
            self.assertEqual(result["season_status"], "UNVERIFIED")

    def test_graduation_year_is_not_program_year(self):
        result = self.check_assess(job("ML PhD Intern - Spring 2027", jd_text=
            "Must be enrolled in a PhD program and graduate in 2028. Work on machine learning for 4 months."))
        self.assertEqual(result["status"], "EXCLUDED")
        self.assertIn("毕业", result["reason"])

    def test_required_fall_return_is_excluded_by_graduation_preference(self):
        result = self.check_assess(job(jd_text="PhD machine learning internship for 12 weeks. "
            "University Enrollment: Must be currently enrolled in and returning to an accredited degree-seeking academic program in the Fall."))
        self.assertEqual(result["status"], "EXCLUDED")
        self.assertIn("秋季", result["reason"])
        self.assertIn("returning", result["graduation_exclusion_evidence"])

    def test_preferred_fall_return_and_unstated_return_do_not_become_hard_exclusions(self):
        for text in ("Preferred: returning to a PhD program in Fall 2027.",
                     "Returning to school in Fall 2027 is not required.",
                     "Currently enrolled in a PhD program throughout the internship."):
            result = self.check_assess(job(jd_text="PhD machine learning internship for 12 weeks. " + text))
            self.assertEqual(result["status"], "CONDITIONAL")
            self.assertTrue(any("直接毕业" in check for check in result["checks"]))

    def test_graduation_deadline_or_wide_window_allows_earlier_completion(self):
        for text in ("Must graduate by Fall 2027.",
                     "Must have an expected graduation date between May 2027 and May 2028."):
            self.assertEqual(self.check_assess(job(jd_text="PhD machine learning internship for 12 weeks. " + text))["status"], "CONDITIONAL")

    def test_required_header_and_separate_preferred_clause_do_not_hide_return(self):
        for text in ("Required: returning to school in Fall 2027.",
                     "Must return to school in Fall 2027; Python experience preferred."):
            self.assertEqual(self.check_assess(job(jd_text="PhD machine learning internship for 12 weeks. " + text))["status"], "EXCLUDED")

    def test_mixed_season_years_do_not_promote_old_spring(self):
        result = self.check_assess(job("2027 ML PhD Intern", jd_text=
            "PhD machine learning internship: Spring 2026 or Summer 2027. Duration is 12 weeks."))
        self.assertNotEqual(result.get("season_status"), "SPRING")

    def test_decimal_pay_evidence_is_not_truncated(self):
        result = self.check_assess(job(jd_text="PhD machine learning internship. Pay: $17.85 - $35.70 per hour."))
        self.assertIn("$17.85 - $35.70", result["pay_evidence"])

    def test_unrelated_internship_and_undergraduate_only_are_excluded(self):
        records = [job("HR PhD Intern - Spring 2027", jd_text="PhD student working in human resources."),
                   job(jd_text="Open to undergraduate students only. Machine learning research for 12 weeks.")]
        for record in records:
            self.assertEqual(self.check_assess(record)["status"], "EXCLUDED")

    def test_only_prefix_and_applied_science_title(self):
        result = self.check_assess(job(jd_text="Only undergraduate students may apply. Machine learning internship."))
        self.assertEqual(result["status"], "EXCLUDED")
        result = self.check_assess(job("Applied Science Co-op - Spring 2027", jd_text=
            "Currently enrolled PhD students work on machine learning. Internship duration is 12 weeks."))
        self.assertEqual(result["status"], "CONDITIONAL")

    def test_undergrad_program_title_requires_other_degree_entry(self):
        result = self.check_assess(job("Undergrad Co-op - ML Quality - Spring 2027", jd_text=
            "Bachelor students build machine learning models and computer vision systems."))
        self.assertEqual(result["status"], "EXCLUDED")
        result = self.check_assess(job("Undergrad or PhD Co-op - ML Quality - Spring 2027"))
        self.assertEqual(result["status"], "CONDITIONAL")

    def test_non_us_closed_and_explicit_cpt_ban_are_excluded(self):
        for record in (job(region="非美国"), job(can_apply=False),
                       job(jd_text="PhD machine learning research. We do not accept CPT or OPT students.")):
            self.assertEqual(self.check_assess(record)["status"], "EXCLUDED")

    def test_unknown_sponsorship_does_not_disqualify_and_no_sponsor_is_evidence(self):
        result = self.check_assess(job(restriction_flags=["NO_SPONSOR"],
            restriction_evidence=["NO_SPONSOR: We do not sponsor visas."]))
        self.assertEqual(result["status"], "CONDITIONAL")
        self.assertIn("We do not sponsor visas.", result["restriction_evidence"][0])

    def test_too_long_excluded_but_missing_duration_kept_for_confirmation(self):
        self.assertEqual(self.check_assess(job(jd_text="PhD machine learning internship for 12 months."))["status"], "EXCLUDED")
        result = self.check_assess(job(jd_text="PhD machine learning internship."))
        self.assertEqual(result["status"], "CONDITIONAL")
        self.assertIsNone(result["duration_weeks"])

    def test_research_title_with_one_ml_signal_and_six_months_is_kept(self):
        result = self.check_assess(job("PhD Research Intern - Spring 2027", jd_text=
            "PhD students work on machine learning during a 6 month internship."))
        self.assertEqual(result["status"], "CONDITIONAL")

    def test_hyphenated_and_to_duration_ranges_are_extracted(self):
        for phrase, expected in (("12-week internship", [12, 12]),
                                 ("3 to 6 month internship", [13, 26])):
            result = self.check_assess(job(jd_text="PhD machine learning research in a " + phrase))
            self.assertEqual(result["duration_weeks"], expected)

    def test_experience_duration_does_not_hide_too_long_internship(self):
        result = self.check_assess(job(jd_text="PhD machine learning. Requires 6 months experience. Internship duration is 8 months."))
        self.assertEqual(result["status"], "EXCLUDED")
        result = self.check_assess(job(jd_text="PhD machine learning. Requires 12 months experience in Python."))
        self.assertEqual(result["status"], "CONDITIONAL")
        self.assertIsNone(result["duration_weeks"])

    def test_explicit_january_start_is_spring_not_graduation_date(self):
        result = self.check_assess(job("ML PhD Intern", jd_text="PhD students build machine learning models. Internship starts January 2027 for 12 weeks."))
        self.assertEqual(result["season_status"], "SPRING")
        result = self.check_assess(job("ML PhD Intern", jd_text="PhD machine learning students graduating in January 2027."))
        self.assertEqual(result["season_status"], "UNVERIFIED")

    def test_build_report_deduplicates_and_does_not_mutate_fulltime_input(self):
        self.assertIsNotNone(build_report, "spring internship selector is missing")
        records = [job(), job(), job("Senior ML Scientist - Full Time", uid="example:456")]
        original = copy.deepcopy(records)
        report = build_report(records)
        self.assertEqual(len(report["candidates"]), 1)
        self.assertEqual(report["counts"]["duplicates"], 1)
        self.assertEqual(records, original)

    def test_cli_writes_local_json_and_readable_candidate_table(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "jobs.json"
            dest = Path(temp) / "result"
            source.write_text(json.dumps([job()]), encoding="utf-8")
            policy = Path(temp) / "policy.json"
            policy.write_text(json.dumps(POLICY), encoding="utf-8")
            process = subprocess.run([sys.executable, str(Path(__file__).with_name("spring_internships.py")),
                                      "--input", str(source), "--output-dir", str(dest), "--policy", str(policy)],
                                     cwd=temp, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads((dest / "candidates.json").read_text(encoding="utf-8"))
            self.assertEqual(result["counts"]["candidates"], 1)
            self.assertFalse(result["live_verification"])
            self.assertIn("Example", (dest / "候选线索.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
