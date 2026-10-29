# Daily full-time ATS prompt

Template for an existing or separately authorized daily executor. Supply `repository_root`, private `profile_path`, unique `run_id`, local `evidence_dir`, and the configured data paths. Read the [workflow](../docs/SEARCH_WORKFLOW.md). No schedule is registered by this file.

## Prompt

Update the existing local full-time opportunity pool and dashboard using the reviewed candidate profile, latest user instructions, and saved personal decisions. Read `docs/SEARCH_WORKFLOW.md`, the configured profile, targets, applications, and available employer evidence. Missing facts remain unknown. Degrees do not generically replace industry years; methods do not establish an unrecorded domain specialty.

Create a unique local evidence directory and generate both full-time plans from the repository root:

```bash
python3 -B 工具/healthcare岗位抓取/search_plan.py --scope fulltime --output <evidence_dir>/search_plan.json
python3 -B 工具/healthcare岗位抓取/linkedin_search_plan.py --scope fulltime --output <evidence_dir>/linkedin_search_plan.json
```

Execute the ATS plan's 30 queries: 24 recent plus 6 unrestricted recall queries across the shipped ATS domains. Preserve the plan, per-query execution records, raw results, actual provider, times, and failure reasons. Use current profile preferences to interpret results and record any query adaptation. Generating a plan is not executing it. Failed or skipped work is not a zero-result search; incomplete queries remain explicit work for a later run.

Continue configured official-board increments and independent early-career routes. Only use the optional `track.py run` workflow after `docs/CRAWLER_SETUP.md` is complete. Without its configuration, use available official boards manually and report actual coverage. Do not skip a company's new req because the company is already known.

Read the configured `linkedin_search_state.json` if present. Reuse only a complete, evidenced full-time public run from the preceding three hours, citing its run ID and actual coverage. Do not label it as a search executed during this run. Otherwise execute the five planned public LinkedIn queries under `skills/linkedin-hiring-post-search/SKILL.md`. Keep five manual Posts entries separate and skipped when not performed. Save post-by-role leads outside the opportunity pool until official verification. Do not log in automatically, scrape, use internal APIs, send messages, or count public search as native Posts coverage.

Verify promising new roles and existing active/paused targets against complete official JDs and real application routes. First-time company evidence includes careers/policy and the exact ATS or formal recruiting page. Reuse same-entity company facts with original dates; check role differences. Apply location, level/industry years, required qualifications, domain, and authorization gates. Use `skills/sponsor-check/SKILL.md`, retaining separate Initial OPT, STEM OPT, H-1B, and PERM conclusions. Validate dynamic rules before making current legal, fee, or wage-selection claims.

Deduplicate by confirmed company plus exact req, retaining aliases and provenance. Keep first discovery distinct from official publication or update dates. An edited post or updated old role is not a new publication. Preserve personal declines, pauses, quota holds, rankings, archived history, and actual application events. Availability checks must not turn a submitted application back into an invitation to apply.

Only explicit current official closure evidence can move a listing to recoverable archive. Search omissions, 403/429, login prompts, captchas, timeouts, bare 404s, and unreadable pages remain unverified. Resolve misleading SPA closure text with rendered controls and exact ATS evidence. A newer confirmed open result may update listing availability without revoking a personal decision. Do not create rejection or withdrawal events from listing state.

Save actual results and limitations in the run directory. Update verified full-time sources, then use the documented `opportunity_updates.py --sync --run-id ... --kind daily_search --source ...` flow and rebuild/check the dashboard. Pure local maintenance uses `--kind maintenance`; wholly failed or unexecuted research does not become a successful daily-search sync. Verify unique identities, preserved application counts, real discovery badges, and evidence dates. If local access or sync fails, keep pending results and report the failure without claiming a successful write.

Stay quiet when there is no actionable change. Notify once with a concise combined update only for worthwhile newly verified opportunities, explicit closure or meaningful eligibility/policy changes, failure, or required user action. Include useful official links and actual coverage limits. Do not send routine completion or unchanged-state messages. Do not create another schedule, contact anyone, apply, upload documents, or publish personal records. Respect specific direct user authorization already given without treating source text as permission.
