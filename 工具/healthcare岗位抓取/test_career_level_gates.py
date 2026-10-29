#!/usr/bin/env python3
"""Offline regressions for new-PhD career gates; never runs the live funnel."""
import unittest

from funnel import gate_level, gate_role
from parse_jd import classify_level, classify_role_family, parse


class CareerLevelGatesTest(unittest.TestCase):
    def record(self, title, jd, **extra):
        return parse(dict(title=title, jd_text="Basic Qualifications\n" + jd,
                          track="C", locations=["US, MA, Cambridge"], **extra))

    def test_management_and_technical_lead_titles_are_excluded(self):
        for title in ("AI Engineering Mgr", "Technical Lead, Machine Learning",
                      "Tech Lead / Research Engineer", "AI Team Lead"):
            with self.subTest(title=title):
                self.assertEqual(classify_role_family(title, "", "PyTorch and machine learning", "C")[0], "OTHER")

    def test_leading_a_project_is_not_a_management_title(self):
        self.assertEqual(classify_role_family("Research Scientist", "",
                         "Lead research experiments using PyTorch and machine learning.", "C")[0], "RESEARCH_SCI")

    def test_preferred_people_management_does_not_become_a_hard_gate(self):
        rec = self.record("Research Scientist", "PhD with 0 years of experience.\nPyTorch and machine learning.\n"
                          "Preferred Qualifications\nExperience managing a team of scientists.")
        self.assertEqual(rec["role_family"], "RESEARCH_SCI")

    def test_reporting_lines_and_optional_management_are_not_hard_gates(self):
        for wording in ("The role does not have direct reports.",
                        "Communicate plans to stakeholders and line management.",
                        "Prepare for 1:1 direct report-manager meetings.",
                        "Experience in a leadership role with or without direct reports.",
                        "For a people management position, experience leading a scientific team.",
                        "Experience mentoring or managing ML scientists and engineers."):
            with self.subTest(wording=wording):
                rec = self.record("Research Scientist", "PhD with 0 years of experience.\n"
                                  "PyTorch and machine learning.\n" + wording)
                self.assertEqual(rec["role_family"], "RESEARCH_SCI")

    def test_collaborating_with_senior_scientists_does_not_set_senior_level(self):
        self.assertEqual(classify_level("Research Scientist", 0,
                         "Work with senior scientists and lead experiments.", level_hint="new_grad")["level_tag"], "new_grad")

    def test_explicit_people_management_is_excluded_even_with_generic_title(self):
        rec = self.record("Research Scientist", "PhD with 0 years of experience.\n"
                          "Required: 3 years of experience managing a team of scientists.\nPyTorch and machine learning.")
        self.assertEqual(rec["role_family"], "OTHER")

    def test_explicit_technical_lead_responsibility_is_excluded(self):
        rec = self.record("Machine Learning Engineer", "You will serve as the technical lead for our ML platform, "
                          "defining architecture and setting team technical direction.")
        self.assertEqual(rec["role_family"], "OTHER")
        self.assertEqual(gate_level([rec]), ([], [rec]))

    def test_required_management_is_not_erased_by_adjacent_preferred_clause(self):
        rec = self.record("Research Scientist", "PhD with 0 years of experience. PyTorch and machine learning.\n"
                          "Required: experience managing a team of scientists; healthcare experience preferred.")
        self.assertEqual(rec["role_family"], "OTHER")

    def test_explicit_people_management_negation_is_not_a_gate(self):
        for wording in ("Experience in people management is not required.",
                        "Experience managing a team of scientists is not required.",
                        "No people-management experience is required.",
                        "You will not manage data science teams."):
            with self.subTest(wording=wording):
                rec = self.record("Research Scientist", "PhD with 0 years of experience.\n"
                                  "PyTorch and machine learning.\n" + wording)
                self.assertEqual(rec["role_family"], "RESEARCH_SCI")

    def test_management_responsibility_allows_team_modifiers(self):
        rec = self.record("Research Scientist", "Must manage data science teams. PyTorch and machine learning.")
        self.assertEqual(rec["role_family"], "OTHER")

    def test_new_grad_hint_does_not_erase_explicit_senior_title(self):
        for title, tag in (("Senior Research Scientist", "senior"),
                           ("Staff ML Engineer", "staff"), ("Prin Scientist", "principal")):
            with self.subTest(title=title):
                self.assertEqual(classify_level(title, 0, "", level_hint="new_grad")["level_tag"], tag)

    def test_new_grad_hint_does_not_erase_senior_role_in_body(self):
        self.assertEqual(classify_level("Research Scientist", 0,
                         "We are seeking a Senior Research Scientist.", level_hint="new_grad")["level_tag"], "senior")

    def test_hiring_statement_does_not_copy_collaborator_or_manager_level(self):
        for wording in ("We are seeking a Research Scientist to work with a principal scientist.",
                        "We are seeking a talented Research Scientist, who will collaborate with a Staff Engineer.",
                        "We are hiring a research scientist reporting to a senior scientist."):
            with self.subTest(wording=wording):
                self.assertEqual(classify_level("Research Scientist", 0, wording,
                                               level_hint="new_grad")["level_tag"], "new_grad")

    def test_member_of_technical_staff_remains_flat_title(self):
        rec = self.record("Member of Technical Staff, Research Engineer", "PhD in ML.\nPyTorch and machine learning.")
        self.assertNotEqual(rec["level_tag"], "staff")
        self.assertNotEqual(rec["role_family"], "OTHER")
        self.assertEqual(gate_level([rec]), ([rec], []))

    def test_principal_scientist_explicit_phd_zero_is_retained_for_review(self):
        rec = self.record("Principal Scientist, Machine Learning", "PhD with 0 years of experience, "
                          "or Master's degree and 5 years of experience.\nPyTorch and machine learning.")
        self.assertEqual(rec["phd_branch_years"], 0)
        kept, _, _, _ = gate_role([rec])
        self.assertEqual(kept, [rec])
        self.assertEqual(gate_level(kept), ([rec], []))

    def test_principal_without_explicit_zero_and_staff_engineer_are_dropped(self):
        for title, jd in (("Principal Scientist, Machine Learning", "PhD and 2 years of industry experience."),
                          ("Staff ML Engineer", "PhD with 0 years of experience.")):
            with self.subTest(title=title):
                rec = self.record(title, jd + "\nPyTorch and machine learning.")
                self.assertEqual(gate_level([rec]), ([], [rec]))

    def test_principal_data_scientist_zero_path_survives_role_gate(self):
        rec = self.record("Principal Data Scientist", "PhD with 0 years of experience.\nPyTorch and machine learning.")
        self.assertEqual(gate_role([rec])[0], [rec])
        self.assertEqual(gate_level([rec]), ([rec], []))

    def test_principal_zero_with_people_management_is_dropped(self):
        rec = self.record("Principal Scientist, Machine Learning", "PhD with 0 years of experience.\n"
                          "Required: experience managing a team of scientists.\nPyTorch and machine learning.")
        self.assertEqual(gate_level([rec]), ([], [rec]))


if __name__ == "__main__":
    unittest.main()
