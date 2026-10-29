"""Offline refresh contracts: evidence, chronology and student constraints."""
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

if importlib.util.find_spec('internship_updates'):
    from internship_updates import merge, decorate
else:
    merge = decorate = None


def role(req='R1', **kw):
    value=dict(id='intern-'+req, company='Example', req=req, title='PhD ML Intern',
                url='https://example.org/jobs/'+req, start='January 2027', duration='12 weeks',
                enrollment='PhD enrolled', return_to_school='Not disclosed',
                opt_status='unconfirmed', opt_note='Not disclosed', group='primary',
                state='conditional', listing_status='open', checked_on='2026-10-26')
    value.update(kw)
    return value


def audit(status='open', at='2026-10-27T06:00:00+00:00', **kw):
    return {'status': status, 'attempts': [dict(kind='ats_api', status=status,
        checked_at_utc=at, url='https://example.org/api/R1', observed='Current official result',
        evidence_type='ats_closed' if status=='closed' else 'current_official_ats_check', **kw)]}


def batch(items, run='run1', at='2026-10-27T07:00:00+00:00', kind='daily_search'):
    return dict(run_id=run, observed_at=at, kind=kind, source='local/evidence.json', items=items)


class InternshipUpdatesTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(merge, 'internship merge implementation is missing')

    def test_new_official_role_gets_first_seen_without_marking_legacy_new(self):
        original={'items':[role()]}
        incoming=role('R2', availability_audit=audit())
        result=merge(original,batch([incoming]))
        self.assertEqual(len(result['items']),2)
        self.assertIsNone(result['items'][0]['first_seen'])
        self.assertEqual(result['items'][1]['first_seen'],'2026-10-27T07:00:00+00:00')
        decorated=decorate(result,dt.date(2026,10,27))
        self.assertFalse(decorated['items'][0]['new'])
        self.assertTrue(decorated['items'][1]['new'])
        self.assertEqual(original,{'items':[role()]})

    def test_repeated_run_and_company_whitespace_do_not_duplicate(self):
        first=merge({'items':[]},batch([role(availability_audit=audit())]))
        again=merge(first,batch([role(company=' EXAMPLE ', availability_audit=audit())]))
        self.assertEqual(first,again)
        later=merge(first,batch([role(company=' EXAMPLE ', availability_audit=audit(at='2026-10-28T06:00:00+00:00'))],run='run2',at='2026-10-28T07:00:00+00:00'))
        self.assertEqual(len(later['items']),1)
        self.assertEqual(later['items'][0]['first_seen'],first['items'][0]['first_seen'])

    def test_partial_search_does_not_close_missing_role(self):
        result=merge({'items':[role()]},batch([]))
        self.assertEqual(result['items'][0]['listing_status'],'open')
        self.assertFalse(result['items'][0].get('archived',False))

    def test_access_failure_cannot_be_mislabeled_as_closed(self):
        original=merge({'items':[]},batch([role(availability_audit=audit())]))
        failure=audit('closed',at='2026-10-28T06:00:00+00:00',http_status=403)
        result=merge(original,batch([dict(company='Example',req='R1',availability_audit=failure)],run='run2',at='2026-10-28T07:00:00+00:00'))
        row=result['items'][0]
        self.assertEqual(row['listing_status'],'unverified')
        self.assertFalse(row.get('archived',False))
        self.assertEqual(row['last_verified'],'2026-10-27T06:00:00+00:00')
        self.assertEqual(result['refresh']['status'],'partial')

    def test_official_closed_404_is_distinct_from_access_failure(self):
        official=audit('closed',http_status=404)
        official['attempts'][0]['observed']='Official job API: {"error": "Job not found"}'
        row=dict(company='Example',req='R1',availability_audit=official)
        result=merge({'items':[role()]},batch([row]))
        self.assertTrue(result['items'][0]['archived'])
        unknown=audit('closed',http_status=None)
        unknown['attempts'][0]['evidence_type']='blocked_official_page'
        result=merge({'items':[role()]},batch([dict(company='Example',req='R1',availability_audit=unknown)]))
        self.assertEqual(result['items'][0]['listing_status'],'unverified')
        timeout=audit('closed',http_status=None)
        timeout['attempts'][0]['observed']='Request timed out before receiving the official page'
        result=merge({'items':[role()]},batch([dict(company='Example',req='R1',availability_audit=timeout)]))
        self.assertFalse(result['items'][0].get('archived',False))

    def test_explicit_ats_cannot_apply_overrides_accidental_open_label(self):
        incoming=dict(company='Example',req='R1',availability_audit=audit('open',canApply=False,posted=True))
        incoming['availability_audit']['attempts'][0].pop('observed')
        self.assertTrue(merge({'items':[role()]},batch([incoming]))['items'][0]['archived'])

    def test_closed_label_without_body_or_structured_false_is_unverified(self):
        for body in (None,'   '):
            with self.subTest(body=body):
                proof=audit('closed')
                if body is None:
                    proof['attempts'][0].pop('observed')
                else:
                    proof['attempts'][0]['observed']=body
                result=merge({'items':[role()]},batch([dict(company='Example',req='R1',availability_audit=proof)]))
                self.assertEqual(result['items'][0]['listing_status'],'unverified')
                self.assertFalse(result['items'][0].get('archived',False))

    def test_explicit_open_fields_conflicting_with_closed_label_are_unverified(self):
        for fields in ({'canApply':True,'posted':True},{'active':True}):
            with self.subTest(fields=fields):
                proof=audit('closed',**fields)
                proof['attempts'][0]['observed']='The listing was marked closed by the batch reviewer.'
                result=merge({'items':[role()]},batch([dict(company='Example',req='R1',availability_audit=proof)]))
                self.assertEqual(result['items'][0]['listing_status'],'unverified')
                self.assertFalse(result['items'][0].get('archived',False))

    def test_smartrecruiters_inactive_result_records_the_structured_reason(self):
        proof=audit('open',active=False)
        proof['attempts'][0].pop('observed')
        result=merge({'items':[role()]},batch([dict(company='Example',req='R1',availability_audit=proof)]))
        self.assertTrue(result['items'][0]['archived'])
        self.assertIn('active=false',result['items'][0]['archive_reason'])

    def test_explicit_close_stays_archived_after_failure_and_new_open_restores(self):
        old={'items':[role()]}
        closed=merge(old,batch([dict(company='Example',req='R1',availability_audit=audit('closed'))]))
        self.assertTrue(closed['items'][0]['archived'])
        self.assertEqual(closed['items'][0]['archive_source'],'https://example.org/api/R1')
        failed=merge(closed,batch([dict(company='Example',req='R1',availability_audit=audit('unverified',at='2026-10-28T06:00:00+00:00'))],run='run2',at='2026-10-28T07:00:00+00:00'))
        self.assertTrue(failed['items'][0]['archived'])
        self.assertEqual(failed['items'][0]['listing_status'],'closed')
        restored=merge(failed,batch([dict(company='Example',req='R1',availability_audit=audit('open',at='2026-10-29T06:00:00+00:00'))],run='run3',at='2026-10-29T07:00:00+00:00'))
        self.assertFalse(restored['items'][0]['archived'])
        self.assertEqual(restored['items'][0]['listing_status'],'open')

    def test_old_evidence_cannot_overwrite_later_closure_or_advance_refresh_clock(self):
        closed=merge({'items':[role()]},batch([dict(company='Example',req='R1',availability_audit=audit('closed',at='2026-10-29T06:00:00+00:00'))],run='new',at='2026-10-29T07:00:00+00:00'))
        result=merge(closed,batch([dict(company='Example',req='R1',availability_audit=audit())],run='old'))
        self.assertTrue(result['items'][0]['archived'])
        self.assertEqual(result['refresh']['last_search_at'],'2026-10-29T07:00:00+00:00')

    def test_manual_pause_and_opt_unknown_are_not_overridden(self):
        base=role();base.update(state='paused',group='backup',priority=4,next_step='Quota hold')
        incoming=role(availability_audit=audit());incoming.update(state='conditional',group='primary',priority=1,opt_status='accepted',opt_note='Accepted',next_step='Apply now')
        result=merge({'items':[base]},batch([incoming]))['items'][0]
        self.assertEqual((result['state'],result['group'],result['priority'],result['next_step']),('paused','backup',4,'Quota hold'))
        self.assertEqual(result['opt_status'],'unconfirmed')

    def test_graduation_filter_survives_official_open_close_and_reopen(self):
        for value in ('exclude_fall','hold_return'):
            with self.subTest(value=value):
                base=role(graduation_filter=value,graduation_filter_reason='User graduation preference',graduation_filter_date='2026-10-26')
                update=role(availability_audit=audit(),graduation_filter='consider',graduation_filter_reason='Automatic reset',graduation_filter_date='2026-10-27')
                current=merge({'items':[base]},batch([update]))
                current=merge(current,batch([dict(company='Example',req='R1',availability_audit=audit('closed',at='2026-10-28T06:00:00+00:00'))],run='close',at='2026-10-28T07:00:00+00:00'))
                self.assertTrue(current['items'][0]['archived'])
                current=merge(current,batch([dict(company='Example',req='R1',availability_audit=audit('open',at='2026-10-29T06:00:00+00:00'))],run='reopen',at='2026-10-29T07:00:00+00:00'))
                row=current['items'][0]
                self.assertFalse(row['archived'])
                self.assertEqual((row['graduation_filter'],row['graduation_filter_reason'],row['graduation_filter_date']),
                                 (value,'User graduation preference','2026-10-26'))

    def test_new_card_retains_graduation_filter_and_has_no_new_badge(self):
        incoming=role(availability_audit=audit(),graduation_filter='hold_return',graduation_filter_reason='Return required, plan unclear',graduation_filter_date='2026-10-27')
        result=merge({'items':[]},batch([incoming]))
        row=decorate(result,dt.date(2026,10,27))['items'][0]
        self.assertEqual(row['graduation_filter'],'hold_return')
        self.assertEqual(row['listing_status'],'open')
        self.assertFalse(row['new'])

    def test_declined_decision_survives_official_open_close_and_reopen(self):
        base=role(state='declined',decision_reason='User will not apply: sponsorship risk',decision_date='2026-10-27')
        update=role(availability_audit=audit(),state='conditional',decision_reason='Automatic reset',decision_date='2026-10-28')
        current=merge({'items':[base]},batch([update]))
        self.assertEqual(current['items'][0]['listing_status'],'open')
        current=merge(current,batch([dict(company='Example',req='R1',availability_audit=audit('closed',at='2026-10-28T06:00:00+00:00'))],run='close',at='2026-10-28T07:00:00+00:00'))
        self.assertTrue(current['items'][0]['archived'])
        current=merge(current,batch([dict(company='Example',req='R1',state='conditional',availability_audit=audit('open',at='2026-10-29T06:00:00+00:00'))],run='reopen',at='2026-10-29T07:00:00+00:00'))
        row=current['items'][0]
        self.assertFalse(row['archived'])
        self.assertEqual(row['listing_status'],'open')
        self.assertEqual((row['state'],row['decision_reason'],row['decision_date']),
                         ('declined','User will not apply: sponsorship risk','2026-10-27'))

    def test_new_declined_card_keeps_decision_without_discovery_badge(self):
        incoming=role(availability_audit=audit(),state='declined',decision_reason='User declined',decision_date='2026-10-27')
        result=merge({'items':[]},batch([incoming]))
        row=decorate(result,dt.date(2026,10,27))['items'][0]
        self.assertEqual((row['state'],row.get('decision_reason'),row.get('decision_date')),
                         ('declined','User declined','2026-10-27'))
        self.assertEqual(row['listing_status'],'open')
        self.assertEqual(row['last_verified'],'2026-10-27T06:00:00+00:00')
        self.assertFalse(row['new'])
        self.assertNotIn('new',result['items'][0], 'render decoration must not mutate the saved record')

    def test_graduation_note_updates_without_inferring_permission_from_missing_return_clause(self):
        incoming=role(availability_audit=audit(),graduation_note='能否实习后直接毕业尚未确认。')
        row=merge({'items':[role()]},batch([incoming]))['items'][0]
        self.assertEqual(row['graduation_note'],'能否实习后直接毕业尚未确认。')

    def test_invalid_or_generic_f1_evidence_cannot_upgrade_preopt_acceptance(self):
        valid={'url':'https://example.org/internship-policy','checked_at_utc':'2026-10-27T06:00:00+00:00',
               'observed':'This internship accepts a valid pre-completion OPT EAD.'}
        cases=[dict(valid,url='not-a-source'),dict(valid,url=123),dict(valid,url='https://not a host/policy'),
               dict(valid,url='https://example..org/policy'),dict(valid,url='https://-bad.example/policy'),
               dict(valid,checked_at_utc='not-a-date'),dict(valid,checked_at_utc='2026-10-27T06:00:00'),
               dict(valid,checked_at_utc='2026-10-28T06:00:00+00:00'),dict(valid,observed='F-1 students are eligible.'),
               dict(valid,observed='The internship accepts OPT students.')]
        for proof in cases:
            with self.subTest(proof=proof):
                incoming=role(availability_audit=audit(),opt_acceptance_evidence=proof)
                incoming.update(opt_status='preopt_accepted',opt_note='Accepted under the policy.')
                result=merge({'items':[role()]},batch([incoming]))['items'][0]
                self.assertEqual(result['opt_status'],'unconfirmed')
                self.assertEqual(result['opt_note'],'Not disclosed')

    def test_opt_acceptance_requires_opt_words_and_valid_evidence(self):
        proof={'url':'https://example.org/internship-policy','checked_at_utc':'2026-10-27T06:00:00+00:00',
               'observed':'This internship accepts students with Optional Practical Training authorization.'}
        incoming=role(availability_audit=audit(),opt_acceptance_evidence=proof)
        incoming.update(opt_status='opt_accepted',opt_note='Employer policy expressly accepts OPT.')
        result=merge({'items':[role()]},batch([incoming]))['items'][0]
        self.assertEqual(result['opt_status'],'opt_accepted')
        proof['observed']='F-1 students are eligible.'
        result=merge({'items':[role()]},batch([incoming]))['items'][0]
        self.assertEqual(result['opt_status'],'unconfirmed')

    def test_explicit_preopt_acceptance_evidence_can_upgrade_status(self):
        proof={'url':'https://example.org/internship-policy','checked_at_utc':'2026-10-27T06:00:00+00:00',
               'observed':'We accept a valid pre-OPT EAD for this full-time internship.'}
        incoming=role(availability_audit=audit(),opt_acceptance_evidence=proof)
        incoming.update(opt_status='preopt_accepted',opt_note='Employer expressly accepts pre-OPT.')
        result=merge({'items':[role()]},batch([incoming]))['items'][0]
        self.assertEqual(result['opt_status'],'preopt_accepted')

    def test_unverified_new_role_or_missing_required_fields_do_not_enter_shortlist(self):
        with self.assertRaises(ValueError):
            merge({'items':[]},batch([dict(company='Example',req='R1',availability_audit=audit())]))
        result=merge({'items':[]},batch([role(availability_audit=audit('unverified'))]))
        self.assertEqual(result['items'],[])
        self.assertEqual(result['refresh']['status'],'partial')

    def test_duplicate_display_id_rejects_entire_batch(self):
        incoming=role('R2',availability_audit=audit());incoming['id']='intern-R1'
        with self.assertRaises(ValueError):
            merge({'items':[role()]},batch([incoming]))

    def test_new_badge_uses_seven_chicago_calendar_days(self):
        result=merge({'items':[]},batch([role(availability_audit=audit(at='2026-10-27T03:59:00+00:00'))],at='2026-10-27T04:00:00+00:00'))
        self.assertTrue(decorate(result,dt.date(2026,11,1))['items'][0]['new'])
        self.assertFalse(decorate(result,dt.date(2026,11,2))['items'][0]['new'])
        snapshot=copy.deepcopy(result);decorate(result,dt.date(2026,10,27));self.assertEqual(result,snapshot)

    def test_maintenance_never_claims_a_daily_search(self):
        result=merge({'items':[role()]},batch([],kind='maintenance'))
        self.assertIsNone(result['refresh']['last_search_at'])
        self.assertEqual(result['refresh']['last_sync_at'],'2026-10-27T07:00:00+00:00')

    def test_cli_atomically_writes_only_selected_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);data=root/'data.json';payload=root/'batch.json';out=root/'out.json'
            data.write_text(json.dumps({'items':[]}),encoding='utf-8')
            payload.write_text(json.dumps(batch([role(availability_audit=audit())])),encoding='utf-8')
            proc=subprocess.run([sys.executable,str(Path(__file__).with_name('internship_updates.py')),'--data',str(data),'--batch',str(payload),'--out',str(out)],capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
            self.assertEqual(len(json.loads(out.read_text())['items']),1)
            self.assertEqual(json.loads(data.read_text()),{'items':[]})


if __name__=='__main__':
    unittest.main()
