# Handshake acquisition and local records

Use the configured repository or runtime root, reviewed private `profile_path`, timezone, writable ignored evidence directory, and actual local data sources. See the [shared workflow](../../../docs/SEARCH_WORKFLOW.md) for setup and the complete target, ledger, and rendering contracts. This package supplies instructions, not a Handshake browser connector, authenticated collector, lead importer, or scheduler.

## Public-index recurring mode

When recurring work is requested, use the configured scheduler independently. Reuse an existing matching task instead of creating a duplicate. The three-hour cadence belongs to scheduler configuration; reading this file does not enable it. A scheduled run uses external public search and does not log in to Handshake or search institution-only results.

The default plan has these four query families. They are reusable public discovery examples, not a candidate profile. Adapt role terms to reviewed preferences, retaining both the planned and actual query and recording the changed scope. If a configured country or role family differs, do not silently claim the defaults were profile-driven.

| query_id | Default public query |
|---|---|
| HS-MED | `site:app.joinhandshake.com/public/jobs/ ("medical imaging" OR "computer vision")` |
| HS-DS | `site:app.joinhandshake.com/public/jobs/ "data scientist"` |
| HS-RS | `site:app.joinhandshake.com/public/jobs/ "research scientist"` |
| HS-MLE | `site:app.joinhandshake.com/public/jobs/ ("machine learning engineer" OR "applied scientist")` |

Keep unrestricted recall by default. Additional recent filters or alternative expressions are separate coverage, and a search engine's date is not an official job date. Prioritize unseen links, new requisitions, and substantive changes to previously seen evidence. Public results can lag, omit jobs, or contain only snippets. Zero results do not mean no openings or no institution-visible opportunities.

Create a unique ignored local run directory, such as `.local/runs/<run-id>/`, and save `handshake_search_plan.json`, `handshake_search_execution.json`, `handshake_leads.json`, relevant official evidence, and a report. These are executor-written records; there is no bundled `handshake_search_plan.py` or automatic importer. Do not pass private profiles, resumes, correspondence, or authorization details to search providers.

For each planned query record `query_id`, original `query`, actual `executed_query` when changed, `provider`, actual `executed_at`, `status`, `result_urls`, `error`, and any requested/observed filters. Use `access_method=public_index`, `observed_surface=web_results`, and `school_scope=null`. Mark the authenticated `native_ui` entry skipped with its actual reason. Normal zero-result requests are completed; unexecuted and failed requests are distinct.

An executor may keep private `handshake_schedule.json` metadata and `handshake_search_state.json` coverage beside its runtime or under a configured ignored local directory. Do not commit live task IDs, task/chat bindings, execution histories, candidate results, or active automation exports. The files do not register schedules. Suggested coverage fields are:

```text
schema_version, scope, timezone, source_mode,
last_attempt_at, last_successful_public_index_search_at,
last_verification_at, last_sync_at, latest_run
```

`latest_run` records a unique run ID, status, public planned/completed/failed/skipped counts, separately skipped native coverage, extra queries, actual changes/additions, evidence/report paths, and limitations. Initialize unknown times as null. Only normal completion of all four planned public queries advances `last_successful_public_index_search_at`; normal zero results may still qualify. Partial or failed work updates the attempt and coverage while preserving the prior successful time. A setup probe is not a complete scheduled run. Public work never changes a native-search success clock. Update verification and sync clocks only after those actual operations succeed.

## Authorized native search

Use the user's chosen browser and its current documented controls. Let the user complete login, SSO, credentials, and verification. Preserve existing tabs and sessions. Read a bounded set of relevant result pages and full descriptions sequentially. Do not collect an entire board, probe private endpoints, or carry a manual session over into unattended account harvesting.

Verify filters and sorting from the actual interface rather than assuming country, degree, date, or Boolean options exist. Changing filters can reset sorting; changing text inside a detail view may leave the same detail open. Count a new results search only when that surface is actually present. Record actual Full-time/Job selection, location or manual country checks, optional degree filters, sorting, page limits, and access failures. AI summaries and profile-match badges do not replace full JD reading. Do not encode a particular institution, selector, result count, or account capability as a permanent rule.

## Source and identity fields

Keep one lead per concrete role and preserve its discovery-query aliases. Unknown fields remain null or empty, not guessed values.

| Fields | Contract |
|---|---|
| `handshake_job_id`, `handshake_posting_id`, `handshake_employer_id` | Distinct observed platform identifiers. An employer profile ID is not a legal entity and an institution Posting ID is not an employer req. |
| `handshake_url`, `school_scope`, `access_method`, `source_query_ids` | Canonical source, actual or unknown institution scope, mode, and discovery provenance. Remove session/authentication/tracking data. |
| `company`, `employer_entity`, `title`, `location` | Supported brand, legal employer, role, and work region; distinguish unknown entity from brand. |
| `posted_raw`, `posted`, `deadline`, `observed_at`, `first_seen` | Preserve date text and only supported normalized dates. Never substitute local discovery or source refresh for original publication. |
| `body_status`, `evidence_paths` | `full_text`, `snippet_only`, or `inaccessible` with necessary local evidence. |
| `provenance`, `application_mode` | `employer_direct`, `imported`, or `unknown`; independently `handshake`, `external`, or `unknown`. A complete platform JD does not prove direct publication. |
| `work_auth_labels`, `work_auth_quote` | Employer/platform statements, not confirmed candidate status or guaranteed Initial OPT/STEM/H-1B/PERM support. |
| `official_urls`, `req`, `identity_evidence` | Official company/job URLs, real req/ATS UUID when known, and explicit platform-to-ATS mapping. |
| `availability`, `checked_at`, `availability_evidence` | Official `open`, `closed`, or `unverified`, the real check time, and exact-role evidence. |
| `decision`, `reason`, `next_step` | Unverified/conflicting, duplicate, excluded, qualified, or conditional with concrete reasons. |

