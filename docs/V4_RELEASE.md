# v4.0.0 release notes

Search workflow package snapshot dated October 30, 2026. The repository remains private.

The v4 package gives the search prompts and skills their own catalogs after the v3 dashboard update. The five prompt bodies and four skills were already included on the v2 source line, including its Handshake follow-up. This release keeps those bodies and adds clearer entry points for choosing, configuring, and installing them.

| Package | Included workflows |
|---|---|
| [Prompts](../prompts/README.md) | Manual full-time, daily full-time ATS, daily internships, three-hour LinkedIn, and three-hour Handshake. |
| [Skills](../skills/README.md) | LinkedIn hiring posts, Handshake, sponsorship assessment, and postdoc fit. |

The existing prompt and skill exports match their recorded v2 export hashes. The LinkedIn, sponsorship, and postdoc source skills also match the source hashes recorded in that manifest. Handshake was checked against its local workflow because the manifest does not record a source hash for it. No prompt or skill body change was needed for this packaging release.

Use the [candidate profile template](CANDIDATE_PROFILE.md) and [shared workflow](SEARCH_WORKFLOW.md) to configure a private installation. Daily and three-hour prompts describe an execution and intended cadence; they do not register a scheduler. Public-index searches, authenticated searches, official verification, and local synchronization keep separate evidence and clocks.

The package excludes personal profiles, real applications, correspondence, resumes, private research, account and schedule bindings, runtime state, and generated personal HTML. It does not change repository visibility or install an account collector, application submitter, or review runner.

`source-manifest-v4.json` records this package's export hashes and validation scope. The v3 dashboard implementation and earlier version tags remain unchanged.

Release validation passed the 209 Python tests, three JavaScript regression files, synthetic demo build, inline-script syntax checks and DOM integration. Package hashes and relative documentation links were checked separately. These checks do not exercise live search services, authenticated accounts, scheduled execution or browser rendering.
