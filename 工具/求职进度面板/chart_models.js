/* Pure, date-only chart models. No ledger mutation or browser-storage access. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.JobDashboardCharts = factory();
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  var DAY = 86400000;

  function day(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    var stamp = Date.parse(value + "T00:00:00Z");
    if (!Number.isFinite(stamp) || new Date(stamp).toISOString().slice(0, 10) !== value) return null;
    return stamp / DAY;
  }
  function iso(value) { return new Date(value * DAY).toISOString().slice(0, 10); }
  function monday(value) { return value - (((value + 3) % 7 + 7) % 7); }
  function todayDay(options) {
    var value = day(options.today);
    if (value === null) throw new TypeError("today must be a valid YYYY-MM-DD date");
    return value;
  }
  function unique(apps) {
    var seen = new Set();
    return (Array.isArray(apps) ? apps : []).filter(function (app) {
      if (!app || typeof app !== "object") return false;
      if (app.id == null || app.id === "") return true;
      var key = String(app.id);
      if (seen.has(key)) return false;
      seen.add(key); return true;
    });
  }
  function firstDate(values, today) {
    var valid = values.map(day).filter(function (value) { return value !== null && value <= today; });
    return valid.length ? Math.min.apply(null, valid) : null;
  }
  function eventDates(app, kind) {
    return (Array.isArray(app.events) ? app.events : []).filter(function (event) {
      return event && event.type === kind;
    }).map(function (event) { return event.date; });
  }

  /* Each series counts a distinct action per application ID. An acknowledgement
     is shown only when the actual submission date is still unknown. Later
     rejection/archiving never removes earlier actions from these series. */
  function weeklyActivity(apps, options) {
    options = options || {};
    var today = todayDay(options), count = options.weeks === undefined ? 12 : options.weeks;
    if (count !== null && (!Number.isInteger(count) || count < 1)) {
      throw new TypeError("weeks must be a positive integer or null");
    }
    var unknownSubmissionCount = 0, first = monday(today);
    var actions = unique(apps).map(function (app) {
      var applied = firstDate(eventDates(app, "applied").concat([app.applied_date]), today);
      var referred = firstDate(eventDates(app, "referral_request"), today);
      var receipt = applied === null ? firstDate(eventDates(app, "ack").concat([app.receipt_date]), today) : null;
      if (app.application_confirmed && applied === null) unknownSubmissionCount++;
      [applied, referred, receipt].forEach(function (value) {
        if (value !== null) first = Math.min(first, monday(value));
      });
      return {app: app, applied: applied, referred: referred, receipt: receipt};
    });
    var end = monday(today), start = count === null ? first : end - (count - 1) * 7;
    var weeks = [], byStart = Object.create(null);
    for (var cursor = start; cursor <= end; cursor += 7) {
      var row = {start: iso(cursor), end: iso(cursor + 6), applications: [], referrals: [], receipts: [], partial: cursor === end};
      weeks.push(row); byStart[cursor] = row;
    }
    actions.forEach(function (action) {
      [["applied", "applications"], ["referred", "referrals"], ["receipt", "receipts"]].forEach(function (pair) {
        var value = action[pair[0]];
        if (value !== null && byStart[monday(value)]) byStart[monday(value)][pair[1]].push(action.app);
      });
    });
    return {weeks: weeks, unknownSubmissionCount: unknownSubmissionCount};
  }

  function actionableDate(app) {
    return app.next && (app.open || app.next_kind === "restart") ? day(app.next.date) : null;
  }
  function lastActivity(app) {
    var value = day(app.last && app.last.date);
    return value === null ? -Infinity : value;
  }
  function timelineRows(apps, options) {
    options = options || {};
    todayDay(options); // Require an explicit as-of date; never use the host timezone.
    var focus = options.mode !== "all";
    var limit = options.limit === undefined ? (focus ? 20 : null) : options.limit;
    if (limit !== null && (!Number.isInteger(limit) || limit < 0)) {
      throw new TypeError("limit must be a non-negative integer or null");
    }
    var rows = unique(apps).filter(function (app) {
      return !focus || (app.open && app.group === "progress") || actionableDate(app) !== null;
    }).sort(function (a, b) {
      var ad = actionableDate(a), bd = actionableDate(b);
      if (ad !== bd) {
        if (ad === null) return 1;
        if (bd === null) return -1;
        return ad - bd;
      }
      var al = lastActivity(a), bl = lastActivity(b);
      if (al !== bl) return bl - al;
      return String(a.company || "").localeCompare(String(b.company || "")) || String(a.id || "").localeCompare(String(b.id || ""));
    });
    var total = rows.length;
    if (limit !== null) rows = rows.slice(0, limit);
    return {rows: rows, total: total, omitted: total - rows.length};
  }

  return {weeklyActivity: weeklyActivity, timelineRows: timelineRows};
});
