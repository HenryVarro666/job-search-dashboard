"""Explicit, isolated local profiles and setup failures; no live requests."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import judge_tools
from spring_internships import load_policy


class ProfileSetupTests(unittest.TestCase):
    def setUp(self):
        self.strength = patch.dict(judge_tools.STRENGTH, clear=False)
        self.evidence = patch.dict(judge_tools.EVID_TAGS, clear=False)
        self.strength.start()
        self.evidence.start()
        self.addCleanup(self.strength.stop)
        self.addCleanup(self.evidence.stop)

    def fact_sheet(self, directory, strength="NONE", omit=None):
        rows = [f"| {tag} | {strength} | fictional fixture |" for tag in judge_tools.DOMAIN_TAGS if tag != omit]
        path = Path(directory) / "候选人事实表.md"
        path.write_text("## 二 · Domain strength\n" + "\n".join(rows), encoding="utf-8")
        return path

    def test_explicit_profile_controls_overlap_and_missing_profile_clears_prior_candidate(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            self.fact_sheet(first, "STRONG")  # Entirely fictional evidence for this fixture.
            judge_tools.load_strength(first)
            row = {"domain_overlap": {"tags": [{"tag": "neuroimaging", "jd_weight": "CORE_TO_JD"}]}}
            judge_tools.normalize_counts(row)
            self.assertEqual(row["domain_overlap"]["tags"][0]["overlap"], "CORE_MATCH")
            judge_tools.EVID_TAGS["E-PUB-FICTIONAL-1ST"] = {"neuroimaging"}
            with self.assertRaises(SystemExit):
                judge_tools.load_strength(second)
            self.assertEqual(set(judge_tools.STRENGTH.values()), {"NONE"})
            self.assertEqual(judge_tools.EVID_TAGS, {})
            self.fact_sheet(second)
            judge_tools.load_strength(second)
            judge_tools.normalize_counts(row)
            self.assertEqual(row["domain_overlap"]["tags"][0]["overlap"], "GAP")

    def test_incomplete_profile_cannot_silently_keep_previous_strengths(self):
        with tempfile.TemporaryDirectory() as temp:
            self.fact_sheet(temp, "STRONG", omit="EHR")
            with self.assertRaises(SystemExit):
                judge_tools.load_strength(temp)
            self.assertEqual(set(judge_tools.STRENGTH.values()), {"NONE"})

    def test_missing_explicit_policy_stops_with_setup_message(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(SystemExit, "policy required"):
                load_policy(Path(temp) / "missing.json")

    def test_tracking_requires_review_inputs_before_fetching(self):
        script = Path(__file__).with_name("track.py")
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([sys.executable, "-B", str(script), "run", "--batch-dir", temp],
                                    cwd=temp, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Local review inputs required", result.stderr)
        self.assertNotIn("$", result.stdout)

    def test_tracking_requires_explicit_batch_before_fetching(self):
        script = Path(__file__).with_name("track.py")
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([sys.executable, "-B", str(script), "run"], cwd=temp,
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--batch-dir is required", result.stderr)
        self.assertNotIn("$", result.stdout)


if __name__ == "__main__":
    unittest.main()
