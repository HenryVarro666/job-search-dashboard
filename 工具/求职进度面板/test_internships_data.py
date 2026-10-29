"""The internship shortlist is a separate local source, not application history."""
import json
from pathlib import Path
import tempfile
import unittest

import make_dashboard as dashboard


class InternshipDataTest(unittest.TestCase):
    def test_loads_a_separate_shortlist_and_preserves_unknown_opt(self):
        self.assertTrue(hasattr(dashboard, "load_internships"), "internship source loader is missing")
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "internships.json"
            source.write_text(json.dumps({"updated": "2026-10-26", "report_path": "reports/research.md",
                "items": [{"id": "example-r1", "company": "Example", "req": "R1", "title": "PhD Intern",
                           "url": "https://example.org/jobs/R1", "state": "paused", "group": "primary",
                           "opt_status": "unconfirmed", "opt_note": "未披露", "checked_on": "2026-10-26"}]}), encoding="utf-8")
            result = dashboard.load_internships(source)
        self.assertEqual(result["items"][0]["opt_status"], "unconfirmed")
        self.assertEqual(result["items"][0]["state"], "paused")
        self.assertIn("obsidian://open?", result["report_url"])
        self.assertNotIn("events", result["items"][0])

    def test_missing_shortlist_has_an_empty_state(self):
        self.assertTrue(hasattr(dashboard, "load_internships"), "internship source loader is missing")
        with tempfile.TemporaryDirectory() as temp:
            result = dashboard.load_internships(Path(temp) / "missing.json")
        self.assertEqual(result["items"], [])

    def test_duplicate_roles_are_reported_instead_of_double_counted(self):
        self.assertTrue(hasattr(dashboard, "load_internships"), "internship source loader is missing")
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "internships.json"
            source.write_text(json.dumps({"items": [{"id": "duplicate"}, {"id": "duplicate"}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                dashboard.load_internships(source)


if __name__ == "__main__":
    unittest.main()
