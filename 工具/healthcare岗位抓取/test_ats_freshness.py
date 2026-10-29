"""Offline checks for publication provenance and full-board cache refreshes."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from adapters import AshbyAdapter, GreenhouseAdapter
import common
import fetch_ats


class PublicationDateTest(unittest.TestCase):
    def setUp(self):
        self.item = {"id": "r1", "title": "Research Scientist", "url": "https://example.test/r1"}
        self.co = {"key": "example", "slug": "example"}

    def test_greenhouse_update_is_not_a_publication(self):
        raw = {"id": "r1", "title": "Research Scientist", "updated_at": "2026-10-27T08:00:00Z"}
        row = GreenhouseAdapter(self.co, {}).normalize(self.item, raw)
        self.assertEqual(row["posted"], "")
        self.assertEqual(row["posted_raw"], "")
        self.assertEqual(row["posted_basis"], "unknown")
        self.assertEqual(row["updated_at"], raw["updated_at"])

    def test_greenhouse_preserves_distinct_publish_and_update_dates(self):
        raw = {"id": "r1", "first_published": "2026-08-02T08:00:00Z",
               "updated_at": "2026-10-27T08:00:00Z"}
        row = GreenhouseAdapter(self.co, {}).normalize(self.item, raw)
        self.assertEqual(row["posted"], "2026-08-02")
        self.assertEqual(row["posted_raw"], raw["first_published"])
        self.assertEqual(row["posted_basis"], "greenhouse.first_published")
        self.assertEqual(row["updated_at"], raw["updated_at"])

    def test_greenhouse_list_does_not_label_updated_as_posted(self):
        raw = {"id": "r1", "title": "Research Scientist", "updated_at": "2026-10-27T08:00:00Z"}
        response = common.Resp(200, "", {"jobs": [raw]}, "", "")
        with patch("adapters.fetch", return_value=response), patch("adapters.write_json"), \
                patch.object(GreenhouseAdapter, "board_cache_path", return_value="unused"):
            row = GreenhouseAdapter(self.co, {}).list_jobs()[0]
        self.assertEqual(row["posted_raw"], "")
        self.assertEqual(row["list_meta"]["updated_at"], raw["updated_at"])

    def test_ashby_date_means_latest_publication(self):
        row = AshbyAdapter(self.co, {}).normalize(self.item, {
            "id": "r1", "publishedAt": "2026-10-27T08:00:00Z"})
        self.assertEqual(row["posted"], "2026-10-27")
        self.assertEqual(row["posted_basis"], "ashby.publishedAt.latest_publication")
        missing = AshbyAdapter(self.co, {}).normalize(self.item, {"id": "r1"})
        self.assertEqual(missing["posted_basis"], "unknown")


class BoardRefreshTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw_patch = patch.object(common, "RAW_DIR", self.temp.name)
        self.raw_patch.start()
        self.addCleanup(self.raw_patch.stop)
        self.log_patch = patch("fetch_ats.log")
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)
        self.directory = Path(common.raw_dir("example"))
        self.item = {"id": "r1", "req_id": "r1", "title": "Research Scientist",
                     "url": "https://example.test/r1"}
        self.old_at = "2026-10-20T08:00:00"
        self.new_at = "2026-10-26T09:00:00"

    def write(self, name, value):
        (self.directory / name).write_text(json.dumps(value), encoding="utf-8")

    def prepare(self, kind="greenhouse", old=True):
        self.co = {"key": "example", "slug": "example", "ats": kind}
        body_key = {"greenhouse": "content", "ashby": "descriptionHtml", "lever": "description"}[kind]
        title_key = "text" if kind == "lever" else "title"
        body = {"id": "r1", title_key: "Research Scientist", body_key: "Original job description"}
        self.write("_list.json", {"_listed_at": self.new_at, "items": [self.item]})
        self.write("_board.json", {"_fetched_at": self.new_at, "jobs": [body]})
        if old:
            self.write("r1.json", {"_adapter": kind, "_fetched_at": self.old_at,
                                   "_url": self.item["url"], "_list_item": self.item, "body": body})
        return body, body_key

    def run_refresh(self, **kwargs):
        with patch("adapters.fetch", side_effect=AssertionError("No network expected")), \
                patch("common.fetch", side_effect=AssertionError("No network expected")):
            fetch_ats.cmd_jd({}, [self.co], **kwargs)

    def test_changed_body_refreshes_all_three_ats_without_fetching(self):
        for kind in ("greenhouse", "ashby", "lever"):
            with self.subTest(kind=kind):
                body, body_key = self.prepare(kind)
                body[body_key] = "Updated official requirements"
                self.write("_board.json", {"_fetched_at": self.new_at, "jobs": [body]})
                self.run_refresh()
                result = common.load_raw("example", "r1")
                self.assertEqual(result["body"], body)
                self.assertEqual(result["_fetched_at"], self.new_at)

    def test_unchanged_body_keeps_original_time_even_with_force(self):
        self.prepare()
        before = (self.directory / "r1.json").read_bytes()
        self.run_refresh(force=True)
        self.assertEqual((self.directory / "r1.json").read_bytes(), before)

    def test_new_body_uses_actual_board_fetch_time(self):
        self.prepare(old=False)
        self.run_refresh()
        self.assertEqual(common.load_raw("example", "r1")["_fetched_at"], self.new_at)

    def test_invalid_missing_or_older_board_preserves_old_raw(self):
        body, body_key = self.prepare()
        before = (self.directory / "r1.json").read_bytes()
        body[body_key] = "Changed content"
        bad_boards = [None, {"jobs": [body]}, {"_fetched_at": "bad-date", "jobs": [body]},
                      {"_fetched_at": self.new_at, "jobs": []},
                      {"_fetched_at": "2026-10-01T08:00:00", "jobs": [body]},
                      {"_fetched_at": self.new_at, "jobs": [{"id": "r1", "title": "Incomplete"}]}]
        for board in bad_boards:
            with self.subTest(board=board):
                self.write("_board.json", board)
                self.run_refresh(force=True)
                self.assertEqual((self.directory / "r1.json").read_bytes(), before)
        (self.directory / "_board.json").write_text("{broken", encoding="utf-8")
        self.run_refresh()
        self.assertEqual((self.directory / "r1.json").read_bytes(), before)
        (self.directory / "_board.json").unlink()
        self.run_refresh()
        self.assertEqual((self.directory / "r1.json").read_bytes(), before)

    def test_replace_failure_does_not_destroy_existing_file(self):
        body, body_key = self.prepare()
        before = (self.directory / "r1.json").read_bytes()
        body[body_key] = "New content"
        self.write("_board.json", {"_fetched_at": self.new_at, "jobs": [body]})
        with patch("fetch_ats.os.replace", side_effect=OSError("write failed")):
            self.run_refresh()
        self.assertEqual((self.directory / "r1.json").read_bytes(), before)
        self.assertEqual(list(self.directory.glob(".board-refresh-*")), [])

    def test_max_details_caps_content_writes(self):
        body, body_key = self.prepare(old=False)
        second = copy.deepcopy(body)
        second["id"] = "r2"
        self.write("_list.json", {"_listed_at": self.new_at,
                                  "items": [self.item, dict(self.item, id="r2", req_id="r2")]})
        self.write("_board.json", {"_fetched_at": self.new_at, "jobs": [body, second]})
        self.run_refresh(max_details=1)
        self.assertTrue((self.directory / "r1.json").exists())
        self.assertFalse((self.directory / "r2.json").exists())

    def enrich_previous(self):
        body, key = self.prepare()
        old = common.load_raw("example", "r1")
        old["body"].update(first_published="2026-08-02T08:00:00Z",
                            pay_input_ranges=[{"min_cents": 10000000, "max_cents": 12000000}],
                            questions=[{"label": "Resume"}])
        self.write("r1.json", old)
        return body, old

    def test_greenhouse_enriched_detail_is_refetched_without_mixing_old_fields(self):
        board, old = self.enrich_previous()
        old_bytes = (self.directory / "r1.json").read_bytes()
        board["content"] = "Updated official requirements"
        self.write("_board.json", {"_fetched_at": self.new_at, "jobs": [board]})
        detail = dict(old["body"], content=board["content"],
                      pay_input_ranges=[{"min_cents": 11000000, "max_cents": 13000000}])
        response = common.Resp(200, "", detail, "", "")
        with patch("common.fetch", return_value=response) as request, \
                patch("fetch_ats.now_iso", return_value="2026-10-27T09:00:00"), \
                patch.object(GreenhouseAdapter, "fetch_detail", side_effect=AssertionError("Board shortcut forbidden")):
            fetch_ats.cmd_jd({}, [self.co])
        self.assertEqual(request.call_count, 1)
        self.assertTrue(request.call_args.args[0].endswith("/jobs/r1?pay_transparency=true&questions=true"))
        result = common.load_raw("example", "r1")
        self.assertEqual(result["body"], detail)
        self.assertEqual(result["_fetched_at"], "2026-10-27T09:00:00")
        history = list((self.directory / "_history").glob("r1_*.json"))
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].read_bytes(), old_bytes)
        self.assertIn(hashlib.sha256(old_bytes).hexdigest(), history[0].name)

    def test_unchanged_enriched_detail_needs_no_network_or_timestamp_change(self):
        self.enrich_previous()
        before = (self.directory / "r1.json").read_bytes()
        self.run_refresh(force=True)
        self.assertEqual((self.directory / "r1.json").read_bytes(), before)

    def test_greenhouse_failed_or_incomplete_detail_preserves_original(self):
        board, old = self.enrich_previous()
        before = (self.directory / "r1.json").read_bytes()
        board["content"] = "Changed requirements"
        self.write("_board.json", {"_fetched_at": self.new_at, "jobs": [board]})
        for response in (common.Resp(429, "", None, "", "blocked"),
                         common.Resp(200, "", board, "", ""),
                         common.Resp(200, "", dict(old["body"], id="wrong"), "", "")):
            with self.subTest(response=response), patch("common.fetch", return_value=response):
                fetch_ats.cmd_jd({}, [self.co])
            self.assertEqual((self.directory / "r1.json").read_bytes(), before)
            self.assertFalse((self.directory / "_history").exists())

    def test_force_repairs_corrupt_raw_and_keeps_exact_original_bytes(self):
        body, _ = self.prepare()
        before = b"{broken original evidence"
        (self.directory / "r1.json").write_bytes(before)
        self.run_refresh()
        self.assertEqual((self.directory / "r1.json").read_bytes(), before)
        self.run_refresh(force=True)
        result = common.load_raw("example", "r1")
        self.assertEqual(result["body"], body)
        self.assertEqual(result["_fetched_at"], self.new_at)
        history = list((self.directory / "_history").glob("r1_*.json"))
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].read_bytes(), before)

    def test_force_repairs_missing_or_invalid_timestamp(self):
        for value in (None, "", "invalid"):
            with self.subTest(value=value):
                self.prepare()
                old = common.load_raw("example", "r1")
                old["_fetched_at"] = value
                self.write("r1.json", old)
                before = (self.directory / "r1.json").read_bytes()
                self.run_refresh()
                self.assertEqual((self.directory / "r1.json").read_bytes(), before)
                self.run_refresh(force=True)
                self.assertEqual(common.load_raw("example", "r1")["_fetched_at"], self.new_at)

    def test_force_cannot_replace_newer_valid_raw(self):
        board, key = self.prepare()
        old = common.load_raw("example", "r1")
        old["_fetched_at"] = "2026-10-27T09:00:00"
        self.write("r1.json", old)
        before = (self.directory / "r1.json").read_bytes()
        board[key] = "Older board content"
        self.write("_board.json", {"_fetched_at": self.new_at, "jobs": [board]})
        self.run_refresh(force=True)
        self.assertEqual((self.directory / "r1.json").read_bytes(), before)

    def test_existing_matching_history_is_reused(self):
        board, key = self.prepare()
        before = (self.directory / "r1.json").read_bytes()
        history = self.directory / "_history"
        history.mkdir()
        backup = history / ("r1_" + hashlib.sha256(before).hexdigest() + ".json")
        backup.write_bytes(before)
        board[key] = "New requirements"
        self.write("_board.json", {"_fetched_at": self.new_at, "jobs": [board]})
        self.run_refresh()
        self.assertEqual(list(history.iterdir()), [backup])
        self.assertEqual(common.load_raw("example", "r1")["body"], board)


if __name__ == "__main__":
    unittest.main()
