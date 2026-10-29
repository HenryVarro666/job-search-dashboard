# Optional crawler setup

The v2 export includes source for ATS collection, offline search planning and review preparation. The crawler is **setup required**. It ships no employer registry, candidate evidence, internship policy, collected job data or external review runner. Do not point it at a private vault by assumption. Python 3.10+ and the standard library are required; HTTP collection uses `urllib`, with system `/usr/bin/curl` as an optional fallback. `track.py --notify` uses macOS `osascript` only when requested.

The existing dashboard demo and repository checks use synthetic local data. They do not run a live crawl. These sources preserve a US healthcare/ML and early-career PhD screening preset, including domain tags, experience gates, four planner query families and America/Chicago date semantics. Those are implementation presets, not facts about the person using the dashboard. Review the preset before applying it to another search. Domain evidence must come from an explicit fact sheet. The crawler does not itself decide whether a person has work authorization or whether an employer will sponsor them.

## Create local configuration

Run these commands from the repository root:

```sh
cp examples/companies.json.example 工具/healthcare岗位抓取/companies.json
cp examples/spring_internship_policy.json.example 工具/healthcare岗位抓取/spring_internship_policy.json
```

The example registry has an empty `companies` list. Add only employers whose official careers page and ATS board you have verified. Its defaults contain `keywords`, `max_total_per_keyword` and `workers`. Each company needs `key` (stable unique string), `name`, `ats`, `track` and `careers_url`. Tracks retain the funnel convention: A applies a healthcare-team filter; B/C/D allow the additional DS/software branches defined in `funnel.py`. This label alone proves no candidate fit.

A fictional Greenhouse configuration shape is:

```json
{
  "defaults": {
    "keywords": ["machine learning", "data scientist"],
    "max_total_per_keyword": 400,
    "workers": 2
  },
  "companies": [
    {
      "key": "example",
      "name": "Example Research",
      "ats": "greenhouse",
      "track": "B",
      "careers_url": "https://example.test/careers",
      "slug": "replace-with-verified-board-slug"
    }
  ]
}
```

This is a schema example, not a working target. Greenhouse, Ashby, Lever, Workable and Gem use `slug`; Workday uses `workday: {"host": "tenant.wd5.myworkdayjobs.com", "tenant": "tenant", "site": "External"}`. Verify all three values from the employer's own ATS URL. Other adapter-specific fields are documented in the `adapters*.py` module docstrings. No discovery script is bundled to infer registry settings automatically. Optional `keywords`, `keyword_blacklist`, `applied_facets`, `must_include`, `skip` and adapter settings should be added only when needed and understood.

`companies.json` is ignored by Git. Runtime directories `raw/`, `out/`, `logs/`, `manual/` and `ref/` are ignored too. Keep candidate documents in `.local/` or outside the repository. Files ending in `.md` are not globally ignored: a fact sheet elsewhere in the repository can be tracked accidentally.

The internship policy example is fictional and disabled. Set `enabled: true` only after choosing the local `target_year`, `candidate_weeks`, `search_keywords` and `school_status`. `candidate_weeks` is a two-number minimum/maximum range. The optional legacy key `graduation_preference.exclude_fall_2027_or_later` is retained for compatibility; when true it applies to **Fall of the configured `target_year` or later**, rather than to a fixed personal graduation cohort. It is false in the example. A disabled policy CLI exits before reading job inputs. A missing policy gives a setup error; `track.py fetch/run` skips this optional branch when no local policy file exists.

## Collect from explicitly configured boards

These commands make network requests. Run them only after replacing the fictional registry entries:

```sh
cd 工具/healthcare岗位抓取
python3 -B fetch_ats.py list --company example
python3 -B fetch_ats.py jd --company example
python3 -B fetch_ats.py build
python3 -B funnel.py
```

`list` enumerates, `jd` fetches or refreshes details, and `build` normalizes local raw files. `all` combines those three collection steps. `status` reports local coverage. `full-board` is an additional enumeration method for supported adapters, not proof that every ATS is exhaustive. Raw responses and logs remain local. `must_include` misses return exit code 2 and need review.

`fetch_ats.py --registry /absolute/path/to/companies.json` overrides the registry **for that invocation only**. Repeat it on each `fetch_ats.py` command if using a custom file. It does not relocate `raw/` or `out/`; those remain beside the tool. `track.py` and `funnel.py` use the default local `companies.json`, so copy your reviewed registry there before using the combined tracking command. The empty example registry cannot crawl anything.

Absence alone never auto-closes a previously reviewed requisition. Missing normalized jobs remain unverified even when the current official list still contains the requisition or an otherwise healthy board no longer lists it. Detail/build failures, failed/stale/empty enumeration and sharply reduced coverage cannot establish closure. Workday queries skipped as overly broad are incomplete, just like failed queries; another successful query does not fill that evidence gap. Tracking closes only an explicit official `can_apply: false` with `source_authority: "primary"`, a valid `fetched_at` from today, and a current official `_listed_at` no later than that detail fetch. Display/manual/unknown sources and cached false values remain unverified. Restoring a prior tracking closure requires the same current primary detail provenance and an explicit `can_apply: true`. Stale, unknown or non-primary rows preserve that closure and are recorded as unverified in both the returned and saved delta. Closures originating from `apply_links.py` remain preserved by tracking. `company_health()` provides freshness/count/query checks, not an exhaustive completeness certificate for every vendor. HTTP 200 and search-engine snippets alone prove neither completeness nor an open job. Publication, update, fetch, discovery and verification dates have separate meanings.

