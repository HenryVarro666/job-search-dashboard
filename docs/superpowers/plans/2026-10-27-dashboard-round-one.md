# Dashboard Round One Implementation Plan

> **For agentic workers:** Use the approved conversation design and assigned ownership; apply test-first changes to behavioral logic and review the integrated result.

**Goal:** Preserve a source-only v1 baseline, then improve statistical accuracy and the local dashboard's everyday usability.

**Architecture:** Keep the Python-standard-library generator, private JSON inputs and self-contained HTML. Add a small reusable chart-model module; retain existing evidence and archive semantics. All assets remain local.

**Tech Stack:** Python, plain JavaScript, CSS, HTML, SVG; Node built-in test runner/assertions.

**Spec:** User-approved first-round recommendations from this conversation, summarized below.

## Global Constraints
- Private business data and generated personal HTML never enter Git.
- Existing application events, silence archiving, candidate eligibility, internship decisions and verification timestamps retain their meanings.
- Rebuilding HTML is not a live search. No automated outbound messages or application submissions.
- No external fonts, scripts, trackers or service dependencies.
- Browser policy blocked file navigation; do not bypass it through another browser surface or a server. Local links can be handed to the user.

## Review Focus
- Rejection without human contact must not increase the highest recorded stage.
- Historical activity counts must be stable when an application later becomes archived or rejected.
- Missing submission dates stay unknown; referral requests and formal submissions use their own dates.
- Details and search preserve the original list/filter context, escape text, and support keyboard dismissal/focus return.
- Dark theme, narrow screens, empty groups and long titles remain readable.

### Task 1: Statistics and models
**Files:** oa_analytics.py; new chart_models.js; synthetic regression tests.
**Interfaces:** window.JobDashboardCharts / CommonJS export: weeklyActivity(apps, options), timelineRows(apps, options). Pure functions, no writes.
- [x] Add failing regression for rejection-only vs real human reply. Fix highest-stage inference and run analytics tests.
- [x] Add weekly model tests covering referral then submission in separate weeks, ack-only unknown submission date, duplicate IDs, archived history and incomplete current week.
- [x] Implement independent weekly series with explicit unknown-date counts, and focused timeline selection.

### Task 2: Main dashboard interaction and presentation
**Files:** dashboard_template.html; synthetic JS interaction tests as needed.
- [x] Restructure today around urgent actions and user decisions, preserving recommendations and all blocking conditions.
- [x] Add progress subviews: current, records, statistics. Keep existing deep links and OA shortcut working.
- [x] Replace scroll-to-bottom jump with accessible side detail panel preserving context; add global lookup across applications, candidates and internships.
- [x] Default to directly labeled state bars, with expandable Sankey detail and links to matching records.
- [x] Render separate weekly application/referral series, focused timeline and explicit waiting days/observation date.
- [x] Unify typography, spacing, contrast and responsive behavior.

### Task 3: Opportunity surface consistency
**Files:** healthcare board_template.html only.
- [x] Harmonize visual style, card information hierarchy, compact filters and labels with the redesigned parent.
- [x] Preserve all eligibility, wage, verification, archive, application and company-filter behavior.

### Task 4: Integrate, verify, package
**Files:** make_dashboard.py generator inclusion; scripts/demo.py; README; synthetic tests.
- [x] Include chart-model source in the generated offline HTML.
- [x] Run existing local Python and JavaScript regression suites plus new tests and synthetic demo build.
- [x] Review full diff, fix concrete regressions, regenerate local private dashboard.
- [x] Verify private JSON hashes unchanged and audit Git tracked files before pushing the redesign.
- [x] Provide original local dashboard link and repository version links with accurate visual-verification limits.


## Verification record (2026-10-28)

- Local dashboard: 124 Python tests passed; all dashboard and candidate-board JavaScript regression files passed.
- Synthetic export: Python/JavaScript regressions, isolated demo generation, inline script syntax and network-disabled DOM interactions passed.
- Real local page: DOM initialization, tabs, progress subviews, search, drawer dismissal and focus restoration passed without rendering or network access.
- All 16 captured private JSON input hashes remain unchanged.
- Vault link check: 0 broken links, 0 ambiguous links; existing unrelated orphan reports remain.
- Two integration findings fixed: the current view now counts its own visible scope; application details retain the full job title.
- Browser layout and screenshot verification remain unperformed because local-file navigation was denied. The README illustration is synthetic artwork.
- Current export comments/defaults were generalized; historical private baseline cleanup and a license decision remain required before any future public release.
