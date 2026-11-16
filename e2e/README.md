# Chrome tests

Browser tests for Chargeback OS, run in your installed Google Chrome with Playwright.
This folder is standalone (npm, not part of the pnpm workspace) and is not deployed.

| Project | What it covers | Needs a server |
|---|---|---|
| `unit` | Every module in `artifacts/chargeback-os/src/lib`, compiled from source and run inside Chrome | No |
| `ui` | Click-through of every page: home, example, guide, run creation, agent actions, scoring, rotate, revoke, delete, 404, legacy office, phone-size layout | Yes |

## Run

```bash
cd e2e
npm install              # first time only; uses your installed Chrome, downloads no browser
npm run tunnel           # forwards the Repl's web port to http://localhost:18080 over SSH
npm test                 # both projects
npm run test:unit        # unit only (no tunnel needed)
npm run report           # open the HTML report from the last run
```

`npm run tunnel` uses the `replit` SSH host from `~/.ssh/config`. The Repl's dev URL sits
behind Replit's login wall, which is why the tests go through the tunnel. To test another
copy of the app, set `BASE_URL`, for example `BASE_URL=http://localhost:5173 npm test`.

The `ui` tests create synthetic runs under a fresh owner session and delete them afterwards.
Their expected results are read from the backend snapshot
`artifacts/api-server/tests/regression_golden.json`, so both suites stay in step.
