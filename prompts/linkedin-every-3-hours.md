# LinkedIn search every three hours

Template for a separately authorized recurring executor. Supply `repository_root`, private `profile_path`, unique `run_id`, local `evidence_dir`, and a writable local LinkedIn state path. Read the [workflow](../docs/SEARCH_WORKFLOW.md). The cadence is a deployment choice; this file does not register or modify automation.

## Prompt

Search public LinkedIn hiring posts for the configured full-time role families, officially verify useful leads, and sync verified changes to the existing local dashboard. Read `docs/SEARCH_WORKFLOW.md`, `skills/linkedin-hiring-post-search/SKILL.md`, the reviewed candidate profile, existing targets/applications, and saved personal decisions. Keep missing profile facts unknown. Do not infer industry experience, domain expertise, timing, or work authorization from a degree or query.

Create a unique local evidence directory and generate the full-time plan from the actual repository root:

```bash
python3 -B 工具/healthcare岗位抓取/linkedin_search_plan.py --scope fulltime --output <evidence_dir>/linkedin_search_plan.json
```

Execute all five `public_web` queries with the available public search provider: four recent family queries plus one unrestricted rotating recall query. The five `native_posts` entries remain separate manual opportunities. Mark them skipped when not performed; public search does not complete them. Record actual queries, provider, time, status, URLs, requested and observed surfaces, applied filters, and errors. Query adaptation and extra recall queries have their own execution records. Do not claim readable post coverage when results contain only Jobs or company pages.

No automatic login, internal APIs, scraping, native pagination, or access-control bypass. Stop restricted routes, record failures or skips, and continue independent public work where possible. Search snippets remain `snippet_only` if the original post cannot be read. Recognize reposts, old recruitment, agencies, candidate self-promotion, and training ads. Verify recruiting relationships where possible rather than accepting a profile claim as employer authority.

Save `linkedin_leads.json` in the documented post-by-role schema. Keep posts without official recruitment evidence as unverified leads. Follow genuine official links, including formal lab/team recruiting pages without an ATS. Preserve real req identity; do not synthesize reqs or merge by title. Prioritize new posts, new reqs, and substantive changes without resetting original discovery or publication dates.

For promising roles, read the complete official JD and real application route. Reuse same-entity careers/policy evidence with its original dates, and check new role restrictions and changes. Apply country/work region, official level, actual industry/post-degree years, required qualifications, domain, and authorization gates. Degrees substitute only where this JD explicitly allows it. Use `skills/sponsor-check/SKILL.md` for independent Initial OPT, STEM OPT, H-1B, and PERM conclusions, distinguishing historical records from this role's commitment. Validate current dynamic rules rather than relying on a policy snapshot.

Only explicit official closure evidence can close a listing. Restricted access, no results, unreadable pages, bare 404s, or deleted posts do not establish closure. If extracted JavaScript-page text says closed while the role or application controls suggest open, compare the rendered page and exact ATS listing; retain conflict as unverified until resolved.

Deduplicate posts by reliable activity ID or canonical permalink and opportunities by confirmed company plus exact req. Preserve multiple source posts for one opportunity. Keep existing personal declines, pauses, quota holds, exclusions, priorities, and application history. An official open listing does not revoke a personal decision, and a closed listing does not create a rejection event.

Save the plan, query execution, leads, official evidence, and report locally. Sync only eligible, officially verified full-time changes through the documented `targets.json` and `opportunity_updates.py` flow, then rebuild/check with `make_dashboard.py`. Keep unverified leads separate. The merger consumes saved sources and never searches online. Do not use `daily_search` for plan-only maintenance, wholly failed searches, or unexecuted work. If local files are unavailable, preserve pending results and report that sync did not occur.

Maintain the local LinkedIn state using the workflow's documented fields. Advance `last_attempt_at` for an actual attempt. Advance `last_successful_search_at` only when all five planned public queries complete normally, even if they yield no useful posts. On partial or failed work, preserve the previous successful time and record real source counts, evidence paths, errors, and limitations. Advance verification and sync clocks only after those operations succeed. Keep manual source counts separate and do not include live schedule or chat IDs in this portable state. The daily full-time task can reuse a complete evidenced run within three hours by citation.

Stay quiet when there is no worthwhile new verified opportunity or important change. Notify once for meaningful additions, explicit closure, material eligibility/policy changes, failure, or required user action, with useful official links and actual coverage. Do not send routine unchanged-state updates. This research does not authorize messages, connections, comments, applications, resume uploads, publication, or new schedules. Honor specific direct authorization already given for an action; a hiring post is not that authorization.
