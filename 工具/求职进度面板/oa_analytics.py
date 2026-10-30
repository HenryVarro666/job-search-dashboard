"""Evidence-based OA history, current work, and final outcomes for the local panel.

These derived fields never change the application ledger. Unknown completion and
missing deadlines stay visible; application confirmation is deliberately narrower
than the number of saved records.
"""
import copy
from collections import Counter
from datetime import date, datetime
import re
from zoneinfo import ZoneInfo

ENDED = {"rejected", "closed", "withdrawn"}
TERMINAL = ENDED | {"offer"}
BUCKET_LABELS = {
    "none": "无OA记录", "active": "待完成", "unknown": "完成待确认",
    "submitted": "已提交待结果", "reused": "复用获批待下一步",
    "advanced": "已进入后续阶段", "expired": "已明确过期", "ended": "已结束 · 保留OA历史",
}


def _ordered(events):
    """Keep unknown-time positions within each day, matching the panel's rule."""
    days = {}
    for event in events:
        days.setdefault(event["date"], []).append(event)
    result = []
    for day in sorted(days):
        rows = days[day]
        timed = iter(sorted((e for e in rows if e.get("time")), key=lambda e: e["time"]))
        result.extend(next(timed) if e.get("time") else e for e in rows)
    return result


def _assessment_events(events):
    # Source strings may be thread IDs rather than message IDs. Do not collapse
    # different summaries/messages just because they share a Gmail thread.
    seen, result = set(), []
    for event in events:
        if event["type"] != "assessment":
            continue
        key = tuple(event.get(k, "") for k in ("date", "time", "source", "summary"))
        if key not in seen:
            seen.add(key)
            result.append(event)
    return result


def _dict(value):
    return value if isinstance(value, dict) else {}


def _metadata_bucket(app):
    assessment = _dict(app.get("assessment"))
    notice = _dict(app.get("assessment_notice"))
    status = str(app.get("assessment_status") or assessment.get("status") or notice.get("status") or "").lower()
    completion = str(app.get("assessment_completion_status") or notice.get("completion_status") or "").lower()
    if status == "stage_notice_waiting_link":
        return "unknown"
    if any(word in status + " " + completion for word in ("unknown", "awaiting_user_confirmation", "pending_user_confirmation")):
        return "unknown"
    if status in ("expired", "timed_out", "not_completed_expired"):
        return "expired"
    if app.get("assessment_submission_verified") is True or notice.get("completion_verified") is True:
        return "submitted"
    if status in ("completed", "submitted", "submitted_waiting_review", "completed_waiting_result", "passed"):
        return "submitted"
    if status in ("not_started", "pending", "in_progress", "active", "link_received_not_started"):
        return "active"
    return None


def _invitation(text):
    return bool(re.search(r"(?:收到|发来|发出|发送|第二轮|新一轮).{0,35}(?:邀请|oa)|(?:测评|oa|测试).{0,8}邀请|(?:new|another|second).{0,20}(?:assessment|invitation)|assessment\s+invitation", text, re.I))


def _fresh_round(text):
    return bool(re.search(r"第二轮|新一轮|(?:new|another|second).{0,20}(?:assessment|invitation)", text, re.I))


def _definitely_after(later, earlier):
    if later["date"] != earlier["date"]:
        return later["date"] > earlier["date"]
    return bool(later.get("time") and earlier.get("time") and later["time"] > earlier["time"])


