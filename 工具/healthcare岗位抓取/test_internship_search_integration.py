"""Offline checks for internship recall alongside the existing company queries."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from adapters import Adapter, WorkdayAdapter
from track import TODAY, company_health


class InternshipSearchIntegrationTest(unittest.TestCase):
    def test_company_queries_include_internships_and_respect_blacklist(self):
        co = {"key": "example", "keywords": ["MONAI", "research intern"],
              "keyword_blacklist": ["co-op"]}
        policy = {"enabled": True,
                  "search_keywords": ["research intern", "PhD intern", "co-op"]}
        with patch("adapters.read_json", return_value=policy):
            self.assertEqual(Adapter(co, {}).keywords(),
                             ["MONAI", "research intern", "PhD intern"])

    def test_disabled_branch_preserves_company_queries(self):
        with patch("adapters.read_json", return_value={"enabled": False,
                                                      "search_keywords": ["PhD intern"]}):
            self.assertEqual(Adapter({"key": "example", "keywords": ["medical"]},
                                     {}).keywords(), ["medical"])

    def test_default_queries_also_receive_internship_recall(self):
        with patch("adapters.read_json", return_value={"enabled": True,
                                                      "search_keywords": ["PhD intern"]}):
            self.assertEqual(Adapter({"key": "example"},
                                     {"defaults": {"keywords": ["health"]}}).keywords(),
                             ["health", "PhD intern"])

    def test_failed_health_query_cannot_be_hidden_by_new_internship_results(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp)
            for name, data in {
                "_list.json": {"_listed_at": TODAY, "adapter": "workday", "items": list(range(80))},
                "_list_prev.json": {"items": list(range(100))},
                "_totals.json": {"healthcare": None, "PhD intern": 80},
            }.items():
                (dest / name).write_text(json.dumps(data), encoding="utf-8")
            cfg = {"companies": [{"key": "example", "ats": "workday",
                                  "keywords": ["healthcare"]}]}
            with patch("track.raw_dir", return_value=temp):
                result = company_health(cfg)["example"]
            self.assertFalse(result["ok"])
            self.assertIn("healthcare", result["why"])

    def test_skipped_broad_workday_query_cannot_certify_other_query_results(self):
        co = {"key": "example", "ats": "workday", "keywords": ["broad", "narrow"],
              "max_total_per_keyword": 400, "workday": {
                  "host": "example.test", "tenant": "example", "site": "jobs"}}
        adapter = WorkdayAdapter(co, {})
        broad = {"total": 1000, "jobPostings": []}
        narrow = {"total": 1, "jobPostings": [
            {"bulletFields": ["R1"], "title": "ML Intern", "externalPath": "/job/R1"}]}
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(adapter, "_page", side_effect=[broad, narrow]), \
                    patch("adapters.read_json", return_value={"enabled": False}), \
                    patch("adapters.raw_dir", return_value=temp):
                items = adapter.list_jobs()
            Path(temp, "_list.json").write_text(json.dumps({
                "_listed_at": TODAY, "adapter": "workday", "items": items}), encoding="utf-8")
            with patch("track.raw_dir", return_value=temp), \
                    patch("adapters.read_json", return_value={"enabled": False}):
                result = company_health({"companies": [co]})["example"]
        self.assertEqual(len(items), 1)
        self.assertFalse(result["ok"])
        self.assertIn("broad", result["why"])

    def test_failed_workday_later_page_is_reported_as_incomplete(self):
        adapter = WorkdayAdapter({"key": "example", "workday": {
            "host": "example.org", "tenant": "example", "site": "jobs"}}, {})
        adapter.jitter = (0, 0)
        first = {"total": 40, "jobPostings": [
            {"bulletFields": ["R1"], "title": "ML Intern", "externalPath": "/job/R1"}]}
        with patch.object(adapter, "_page", side_effect=[first, None]):
            result = adapter._enumerate("healthcare")
        self.assertEqual(list(result[0]), ["R1"])
        self.assertIsNone(result[1])


if __name__ == "__main__":
    unittest.main()
