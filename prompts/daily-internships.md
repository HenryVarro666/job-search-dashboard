# Daily internship prompt

Template for a separately configured daily executor. Supply `repository_root`, private `profile_path`, unique `run_id`, local `evidence_dir`, and the existing internship/application paths. Configure the desired season, duration, enrollment, and degree-completion constraints in the private profile. Read the [workflow](../docs/SEARCH_WORKFLOW.md). This file registers no schedule.

## Prompt

Update only the independent internship pool and its local dashboard view. Read `docs/SEARCH_WORKFLOW.md`, the reviewed candidate profile, `internships.json`, actual application history, and personal decisions. Keep employer requirements and the candidate's actual enrollment, return-to-school, timing, and authorization separate. Do not assume delayed completion, extended enrollment, current EAD validity, or a particular authorization path to make an internship fit.

Create a unique local evidence directory and generate plans from the repository root:

```bash
python3 -B 工具/healthcare岗位抓取/search_plan.py --scope internship --output <evidence_dir>/search_plan.json
python3 -B 工具/healthcare岗位抓取/linkedin_search_plan.py --scope internship --output <evidence_dir>/linkedin_search_plan.json
```

Execute all 12 ATS queries, comprising 6 recent and 6 unrestricted recall queries. Preserve original and actual queries, real provider, actual time, completed/failed/skipped state, result URLs, errors, and raw evidence. The shipped query terms seek spring/off-cycle/year-round research, ML, and data-science internships; adapt and record actual terms when the configured target window differs. Check known-company official increments and independent early-career routes. Optional crawler output and `spring_internships.py` assessment require the local setup in `docs/CRAWLER_SETUP.md`; cached candidates do not establish live availability or suitability.

Execute the two planned public LinkedIn queries using `skills/linkedin-hiring-post-search/SKILL.md`. Retain the two native Posts entries for manual use, marked skipped when not performed. Keep public and manual coverage separate. Do not use a full-time LinkedIn run as completion of these queries. No automatic login, internal APIs, scraping, native pagination, or outreach.

Verify each useful new role from the complete official ATS or formal recruitment source. Recheck existing unarchived internships; revisit archived listings only when there is new relevant official evidence. Confirm country/work region, degree level, actual industry requirements, required skills/domain, start window, length, hours, pay, enrollment, return-to-school, degree-completion constraints, and authorization wording. Distinguish required, preferred, unclear, and form questions. A spring keyword does not establish a start option, and a form asking about return to school does not by itself prove every answer is disqualifying.

Use `skills/sponsor-check/SKILL.md` with same-entity evidence reuse and role-specific differences. Separate legal availability of an authorization, the candidate's actual eligibility, and employer acceptance. General F-1 FAQs and CPT/OPT form fields are not acceptance evidence. Keep unknown OPT acceptance unconfirmed; an accepted label requires current official, specific employer wording with URL and actual checked time. Do not silently exclude an otherwise possible authorization route merely because a different route is unavailable.

Keep unverified or req-less leads outside internship cards. Deduplicate confirmed roles by company and real req; never invent an identifier to satisfy the merger. Preserve prior first discovery, post/publication/update/verification dates, manual declines, pauses, rankings, graduation filters, application facts, and supporting reasons. Official open status does not cancel a personal decline or hold. Already submitted roles keep their application record and an appropriate next step.

Close only with substantive official closure text or actual ATS false fields for the exact req. Preserve explicit open fields when labels conflict. Restricted access, login, captcha, timeout, search omission, unreadable SPA text, and bare 404 are unverified. Check rendered role/application controls and exact current ATS records when extracted closure wording conflicts. Access failure does not clear a prior successful check or reopen an archived role.

Save the evidence/report and prepare `<evidence_dir>/batch.json` using the exact `internship_updates.py` contract in `docs/SEARCH_WORKFLOW.md`. Header: `run_id`, `kind`, timezone-aware `observed_at`, local `source`, and `items`; set `complete: false` when actual coverage is incomplete. Use `daily_search` only for an actual search run and `maintenance` for local-only work. New cards require every documented field and successful official open evidence; existing updates use accurate company/req identity and timed `availability_audit.attempts`.

After meaningful actual research and saved review, run:

```bash
python3 -B 工具/求职进度面板/internship_updates.py --batch <evidence_dir>/batch.json
python3 -B 工具/求职进度面板/make_dashboard.py
python3 -B 工具/求职进度面板/make_dashboard.py --check
```

Do not record wholly failed or unexecuted research as a successful search. Check unique company/req identities, unchanged application events, evidence clocks, archive counts, preserved manual decisions, and real discovery badges. If local access is unavailable, keep pending research and state that sync did not occur.

Stay quiet without actionable change. Notify once for worthwhile new verified internships, explicit closure, important condition changes, failed search/sync, or required user action, with useful official links and actual limits. Do not apply, upload documents, contact employers, publish private data, or create another schedule. Honor specific direct authorization already given for an action.
