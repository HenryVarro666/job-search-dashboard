#!/usr/bin/env python3
"""Durable local opportunity discovery/verification ledger. Never fetches jobs."""
import argparse
import copy
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / 'opportunity_refresh.json'
TIMEZONE = 'America/Chicago'
WINDOW = 7


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def stamp(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def identity(item):
    """Reqs are scoped to exact normalized company; titles never infer identity."""
    company = re.sub(r'\s+', ' ', str(item.get('company') or '').strip()).casefold()
    req = str(item.get('req') or '').strip().casefold()
    return company + '::' + req if company and req else 'uid::' + str(item.get('uid') or '')


def verification(item):
    audit = item.get('availability_audit') or {}
    attempts = audit.get('attempts') or audit.get('evidence') or []
    successful = []
    for raw in attempts:
        row = dict(raw)
        kind, etype = row.get('kind', 'job_page'), str(row.get('evidence_type') or '')
        if kind != 'job_page' or etype in ('official_search_index', 'blocked_official_page', 'preference_exclusion'):
            continue
        if row.get('status') in ('open', 'closed') and row.get('url') and (row.get('checked_at_utc') or row.get('checked_date')):
            successful.append(row)
    successful.sort(key=lambda r: stamp(r.get('checked_at_utc') or r.get('checked_date')) or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
    last = successful[-1] if successful else None
    actual_attempts=[row for row in attempts if row.get('evidence_type')!='preference_exclusion']
    latest = audit.get('latest_attempt')
    if not latest or latest.get('evidence_type')=='preference_exclusion':
        latest=actual_attempts[-1] if actual_attempts else None
    preference_only=audit.get('status')=='excluded'
    status = (last or {}).get('status','unknown') if preference_only else audit.get('status') or 'unknown'
    if status not in ('open', 'closed', 'unverified'):
        status = 'unknown'
    # An access failure is a check failure, even when legacy source called it closed.
    latest_kind = (latest or {}).get('kind', 'job_page')
    if latest and (latest_kind != 'job_page' or (latest or {}).get('evidence_type') in ('blocked_official_page','official_search_index') or (latest or {}).get('status') in ('unverified','error','failed')):
        status = 'unverified'
    if status in ('open', 'closed') and not last:
        status = 'unverified'
    legacy_closure=item.get('source_listing_closure') or {}
    legacy_note=str(legacy_closure.get('reason') or item.get('status_note') or '')
    legacy_date=legacy_closure.get('date') or item.get('status_date')
    legacy_failure_signal=bool(legacy_closure or item.get('status_label') == '已下线') and re.search(
        r'403|429|CAPTCHA|验证码|无法访问|访问失败|404.*(?:可能|疑似)|(?:可能|疑似).*404', legacy_note, re.I)
    legacy_at=stamp(legacy_date)
    audit_at=stamp(audit.get('checked_at_utc') or (latest or {}).get('checked_at_utc') or (latest or {}).get('checked_date'))
    legacy_failure=bool(legacy_failure_signal and (not audit or (legacy_at and (not audit_at or legacy_at>audit_at))))
    source=(latest or {}).get('url') or audit.get('canonical_url') or ''
    note=(latest or {}).get('observed') or audit.get('blocker_reason') or ''
    attempted=(((latest or {}).get('checked_at_utc') or (latest or {}).get('checked_date')) if preference_only
               else audit.get('checked_at_utc') or (latest or {}).get('checked_at_utc'))
    if legacy_failure:
        status='unverified';source=item.get('post') or item.get('apply') or source
        note=legacy_note;attempted=legacy_date or None
        attempts=list(attempts)+[{'kind':'legacy_listing_failure','status':'unverified','url':source,
                                 'observed':note,'checked_date':attempted}]
    return {'verification_status': status,
            'last_verified': (last or {}).get('checked_at_utc') or (last or {}).get('checked_date'),
            'verification_source': source,'verification_note':note,
            'last_verification_attempt':attempted,'verification_history': attempts}


def observation(item):
    # Application status, render time, delta/new flags and recommendations are derived.
    data = {k:item.get(k) for k in ('uid','company','req','title','post','apply','source_intent','source_archive_reason','source_archive_source','source_archived_at')}
    if item.get('source_intent') != 'dropped':
        for key in ('source_archive_reason','source_archive_source','source_archived_at'):
            data.pop(key,None)
    data.update(verification(item))
    return data


def fingerprint(obs):
    # uid alias changes do not create a second discovery or spurious source change.
    data = {k:v for k,v in obs.items() if k != 'uid'}
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def load_state(path=STATE_FILE):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(value,dict) or not value.get('initialized_at') or not isinstance(value.get('records'),dict):
            raise ValueError('Invalid opportunity registry; restore a backup before initializing or syncing.')
        return value
    except FileNotFoundError:
        return {}


def canonical_items(items):
    """One card per exact company/req, with application history taking precedence."""
    grouped={}
    for raw in items:
        key=identity(raw); prior=grouped.get(key); item=dict(raw)
        aliases=list((prior or {}).get('_source_aliases') or [])
        if item.get('uid') not in aliases:aliases.append(item.get('uid'))
        # Applied history beats any second source's pending or listing-closure state.
        if prior and prior.get('status') in ('applied','rejected') and item.get('status') not in ('applied','rejected'):
            item=dict(prior)
        item['_source_aliases']=aliases;grouped[key]=item
    return list(grouped.values())


def _record(item, observed_at, baseline, batch):
    obs=observation(item)
    return {'aliases':item.get('_source_aliases') or [item['uid']], 'first_seen':None if baseline else observed_at,
            'first_seen_basis':'historical_baseline_unknown' if baseline else 'first_observed_in_refresh',
            'discovery_batch':None if baseline else batch, 'discovery_baseline':baseline,
            'snapshot':copy.deepcopy(item), 'source_fingerprint':fingerprint(obs), **{k:v for k,v in obs.items() if k!='uid'}}


def initialize(items, observed_at=None, existing=None):
    if (existing or {}).get('initialized_at'):
        return copy.deepcopy(existing)
    at=observed_at or now_iso()
    state={'version':1,'timezone':TIMEZONE,'new_window_days':WINDOW,'initialized_at':at,'last_refresh_at':None,
           'last_sync_at':None,'records':{},'runs':[]}
    for item in canonical_items(items):
        key=identity(item)
        if key not in state['records']:
            state['records'][key]=_record(item,at,True,None)
        elif item['uid'] not in state['records'][key]['aliases']:
            state['records'][key]['aliases'].append(item['uid'])
    return state


def sync(items, state, run_id, kind, source, observed_at=None):
    if not state.get('initialized_at'):
        raise ValueError('Initialize the historical baseline before syncing a discovery batch.')
    if kind not in ('daily_search','maintenance') or not run_id or not source:
        raise ValueError('A run ID, daily_search/maintenance kind and source are required.')
    out=copy.deepcopy(state); at=observed_at or now_iso()
    for item in canonical_items(items):
        key=identity(item); obs=observation(item); rec=out['records'].get(key)
        if rec is None:
            out['records'][key]=_record(item,at,False,run_id)
            continue
        for alias in item.get('_source_aliases') or [item['uid']]:
            if alias not in rec['aliases']:rec['aliases'].append(alias)
        history=rec.get('verification_history',[])
        for attempt in obs['verification_history']:
            if attempt not in history:history.append(copy.deepcopy(attempt))
        last=rec.get('last_verified')
        rec.update({k:v for k,v in obs.items() if k not in ('uid','verification_history')})
        if not obs.get('last_verified'):rec['last_verified']=last
        elif last and (stamp(last) or dt.datetime.min.replace(tzinfo=dt.timezone.utc))>(stamp(obs['last_verified']) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)):rec['last_verified']=last
        rec['verification_history']=history
        rec['snapshot']=copy.deepcopy(item); rec['source_fingerprint']=fingerprint(obs)
    out['last_sync_at']=at
    if kind=='daily_search':out['last_refresh_at']=at
    run={'id':run_id,'kind':kind,'source':source,'observed_at':at}
    if not any(x['id']==run_id for x in out['runs']):out['runs'].append(run)
    return out


def decorate(items, state, today=None):
    today=today or dt.datetime.now(ZoneInfo(TIMEZONE)).date()
    records=state.get('records') or {}; decorated=[]; pending=0
    for raw in canonical_items(items):
        item=dict(raw); rec=records.get(identity(item)); obs=observation(item)
        if rec is None or rec.get('source_fingerprint')!=fingerprint(obs):pending+=1
        metadata=rec or _record(item,None,True,None)
        for key in ('first_seen','first_seen_basis','discovery_batch','discovery_baseline','last_verified','verification_status','verification_source','verification_note','last_verification_attempt'):
            item[key]=metadata.get(key)
        first_seen=metadata.get('first_seen')
        first=stamp(first_seen)
        if first and re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(first_seen)):
            first_date=dt.date.fromisoformat(first_seen)
        else:
            first_date=first.astimezone(ZoneInfo(TIMEZONE)).date() if first else None
        age=(today-first_date).days if first_date else None
        item['new']=bool(rec and not rec.get('discovery_baseline') and age is not None and 0<=age<WINDOW and item.get('status')=='pending')
        # Display the latest unsynced access result honestly; preserve saved success time.
        if rec and rec.get('source_fingerprint')!=fingerprint(obs):
            for key in ('verification_status','verification_source','verification_note','last_verification_attempt'):
                item[key]=obs[key]
            item['last_verified']=obs.get('last_verified') or rec.get('last_verified')
        if item.get('status') == 'closed' and item.get('status_label') == '已下线' and item.get('verification_status') in ('open','unverified'):
            item['legacy_listing_closure'] = {'date':item.get('status_date'),'note':item.get('status_note')}
            item.update(status='pending',status_label='待投 / 官网待核实' if item['verification_status']=='unverified' else '待投', status_date='',status_note=item.get('verification_note') or '旧下线判断为访问受限；当前开放状态待核实。')
        explicit_closed=item.get('verification_status')=='closed'
        if item.get('status')=='pending' and explicit_closed:
            item.update(status='closed',status_label='官网已关闭',status_date=str(item.get('last_verified') or '')[:10],
                        status_note=item.get('verification_note') or '官方核验明确关闭；保留记录，可恢复。',opportunity_archive=True)
            item['new']=False
        decorated.append(item)
    successful=[x.get('last_verified') for x in decorated if x.get('last_verified')]
    unverified=sum(x.get('verification_status') in ('unverified','unknown') and x.get('status')=='pending' for x in decorated)
    unknown=sum(x.get('verification_status')=='unknown' and x.get('status')=='pending' for x in decorated)
    status='pending_sync' if pending else ('baseline_ready' if not state.get('last_refresh_at') else ('partial' if unverified else 'synced'))
    return decorated, {'initialized_at':state.get('initialized_at'),'last_refresh_at':state.get('last_refresh_at'),
        'last_sync_at':state.get('last_sync_at'),
        'latest_verification_at':max(successful,key=lambda value:stamp(value) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)) if successful else None,'sync_status':status,'pending_sync_count':pending,
        'new_count':sum(x['new'] for x in decorated),'unverified_count':unverified,'unknown_count':unknown,
        'archive_count':sum(x.get('status') in ('closed','excluded') for x in decorated),
        'new_window_days':WINDOW,'timezone':TIMEZONE}


def source_items(pool_path=None):
    hc=HERE.parent/'healthcare岗位抓取'
    if str(hc) not in sys.path:sys.path.insert(0,str(hc))
    import render_board
    path=Path(pool_path) if pool_path else hc/'pool_board.json'
    pool=json.loads(path.read_text(encoding='utf-8'))
    return render_board.assemble(pool, opportunity_state=False)['items']


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--initialize',action='store_true'); action.add_argument('--sync',action='store_true')
    parser.add_argument('--state',default=str(STATE_FILE));parser.add_argument('--pool')
    parser.add_argument('--run-id');parser.add_argument('--kind',choices=('daily_search','maintenance'))
    parser.add_argument('--source');parser.add_argument('--observed-at')
    args=parser.parse_args(argv); path=Path(args.state)
    try:existing=load_state(path)
    except (ValueError,OSError) as exc:parser.error(str(exc))
    items=source_items(args.pool)
    if args.initialize:state=initialize(items,args.observed_at,existing)
    else:
        try:state=sync(items,existing,args.run_id,args.kind,args.source,args.observed_at)
        except ValueError as exc:parser.error(str(exc))
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temp.replace(path)
    _,summary=decorate(items,state)
    print(json.dumps(summary,ensure_ascii=False,indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
