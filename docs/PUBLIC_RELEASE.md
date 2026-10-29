# Public-release preparation

This document prepares a future release. It does not authorize changing repository visibility. The repository remains private until its owner approves that change.

## Content review

- [ ] Inspect every tracked file and the full Git history for private application records, correspondence, resume content, personal paths, account identifiers, schedules, and credentials.
- [x] Confirm that only fictional data appears in demos, tests, documentation, and images.
- [ ] Keep real JSON inputs, source documents, generated private HTML, and local reference datasets out of the repository. Ignoring a file does not remove it from Git history.
- [ ] Review inherited source comments and region-specific defaults. Explain configurable defaults without presenting them as universal rules.
- [ ] Check the rights and provenance of all included code and artwork before choosing a license.

## Reproducibility and presentation

- [ ] Clone into a clean directory and run `python3 scripts/demo.py` using a supported Python version.
- [ ] Run the documented regression checks against the exact release commit.
- [ ] Verify the synthetic demo in a browser, including a narrow viewport, dark mode, keyboard navigation, search, details, and charts.
- [x] Confirm that the README describes the released behavior, not planned work.
- [ ] Check relative links and image rendering on GitHub.
- [ ] If replacing the illustration with actual screenshots, capture only an isolated synthetic demo and label it accurately.

The source review removed a private example comment and a personal default filter from the current export. Their earlier versions remain in the private baseline history; sanitize that history before publishing it. The preserved v1 tag is useful for local comparison and is not yet approved for a public release.

The v2 snapshot adds sanitized search tools, skills and prompt templates. Its `*.json.example` files are setup inputs, not live registries or schedules. The v2 review does not replace the full-history audit above; earlier private-history concerns remain applicable before any visibility change.

## Release decision

- [ ] Choose and add a license after the rights review. Do not describe the project as open source before a license has been selected.
- [ ] Create release notes that distinguish the preserved v1 baseline from the redesigned version and disclose material limitations.
- [ ] Obtain the owner's explicit decision to make the repository public.

## Documentation artwork

`docs/assets/dashboard-overview.svg` is authored vector artwork. `dashboard-overview.png` is its raster export for README compatibility. It depicts a fictional layout and fictional values, with English labels for an audience-facing overview. Neither file is a screenshot or evidence that the corresponding browser interactions were tested.
