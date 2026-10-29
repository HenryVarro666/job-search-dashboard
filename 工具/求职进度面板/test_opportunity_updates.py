"""Behavioral fixtures for durable discovery, verification and archive safety."""
import copy
import datetime as dt
import unittest
import opportunity_updates as updates


def job(uid='a', req='R1', **kw):
    return dict(uid=uid, company='Example', req=req, title='ML Scientist', post='https://example.com/'+req,
                status='pending', tier='🟢 投', **kw)


def verified(state, at='2026-10-26T00:00:00+00:00', **kw):
    return {'status':state,'checked_at_utc':at,'canonical_url':'https://example.com/R1',
            'attempts':[dict(kind='job_page',status=state,checked_at_utc=at,url='https://example.com/R1',observed=kw.get('note','verified'))]}


class DiscoveryTests(unittest.TestCase):
    # Break caught: treating all historical delta flags as first discoveries.
    def test_baseline_is_not_new_and_unknown_first_seen_stays_unknown(self):
        old=job(new=True)
        state=updates.initialize([old], '2026-10-27T02:00:00+00:00')
        items, summary=updates.decorate([old],state,dt.date(2026,10,26))
        self.assertFalse(items[0]['new']); self.assertIsNone(items[0]['first_seen'])
        self.assertTrue(items[0]['discovery_baseline'])
        self.assertEqual(summary['sync_status'],'baseline_ready')
        self.assertIsNone(summary['last_refresh_at'])

    # Break caught: reruns resetting discovery time, source aliases duplicating news.
    def test_sync_new_exact_role_alias_and_seven_day_boundary(self):
        state=updates.initialize([job()], '2026-10-20T00:00:00+00:00')
        rows=[job(),job('b','R2')]
        state=updates.sync(rows,state,'batch1','daily_search','official sources','2026-10-21T05:00:00+00:00')
        state2=updates.sync([job(),job('manual:b','R2')],state,'batch2','daily_search','official sources','2026-10-22T05:00:00+00:00')
        rows2, summary=updates.decorate([job(),job('manual:b','R2')],state2,dt.date(2026,10,27))
        self.assertTrue(rows2[1]['new']); self.assertEqual(rows2[1]['first_seen'],'2026-10-21T05:00:00+00:00')
        self.assertEqual(rows2[1]['discovery_batch'],'batch1'); self.assertEqual(len(state2['records']),2)
        rows3,_=updates.decorate(rows,state2,dt.date(2026,10,28)); self.assertFalse(rows3[1]['new'])

    # Break caught: HTML/application status changes falsely claiming source refresh pending.
    def test_pending_sync_ignores_application_only_fields(self):
        state=updates.initialize([job()], '2026-10-20T00:00:00+00:00')
        _, summary=updates.decorate([job(status_label='投递',status_date='2026-10-26',status_note='ack')|{'status':'applied'}],state,dt.date(2026,10,26))
        self.assertEqual(summary['pending_sync_count'],0)
        _, summary=updates.decorate([job(),job('b','R2')],state,dt.date(2026,10,26))
        self.assertEqual(summary['sync_status'],'pending_sync'); self.assertEqual(summary['pending_sync_count'],1)

    # Break caught: failed access replacing last successful check or becoming closure.
    def test_failure_keeps_last_success_but_is_unverified(self):
        original=job(availability_audit=verified('open'))
        state=updates.initialize([original], '2026-10-26T00:00:00+00:00')
        failed=job(availability_audit={'status':'closed','checked_at_utc':'2026-10-27T00:00:00+00:00',
            'attempts':[{'kind':'page_unreadable','checked_at_utc':'2026-10-27T00:00:00+00:00','observed':'CAPTCHA','url':'https://example.com/R1'}]})
        state=updates.sync([failed],state,'b2','daily_search','official','2026-10-27T00:00:00+00:00')
        rows,s=updates.decorate([failed],state,dt.date(2026,10,27))
        self.assertEqual(rows[0]['status'],'pending'); self.assertEqual(rows[0]['verification_status'],'unverified')
        self.assertEqual(rows[0]['last_verified'],'2026-10-26T00:00:00+00:00'); self.assertEqual(s['sync_status'],'partial')

    # Break caught: closing listing rejecting applied user or losing preference exclusions.
    def test_explicit_closure_applied_precedence_and_restore(self):
        state=updates.initialize([job()], '2026-10-26T00:00:00+00:00')
        closed=job(availability_audit=verified('closed'))
        state=updates.sync([closed],state,'c','daily_search','official','2026-10-27T00:00:00+00:00')
        rows,_=updates.decorate([closed],state,dt.date(2026,10,27)); self.assertEqual(rows[0]['status'],'closed')
        rows,_=updates.decorate([closed|{'status':'rejected'}],state,dt.date(2026,10,27)); self.assertEqual(rows[0]['status'],'rejected')
        opened=job(availability_audit=verified('open','2026-10-28T00:00:00+00:00'))
        state=updates.sync([opened],state,'o','daily_search','official','2026-10-28T00:00:00+00:00')
        rows,_=updates.decorate([opened],state,dt.date(2026,10,28)); self.assertEqual(rows[0]['status'],'pending')
        rows,_=updates.decorate([opened|{'source_intent':'dropped','status':'excluded'}],state,dt.date(2026,10,28)); self.assertEqual(rows[0]['status'],'excluded')

    # Break caught: legacy CXS403 "likely closed" permanently hiding an unverified job.
    def test_access_failure_overrides_only_listing_closure(self):
        row=job(status_label='已下线',status_note='疑似关闭 CXS403')|{'status':'closed'}
        state=updates.initialize([row], '2026-10-26T00:00:00+00:00')
        rows,_=updates.decorate([row],state,dt.date(2026,10,26))
        self.assertEqual(rows[0]['status'],'pending')
        self.assertEqual(rows[0]['verification_status'],'unverified')
        app=row|{'status_label':'已关闭','status_note':'用户撤回'}
        rows,_=updates.decorate([app],state,dt.date(2026,10,26))
        self.assertEqual(rows[0]['status'],'closed')

    # Break caught: a vague word reviving a listing with explicit inactive ATS evidence.
    def test_legacy_inactive_false_stays_closed_and_failure_reason_is_traceable(self):
        inactive=job(status_label='已下线',status_note='在招核验疑似关闭：帖子存在但 active=false')|{'status':'closed'}
        state=updates.initialize([inactive],'2026-10-26T00:00:00+00:00')
        rows,_=updates.decorate([inactive],state,dt.date(2026,10,26))
        self.assertEqual(rows[0]['status'],'closed')
        failure=job(status_label='已下线',status_date='2026-09-29',status_note='CXS403 无法访问')|{'status':'closed'}
        state=updates.initialize([failure],'2026-10-26T00:00:00+00:00')
        rows,_=updates.decorate([failure],state,dt.date(2026,10,26))
        self.assertEqual(rows[0]['verification_note'],'CXS403 无法访问')
        self.assertEqual(rows[0]['verification_source'],'https://example.com/R1')
        self.assertEqual(rows[0]['last_verification_attempt'],'2026-09-29')
        self.assertIsNone(rows[0]['last_verified'])

    # Break caught: older open audit hiding a newer ambiguous listing failure.
    def test_newer_ambiguous_404_preserves_older_success_without_claiming_open(self):
        row=job(status_label='已下线',status_note='HTTP404可能撤下',status_date='2026-10-21',
                availability_audit=verified('open','2026-10-17T07:15:23+00:00'))|{'status':'closed'}
        state=updates.initialize([row],'2026-10-27T00:00:00+00:00')
        rows,_=updates.decorate([row],state,dt.date(2026,10,27))
        self.assertEqual(rows[0]['status'],'pending')
        self.assertEqual(rows[0]['verification_status'],'unverified')
        self.assertEqual(rows[0]['last_verified'],'2026-10-17T07:15:23+00:00')
        self.assertEqual(rows[0]['last_verification_attempt'],'2026-10-21')
        self.assertEqual(rows[0]['verification_note'],'HTTP404可能撤下')
        # A newer direct official success is allowed to supersede that failure.
        row['availability_audit']=verified('open','2026-10-22T00:00:00+00:00')
        state=updates.initialize([row],'2026-10-27T00:00:00+00:00')
        rows,_=updates.decorate([row],state,dt.date(2026,10,27))
        self.assertEqual(rows[0]['verification_status'],'open')
        self.assertEqual(rows[0]['last_verification_attempt'],'2026-10-22T00:00:00+00:00')

    # Break caught: maintenance being reported as a completed daily job search.
    def test_maintenance_does_not_claim_a_search_was_completed(self):
        state=updates.initialize([job()], '2026-10-26T00:00:00+00:00')
        state=updates.sync([job()],state,'m','maintenance','metadata maintenance','2026-10-27T00:00:00+00:00')
        self.assertIsNone(state['last_refresh_at'])
        self.assertEqual(state['last_sync_at'],'2026-10-27T00:00:00+00:00')

    # Break caught: application-side copy edits triggering false source changes.
    def test_pending_manual_archive_copy_does_not_change_fingerprint(self):
        row=job(source_intent='',source_archive_reason='old line')
        state=updates.initialize([row], '2026-10-26T00:00:00+00:00')
        rows,summary=updates.decorate([row|{'source_archive_reason':'receipt added'}],state,dt.date(2026,10,26))
        self.assertEqual(summary['pending_sync_count'],0)

    # Break caught: stale closed flag wins over newest explicit failed check.
    def test_latest_explicit_unverified_wins_over_historical_closed(self):
        audit=verified('closed')
        audit['attempts'].append(dict(kind='job_page',status='unverified',checked_at_utc='2026-10-27T00:00:00+00:00',url='https://example.com/R1',observed='error'))
        row=job(availability_audit=audit)
        state=updates.initialize([row], '2026-10-27T00:00:00+00:00')
        rows,_=updates.decorate([row],state,dt.date(2026,10,27))
        self.assertEqual(rows[0]['status'],'pending');self.assertEqual(rows[0]['verification_status'],'unverified')

    # Break caught: simultaneous manual/pool aliases double-counting new discoveries.
    def test_coexisting_source_aliases_render_one_discovery(self):
        state=updates.initialize([job()], '2026-10-20T00:00:00+00:00')
        rows=[job(),job('pool-b','R2'),job('manual:b','R2')]
        state=updates.sync(rows,state,'b','daily_search','official','2026-10-26T00:00:00+00:00')
        result,summary=updates.decorate(rows,state,dt.date(2026,10,26))
        self.assertEqual(len(result),2);self.assertEqual(summary['new_count'],1)

    # Break caught: invalid saved state silently resetting discovery history.
    def test_corrupt_registry_is_an_explicit_error(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'registry.json';path.write_text('{broken')
            with self.assertRaises(ValueError):updates.load_state(path)

    # Break caught: baseline with existing inaccessible pages claiming a search ran.
    def test_baseline_with_unverified_still_has_no_completed_search(self):
        row=job(availability_audit={'status':'unverified','attempts':[]})
        state=updates.initialize([row], '2026-10-27T00:00:00+00:00')
        _,summary=updates.decorate([row],state,dt.date(2026,10,27))
        self.assertEqual(summary['sync_status'],'baseline_ready');self.assertEqual(summary['unverified_count'],1)

    # Break caught: preference-only edits being stamped as official rechecks.
    def test_preference_timestamp_is_not_a_verification_attempt(self):
        audit=verified('open')
        audit['status']='excluded';audit['checked_at_utc']='2026-10-27T00:00:00+00:00'
        audit['attempts'].append(dict(kind='job_page',status='excluded',evidence_type='preference_exclusion',checked_at_utc='2026-10-27T00:00:00+00:00',observed='User preference'))
        state=updates.initialize([job(availability_audit=audit)],'2026-10-27T00:00:00+00:00')
        record=next(iter(state['records'].values()))
        self.assertEqual(record['last_verification_attempt'],'2026-10-26T00:00:00+00:00')
        self.assertEqual(record['verification_status'],'open')

    # Break caught: a missing scan row erasing historical registry evidence.
    def test_missing_scan_entry_is_retained_without_closure(self):
        state=updates.initialize([job()], '2026-10-26T00:00:00+00:00')
        state=updates.sync([],state,'empty','daily_search','official','2026-10-27T00:00:00+00:00')
        self.assertEqual(len(state['records']),1)
        self.assertEqual(next(iter(state['records'].values()))['verification_status'],'unknown')


class RendererIntegrationTests(unittest.TestCase):
    # Break caught: silently dropping an excluded manual target from recoverable history.
    def test_manual_dropped_retained_and_applied_history_wins(self):
        import json, tempfile, sys
        from pathlib import Path
        from unittest.mock import patch
        hc=Path(__file__).resolve().parent.parent/'healthcare岗位抓取'
        sys.path.insert(0,str(hc));import render_board
        rows=[dict(id='a',company='Example',req='R1',title='ML Scientist',status='dropped',
                   listing_status='closed',listing_checked_at='2026-10-26',
                   listing_evidence=[dict(status='closed',checked_at_utc='2026-10-26',url='https://example.com/R1',observed='Job closed')],
                   archive_reason='Job closed',archive_source='https://example.com/R1')]
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/'targets.json';target.write_text(json.dumps({'targets':rows}))
            with patch.object(render_board,'TARGETS',str(target)):
                result=render_board.manual_items([],dt.date(2026,10,27))
        self.assertEqual(len(result),1);self.assertEqual(result[0]['source_intent'],'dropped')
        self.assertEqual(result[0]['status'],'closed');self.assertEqual(result[0]['status_note'],'Job closed')

    # Break caught: submitting a pool job removing its official audit and creating false pending sync.
    def test_assembled_application_change_keeps_source_audit(self):
        import sys
        from pathlib import Path
        from unittest.mock import patch
        hc=Path(__file__).resolve().parent.parent/'healthcare岗位抓取'
        sys.path.insert(0,str(hc));import render_board
        audit={'scope':'pending_targets_only','records':[dict(source_records=[dict(source_type='scanning_pool',source_id='pool-a')],
            status='open',checked_at_utc='2026-10-26T00:00:00+00:00',note='open',
            evidence=[dict(status='open',checked_at_utc='2026-10-26T00:00:00+00:00',url='https://example.com/R1',observed='open')])]}
        def read(path,default=None):return audit if path==render_board.PENDING_AUDIT else {}
        pool={'items':[job('pool-a','R1',pri_base=1,pri_why_base=[])]}
        with patch.object(render_board,'read_json',side_effect=read):
            before=render_board.assemble(pool,applications=[],opportunity_state=False)
            state=updates.initialize(before['items'],'2026-10-26T00:00:00+00:00')
            after=render_board.assemble(pool,applications=[dict(id='app-a',company='Example',req='R1',title='ML Scientist',events=[dict(type='applied',date='2026-10-26')])],opportunity_state=state)
        self.assertEqual(after['items'][0]['status'],'applied')
        self.assertEqual(after['items'][0]['verification_status'],'open')
        self.assertEqual(after['opportunity_refresh']['pending_sync_count'],0)

    # Break caught: applying a legacy-failure job erasing its independent listing evidence.
    def test_assembled_application_change_keeps_legacy_listing_failure(self):
        import sys
        from pathlib import Path
        from unittest.mock import patch
        hc=Path(__file__).resolve().parent.parent/'healthcare岗位抓取'
        sys.path.insert(0,str(hc));import render_board
        def read(path,default=None):
            return {'pool-a':{'date':'2026-10-21','reason':'CXS403 无法访问'}} if str(path).endswith('closed.json') else {}
        pool={'items':[job('pool-a','R1',pri_base=1,pri_why_base=[])]}
        with patch.object(render_board,'read_json',side_effect=read):
            before=render_board.assemble(pool,applications=[],opportunity_state=False)
            state=updates.initialize(before['items'],'2026-10-26T00:00:00+00:00')
            after=render_board.assemble(pool,applications=[dict(id='app-a',company='Example',req='R1',title='ML Scientist',events=[dict(type='applied',date='2026-10-26')])],opportunity_state=state)
        self.assertEqual(after['items'][0]['status'],'applied')
        self.assertEqual(after['items'][0]['verification_status'],'unverified')
        self.assertEqual(after['opportunity_refresh']['pending_sync_count'],0)

    # Break caught: Today silently losing held or unreadable new discoveries.
    def test_today_discovers_pending_nonstretch_including_held(self):
        import make_dashboard
        rows=[job(new=True,first_seen='2026-10-26T06:00:00+00:00',verification_status='unverified',
                  priority_review={'actionable':False})]
        result=make_dashboard.today_panel({'items':rows},{'upcoming':[]},[],dt.date(2026,10,26))
        self.assertEqual(result['n_discovered'],1);self.assertTrue(result['discovered'][0]['paused'])
        self.assertEqual(result['discovered'][0]['first_seen'],'2026-10-26T06:00:00+00:00')


if __name__=='__main__':unittest.main()
