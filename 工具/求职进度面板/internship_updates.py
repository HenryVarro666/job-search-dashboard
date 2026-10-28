#!/usr/bin/env python3
"""Merge a locally saved, officially verified internship batch. Never searches online.

Batch: {run_id, kind: daily_search|maintenance, observed_at: aware ISO timestamp,
source: local evidence path, items: [complete new card or company/req update]}.
Each update supplies availability_audit.attempts with kind job_page|ats_api,
status open|closed|unverified, checked_at_utc, official url, observed and
evidence_type. Closure requires ats_closed / explicit_closed_page with nonempty
official observed text, or an ATS canApply=false / posted=false / active=false result. Explicit
open fields conflicting with closure labels remain unverified. Access failures
never close roles.

Run: python3 internship_updates.py --batch batch.json [--data internships.json]
     [--out internships.json]. Search evidence is produced by the scheduled agent.
"""
import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
TIMEZONE = ZoneInfo('America/Chicago')
REQUIRED = ('company','req','title','url','start','duration','enrollment','return_to_school','opt_status','opt_note','group')
MANUAL = ('id','company','req','state','group','priority','decision_reason','decision_date','graduation_filter','graduation_filter_reason','graduation_filter_date')
CONTENT = ('title','url','location','start','duration','hours','pay','enrollment','return_to_school','graduation_note','opt_status','opt_note','opt_acceptance_evidence','fit','caveats','next_step','sources')
FAILURE = re.compile(r'\b(?:403|429|401|CAPTCHA)\b|验证码|访问失败|无法访问|登录后|sign.in required|access denied|timed? ?out|connection error|network error|SSL error',re.I)


def instant(value):
    parsed=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Run and evidence timestamps must include a timezone.')
    return parsed


def identity(row):
    company=re.sub(r'\s+',' ',str(row.get('company') or '').strip()).casefold()
    req=str(row.get('req') or '').strip().casefold()
    if not company or not req:
        raise ValueError('Each internship needs company and req.')
    return company+'::'+req


