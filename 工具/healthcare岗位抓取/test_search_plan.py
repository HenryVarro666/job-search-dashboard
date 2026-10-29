import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import search_plan


DAY = dt.date(2026, 10, 27)


class SearchPlanTest(unittest.TestCase):
    def test_fulltime_counts_and_both_greenhouse_hosts(self):
        plan = search_plan.build_plan(day=DAY)
        self.assertEqual(plan["coverage"]["queries"], 30)
        self.assertEqual(plan["coverage"]["recent"], 24)
        self.assertEqual(plan["coverage"]["recall"], 6)
        hosts = {q["domain"] for q in plan["queries"]}
        self.assertEqual(hosts, set(search_plan.DOMAINS))
        self.assertTrue({"boards.greenhouse.io", "job-boards.greenhouse.io"} <= hosts)
        self.assertEqual(len({q["id"] for q in plan["queries"]}), 30)

    def test_rotation_is_deterministic_and_covers_four_groups(self):
        plans = [search_plan.build_plan(day=DAY + dt.timedelta(days=n)) for n in range(5)]
        families = [p["coverage"]["fulltime_recall_family"] for p in plans]
        self.assertEqual(len(set(families[:4])), 4)
        self.assertEqual(families[0], families[4])
        self.assertEqual(plans[0], search_plan.build_plan(day=DAY))

    def test_recent_and_recall_do_not_share_date_constraints(self):
        for q in search_plan.build_plan("all", DAY)["queries"]:
            params = parse_qs(urlparse(q["google_url"]).query)
            self.assertEqual(params["q"], [q["query"]])
            self.assertLess(len(q["query"].split()), 32)
            self.assertNotIn("-senior", q["query"].lower())
            self.assertNotIn("-staff", q["query"].lower())
            self.assertEqual(q["status"], "not_run")
            if q["window"] == "recent":
                self.assertIn("after:2026-10-20", q["query"])
                self.assertEqual(params["tbs"], ["qdr:w"])
                self.assertEqual(q["recency_days"], 7)
                self.assertIn('"', q["query"])
            else:
                self.assertNotIn("after:", q["query"])
                self.assertNotIn("tbs", params)
                self.assertNotIn('"', q["query"])
                self.assertNotIn("PhD", q["query"])
                self.assertNotIn("Remote", q["query"])
                self.assertIsNone(q["recency_days"])

    def test_internship_separate_and_not_fixed_to_calendar_year(self):
        plan = search_plan.build_plan("internship", DAY)
        self.assertEqual(plan["coverage"]["queries"], 12)
        self.assertIsNone(plan["coverage"]["fulltime_recall_family"])
        for q in plan["queries"]:
            self.assertEqual(q["scope"], "internship")
            self.assertNotIn("2027", q["query"])
        self.assertEqual(search_plan.build_plan("all", DAY)["coverage"]["queries"], 42)

    def test_default_day_uses_chicago_at_utc_midnight_boundary(self):
        real_datetime = dt.datetime

        class FixedClock(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return real_datetime(2026, 10, 28, 1, tzinfo=dt.timezone.utc).astimezone(tz)

        with patch.object(search_plan.dt, "datetime", FixedClock):
            plan = search_plan.build_plan()
        self.assertEqual(plan["date"], "2026-10-27")
        self.assertEqual(plan["timezone"], "America/Chicago")

    def test_build_does_not_read_personal_files(self):
        with patch("builtins.open", side_effect=AssertionError("unexpected file read")), \
                patch.object(Path, "read_text", side_effect=AssertionError("unexpected file read")):
            plan = search_plan.build_plan("all", DAY)
        self.assertEqual(plan["status"], "planned_not_executed")
        self.assertNotIn("first_seen", json.dumps(plan))

    def test_cli_writes_only_requested_output_or_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "plan.json"
            search_plan.main(["--scope", "internship", "--date", "2026-10-27", "--output", str(output)])
            self.assertEqual(json.loads(output.read_text())["coverage"]["queries"], 12)
            self.assertEqual(list(Path(tmp).iterdir()), [output])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            search_plan.main(["--date", "2026-10-27"])
        self.assertEqual(json.loads(buf.getvalue())["coverage"]["queries"], 30)

    def test_invalid_scope_rejected(self):
        with self.assertRaises(ValueError):
            search_plan.build_plan("other", DAY)


if __name__ == "__main__":
    unittest.main()
