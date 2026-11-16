# Security policy

Chargeback OS is a synthetic sandbox: it holds no real payment data, and the
optional PayPal mode uses PayPal's developer sandbox only. Security reports are
still very welcome.

## Reporting a vulnerability

- Use GitHub's **private vulnerability reporting** on this repository
  ("Security" tab, then "Report a vulnerability"), or email
  **hello@chargebackos.dev** with "Security" in the subject.
- Please don't open a public issue, and don't test against other users' runs.
- Include the steps to reproduce and what you think the impact is.
- We aim to reply within 5 working days. This is a one-person project, so
  please be patient; we'll credit you in the fix if you'd like.

## In scope

The code in this repository and the public service at https://chargebackos.dev:
run isolation, agent credentials and OAuth, the HTTP and MCP APIs, rate limits,
research-data handling.

## Out of scope

- Denial-of-service or load testing against the public service.
- Social engineering, physical attacks, or third-party services (Replit, PayPal,
  Cloudflare, GitHub) themselves.
- The synthetic scenarios behaving "dishonestly": that's the product.
