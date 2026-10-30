# Public release record

Publication preparation dated October 30, 2026. The owner explicitly authorized public visibility and selected the MIT license for this project.

## Published scope

The repository contains the offline dashboard runtime, its development checks, optional configured discovery tools, five search prompts and four portable skills. A fictional demo and reproducible browser screenshots illustrate the interface. The [English README](../README.md) and [Chinese guide](../README.zh-CN.md) describe the actual scope and setup.

Personal application ledgers, correspondence, resumes, research notes, local references, credentials, account bindings, active schedules and generated personal HTML are excluded. Blank setup examples and fictional fixtures are identified as such.

## History preparation

Before public visibility, reachable source history is reviewed along with branch and annotated-tag metadata. Two early personal example defaults are generalized and personal commit/tag email metadata is replaced with the owner's GitHub noreply identity. A private local backup is kept outside the published history. Version names v1.0.0 through v4.0.0 are retained; commit and tag object IDs change during sanitization.

Export manifests retain original source provenance hashes and refresh exported-file hashes where sanitization changes a snapshot. Base-commit references are mapped to the sanitized history. A source hash identifies the source reviewed at the time; it is distinct from the hash of the portable export.

## Validation

The regression command is `python3 scripts/check.py`: 209 Python tests, JavaScript regressions, synthetic demo generation, generated-script syntax checks and DOM integration. Browser capture separately exercises all main tabs, progress subviews, assessment/detail views, light/dark mode and a narrow viewport with external requests blocked.

Documentation paths, image dimensions, versioned manifest hashes and reachable publication history are reviewed before the visibility change. A fresh checkout is used for the final source verification. These checks cover the shipped local demo; they do not establish live vacancy availability, authenticated account behavior or unattended scheduler success.

## License and provenance

[MIT](../LICENSE), selected by the owner. Third-party development dependencies retain their own licenses. The interface keeps its own Python/JSON runtime and chart behavior; layout inspiration is credited in the README.

Real screenshots and older authored illustrations are distinguished in [image provenance](SCREENSHOTS.md). No screenshots of private job-search records are published.
