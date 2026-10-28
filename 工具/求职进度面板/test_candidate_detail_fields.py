"""Synthetic candidate details must retain decision-blocking facts in search results."""
import datetime as dt
import sys
import unittest
import make_dashboard as dashboard

sys.path.insert(0, str(dashboard.HC_DIR))

class CandidateDetailFields(unittest.TestCase):
    def test_candidate_search_payload_preserves_unresolved_conditions(self):
        row = {"uid": "manual:demo", "company": "Example", "req": "DEMO", "title": "Research Scientist",
               "tier": "🟡 条件投", "status": "pending", "track": "M", "role": "research",
               "elig_line": "Experience requirement needs confirmation", "loc": "Example City", "pay": "Undisclosed",
               "action": "Read the requirements", "judge_line": "Method transfer only",
               "priority_review": {"rank": 1, "reason": "Synthetic review", "actionable": True,
                                   "next_step": "Confirm the experience wording", "blockers": [
                                       {"kind": "经验", "detail": "Research equivalence is unconfirmed"}]}}
        result = dashboard.today_panel({"items": [row], "roles": {}}, {"upcoming": []}, [], dt.date(2026, 10, 27))
        card = result["todo"][0]
        self.assertEqual(card.get("elig_line"), row["elig_line"])
        self.assertEqual(card.get("loc"), row["loc"])
        self.assertEqual(card.get("action"), row["priority_review"]["next_step"])
        self.assertEqual(card.get("blockers"), "经验：Research equivalence is unconfirmed")
        self.assertEqual(card.get("reason"), "Synthetic review")

if __name__ == "__main__": unittest.main()
