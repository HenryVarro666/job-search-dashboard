---
name: handshake-job-search
description: Discover Handshake full-time opportunities through an authorized native search or scheduled public-index search, verify official jobs, and sync deduplicated local dashboard records.
---

# Handshake job search

Read the configured private profile or the blank [candidate template](../../docs/CANDIDATE_PROFILE.md), then the [shared search workflow](../../docs/SEARCH_WORKFLOW.md). Locate the checkout containing `工具/求职进度面板/` from task configuration if this skill is installed elsewhere. Do not depend on a source installation, institution, or personal path. Blank profile fields remain unknown; query examples do not establish the candidate's qualifications, graduation date, experience, authorization, or preferences.

Choose the requested discovery mode:

- **Authorized native search:** The user completes login and any credential or verification steps. Use a bounded, sequential search through the normal visible student/alumni interface. Confirm the actual Job/Full-time filters, location scope, degree options, sort, and results surface. Institution visibility and profile matching are not proof of eligibility. Record unexecuted login-dependent work as skipped.
- **Scheduled public-index search:** Read [public search and local record contracts](references/workflow.md). Execute four configured short public queries through an available external search provider. This mode does not search the authenticated institution account. Record `native_ui` as skipped; public success never updates a native-search clock. The skill and prompt do not create a scheduler or demonstrate that a run occurred.

Do not develop an authenticated Handshake collector, call private endpoints, automate account creation, or traverse job boards in bulk. Read current platform access conditions when changing the acquisition method. Stop restricted sources without bypassing login, captcha, rate, or permission controls. Employer official ATS verification is a separate source.

## Verify before recommendation

Keep Handshake Job ID, institution Posting ID, employer profile ID, and external ATS requisition/UUID separate. Preserve the observed institution scope or leave it unknown. Imported jobs may look like employer posts; a profile, full body, or application button alone does not establish direct publication. Confirm external official URLs and the exact role mapping. Where a role is demonstrably employer-direct and accepted only through Handshake, preserve that first-party recruitment evidence and explain why an external ATS is not applicable.

Read the company careers/policy source and complete official ATS or formal recruitment page. Reuse dated company evidence only for the same legal entity; verify the current role and changed conditions. A snippet, inaccessible page, missing search result, expired platform link, or institution-access failure does not establish closure. A JavaScript shell or conflicting extracted label remains unverified until resolved against reliable official evidence. The platform's AI summary and profile match message are not the original requirements.

Apply the configured candidate's country, level, actual experience, required skills, domain, and authorization gates. A degree replaces experience only when the role explicitly allows it. Preserve OR alternatives, required versus preferred language, transferable methods, and missing evidence. Do not infer clinical expertise from general ML or industry experience from academic tenure. For viable roles, give a staged decision and proportionate preparation cost rather than an acceptance probability.

Use the sibling [sponsor-check](../sponsor-check/SKILL.md) where authorization matters. Separate Initial OPT, STEM OPT, H-1B, and PERM evidence; preserve legal-entity scope, original check dates, and disclosure coverage. Platform work-authorization labels express stated preferences, not confirmed candidate status or a guarantee of employer support. Salary, location, SOC, wage year, and actual offer uncertainty remain explicit.

## Deduplicate and sync

Save plans, actual execution, source leads, and official evidence in a configured ignored local run directory. The [reference](references/workflow.md) defines Handshake provenance and run-state fields. Keep platform posting text, official publication/update fields, local discovery, successful verification, and sync times separate. Repeated searches do not reset discovery dates.

Before writing, reread current applications, targets, scanning-pool input, and discovery ledger. Match verified company/entity aliases and exact ATS req/UUID. Multiple platform listings can support one opportunity. Do not merge by title alone, invent an employer req, overwrite concurrent edits, or reopen a personal decline, hold, exclusion, referral-only route, or submitted application. Preserve platform IDs as source aliases. A req-less verified full-time role can use a stable local ID based on its real Handshake Job ID with `req` empty; check prior platform/URL aliases manually because the ledger cannot infer them.

Only officially verified and qualified or explicitly conditional roles enter `targets.json`. Keep unverified, conflicting, or excluded leads separate. Use the shared workflow's actual local helpers after merging; they neither fetch Handshake jobs nor import a lead file automatically. Initialize a historical ledger only once when required by the configured installation. Plan generation, smoke checks, and failed or unexecuted work are not successful searches.

Report actual coverage and limitations, verified roles, additions, duplicates, exclusions, unresolved gates, and sync outcome. Scheduled runs remain quiet without actionable change and report meaningful opportunities, failures, or required user action. Search does not authorize applications, messages, connections, uploads, publication, account changes, alerts, or new schedules. Honor direct user authorization for a specific later action; button clicks, Pending, and self-reported application status do not prove ATS receipt. Personal profiles and runtime records remain local.
