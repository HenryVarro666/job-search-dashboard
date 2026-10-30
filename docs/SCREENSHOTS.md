# Screenshot gallery and provenance

Every gallery image is a real Chromium capture of the isolated fictional demo. No personal job-search workspace, application records, account session, inbox, resume or live employer data was used.

## Gallery

| Image | View | Full capture |
|---|---|---|
| [01-today.png](assets/screenshots/01-today.png) | Today | 1440 × 1444 |
| [02-discovered.png](assets/screenshots/02-discovered.png) | Recent discoveries | 1440 × 700 |
| [03-opportunities.png](assets/screenshots/03-opportunities.png) | Opportunities | 1440 × 2389 |
| [04-internships.png](assets/screenshots/04-internships.png) | Internships | 1440 × 1105 |
| [05-progress-current.png](assets/screenshots/05-progress-current.png) | Current progress | 1440 × 2574 |
| [06-progress-records.png](assets/screenshots/06-progress-records.png) | All records | 1440 × 1644 |
| [07-progress-statistics.png](assets/screenshots/07-progress-statistics.png) | Statistics | 1440 × 3152 |
| [08-application-flow.png](assets/screenshots/08-application-flow.png) | Application flow | 1344 × 1176 |
| [09-detail-drawer.png](assets/screenshots/09-detail-drawer.png) | Record drawer | 1440 × 1060 |
| [10-dark-mode.png](assets/screenshots/10-dark-mode.png) | Dark mode | 1440 × 1444 |
| [11-oa-workspace.png](assets/screenshots/11-oa-workspace.png) | OA / assessments | 1344 × 603 |
| [12-mobile-today.png](assets/screenshots/12-mobile-today.png) | Mobile today | 390 × 2223 |

`assets/screenshots/previews/` contains viewport previews for the README. Most desktop previews are 1440 × 1060; the mobile preview is 390 × 844. Flow and assessment previews use complete element captures. Full captures keep the page content; the opportunity page expands the actual browser viewport to expose the embedded board without altering the production DOM or CSS.

## Reproduce

```bash
npm install --no-save --package-lock=false playwright
npx playwright install chromium
node scripts/capture_screenshots.cjs
```

An existing Playwright module and Chromium binary can instead be supplied through `PLAYWRIGHT_MODULE` and `CHROMIUM_EXECUTABLE`. These are optional development inputs; the dashboard generator has no new runtime dependency.

The capture script rebuilds the dedicated `.local/demo/` directory from fictional fixtures, opens a fresh browser context, blocks external requests, navigates the real controls and checks for uncaught page errors. It never uses a signed-in browser profile. The demo reporting date is fixed to October 27, 2026; the displayed generation time is the actual capture time. No live searches were performed.

The fictional records exercise open, unverified and archived listings, personal internship decisions, unknown dates, referrals, assessments and terminal outcomes. Example salaries and offer states are interface fixtures, not results or compensation claims.

## Other artwork

`readme-banner.svg` is a code-authored decorative banner. Its small abstract flow is illustrative; the page gallery supplies actual application screenshots.

The older `dashboard-overview.svg` and its `dashboard-overview.png` raster export are authored illustrations of a fictional layout. They are retained for source history and are not used as proof of current browser behavior.

The README and share-card composition were prepared with AI assistance. The interface captures themselves come from the running synthetic demo.
