# Job Search Dashboard

A local, offline job-search dashboard generated from JSON records with Python's standard library. The UI uses HTML, CSS, JavaScript, and SVG without external assets.

## Version 1 baseline

`v1.0.0` preserves the existing runtime source before the interface and chart redesign. `source-manifest.json` records the source-file hashes at extraction. The dashboard covers daily actions, candidate jobs, application history, assessments, and internships.

Only source code is versioned. Personal application data, email records, research notes, resumes, schedules, and generated private pages are excluded. The repository is private.

## Synthetic demo

Requires Python 3.10 or later. Run `python3 scripts/demo.py`. The script builds an isolated synthetic example under `.local/demo/` and prints the generated HTML path. Open that HTML in your browser. It does not read or modify a personal vault.

## Existing local installation

The original directory layout is retained so the source can be used by an existing local installation. Its private JSON inputs stay outside version control. `python3 工具/求职进度面板/make_dashboard.py` generates the offline page when local data is provided. The included supporting modules resolve the existing imports; this repository does not include the full job-search crawler or its datasets.

## Data meaning

Referral requests, confirmed applications, assessment records, and outcomes are distinct. Silence archiving is a local display decision, not a rejection. Rebuilding HTML does not perform a live search or verify job availability. The v1 baseline predates the chart-semantics fixes described in later changes.
