/* Pure behavioral checks using synthetic rows only. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = path.join(__dirname, 'chart_models.js');
assert.ok(fs.existsSync(source), 'chart models are required for event-based weekly counts');
const {weeklyActivity, timelineRows} = require(source);
const event = (type, date) => ({type, date});
const app = (id, events = [], extra = {}) => ({id, company: 'Synthetic Company', events, ...extra});
const options = {today: '2026-10-27', weeks: 3};
const ids = rows => rows.map(a => a.id);

const referred = app('referral-then-application', [event('referral_request', '2026-10-13'),
  event('referral_request', '2026-10-14'), event('referral_submitted', '2026-10-15'),
  event('applied', '2026-10-20'), event('ack', '2026-10-21')],
  {application_confirmed: true, applied_date: '2026-10-20', group: 'waiting'});
const acknowledged = app('ack-only', [event('ack', '2026-10-22')], {application_confirmed: true});
const undated = app('undated', [event('rejected', '2026-10-23')], {application_confirmed: true});
const result = weeklyActivity([referred, acknowledged, undated, referred], options);
assert.deepEqual(result.weeks.map(w => [w.start, w.end, w.partial]), [
  ['2026-10-12', '2026-10-18', false], ['2026-10-19', '2026-10-25', false],
  ['2026-10-26', '2026-11-01', true]]);
assert.deepEqual(result.weeks.map(w => ids(w.applications)), [[], ['referral-then-application'], []]);
assert.deepEqual(result.weeks.map(w => ids(w.referrals)), [['referral-then-application'], [], []]);
assert.deepEqual(result.weeks.map(w => ids(w.receipts)), [[], ['ack-only'], []]);
assert.equal(result.unknownSubmissionCount, 2, 'receipt and evidence-only records do not acquire submission dates');

const archived = {...referred, group: 'archived', open: false};
const rejected = {...referred, group: 'rejected', open: false};
for (const changed of [archived, rejected]) {
  const got = weeklyActivity([changed], options);
  assert.deepEqual(got.weeks.map(w => [w.applications.length, w.referrals.length]), [[0, 1], [1, 0], [0, 0]],
    'changing the current outcome cannot change historical action volume');
}

const eventOnly = app('event-only', [event('applied', '2026-10-18'), event('applied', '2026-10-19')]);
const fieldOnly = app('field-only', [], {applied_date: '2026-10-25', application_confirmed: true});
const future = app('future', [event('referral_request', '2026-11-02'), event('applied', '2026-11-03')]);
const bad = app('invalid', [event('applied', '2026-02-31'), event('referral_request', 'not-a-date')]);
const guarded = weeklyActivity([eventOnly, fieldOnly, future, bad], options);
assert.deepEqual(guarded.weeks.map(w => ids(w.applications)), [['event-only'], ['field-only'], []]);
assert.equal(guarded.weeks.flatMap(w => w.referrals).length, 0, 'future and invalid events are excluded');
assert.deepEqual(weeklyActivity([], options).weeks.map(w => w.applications.length), [0, 0, 0],
  'empty periods are visible rather than omitted');
assert.deepEqual(weeklyActivity([], {today: '2026-10-27', weeks: null}).weeks.map(w => w.start), ['2026-10-26']);
assert.deepEqual(weeklyActivity([app('old', [event('applied', '2026-10-04')])],
  {today: '2026-10-27', weeks: null}).weeks.map(w => w.start),
  ['2026-09-28', '2026-10-05', '2026-10-12', '2026-10-19', '2026-10-26']);
assert.equal(weeklyActivity([], {today: '2026-11-01', weeks: 1}).weeks[0].partial, true,
  'Sunday is still the current week; a date-only snapshot cannot certify that the day is complete');
assert.throws(() => weeklyActivity([], {today: '2026-02-31'}), /today/);

const input = [
  app('old-undated-oa', [], {open: true, group: 'progress', last: {date: '2026-07-01'}}),
  app('waiting-no-action', [], {open: true, group: 'waiting', last: {date: '2026-10-27'}}),
  app('stale-ended-task', [], {open: false, group: 'rejected', next: {date: '2026-10-20'}}),
  app('restart', [], {open: false, group: 'rejected', next_kind: 'restart', next: {date: '2026-10-29'}}),
  app('soon', [], {open: true, group: 'waiting', next: {date: '2026-10-28'}, last: {date: '2026-10-22'}}),
  app('overdue', [], {open: true, group: 'progress', next: {date: '2026-10-25'}}),
  app('invalid-next', [], {open: true, group: 'waiting', next: {date: '2026-02-31'}}),
];
const before = JSON.stringify(input);
const focus = timelineRows(input, {today: '2026-10-27', mode: 'focus'});
assert.deepEqual(ids(focus.rows), ['overdue', 'soon', 'restart', 'old-undated-oa']);
assert.equal(focus.total, 4); assert.equal(focus.omitted, 0);
const limited = timelineRows(input, {today: '2026-10-27', mode: 'focus', limit: 2});
assert.deepEqual(ids(limited.rows), ['overdue', 'soon']);
assert.equal(limited.total, 4); assert.equal(limited.omitted, 2);
assert.equal(timelineRows(input, {today: '2026-10-27', mode: 'all'}).rows.length, 7,
  'history keeps ended and waiting records');
assert.equal(timelineRows(input.concat(input[0]), {today: '2026-10-27', mode: 'all', limit: null}).rows.length, 7);
assert.equal(JSON.stringify(input), before, 'models do not mutate ledger rows');
assert.deepEqual(timelineRows([], {today: '2026-10-27', mode: 'focus'}), {rows: [], total: 0, omitted: 0});
console.log('chart model regressions passed');
