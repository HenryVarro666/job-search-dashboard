"""Offline checks for the LinkedIn hiring-post discovery plan."""
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
import urllib.request


SCRIPT = Path(__file__).with_name("linkedin_search_plan.py")
DAY = dt.date(2026, 10, 28)


class LinkedinSearchPlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import linkedin_search_plan
        cls.planner = linkedin_search_plan

    def test_scopes_keep_fulltime_and_internship_separate(self):
        for scope, total, recent, recall, per_source in (
            ("fulltime", 10, 8, 2, (5, 4, 1)),
            ("internship", 4, 2, 2, (2, 1, 1)),
            ("all", 14, 10, 4, (7, 5, 2)),
        ):
            with self.subTest(scope=scope):
                plan = self.planner.build_plan(scope, DAY)
                self.assertEqual(plan["scope"], scope)
                self.assertEqual(len(plan["queries"]), total)
                self.assertEqual(plan["coverage"]["queries"], total)
                self.assertEqual(plan["coverage"]["recent"], recent)
                self.assertEqual(plan["coverage"]["recall"], recall)
                self.assertEqual(len({q["id"] for q in plan["queries"]}), total)
                for source in ("native_posts", "public_web"):
                    coverage = plan["coverage"][source]
                    self.assertEqual(
                        (coverage["queries"], coverage["recent"], coverage["recall"]),
                        per_source,
                    )
                scopes = {q["scope"] for q in plan["queries"]}
                self.assertEqual(scopes, {"fulltime", "internship"} if scope == "all" else {scope})

    def test_native_urls_roundtrip_short_boolean_queries_without_web_syntax(self):
        for q in self.planner.build_plan("all", DAY)["queries"]:
            if q["source"] != "native_posts":
                continue
            parsed = urlparse(q["search_url"])
            self.assertEqual(parsed.scheme, "https")
            self.assertEqual(parsed.netloc, "www.linkedin.com")
            self.assertEqual(parsed.path, "/search/results/content/")
            self.assertEqual(parse_qs(parsed.query), {"keywords": [q["query"]]})
            self.assertEqual(q["provider"], "linkedin_manual")
            self.assertLess(len(q["query"].split()), 20)
            for forbidden in ("site:", "after:", "*", "-senior", "-staff", "“", "”"):
                self.assertNotIn(forbidden, q["query"])
            self.assertNotRegex(q["query"], r"\b(?:and|or)\b")
            self.assertEqual(q["query"].count('"') % 2, 0)
            self.assertEqual(q["query"].count("("), q["query"].count(")"))
            self.assertIn(" AND ", q["query"])

    def test_public_web_searches_both_post_paths_with_separate_recency(self):
        for q in self.planner.build_plan("all", DAY)["queries"]:
            if q["source"] != "public_web":
                continue
            self.assertEqual(q["provider"], "public_web")
            self.assertEqual(q["requested_surface"], "indexed_linkedin_posts")
            self.assertFalse(q["manual_session_required"])
            self.assertTrue(q["query"].startswith(
                "(site:linkedin.com/posts/ OR site:linkedin.com/feed/update/) AND "
            ))
            self.assertEqual(q["search_url"], q["google_url"])
            params = parse_qs(urlparse(q["google_url"]).query)
            self.assertEqual(params["q"], [q["query"]])
            self.assertEqual(q["recency"]["basis"], "search_engine_approximate")
            self.assertFalse(q["recency"]["verified"])
            if q["window"] == "recent":
                self.assertTrue(q["query"].endswith(" after:2026-10-21"))
                self.assertEqual(params["tbs"], ["qdr:w"])
            else:
                self.assertNotIn("after:", q["query"])
                self.assertNotIn("tbs", params)

    def test_recent_targets_join_our_team_and_recall_avoids_qualification_gates(self):
        targets = {
            "medical_imaging": '"join our team" AND "medical imaging"',
            "tech_fintech_ds": '"join our team" AND "data scientist"',
            "applied_research": '"join our team" AND "research scientist"',
            "phd_mle": '"join our team" AND "machine learning"',
            "phd_spring": '"join our team" AND intern',
        }
        for q in self.planner.build_plan("all", DAY)["queries"]:
            if q["window"] == "recent":
                self.assertIn(targets[q["family"]], q["query"])
                self.assertEqual(q["time_filter"], "past_week")
                self.assertEqual(q["recency_days"], 7)
            else:
                self.assertEqual(q["time_filter"], "none")
                self.assertIsNone(q["recency_days"])
                self.assertNotIn("join our team", q["query"])
                for gate in ("phd", "new grad", "united states", "visa", "spring", "2027"):
                    self.assertNotIn(gate, q["query"].lower())
                if q["scope"] == "fulltime":
                    self.assertIn("(hiring OR openings)", q["query"])
                else:
                    self.assertIn("hiring AND (intern OR internship)", q["query"])

    def test_rotation_covers_four_days_and_stable_ids_do_not_depend_on_day(self):
        plans = [self.planner.build_plan(day=DAY + dt.timedelta(days=n)) for n in range(5)]
        families = [p["coverage"]["fulltime_recall_family"] for p in plans]
        self.assertEqual(set(families[:4]),
                         {"medical_imaging", "tech_fintech_ds", "applied_research", "phd_mle"})
        self.assertEqual(families[0], families[4])
        self.assertEqual(plans[0], self.planner.build_plan(day=DAY))
        self.assertEqual({q["id"] for q in plans[0]["queries"]},
                         {q["id"] for q in plans[4]["queries"]})
        for plan in plans:
            self.assertEqual(plan["coverage"]["fulltime_recall_cycle_days"], 4)
            recall = [q for q in plan["queries"] if q["window"] == "recall"]
            self.assertEqual({q["family"] for q in recall},
                             {plan["coverage"]["fulltime_recall_family"]})
        internship = self.planner.build_plan("internship", DAY)
        self.assertIsNone(internship["coverage"]["fulltime_recall_family"])
        self.assertIsNone(internship["coverage"]["fulltime_recall_cycle_days"])

    def test_generation_does_not_claim_execution_or_verified_native_filters(self):
        plan = self.planner.build_plan("all", DAY)
        self.assertEqual(plan["status"], "planned_not_executed")
        for q in plan["queries"]:
            self.assertEqual(q["status"], "not_run")
            self.assertFalse(q["filters_verified"])
            if q["source"] == "native_posts":
                self.assertTrue(q["manual_session_required"])
                self.assertEqual(q["requested_surface"], "Posts")
                self.assertEqual(q["requested_filters"], {
                    "surface": "Posts", "sort": "latest",
                    "date": "past_week" if q["window"] == "recent" else "none",
                })
                self.assertEqual(q["recency"]["basis"], "post_time")
                self.assertFalse(q["recency"]["verified"])

    def test_build_uses_no_file_or_network_access(self):
        with patch("builtins.open", side_effect=AssertionError("unexpected file access")), \
                patch.object(Path, "open", side_effect=AssertionError("unexpected file access")), \
                patch.object(Path, "mkdir", side_effect=AssertionError("unexpected file access")), \
                patch.object(socket, "socket", side_effect=AssertionError("unexpected network")), \
                patch.object(socket, "getaddrinfo", side_effect=AssertionError("unexpected network")), \
                patch.object(urllib.request, "urlopen", side_effect=AssertionError("unexpected network")):
            plan = self.planner.build_plan("all", DAY)
        self.assertEqual(len(plan["queries"]), 14)
        self.assertNotIn("first_seen", json.dumps(plan))

    def test_default_day_uses_chicago_on_both_sides_of_midnight(self):
        real_datetime = dt.datetime
        for utc_hour, expected_day in ((4, "2026-10-27"), (5, "2026-10-28")):
            class FixedClock(real_datetime):
                @classmethod
                def now(cls, tz=None):
                    return real_datetime(2026, 10, 28, utc_hour,
                                         tzinfo=dt.timezone.utc).astimezone(tz)

            with patch.object(self.planner.dt, "datetime", FixedClock):
                plan = self.planner.build_plan()
            self.assertEqual(plan["date"], expected_day)
            self.assertEqual(plan["timezone"], "America/Chicago")

    def test_output_file_and_stdout_are_json_and_only_requested_file_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "plan.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.planner.main(["--scope", "internship", "--date", "2026-10-28",
                                   "--output", str(output)])
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(json.loads(output.read_text())["coverage"]["queries"], 4)
            self.assertEqual(list(Path(tmp).iterdir()), [output])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.planner.main(["--scope", "all", "--date", "2026-10-28"])
        self.assertEqual(json.loads(stdout.getvalue())["coverage"]["queries"], 14)

    def test_invalid_scope_and_cli_date_are_rejected(self):
        with self.assertRaises(ValueError):
            self.planner.build_plan("other", DAY)
        for args in (["--scope", "other"], ["--date", "not-a-date"],
                     ["--date", "20261028"], ["--date", "2026-W44-3"]):
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                self.planner.main(args)
            self.assertEqual(error.exception.code, 2)


class LinkedinSearchPlanCliTest(unittest.TestCase):
    def test_cli_defaults_to_ten_fulltime_queries_on_stdout(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--date", "2026-10-28"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["scope"], "fulltime")
        self.assertEqual(plan["coverage"]["queries"], 10)
        self.assertEqual(len(plan["queries"]), 10)


if __name__ == "__main__":
    unittest.main()
