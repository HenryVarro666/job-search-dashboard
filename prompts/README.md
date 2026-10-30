# Search prompts

The v4 package provides five execution templates. Choose a template after configuring a reviewed private [candidate profile](../docs/CANDIDATE_PROFILE.md) and the [local workflow](../docs/SEARCH_WORKFLOW.md).

| Template | Scope |
|---|---|
| [Manual full-time search](manual-fulltime-search.md) | One full-time search with ATS and LinkedIn discovery. |
| [Daily full-time ATS search](daily-fulltime-ats-search.md) | Full-time ATS discovery and existing-company checks, with separate LinkedIn coverage. |
| [Daily internships](daily-internships.md) | An independent internship pool with timing, enrollment, and authorization checks. |
| [LinkedIn every three hours](linkedin-every-3-hours.md) | Public hiring-post queries, official verification, and local synchronization. |
| [Handshake every three hours](handshake-every-3-hours.md) | Four public-index queries, official verification, and local synchronization. |

Supply the actual `repository_root`, private `profile_path`, unique `run_id`, and writable local `evidence_dir`. Recurring templates also need their documented private state paths. Follow each template's input contract; these task settings are not new command-line options.

Use an agent or person with the required search tools to execute a template. The repository's query planners only produce offline plans. Save actual execution and official evidence before invoking the local merge helpers.

Daily and three-hour names describe intended cadence. Configure an authorized scheduler separately; copying a template does not create or enable a task. Public-index coverage stays separate from logged-in account search.

The templates use the [search skills](../skills/README.md). Keep personal profiles, application records, correspondence, evidence, runtime state, and generated personal HTML outside version control. See the [v4 release notes](../docs/V4_RELEASE.md) for package scope.