def _stamp_event(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo:
        stamp = stamp.astimezone(ZoneInfo("America/Chicago"))
    return {"date": stamp.date().isoformat(), "time": stamp.strftime("%H:%M") if "T" in value else ""}


def _new_round_after(assessments, evidence_at):
    """Old completion/reuse cannot discharge an explicitly newer invitation."""
    marker = _stamp_event(evidence_at)
    for invitation in assessments:
        text = invitation.get("summary", "")
        if not _invitation(text):
            continue
        if marker and _definitely_after(marker, invitation):
            continue
        if marker and _definitely_after(invitation, marker):
            return True
        older_completion = any(_definitely_after(invitation, old) and _text_bucket([old]) == "submitted"
                               for old in assessments)
        if _fresh_round(text) or older_completion:
            return True
    return False


def _text_bucket(events):
    """Use explicit completion; an actual later invitation starts a fresh round."""
    bucket = "unknown"
    for event in events:
        text = event.get("summary", "")
        # Parentheses often preserve an older reminder alongside a newer
        # explicit completion confirmation.
        primary = re.sub(r"（[^）]*）|\([^)]*\)", "", text)
        lower = primary.lower()
        negative = bool(re.search(r"未完成|尚未完成|没有完成证据|无完成证据|尚无.{0,12}(?:完成|提交).{0,8}证据|(?:完成|提交).{0,8}(?:待确认|未知)|\b(?:not|never|no|without|hasn't|haven't|didn't|isn't|wasn't)\b.{0,45}\b(?:completed|submitted)\b|\bwhether\b.{0,40}\b(?:completed|submitted)\b|completion.{0,25}(?:unknown|not.{0,8}confirmed)", lower))
        complete = not negative and bool(re.search(r"已完成|完成提交.{0,5}(?:确认|回执)|已交卷|已提交|\bcompleted\b|\bsubmitted\b", lower))
        invitation = _invitation(lower)
        fresh_round = _fresh_round(lower)
        if invitation and (not complete or fresh_round):
            bucket = "unknown"
        elif complete:
            bucket = "submitted"
    return bucket


def _deadline(app):
    assessment, notice = _dict(app.get("assessment")), _dict(app.get("assessment_notice"))
    values = [app.get("assessment_deadline"), assessment.get("deadline"), notice.get("deadline")]
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            try:
                date.fromisoformat(value)
            except ValueError:
                continue
            return value, "明确测评截止日"
        if "无正式截止" in value or re.search(r"no\s+(?:formal\s+)?deadline", value, re.I):
            return None, "无正式截止；按招聘方说明处理"
    return None, "截止未记录"


def verified_application_existence(app):
    """Explicit employer evidence confirms existence, never the submission date."""
    return (app.get("application_existence_verified") is True
            and app.get("existence_evidence_kind") in
            ("withdrawal_confirmation", "application_limit_notice", "employer_rejection_email"))


def derive_analytics(app, events, stage):
    """Return four orthogonal fields; stage is the panel's current resolved stage."""
    ordered = _ordered(events)
    terminal = [e for e in ordered if e["type"] in TERMINAL]
    effective = terminal[-1]["type"] if terminal else stage
    assessments = _assessment_events(ordered)
    assessment = _dict(app.get("assessment"))
    reuse = app.get("assessment_reuse_approved") is True
    metadata = _metadata_bucket(app)
    has_history = bool(assessments or reuse or metadata or _dict(app.get("assessment_notice")).get("actual_test_link_received_verified") is True)
    dates = [e["date"] for e in assessments]
    reuse_date = app.get("assessment_reuse_approved_at_cdt")
    if reuse and isinstance(reuse_date, str):
        dates.append(reuse_date[:10])
    completion_at = (app.get("assessment_confirmation_received_at_cdt") or assessment.get("confirmation_received_at_cdt")
                     or app.get("actual_assessment_submitted_at") or assessment.get("actual_completed_at")
                     or assessment.get("confirmation_received_at_utc"))
    if metadata == "submitted" and _new_round_after(assessments, completion_at):
        metadata = None
    if reuse and _new_round_after(assessments, reuse_date):
        reuse = False
    invitations = [e for e in assessments if _invitation(e.get("summary", ""))]
    latest_work = (invitations or assessments)[-1] if assessments else None
    later_interview = latest_work is None or any(e["type"] == "interview" and _definitely_after(e, latest_work) for e in ordered)
    if not has_history:
        bucket = "none"
    elif effective in ENDED:
        bucket = "ended"
    elif effective == "offer" or (effective == "interview" and later_interview and metadata not in ("active", "unknown")):
        bucket = "advanced"
    elif reuse:
        bucket = "reused"
    else:
        bucket = metadata or _text_bucket(assessments)
    deadline, deadline_note = _deadline(app)
    notice = _dict(app.get("assessment_notice"))
    status = str(app.get("assessment_status") or assessment.get("status") or notice.get("status") or "").lower()
    waiting_for_link = (effective not in TERMINAL and bucket == "unknown"
                        and status == "stage_notice_waiting_link"
                        and notice.get("actual_test_link_received_verified") is False)
    confirmed = (any(e["type"] in ("applied", "ack") for e in ordered)
                 or app.get("stage") == "applied" or verified_application_existence(app))
    types = {e["type"] for e in ordered}
    highest = ("offer" if "offer" in types else "interview" if "interview" in types else
               "assessment" if has_history else "reply" if "human_reply" in types else
               "application" if confirmed else "unconfirmed")
    if effective in TERMINAL:
        outcome = effective
    elif effective == "reactivation_pending":
        outcome = "reactivation_pending"
    elif bucket in ("active", "unknown"):
        outcome = "oa_active"
    elif bucket == "submitted":
        outcome = "oa_submitted"
    elif bucket == "reused":
        outcome = "oa_reused"
    elif bucket == "expired":
        outcome = "oa_expired"
    elif effective == "interview":
        outcome = "interview"
    elif effective == "screening":
        outcome = "contact"
    else:
        outcome = "waiting" if confirmed else "unconfirmed"
    return {
        "application_confirmed": confirmed,
        "history_highest": highest,
        "flow_outcome": outcome,
        "oa": {"has_history": has_history, "bucket": bucket, "label": BUCKET_LABELS[bucket],
               "waiting_for_link": waiting_for_link,
               "deadline": deadline, "deadline_note": deadline_note,
               "first_date": min(dates) if dates else None, "last_date": max(dates) if dates else None,
               "outcome": effective if effective in TERMINAL else None},
    }


def unmapped_updates(data):
    """Keep each unresolved company-level invitation once, never per candidate job."""
    result, seen = [], set()
    for update in data.get("company_updates", []):
        if update.get("type") != "assessment_invitation_unmapped":
            continue
        if update.get("application_id") or update.get("mapped_application_id") or update.get("resolved"):
            continue
        if update.get("status") in ("mapped", "closed", "resolved", "withdrawn"):
            continue
        source = update.get("message_id") or update.get("source_url") or update.get("id")
        key = (update.get("company", "").casefold(), source)
        if key in seen:
            continue
        seen.add(key)
        result.append(copy.deepcopy(update))
    return result


def analytics_summary(apps, unresolved):
    """Count distinct application IDs; preserve terminal OA in its own bucket."""
    rows = list({a["id"]: a for a in apps}.values())
    counts = Counter(a["oa"]["bucket"] for a in rows if a["oa"]["has_history"])
    confirmed = sum(a["application_confirmed"] for a in rows)
    return {"records": len(rows), "confirmed_applications": confirmed,
            "unconfirmed_records": len(rows) - confirmed,
            "oa_total": sum(counts.values()),
            "oa_buckets": {key: counts[key] for key in BUCKET_LABELS if key != "none"},
            "unmapped_assessments": len(unmapped_updates({"company_updates": unresolved}))}
