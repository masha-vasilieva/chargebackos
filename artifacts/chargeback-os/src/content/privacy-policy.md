# Privacy Policy

**Chargeback OS** · Last updated: 19 November 2026 · Version 1.1

Chargeback OS is a sandbox that tests how AI purchasing agents behave when a seller lies. It uses synthetic money only. We keep personal data to a minimum: there are no accounts, and we never ask for your name, email or payment details to use it.

## 1. Who we are

The service is run by **Maria Vasilieva**, an individual based in Portugal ("we", "us"). We are the controller of the personal data described here. If a company takes over the service later, we will update this page.

Contact for anything about privacy: **hello@chargebackos.dev**. Postal address: available on request by email.

## 2. What we collect, why, and on what basis

| Data | Why we use it | Legal basis (GDPR) |
|---|---|---|
| A random **owner ID** stored in a cookie | So only your browser can see and manage your runs | Providing the service you asked for (Art. 6(1)(b)) |
| **Your runs**: scenario, your agent's tool calls and the simulator's replies, score, and any labels or notes you type | To run the test and show you the report | Providing the service (Art. 6(1)(b)) |
| **Your IP address**, converted to a one-way code (hash) | To limit how many runs and actions one connection can make and to prevent abuse | Our legitimate interest in keeping the service secure and available (Art. 6(1)(f)) |
| **Agent sign-in records**: the name and return address your agent's app gives, and short-lived access codes | So your agent can connect to the one run you approved | Providing the service (Art. 6(1)(b)) |
| **Page views and a few named button clicks** (for example, clicks on the audit page's "Book a call" and email links), counted by our hosting provider's analytics | To see how many people visit, which pages they use and whether the audit offer gets any interest | Legitimate interest (Art. 6(1)(f)); no cookies, no cross-site tracking, and the count records only that a click happened, not who clicked or anything you typed |
| **A run explanation**, only if you ask for one | To write a plain-English summary of your run | Providing the service (Art. 6(1)(b)) |
| **Research record**, only if you opt in (section 3) | To study how AI agents handle deceptive sellers | Your consent (Art. 6(1)(a)) |
| **Emails you send us** | To reply to you | Legitimate interest in supporting users (Art. 6(1)(f)) |

Daily totals (runs created, actions accepted, research opt-ins per scenario) contain no identifiers and are not personal data.

**Please don't enter personal information, real credentials, API keys, logs or payment details** in labels, notes or runs. The sandbox needs none of it.

We do not sell your data, show ads, profile you, or make automated decisions about you. The score rates an AI agent's behaviour in a synthetic scenario, not you.

## 3. Research contributions (optional)

Research sharing is **off by default**. You can turn it on for a single run when you start it, or later from the run page after previewing the exact record. The sandbox works the same without it.

- **Included:** scenario and version numbers; the client, model family, prior exposure and coaching details you report; the synthetic state; your agent's recorded actions and the simulator's replies; the score and derived observations. Times appear as elapsed time since the run started.
- **Left out:** run and owner IDs, your labels, credentials, comparison links and your other runs. A random reference is kept separately only so we can honour a withdrawal or deletion.
- **Not anonymous:** the record is linked to your run through that reference until it is deleted.
- **Use:** we use it ourselves to study agent behaviour and improve buyer protection for AI agents. We do not publish it, sell it or give it to other organisations under this consent. If we ever want to publish a dataset, we will use only fully anonymous aggregate data or ask for your consent again.
- **Withdraw any time:** untick "Research opt-in active" on the run page. The record then stops being used and the run returns to the normal 7-day deletion date, counted from when the run was created (an older run may be deleted straight away, so export it first if you need it). Withdrawal does not affect research already carried out.

## 4. Cookies and browser storage

We use only cookies the service needs to work, so we do not show a cookie banner.

| Cookie | Set by | Purpose | Lifetime |
|---|---|---|---|
| Owner cookie | Us | Proves this browser owns its runs; HttpOnly, not readable by page scripts | 31 days |
| `GAESA` | Our hosting infrastructure | Routes requests to the right server | up to 30 days |

Our hosting provider's analytics script sets no cookies. Losing or clearing the owner cookie means losing access to your runs; there is no account recovery.

The 'Book a call' link opens Calendly, a separate service with its own privacy policy; we do not share anything with it until you click through and book.

## 5. Who else handles your data

These providers process data on our behalf:

| Provider | Purpose |
|---|---|
| Replit, Inc. | Hosting, database, server logs and page-view analytics |
| Cloudflare, Inc. | Domain name service and email forwarding (below) |
| Anthropic, PBC | Writes the run explanation, only when you ask. Receives the scenario, tool names, result codes and check results: no IDs, labels or free text |
| PayPal | Only for runs that use PayPal **sandbox** mode: test payments and refunds with sandbox money. Receives the run ID as a reference, nothing about you |
| Functional Software, Inc. (Sentry) | Error reports: when our server crashes or returns a server error, Sentry receives the error type and where in our code it happened, so we can fix it. We configure it to send no request contents, headers, cookies, IP addresses, user IDs or error messages. Stored in the EU (Frankfurt) |
| Cloudflare, Inc. (Email Routing) and Google LLC (Gmail) | Cloudflare forwards emails sent to hello@chargebackos.dev to our Gmail mailbox, where Google stores them |

Some of these providers are based in the United States. Where data is transferred outside the EU/EEA, we rely on the EU–US Data Privacy Framework (where the provider is certified) or the European Commission's Standard Contractual Clauses.

## 6. How long we keep data

| Data | Kept |
|---|---|
| Private runs, including labels and explanations | 7 days from creation, then deleted |
| Runs shared for research, and their research record | 30 days from creation, then deleted |
| Owner ID | Up to 31 days |
| Agent sign-in records | Until they expire, at most the life of the run |
| Hashed-IP rate-limit counters | Up to 24 hours |
| Usage totals without identifiers | Up to 90 days; research opt-in totals up to 24 months |
| Server logs | Up to 30 days, as set by our hosting provider |
| Error reports (type and code location only) | Up to 90 days |
| Emails | Up to 12 months after the conversation ends |

Deleting a run removes it from our live database immediately. A copy may remain in our hosting provider's backups for up to 7 days, after which it is overwritten; we cannot delete from those backups earlier.

## 7. Your rights

You can ask us to give you a copy of your data (the run page also has a JSON export), correct it, delete it (you can delete any run yourself on the run page), restrict our use of it, object to uses based on legitimate interest, or withdraw research consent.

Because we don't know who you are, we may ask you to show a run is yours, for example by contacting us from the browser that holds the owner cookie. We will never ask for your agent token or other secrets. We reply within one month.

You may also complain to the Portuguese data protection authority, the **Comissão Nacional de Proteção de Dados (CNPD)**, www.cnpd.pt, or to the authority in your own country. We would appreciate the chance to fix the problem first: hello@chargebackos.dev.

## 8. Security

Data travels over HTTPS. The owner cookie cannot be read by page scripts. Agent tokens are short-lived, valid for one run, and stored only as hashes. Payment, extra-payment and secret-sharing actions in scenarios are blocked by design.

## 9. Children

Chargeback OS is a developer tool and is not intended for people under 16.

## 10. Changes

If we change this policy we will update the date above. If a change affects research you have already contributed, we will ask for your consent again.
