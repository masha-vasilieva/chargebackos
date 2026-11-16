# Chargeback OS

**Test how purchasing agents handle a purchase that goes wrong.**

Chargeback OS is an early, synthetic sandbox for developers building agents that buy APIs, datasets, or work from other agents. It lets you see whether an agent checks what was charged and delivered, stays within its authority, and verifies a refund before claiming success.

This is a research sandbox, not a live refund, chargeback, fraud-detection, or safety-certification service. Optional runs on PayPal's developer **sandbox** use sandbox money only.

## Try it

- [Open the sandbox](https://chargebackos.dev)
- [Read the agent guide](https://chargebackos.dev/guide)
- [View the HTTP API schema](https://chargebackos.dev/api/openapi.json)

Create a run in the browser, then connect an agent you run yourself over HTTP or Streamable HTTP MCP. You do not install a Chargeback OS SDK; your client still needs a compatible HTTP or MCP setup. The guide walks through connecting and handling the temporary run credential.

## What an agent can do

The simulator provides fixed tools to inspect an offer, make a synthetic purchase, check payment and delivery, request and check a refund, and finish a run. Some scenarios also test whether the agent follows an unauthorized extra-payment request or a fake-secret instruction.

Current scenarios cover non-delivery, late delivery, pending and completed refunds, seller instructions, and sellers who chat casually and push payment off the platform. They include benign controls, so an agent should not treat every seller or failed request as fraudulent.

Each run produces a private action timeline and deterministic checks linked to observed events. The checks can show whether the agent verified payment, delivery, or refund status and stayed within the run's permissions. They do not reveal the agent's private reasoning or prove why it acted.

## Data, permissions, and limits

- All purchases and balances are synthetic. Do not enter real payment details, customer data, or production logs.
- Your agent runs on your own infrastructure. The service does not execute uploaded code, host a model, or make arbitrary network requests for an agent.
- Agent credentials are temporary and scoped to one run. They allow simulator actions, not owner access or changes to research consent.
- Runs are private to the browser-owner session. Research use is optional and off by default. You can inspect, delete, or withdraw consent for a run.
- Private runs are retained for 7 days from creation; opted-in runs for 30 days. Losing the browser-owner cookie means losing access; there is no account recovery.
- The service records observable tool activity and simulator results, not chain-of-thought. Agent labels and configuration details are self-reported, not independently verified.
- Scenarios and scoring checks are public, not secret holdouts. Results are not a model leaderboard or a representative benchmark.
- The public service is capped (new runs per day, retained runs, actions per run).

For the OAuth model and client-verification limits, see [Scoped MCP OAuth security](docs/sandbox-oauth-security.md). For the research questions and evaluation limits, see [Agent experience research](docs/agent-experience-research.md).

## Run locally

The web app and API run as separate workspace services. The API requires PostgreSQL; configure `DATABASE_URL` and apply the existing API schema plus [`artifacts/api-server/sandbox_schema.sql`](artifacts/api-server/sandbox_schema.sql). OAuth testing also requires the canonical HTTPS origin in `SANDBOX_OAUTH_ORIGIN`.

Install workspace dependencies:

```sh
pnpm install
```

In separate terminals, start the API and web app:

```sh
pnpm --filter @workspace/api-server run dev
```

```sh
pnpm --filter @workspace/chargeback-os run dev
```

Run the API tests against a disposable development database:

```sh
uv run --no-sync python -m unittest discover -s artifacts/api-server/tests
```

Some isolated tests create and drop database schemas. Do not run them against production or a shared database.

Python dependencies are managed with [uv](https://docs.astral.sh/uv/) (`uv sync`).

## Security

Please report vulnerabilities privately; see [SECURITY.md](SECURITY.md). Don't open a public issue for a security problem.

## License

Open source under the [MIT License](LICENSE), Copyright (c) 2026 Maria Vasilieva. Third-party notices: [THIRD_PARTY.md](THIRD_PARTY.md).

The Chargeback OS name and logo are not licensed under MIT. You may say your project is based on Chargeback OS, but please don't present a fork or your own service as the official one.

If you host this code for other people, you are responsible for your own privacy notice and terms; the hosted service at chargebackos.dev has its own.

Not affiliated with or endorsed by PayPal, Anthropic, Visa, Mastercard or any card network; their names appear only to describe compatibility.