## Prepare a private review batch

From the repository root, create an explicit local batch:

```sh
batch_dir="$PWD/.local/crawler-batch"
mkdir -p "$batch_dir/数据"
cp examples/候选人事实表.md "$batch_dir/候选人事实表.md"
```

Edit that private fact sheet using reviewed evidence. `judge_tools.load_strength()` requires a `## 二` table with every tag from the example and an exact value `STRONG`, `STRONG_WITH_CAVEAT`, `PARTIAL` or `NONE`. All bundled defaults are `NONE`. Missing/incomplete tables stop ranking; loading a new batch clears prior strengths and publication mappings, so a different candidate cannot inherit them. The optional §一 publication rows use the evidence-ID syntax explained in the template. A blank table is no evidence; it is not a positive match.

The domain-strength table does not supply research tenure. For a JD clause that explicitly accepts research and requires more than zero years, the external reviewer must verify this candidate's non-overlapping relevant research duration and local evidence, then include these fields under that judgment's `hard_gates.years`:

```json
{
  "verified_research_years": null,
  "research_evidence_ids": []
}
```

Replace `null` only with a finite nonnegative number supported by reviewed local evidence, and fill `research_evidence_ids` with a nonempty list of the corresponding source IDs. Record the as-of date and periods in the private fact sheet. Do not double-count concurrent appointments or assume that a degree supplies years. No duration is inferred from a personal start date, the calendar, or another batch. Missing/invalid duration or missing source IDs leaves that experience requirement conditional; an explicitly verified total below the required years fails. The reviewer is responsible for checking that the IDs resolve to actual evidence and that the employer accepts that type of experience. The script validates the fields, not the evidence documents. Roles with no positive experience requirement do not need a duration field merely to pass that gate.

Supply your own reviewed prompt and compatible judgment schema. Neither is bundled with the crawler; the prompts under `prompts/` describe search tasks and are not substitutes for these reviewer inputs:

```sh
cp /absolute/path/to/review-prompt.md "$batch_dir/数据/判读提示词_v1.md"
cp /absolute/path/to/judgment-schema.json "$batch_dir/数据/判读schema_v1.json"
python3 -B 工具/healthcare岗位抓取/track.py run --batch-dir "$batch_dir"
```

Replace the two source paths with real local files before running. `run` validates those three input paths, then performs fetch, delta and stage. `delta` and `stage` can also be invoked separately with the same explicit `--batch-dir`. `delta` does not require reviewer inputs; `stage/run` do. `track.py fetch` runs collection without a batch. No command falls back to a personal batch or reads a private career archive outside the specified batch.

`stage` writes `数据/判读输入/<batch>.json` and `数据/追踪/next_judge_args.json`. The latter names the fact sheet, prompt, schema and generated batches for a human or separately authorized agent. It does not launch an LLM, install a workflow, submit applications or contact anyone. Review inputs and outputs may contain private facts; do not send them to an external service without authorization.

The external reviewer must produce the expected judgment objects in `数据/判读输出/`. The fields consumed by `judge_tools.py` include `uid`, `official_level`, `hard_gates`, `standout_coverage`, `domain_overlap`, `domain_fit`, `judge_meta` and `funnel`; consult `recompute()` and `derive()` before adopting a schema. The schema path is for the external reviewer: this tool does not supply a full JSON Schema validator. Then use:

```sh
python3 -B 工具/healthcare岗位抓取/judge_tools.py recompute --batch-dir "$batch_dir"
python3 -B 工具/healthcare岗位抓取/judge_tools.py verifyset --batch-dir "$batch_dir"
```

The verification set also requires external review before `reconcile`. `eligset` prepares eligibility-review inputs for an external reviewer. No review outputs or final judgments ship with this repository.

Optional drift detection reads only `$batch_dir/career-archive.md` and the corresponding `career-archive.md md5=...` snapshot line in the fact sheet. If either file is absent, drift is unknown; no other vault is searched. Existing batch review outputs, hashes and source provenance are needed for meaningful change comparisons. A first run with no prior review state only prepares new candidates.

## Offline planning and regression checks

These planners only construct search queries and links. They do not search, sign in, send messages or prove that a job is new or open:

```sh
python3 -B 工具/healthcare岗位抓取/search_plan.py --scope all --output .local/ats-plan.json
python3 -B 工具/healthcare岗位抓取/linkedin_search_plan.py --scope all --output .local/linkedin-plan.json
```

Both retain the four source query-family presets and Chicago calendar behavior. Set `--date YYYY-MM-DD` for a reproducible plan. Search execution and official verification are separate actions. Native LinkedIn Posts filters require manual confirmation; public indexed posts do not certify native Posts coverage.

Run the synthetic tests without live collection:

```sh
python3 -B -m unittest discover -s 工具/healthcare岗位抓取 -p 'test_*.py'
python3 -B -m unittest discover -s 工具/求职进度面板 -p 'test_*.py'
```

The spring selector tests inject a synthetic policy and pass an explicit temporary policy to CLI tests. ATS tests mock HTTP responses. Profile tests use temporary fictional fact sheets. None requires a live registry, private policy, reference database, personal application history or collected job data. Passing tests validates those local behaviors; live ATS availability still needs separate verification after setup.
