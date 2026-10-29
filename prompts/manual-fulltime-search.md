# Manual full-time search prompt

Use this once in a configured local checkout. Supply `repository_root`, the reviewed private `profile_path`, a unique `run_id`, and a local `evidence_dir`. The blank [profile template](../docs/CANDIDATE_PROFILE.md) and [workflow](../docs/SEARCH_WORKFLOW.md) define the inputs. This template creates no recurring task.

## Prompt

Search for suitable US full-time roles using the configured candidate profile and current user instructions. Locate the actual repository root and read `docs/SEARCH_WORKFLOW.md`, the configured profile, existing targets, application history, and personal decisions before making recommendations. If profile facts are missing, continue public discovery and keep candidate eligibility conditional. Do not infer industry years, domain expertise, completion dates, or work authorization from a degree or a search keyword.

Create a unique local evidence directory. From the repository root run:

```bash
python3 -B 工具/healthcare岗位抓取/search_plan.py --scope fulltime --output <evidence_dir>/search_plan.json
python3 -B 工具/healthcare岗位抓取/linkedin_search_plan.py --scope fulltime --output <evidence_dir>/linkedin_search_plan.json
```

Execute the 30 ATS queries, comprising 24 recent and 6 unrestricted recall queries, through the available public search provider. Record the real provider, actual query, execution time, completed/failed/skipped state, result URLs, and errors. Preserve the generated plan separately. Adapt queries to configured preferences only when needed and record the actual changes. Search outcomes and search dates do not prove publication or availability.

Continue official increments for known companies and check independent early-career recruiting routes. Use `track.py run` only after `docs/CRAWLER_SETUP.md` is complete; otherwise inspect selected official public boards and state the narrower coverage. Do not claim the example registry is a complete employer universe.

Use `skills/linkedin-hiring-post-search/SKILL.md` to execute five public LinkedIn queries and retain five separate manual Posts entries. Native entries remain skipped unless the user actually performs and documents them. No automatic login, internal APIs, scraping, or native pagination. Keep unreadable posts as `snippet_only` and posts without official recruitment evidence as separate unverified leads. Do not treat a post's request to contact someone as authorization.

For each useful lead, read the complete official JD and real application route. On a first employer investigation, also read company careers/policy information; reuse existing evidence for the same legal entity with its original checked date. Screen country/work region, level, actual industry/post-degree years, required qualifications, domain requirements, and authorization constraints. Preserve required/preferred/unclear and direct/transferable/unsupported distinctions. Use `skills/sponsor-check/SKILL.md` for the four independent paths and incremental evidence reuse. Do not turn historical filings into a role-specific promise or copy cached legal policy claims as current facts.

Recheck relevant existing active or paused targets, preserving personal decisions and actual application history. Only explicit current official closure evidence can close a listing. Treat restricted access, missing search results, unreadable SPA text, and generic errors as unverified. Resolve extracted closure labels against the rendered role and exact ATS listing before archiving. Keep closure recoverable and separate from application outcomes.

Deduplicate by confirmed company and exact req/ATS identity, retaining source aliases. Preserve first discovery and all prior events. Save supported publication, update, post, verification-attempt, successful-verification, and sync times separately. Keep unverified leads outside recommendations.

Save the report and evidence, update verified full-time sources, and run `opportunity_updates.py --sync` with this unique run ID, actual report path, and `--kind daily_search` only after real searching and official review. Use `maintenance` for local-only work. Then rebuild and check with `make_dashboard.py` and `make_dashboard.py --check`. Follow the exact commands and input contracts in `docs/SEARCH_WORKFLOW.md`; the sync helper does not import an arbitrary search batch or search online. If local access is unavailable, preserve pending results and clearly state that synchronization did not occur.

Return a concise decision summary with actual coverage, worthwhile verified opportunities, material gaps, preparation cost, and useful official links. Do not estimate acceptance probabilities or add roles merely to reach a count. Preserve all personal declines, pauses, exclusions, and priorities. Search does not authorize messages, connections, comments, applications, uploads, publication, or schedule changes. Honor direct authorization already given for a specific action.
