"""Discovery dates remain calendar dates; timestamps use Chicago local dates."""
import datetime as dt
import unittest

import opportunity_updates as updates


class DiscoveryDateTests(unittest.TestCase):
    def discovered(self, first_seen):
        row = {
            'uid': 'discovery-date-fixture',
            'company': 'Example',
            'req': 'R1',
            'title': 'Research Scientist',
            'post': 'https://example.com/R1',
            'status': 'pending',
        }
        state = updates.initialize([], '2026-10-20T12:00:00+00:00')
        state = updates.sync(
            [row], state, 'date-boundary', 'daily_search', 'fixture', first_seen
        )
        return row, state

    # Break caught: treating a known calendar discovery date as midnight UTC.
    def test_date_only_discovery_is_new_through_november_first(self):
        row, state = self.discovered('2026-10-26')
        for today, expected in (
            (dt.date(2026, 10, 25), False),
            (dt.date(2026, 10, 26), True),
            (dt.date(2026, 11, 1), True),
            (dt.date(2026, 11, 2), False),
        ):
            with self.subTest(today=today):
                decorated, summary = updates.decorate([row], state, today)
                self.assertEqual(decorated[0]['new'], expected)
                self.assertEqual(summary['new_count'], int(expected))
                self.assertEqual(decorated[0]['first_seen'], '2026-10-26')
                self.assertEqual(state['records']['example::r1']['first_seen'], '2026-10-26')

    # Break caught: using the UTC date of a timestamp across Chicago midnight.
    def test_full_timestamp_still_uses_chicago_calendar_boundary(self):
        for first_seen, today, expected in (
            ('2026-10-26T04:59:59Z', dt.date(2026, 10, 31), True),
            ('2026-10-26T04:59:59Z', dt.date(2026, 11, 1), False),
            ('2026-10-26T05:00:00Z', dt.date(2026, 11, 1), True),
            ('2026-10-26T05:00:00Z', dt.date(2026, 11, 2), False),
        ):
            with self.subTest(first_seen=first_seen, today=today):
                row, state = self.discovered(first_seen)
                decorated, _ = updates.decorate([row], state, today)
                self.assertEqual(decorated[0]['new'], expected)
                self.assertEqual(decorated[0]['first_seen'], first_seen)

    # Break caught: changing the shared timestamp parser to reinterpret verification dates.
    def test_verification_keeps_existing_utc_order_for_date_only_evidence(self):
        result = updates.verification({
            'availability_audit': {
                'status': 'closed',
                'attempts': [
                    {'kind': 'job_page', 'status': 'open',
                     'checked_at_utc': '2026-10-25T23:59:59Z',
                     'url': 'https://example.com/R1'},
                    {'kind': 'job_page', 'status': 'closed',
                     'checked_date': '2026-10-26',
                     'url': 'https://example.com/R1'},
                    {'kind': 'job_page', 'status': 'open',
                     'checked_at_utc': '2026-10-26T00:00:01Z',
                     'url': 'https://example.com/R1'},
                ],
            },
        })
        self.assertEqual(result['last_verified'], '2026-10-26T00:00:01Z')


if __name__ == '__main__':
    unittest.main()
