"""Brain MRI evidence must not waive another clinical domain's requirements."""
import unittest

import judge_tools
from judge_tools import apply_eligibility, derive
from test_judge_eligibility import judgment, audit


def with_domain(status, required="NOT_REQUIRED"):
    j = judgment()
    j["domain_fit"] = {
        "status": status, "required_domain_status": required,
        "jd_domain": "oncology / digital pathology",
        "jd_quote": "Familiarity with oncology and biomarker development",
        "candidate_evidence_ids": ["E-FICTIONAL-RESEARCH"],
        "gap_cn": "脑 MRI 经历不能证明肿瘤和 biomarker 知识。",
        "transfer_basis_cn": "Python / ML 方法有重叠。",
    }
    return j


class DomainFitTests(unittest.TestCase):
    def test_required_oncology_knowledge_is_not_waived_by_general_ml(self):
        self.assertEqual(derive(with_domain("GAP", "UNMET"))[0], "🔴 不投")

    def test_same_medical_imaging_tag_does_not_make_cross_modality_direct(self):
        self.assertEqual(derive(with_domain("METHOD_TRANSFER"))[0], "🟡 条件投")

    def test_department_or_unknown_project_does_not_prove_fit(self):
        self.assertEqual(derive(with_domain("UNVERIFIED", "UNVERIFIED"))[0], "🟡 条件投")

    def test_generic_software_work_does_not_require_clinical_expertise(self):
        self.assertEqual(derive(with_domain("GENERAL_TECH"))[0], "🟢 投")

    def test_direct_brain_imaging_work_can_remain_green(self):
        self.assertEqual(derive(with_domain("DIRECT", "MET"))[0], "🟢 投")

    def test_inconsistent_label_cannot_waive_unmet_required_domain(self):
        self.assertEqual(derive(with_domain("DIRECT", "UNMET"))[0], "🔴 不投")

    def test_eligibility_pass_cannot_restore_a_domain_failure(self):
        j = with_domain("GAP", "UNMET")
        self.assertEqual(apply_eligibility(j, audit("ELIGIBLE_NEW_GRAD", True), "🟢 投")[0], "🔴 不投")

    def test_legacy_missing_domain_review_is_not_green(self):
        j = judgment()
        j.pop("domain_fit")
        self.assertEqual(derive(j)[0], "🟡 条件投")

    def test_saved_review_restricts_spend_and_preserves_original_summary(self):
        j = judgment()
        j["one_line_cn"] = "Strong medical imaging match"
        j["cost_tiers"] = {k: {"cost": "8–20 h", "judgement_cn": "值得做", "p_offer_band": "5–15%"} for k in "ABC"}
        apply_review = getattr(judge_tools, "apply_domain_audit", lambda obj, fit: obj)
        apply_review(j, with_domain("METHOD_TRANSFER")["domain_fit"])
        self.assertEqual(j["cost_tiers"]["B"]["judgement_cn"], "不做")
        self.assertEqual(j["cost_tiers"]["A"]["cost"], "0.5–1 h")
        self.assertEqual(j["domain_review_previous"]["one_line_cn"], "Strong medical imaging match")

    def test_old_saved_review_does_not_override_new_jd_judgment(self):
        j = with_domain("DIRECT", "MET")
        j["domain_fit"].update(policy_version="2026-10-17-domain1", as_of_date="2026-10-18")
        old = dict(with_domain("GAP", "UNMET")["domain_fit"],
                   policy_version="2026-10-17-domain1", as_of_date="2026-10-17")
        judge_tools.apply_domain_audit(j, old)
        self.assertEqual(j["domain_fit"]["status"], "DIRECT")


if __name__ == "__main__":
    unittest.main()