Imported jobs must return to the employer's official source. A demonstrably employer-direct Handshake posting may serve as the formal job page when no external ATS applies, with separate employer identity/policy evidence. Otherwise leave provenance unknown. Do not click Apply solely to discover a destination when that can create Pending or another platform application side effect; inspect available links or locate the exact official role independently.

Verify official availability, candidate hard gates, and role-specific sponsorship before a recommendation. Record uncertainty rather than upgrading a search snippet. Access failures, institution restrictions, a disappeared source listing, or a bare 404 do not close an external official role. Use the shared evidence-reuse rules and sibling sponsor-check skill rather than repeating unchanged company investigations.

## Deduplication and dashboard sync

Before every write, reread the configured `applications.json`, `targets.json`, optional `pool_board.json`, and `opportunity_refresh.json`. Back up inputs and merge incrementally with atomic writes. Confirm company/entity aliases and exact ATS identities before grouping. Preserve prior first discovery, real application history, and all user decisions. An already-applied role reappearing publicly stays applied.

For a verified full-time role without an employer req, keep `req` empty and use a stable local `id=handshake-<real-job-id>` only as a local key. The ledger falls back to UID when company/req are missing and cannot infer platform aliases. Check existing Job IDs and canonical URLs first. Same-title multi-location roles remain separate until exact identity is established. Req-less internships do not bypass the internship helper's real-req requirement.

Write qualified or explicitly conditional, officially verified full-time targets using the existing [target schema](../../../docs/SEARCH_WORKFLOW.md#save-and-sync). Preserve real req/UUID, official URL, conditions, source mappings, supported salary/date fields, `listing_status`, `listing_checked_at`, and substantive `listing_evidence`. Unverified leads stay in the run directory. Do not convert the lead schema directly into purported helper input: neither the ledger nor renderer imports it.

After an actual saved search and official review, use the existing helper from the configured root:

```bash
python3 -B 工具/求职进度面板/opportunity_updates.py --sync --run-id <unique-run-id> --kind daily_search --source .local/runs/<run-id>/report.md
python3 -B 工具/求职进度面板/make_dashboard.py --check
python3 -B 工具/求职进度面板/make_dashboard.py
```

Mark partial coverage in the source report. Do not use `daily_search` for plan creation, smoke checks, or wholly failed/unexecuted searches. Use `maintenance` only when actual local maintenance needs syncing. Do not reinitialize an existing discovery baseline. The helper reads saved source files, not the network or lead files. It does not prove search completion. Confirm derived cards have unique identities, source dates remain intact, and application events have not changed. Keep generated HTML private because it embeds local records.

No-change recurring runs remain quiet; report worthwhile newly verified opportunities, material changes, failed searches/syncs, or required user action. Local write failure must be reported as a failed/pending sync, not success. Search, drafting, an external Apply click, Pending, or an uncorroborated platform self-report does not establish ATS receipt. Sending, applying, uploading, account changes, alerts, or additional schedules need their own applicable user authorization.

## Platform references

Read the affected official source again when changing acquisition assumptions or encountering changed behavior. These links document platform concepts, not permission to automate a private account:

- [Search and institution visibility](https://support.joinhandshake.com/hc/en-us/articles/218693408-Searching-for-Jobs-and-Internships)
- [Job and institution Posting identifiers](https://support.joinhandshake.com/hc/en-us/articles/227843887-Searching-for-Jobs-in-Handshake-Career-Services)
- [Imported jobs for students](https://support.joinhandshake.com/hc/en-us/articles/34372029993239-Imported-Jobs-on-Handshake) and [institution-side imported-job guidance](https://support.joinhandshake.com/hc/en-us/articles/34276134429719-EDU-Jobs-Imported-into-Handshake-from-External-Sites)
- [Employer posting fields](https://support.joinhandshake.com/hc/en-us/articles/218693198-How-to-Post-a-Job) and [external application status](https://support.joinhandshake.com/hc/en-us/articles/360043604173-Jobs-Apply-Externally)
- [Native saved-search alerts](https://support.joinhandshake.com/hc/en-us/articles/218693388-Saving-Job-Searches-and-Receiving-Job-Alerts), [API access policy](https://support.joinhandshake.com/hc/en-us/articles/360000869147-University-Technical-Support-Guidelines-and-Resources), and [Terms of Service](https://joinhandshake.com/legal/tos/)
