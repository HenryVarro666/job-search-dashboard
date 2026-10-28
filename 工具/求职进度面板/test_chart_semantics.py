"""Synthetic regressions for highest-stage chart semantics."""
import unittest

from oa_analytics import derive_analytics


class HighestStageTests(unittest.TestCase):
    def derive(self, kinds):
        events = [{"type": kind, "date": f"2026-10-{i + 1:02d}", "summary": "Synthetic event"}
                  for i, kind in enumerate(kinds)]
        return derive_analytics({"id": "synthetic", "events": events}, events, "rejected")

    def test_rejection_after_application_does_not_invent_recruiter_contact(self):
        result = self.derive(["applied", "rejected"])
        self.assertEqual(result["history_highest"], "application")
        self.assertEqual(result["flow_outcome"], "rejected")
        self.assertTrue(result["application_confirmed"])

    def test_rejection_without_application_evidence_remains_unconfirmed(self):
        result = self.derive(["rejected"])
        self.assertEqual(result["history_highest"], "unconfirmed")
        self.assertFalse(result["application_confirmed"])

    def test_rejection_preserves_actual_highest_recorded_stage(self):
        for event, expected in (("human_reply", "reply"), ("assessment", "assessment"),
                                ("interview", "interview"), ("offer", "offer")):
            with self.subTest(event=event):
                result = self.derive(["applied", event, "rejected"])
                self.assertEqual(result["history_highest"], expected)
                self.assertEqual(result["flow_outcome"], "rejected")


if __name__ == "__main__":
    unittest.main()
