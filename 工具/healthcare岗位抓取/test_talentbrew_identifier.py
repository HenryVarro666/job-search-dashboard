#!/usr/bin/env python3
"""Offline normalization regressions for TalentBrew JSON-LD requisition IDs."""
import json
import unittest

from adapters_ext_b import TalentBrewAdapter
from adapters_ext_c import RadancyAdapter


class TalentBrewIdentifierTests(unittest.TestCase):
    def setUp(self):
        self.adapter = TalentBrewAdapter({
            "key": "example-health", "name": "Example Health",
            "talentbrew": {"host": "careers.example.test", "company_id": "10000"},
        }, {})
        self.item = {
            "id": "90000000001", "req_id": "R123", "title": "AI/ML Engineer",
            "url": "https://careers.example.test/job/example-city/ml-engineer/10000/90000000001",
        }

    def test_property_value_identifier_becomes_plain_requisition_id(self):
        raw = {"ld": {"identifier": {"@type": "PropertyValue", "name": "Job ID", "value": "R123"}},
               "info": {"Requisition number": "wrong-fallback"}}
        rec = self.adapter.normalize(self.item, raw)
        self.assertEqual(rec["req_id"], "R123")
        self.assertEqual(rec["job_id"], "90000000001")

    def test_legacy_string_identifier_remains_authoritative(self):
        rec = self.adapter.normalize(self.item, {
            "ld": {"identifier": "R123"}, "info": {"Requisition number": "wrong-fallback"}})
        self.assertEqual(rec["req_id"], "R123")

    def test_missing_identifier_uses_page_requisition_before_list_id(self):
        rec = self.adapter.normalize(self.item, {"ld": {}, "info": {"Requisition number": "R456"}})
        self.assertEqual(rec["req_id"], "R456")

    def test_missing_identifier_and_requisition_use_job_number(self):
        rec = self.adapter.normalize(self.item, {"info": {"Job Number": "R789"}})
        self.assertEqual(rec["req_id"], "R789")

    def test_missing_identifier_and_page_ids_use_list_requisition(self):
        self.assertEqual(self.adapter.normalize(self.item, {})["req_id"], "R123")

    def test_unusable_property_value_falls_back_instead_of_stringifying_object(self):
        for identifier in ({"@type": "PropertyValue"}, {"value": None}, {"value": ""}):
            with self.subTest(identifier=identifier):
                rec = self.adapter.normalize(self.item, {
                    "ld": {"identifier": identifier}, "info": {"Requisition number": "R456"}})
                self.assertEqual(rec["req_id"], "R456")


class RadancyIdentifierTests(unittest.TestCase):
    def setUp(self):
        self.adapter = RadancyAdapter({
            "key": "example-retail", "name": "Example Retail",
            "radancy": {"host": "jobs.example.test"},
        }, {})
        self.item = {"id": "90000000002", "req_id": "90000000002", "title": "Software Engineer",
                     "url": "https://jobs.example.test/en/job/example-city/software-engineer/2000/90000000002"}

    def normalize(self, **fields):
        ld = {"@type": "JobPosting", "description": "<p>Job description.</p>", **fields}
        raw = '<script type="application/ld+json">' + json.dumps(ld) + '</script>'
        return self.adapter.normalize(self.item, raw)

    def test_property_value_identifier_preserves_req_separately_from_posting_id(self):
        rec = self.normalize(identifier={"@type": "PropertyValue", "name": "Job ID", "value": "R111BR"})
        self.assertEqual(rec["req_id"], "R111BR")
        self.assertEqual(rec["job_id"], "90000000002")

    def test_legacy_string_identifier_is_preserved(self):
        self.assertEqual(self.normalize(identifier="R111BR")["req_id"], "R111BR")

    def test_missing_identifier_uses_list_id(self):
        self.assertEqual(self.normalize()["req_id"], "90000000002")

    def test_property_value_without_value_uses_list_id(self):
        self.assertEqual(self.normalize(identifier={"@type": "PropertyValue"})["req_id"], "90000000002")


if __name__ == "__main__":
    unittest.main()
