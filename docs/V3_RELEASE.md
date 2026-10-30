# v3.0.0 release notes

> Historical source snapshot. The repository now uses the [MIT license](../LICENSE); see the [public release record](PUBLIC_RELEASE.md) for the current publication status.

Dashboard source snapshot dated October 30, 2026. The repository remains private.

## Dashboard changes

- Start the application flow from one confirmed-application node. Group current outcomes like the stage cards and order them from offer to rejection. Keep OA and interview history in the assessment view and record timelines.
- Open the flow by default, reduce unnecessary crossings, improve small-node labels, and keep its accessible data table and record links consistent with the diagram.
- Position sticky table headers within their scroll container and allow long channels, filenames and status text to wrap.
- Accept an employer rejection as evidence that a historical application exists only when that evidence is explicitly verified. Keep missing submission and receipt dates unknown.

## Scope and validation

This release syncs the dashboard runtime from the local source installation while retaining the repository's privacy adaptations. Personal default company filters and personal example filenames remain excluded. Search prompts and skills are inherited from v2; their separate package refresh follows in v4.

Run `python3 scripts/check.py` for synthetic Python and JavaScript regressions, demo generation, inline-script syntax checks and DOM integration. These checks use fictional records, with network access disabled in DOM checks. They do not verify live jobs, real accounts, browser rendering or actual submissions.

Release validation passed 209 Python tests (59 dashboard and 150 discovery), three JavaScript regression files, synthetic demo generation, inline-script syntax checks and full-page DOM integration. The added flow and rejection-evidence regressions also fail against the previous source baseline.

`source-manifest-v3.json` records the synchronized source and exported hashes. Private application data, correspondence, resumes, generated private HTML, reference data and live schedule state are excluded. Earlier version tags remain unchanged.
