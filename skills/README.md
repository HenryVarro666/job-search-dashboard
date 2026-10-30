# Search skills

The v4 package includes four portable skills. They use a reviewed private [candidate profile](../docs/CANDIDATE_PROFILE.md) and the shared [search workflow](../docs/SEARCH_WORKFLOW.md).

| Skill | Purpose |
|---|---|
| [LinkedIn hiring-post search](linkedin-hiring-post-search/SKILL.md) | Public hiring-post discovery, separate manual Posts entries, and official-job verification. |
| [Handshake job search](handshake-job-search/SKILL.md) | Authorized native search or separate public-index discovery, followed by verification and local sync. |
| [Sponsorship assessment](sponsor-check/SKILL.md) | Separate Initial OPT, STEM OPT, H-1B, and PERM evidence, with reuse by legal entity. |
| [Postdoc fit search](postdoc-fit-search/SKILL.md) | Official vacancies, funding, research requirements, appointment conditions, and workload evidence. |

Read the selected `SKILL.md` from this checkout, or copy its entire directory into your agent's configured skills directory. Preserve supporting `references/` and `agents/` files where present. The Handshake skill includes both.

When using installed copies, provide `repository_root` in the task and read shared documents from that checkout. Links to sibling skills and repository documents assume the original layout; installation alone does not supply those dependencies or configure a private workspace.

Supply the reviewed private `profile_path`, local evidence directory, and existing data paths before a live run. Use the [prompt catalog](../prompts/README.md) for complete execution templates. No account connector, scheduler, application submitter, or external review runner is installed by copying these skills.

These are general exports. Candidate facts, account and schedule bindings, private employer research, execution state, and application materials remain local. See the [v4 release notes](../docs/V4_RELEASE.md).
