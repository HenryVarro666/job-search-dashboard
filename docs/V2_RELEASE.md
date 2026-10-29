# v2.0.0 release notes

Released as a source snapshot on October 28, 2026. The repository remains private; this release does not change visibility or select an open-source license.

## Search workflow

- Add portable LinkedIn hiring-post, sponsorship-evidence and postdoc-search skills, with a blank candidate profile.
- Include manual full-time, daily full-time ATS, daily internship and three-hour LinkedIn execution prompts. These are templates, not installed schedules.
- Export offline ATS and LinkedIn planners. Generated entries remain `not_run`; native Posts and public-index coverage are separate.
- Include the registered-company collection and review-staging source as an optional configured component. Local employer registries, fact sheets and output data are not bundled.

## Evidence and verification

Discovery, post time, job publication, official checks and application events remain distinct. Search snippets, blocked pages and absent search results cannot establish that a job is open or closed. Exact company and requisition identity drive opportunity deduplication; personal exclusions and application histories are preserved.

Qualification review precedes investment in materials. Candidate-specific experience, degree status, domain expertise and authorization must come from configured evidence. Historical employer filings do not promise sponsorship for a specific job. All legal rules and current employer policies require dated official verification.

The runtime tests cover offline query semantics, chronology, repeated merges, archive safety, internship constraints and selected collector behavior. The synthetic build continues to exercise dashboard navigation, filters, detail panels and chart semantics. These checks do not certify live provider availability, a logged-in account flow, final application submission or unattended scheduler success.

Pre-release review also corrected inherited assumptions. Missing detail/build output and incomplete Workday queries remain unverified; absence alone cannot archive a role. Closing or reopening requires fresh primary detail evidence with an explicit status. A research-counting qualification branch now requires a verified candidate duration and reviewed evidence IDs, rather than a fixed personal start date. Missing duration remains conditional. The final local check passed 205 Python tests plus JavaScript, synthetic-build and DOM checks.

## Packaging

The v1 and v1.1 tags remain intact. Existing privacy and portability changes in the dashboard runtime are retained rather than overwritten by the private source installation. The v2 manifest records source and exported file hashes separately where adaptation was required.

Excluded: real applications, target lists, opportunity ledgers, correspondence, resumes, candidate research notes, crawler responses, wage-reference datasets, generated private HTML, credentials, account IDs and live scheduler state. Only blank or fictional examples are included. New source modules use explicit local setup instead of inheriting a particular candidate's strengths or private batch directory.

See [search workflow](SEARCH_WORKFLOW.md), [candidate profile](CANDIDATE_PROFILE.md), [collector setup](CRAWLER_SETUP.md), and [public-release preparation](PUBLIC_RELEASE.md).

## v2 follow-up: Handshake

Add the portable [Handshake skill](../skills/handshake-job-search/SKILL.md) and [three-hour prompt](../prompts/handshake-every-3-hours.md). The skill supports an authorized, bounded native search and a separate scheduled public-index mode. The recurring template executes four configurable queries, records native account coverage as skipped, and advances its public success clock only after all planned queries complete normally.

Both modes reuse official-source verification, candidate gates, exact requisition and source-alias deduplication, and the existing local target/ledger/dashboard workflow. Imported listings, access failures, platform dates, personal decisions, and application events retain their separate meanings. No Handshake connector, authenticated collector, importer, or scheduler is added.

This is a follow-up commit on the v2 source line; the existing `v2.0.0` tag is unchanged. Private profiles, account and schedule bindings, execution state, search results, applications, resumes and evidence are excluded. The export manifest includes the new templates and refreshed hashes.
