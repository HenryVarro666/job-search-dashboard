"""Offline tracking regressions: missing or stale evidence never closes a role."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import track


class TrackingClosureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / "raw"
        self.out = self.root / "out"
        self.batch = self.root / "batch"
        for directory in (self.raw, self.out, self.batch / "数据"):
            directory.mkdir(parents=True)
        self.cfg = {"companies": [{"key": "example", "ats": "greenhouse"}]}
        self.judged = {"uid": "example:R1", "req_id": "R1", "company": "Example",
                       "title": "Research Scientist", "final_tier": "🟢 投"}
        self.write(self.batch / "数据" / "终评_fixture.json", [self.judged])
        self.write(self.raw / "_list.json", {
            "_listed_at": track.TODAY + "T09:00:00", "adapter": "greenhouse",
            "items": [{"id": "R1", "req_id": "R1"}]})
        for target, value in (("track.OUT_DIR", str(self.out)),
                              ("track.load_companies", self.cfg),
                              ("track.raw_dir", str(self.raw))):
            mock = patch(target, value) if target.endswith("OUT_DIR") else patch(target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        log = patch("track.append_log")
        log.start()
        self.addCleanup(log.stop)

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def delta(self, records):
        self.write(self.out / "jobs_all.json", records)
        return track.cmd_delta(str(self.batch))

    def closure_ledger(self):
        return json.loads((self.batch / "数据" / "追踪" / "closed.json").read_text())

    def seed_closure(self, source="track"):
        directory = self.batch / "数据" / "追踪"
        directory.mkdir(exist_ok=True)
        closure = {"date": "2020-01-01", "reason": "Official closed response",
                   "source": source, "company": "Example", "title": "Research Scientist"}
        self.write(directory / "closed.json", {"example:R1": closure})
        return closure

    def assert_restore_unverified(self, record):
        closure = self.seed_closure()
        delta = self.delta([record])
        self.assertEqual(self.closure_ledger(), {"example:R1": closure})
        self.assertEqual([row["uid"] for row in delta["unverifiable"]], ["example:R1"])
        persisted = json.loads((self.batch / "数据" / "追踪" / f"delta_{track.STAMP}.json").read_text())
        self.assertEqual([row["uid"] for row in persisted["unverifiable"]], ["example:R1"])

    def test_stale_unknown_can_apply_preserves_prior_closure_and_records_unverified(self):
        self.assert_restore_unverified(dict(self.judged, can_apply=None,
                                            source_authority="primary", fetched_at="2020-01-01T10:00:00"))

    def test_stale_true_cannot_restore_prior_closure(self):
        self.assert_restore_unverified(dict(self.judged, can_apply=True,
                                            source_authority="primary", fetched_at=track.TODAY + "T08:00:00"))

    def test_unknown_flag_or_non_primary_source_cannot_restore_prior_closure(self):
        for can_apply, authority in ((None, "primary"), (True, "display"), (True, "manual"), (True, None)):
            with self.subTest(can_apply=can_apply, source_authority=authority):
                self.assert_restore_unverified(dict(self.judged, can_apply=can_apply,
                    source_authority=authority, fetched_at=track.TODAY + "T10:00:00"))

    def test_current_primary_true_restores_a_tracking_closure(self):
        self.seed_closure()
        delta = self.delta([dict(self.judged, can_apply=True, source_authority="primary",
                                 fetched_at=track.TODAY + "T10:00:00")])
        self.assertEqual(self.closure_ledger(), {})
        self.assertEqual(delta["closed"], [])
        self.assertEqual(delta["unverifiable"], [])

    def test_current_true_still_preserves_apply_links_origin_closure(self):
        closure = self.seed_closure("apply_links")
        self.delta([dict(self.judged, can_apply=True, source_authority="primary",
                         fetched_at=track.TODAY + "T10:00:00")])
        self.assertEqual(self.closure_ledger(), {"example:R1": closure})

    def test_current_listed_req_missing_after_detail_or_build_failure_is_unverified(self):
        delta = self.delta([])
        self.assertTrue(delta["company_health"]["example"]["ok"])
        self.assertEqual(delta["closed"], [])
        self.assertEqual([row["uid"] for row in delta["unverifiable"]], ["example:R1"])
        self.assertEqual(self.closure_ledger(), {})

    def test_even_fresh_list_absence_does_not_prove_closure(self):
        self.write(self.raw / "_list.json", {
            "_listed_at": track.TODAY + "T09:00:00", "adapter": "greenhouse",
            "items": [{"id": "R2", "req_id": "R2"}]})
        delta = self.delta([])
        self.assertEqual(delta["closed"], [])
        self.assertEqual(len(delta["unverifiable"]), 1)
        self.assertEqual(self.closure_ledger(), {})

    def test_cached_false_from_before_current_enumeration_is_unverified(self):
        delta = self.delta([dict(self.judged, can_apply=False,
                                 fetched_at=track.TODAY + "T08:00:00")])
        self.assertEqual(delta["closed"], [])
        self.assertEqual(len(delta["unverifiable"]), 1)
        self.assertEqual(self.closure_ledger(), {})

    def test_false_without_fetch_provenance_is_unverified(self):
        delta = self.delta([dict(self.judged, can_apply=False)])
        self.assertEqual(delta["closed"], [])
        self.assertEqual(len(delta["unverifiable"]), 1)
        self.assertEqual(self.closure_ledger(), {})

    def test_current_false_from_non_primary_or_unknown_source_is_unverified(self):
        for authority in (None, "display", "manual"):
            with self.subTest(source_authority=authority):
                delta = self.delta([dict(self.judged, can_apply=False,
                                         source_authority=authority,
                                         fetched_at=track.TODAY + "T10:00:00")])
                self.assertEqual(delta["closed"], [])
                self.assertEqual(len(delta["unverifiable"]), 1)
                self.assertEqual(self.closure_ledger(), {})

    def test_current_explicit_false_is_recorded_as_closure(self):
        delta = self.delta([dict(self.judged, can_apply=False, source_authority="primary",
                                 fetched_at=track.TODAY + "T10:00:00")])
        self.assertEqual([row["uid"] for row in delta["closed"]], ["example:R1"])
        self.assertEqual(delta["unverifiable"], [])
        self.assertEqual(self.closure_ledger()["example:R1"]["source"], "track")


if __name__ == "__main__":
    unittest.main()
