"""Synthetic employer evidence confirms existence without inventing dates."""
import copy
from datetime import date
import unittest

import make_dashboard


def application(**metadata):
    return {
        "id": "example-role", "company": "Example", "title": "Engineer",
        "events": [
            {"type": "referral_request", "date": "2026-09-06"},
            {"type": "referral_submitted", "date": "2026-09-10"},
            {"type": "rejected", "date": "2026-10-29",
             "summary": "Verified employer rejection for this requisition"},
        ],
        **metadata,
    }


def derive(app):
    return make_dashboard.derive(app, date(2026, 10, 30), "fixture@example.test")


class ApplicationEvidenceTests(unittest.TestCase):
    def test_verified_rejection_confirms_existence_without_submission_dates(self):
        app = application(application_existence_verified=True,
                          existence_evidence_kind="employer_rejection_email")
        before = copy.deepcopy(app)
        result = derive(app)
        self.assertTrue(result["application_confirmed"])
        self.assertEqual(result["stage"], "rejected")
        self.assertEqual(result["flow_outcome"], "rejected")
        self.assertEqual(result["history_highest"], "application")
        self.assertIsNone(result["applied_date"])
        self.assertIsNone(result["receipt_date"])
        self.assertTrue(result["submission_date_unknown"])
        self.assertEqual(result["submission"]["basis"],
                         "招聘拒信证明申请存在；实际提交日期和收件日期均未知")
        self.assertEqual(app, before)

    def test_rejection_requires_explicit_boolean_verification_and_supported_kind(self):
        cases = (
            {},
            {"existence_evidence_kind": "employer_rejection_email"},
            {"application_existence_verified": False, "existence_evidence_kind": "employer_rejection_email"},
            {"application_existence_verified": "true", "existence_evidence_kind": "employer_rejection_email"},
            {"application_existence_verified": True, "existence_evidence_kind": "general_autoresponse"},
        )
        for metadata in cases:
            with self.subTest(metadata=metadata):
                result = derive(application(**metadata))
                self.assertFalse(result["application_confirmed"])
                self.assertEqual(result["stage"], "rejected")
                self.assertIsNone(result["submission"])
                self.assertIsNone(result["applied_date"])
                self.assertIsNone(result["receipt_date"])

    def test_dated_submission_or_receipt_keeps_its_own_evidence(self):
        for kind, field in (("applied", "applied_date"), ("ack", "receipt_date")):
            with self.subTest(kind=kind):
                app = application(application_existence_verified=True,
                                  existence_evidence_kind="employer_rejection_email")
                app["events"].insert(0, {"type": kind, "date": "2026-09-11"})
                result = derive(app)
                self.assertTrue(result["application_confirmed"])
                self.assertEqual(result[field], "2026-09-11")
                if kind == "ack":
                    self.assertIsNone(result["applied_date"])

    def test_existing_withdrawal_evidence_preserves_unknown_dates(self):
        app = application(application_existence_verified=True,
                          existence_evidence_kind="withdrawal_confirmation")
        app["events"] = [{"type": "withdrawn", "date": "2026-10-27"}]
        result = derive(app)
        self.assertTrue(result["application_confirmed"])
        self.assertEqual(result["flow_outcome"], "withdrawn")
        self.assertIsNone(result["applied_date"])
        self.assertIsNone(result["receipt_date"])
        self.assertEqual(result["submission"]["basis"],
                         "撤回确认或额度通知证明申请曾存在；通知日不是提交日或收件日")


if __name__ == "__main__":
    unittest.main()