def valid_opt_evidence(proof, status, observed_at):
    """Minimum source/time/term checks; the scheduled review judges the policy."""
    if not isinstance(proof,dict) or not isinstance(proof.get('observed'),str) or not isinstance(proof.get('url'),str):
        return False
    try:
        parsed=urlsplit(proof.get('url') or '')
        host=(parsed.hostname or '').encode('idna').decode('ascii')
        labels=host.rstrip('.').split('.')
        if parsed.scheme not in ('http','https') or len(host)>253 or any(not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?',label) for label in labels):
            return False
        parsed.port  # Reject malformed or out-of-range ports.
        if instant(proof.get('checked_at_utc'))>instant(observed_at):
            return False
    except (ValueError,TypeError,UnicodeError):
        return False
    words=re.sub(r'[\u2010-\u2015]','-',proof['observed'])
    pattern=r'\bpre[-\s]*(?:completion[-\s]*)?(?:opt|optional\s+practical\s+training)\b' if status=='preopt_accepted' else r'\bopt\b|optional\s+practical\s+training'
    return bool(re.search(pattern,words,re.I))


def evidence(row, observed_at):
    attempts=(row.get('availability_audit') or {}).get('attempts') or []
    valid=[]
    for raw in attempts:
        item=copy.deepcopy(raw)
        at=item.get('checked_at_utc') or item.get('checked_date')
        if not at or not re.match(r'^https?://',item.get('url') or '',re.I):
            continue
        when=instant(at)
        if when>instant(observed_at):
            raise ValueError('Evidence cannot postdate the batch observation.')
        item['checked_at_utc']=at
        status=item.get('status')
        flag_names=('canApply','can_apply','posted','active')
        flags=tuple(item.get(name) for name in flag_names)
        negative=any(value is False for value in flags)
        close_label=item.get('evidence_type') in ('ats_closed','explicit_closed_page')
        body=item.get('observed')
        explicit=negative or (close_label and isinstance(body,str) and bool(body.strip()))
        if negative and not (isinstance(body,str) and body.strip()):
            item['observed']='官方 ATS 结构字段：'+', '.join(name+'=false' for name in flag_names if item.get(name) is False)
        conflict=not negative and any(value is True for value in flags) and (status=='closed' or close_label)
        code=int(item.get('http_status') or 200)
        failed=(conflict or ('http_status' in item and item['http_status'] is None)
                or (code>=400 and not (code in (404,410) and explicit)) or FAILURE.search(item.get('observed') or '')
                or item.get('kind') not in ('job_page','ats_api')
                or item.get('evidence_type') in ('blocked_official_page','official_search_index'))
        if explicit:
            status=item['status']='closed'
        if failed or status not in ('open','closed') or (status=='closed' and not explicit):
            item['status']='unverified'
        valid.append(item)
    return max(valid,key=lambda x:instant(x['checked_at_utc'])) if valid else None


def last_time(row):
    value=row.get('last_verification_attempt') or row.get('last_verified')
    if value:
        if len(value)==10:
            return dt.datetime.combine(dt.date.fromisoformat(value),dt.time(),TIMEZONE)
        return instant(value)
    if row.get('checked_on'):
        return dt.datetime.combine(dt.date.fromisoformat(row['checked_on']),dt.time(),TIMEZONE)
    return dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def merge(data, batch):
    """Return a new document; validate the entire batch before any file write."""
    at=batch.get('observed_at');instant(at)
    if batch.get('kind') not in ('daily_search','maintenance') or not batch.get('run_id') or not batch.get('source'):
        raise ValueError('Batch requires run_id, kind, observed_at and source.')
    if not isinstance(batch.get('items'),list):
        raise ValueError('Batch items must be a list.')
    out=copy.deepcopy(data)
    if any(run.get('id')==batch['run_id'] for run in out.get('runs',[])):
        return out
    rows={};ids=set()
    for row in out.setdefault('items',[]):
        key=identity(row)
        if key in rows:
            raise ValueError('Duplicate company/req in current internship data.')
        if not row.get('id') or row['id'] in ids:
            raise ValueError('Missing or duplicate internship display id.')
        ids.add(row['id'])
        row.setdefault('first_seen',None);row.setdefault('discovery_baseline',True)
        if row.get('sources') and row.get('checked_on') and row.get('listing_status') in ('open','closed'):
            row.setdefault('last_verified',row['checked_on'])
        rows[key]=row
    counts={'added':0,'closed':0,'reopened':0,'unverified':0,'stale':0}
    for incoming in batch['items']:
        key=identity(incoming);proof=evidence(incoming,at);row=rows.get(key)
        if row is None:
            missing=[k for k in REQUIRED if not incoming.get(k)]
            if missing:
                raise ValueError('New internship is missing: '+', '.join(missing))
            if not proof or proof['status']!='open':
                counts['unverified']+=1
                continue
            row={k:copy.deepcopy(v) for k,v in incoming.items() if k in CONTENT+MANUAL}
            row.setdefault('id','intern-'+hashlib.sha256(key.encode()).hexdigest()[:16])
            if row['id'] in ids:
                raise ValueError('Duplicate internship display id.')
            ids.add(row['id'])
            row.update(first_seen=at,discovery_baseline=False,discovery_batch=batch['run_id'])
            row.setdefault('state','conditional');rows[key]=row;out['items'].append(row);counts['added']+=1
        elif proof and instant(proof['checked_at_utc'])<=last_time(row):
            counts['stale']+=1
            continue
        if not proof:
            counts['unverified']+=1
            continue
        old=copy.deepcopy(row)
        for field in CONTENT:
            if field in incoming:
                row[field]=copy.deepcopy(incoming[field])
        if old.get('state')=='paused':
            row['next_step']=old.get('next_step') or row.get('next_step')
        accepted=row.get('opt_status') in ('accepted','preopt_accepted','opt_accepted')
        opt_proof=row.get('opt_acceptance_evidence') or {}
        if accepted and not valid_opt_evidence(opt_proof,row['opt_status'],at):
            row['opt_status']=old.get('opt_status') if old.get('opt_status') not in ('accepted','preopt_accepted','opt_accepted') else 'unconfirmed'
            row['opt_note']=old.get('opt_note') if old.get('opt_status') not in ('accepted','preopt_accepted','opt_accepted') else '未保存雇主明确接受 OPT 的证据，仍待确认。'
            row['opt_status']=row.get('opt_status') or 'unconfirmed'
        history=row.setdefault('verification_history',[])
        if proof not in history:
            history.append(proof)
        row['verification_history']=history[-30:]
        row.update(last_verification_attempt=proof['checked_at_utc'],verification_note=proof.get('observed') or '',verification_status=proof['status'])
        if proof['status']=='unverified':
            counts['unverified']+=1
            if not row.get('archived'):
                row['listing_status']='unverified'
        else:
            row.update(last_verified=proof['checked_at_utc'],checked_on=instant(proof['checked_at_utc']).astimezone(TIMEZONE).date().isoformat(),listing_status=proof['status'])
            closed=proof['status']=='closed'
            counts['closed']+=int(closed and not old.get('archived'))
            counts['reopened']+=int(not closed and bool(old.get('archived')))
            row['archived']=closed
            if closed:
                row.update(archive_date=row['checked_on'],archive_reason=proof.get('observed') or '官方证据明确关闭',archive_source=proof['url'])
            else:
                for field in ('archive_date','archive_reason','archive_source'):
                    row.pop(field,None)
    refresh=out.setdefault('refresh',{})
    prior=refresh.get('last_sync_at')
    if not prior or instant(at)>=instant(prior):
        refresh.update(last_sync_at=at,last_run_id=batch['run_id'],status='partial' if counts['unverified'] or batch.get('complete') is False else 'synced',
                       note='已合并官方核验批次；访问失败不作关闭。' if batch['kind']=='daily_search' else '本地维护同步；不代表重新联网搜索或核验。',counts=counts)
    refresh.setdefault('last_search_at',None)
    if batch['kind']=='daily_search' and (not refresh['last_search_at'] or instant(at)>instant(refresh['last_search_at'])):
        refresh['last_search_at']=at
    out['runs']=(out.get('runs',[])+[dict(id=batch['run_id'],kind=batch['kind'],observed_at=at,source=batch['source'],counts=counts)])[-60:]
    out['updated']=instant(refresh['last_sync_at']).astimezone(TIMEZONE).date().isoformat()
    return out


def decorate(data, today=None):
    """Derive the seven-day badge without changing refresh or verification clocks."""
    out=copy.deepcopy(data);today=today or dt.datetime.now(TIMEZONE).date()
    if isinstance(today,str):
        today=dt.date.fromisoformat(today)
    for row in out.get('items',[]):
        if row.get('archived_at') and not row.get('archive_date'):
            row['archive_date']=str(row['archived_at'])[:10]
        first=row.get('first_seen')
        age=(today-instant(first).astimezone(TIMEZONE).date()).days if first else None
        row['new']=bool(first and not row.get('discovery_baseline') and 0<=age<7 and not row.get('archived') and row.get('listing_status')!='closed'
                        and row.get('state')!='declined' and row.get('graduation_filter') not in ('exclude_fall','hold_return'))
    return out


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch',required=True);parser.add_argument('--data',default=str(HERE/'internships.json'));parser.add_argument('--out')
    args=parser.parse_args()
    data=json.loads(Path(args.data).read_text(encoding='utf-8'))
    result=merge(data,json.loads(Path(args.batch).read_text(encoding='utf-8')))
    target=Path(args.out or args.data)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=target.parent,prefix=target.name+'.',delete=False) as handle:
        temporary=Path(handle.name);json.dump(result,handle,ensure_ascii=False,indent=2);handle.write('\n')
    try:
        os.replace(temporary,target)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps(result.get('refresh',{}),ensure_ascii=False))


if __name__=='__main__':
    main()
