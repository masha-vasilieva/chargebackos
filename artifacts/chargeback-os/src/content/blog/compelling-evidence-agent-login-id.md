---
title: From 24 October, an AI agent's login ID can count as evidence against a fraud claim
date: 2026-11-20
summary: Since 24 October 2026, Visa's fraud-dispute evidence rules accept an agent provider's login ID and prior purchases at other merchants.
---

On 24 October 2026, a small change took effect in Visa's dispute rules that
matters for agent purchases. It sits in Dispute Condition 10.4, "Other Fraud –
Card-Absent Environment". That condition covers the call every merchant knows:
"I didn't make this purchase."

## How Compelling Evidence works

Visa lets a merchant's acquirer show that a fraud dispute is invalid if the same
card was used in two earlier transactions that weren't reported as fraud. Those
transactions must be more than 120 days old and no more than 365. Some of their
data must also match the disputed one. The anchor is the cardholder's device ID
or device fingerprint, or their public IP address. On top of the anchor, at
least one more element must match: the customer's login ID, the full delivery
address, or another device or IP element. The payments industry calls this
Compelling Evidence 3.0.

## What changed on 24 October

Visa's 24 October 2026 rulebook carries two versions of the rule. One covers
disputes processed through 23 October; the other covers disputes processed from
24 October. Three differences matter here.

1. **Prior purchases can be at other merchants.** The new text says the card, or
   a token linked to it, was used "at one or more Merchants" in the two earlier
   transactions. The old text didn't include that phrase.
2. **Agent login IDs count.** The login ID element now "includes login IDs for an
   Agentic Payment Provider and Merchant's e-commerce site or application". It
   must be in clear text and "a value that the Cardholder recognizes".
3. **Device ID and device fingerprint now count as one element.** An acquirer
   can't submit both to make up the numbers.

## Why this matters for agent purchases

Visa's agent rules already say the cardholder is responsible for the agent's
actions "as if the Cardholder initiated the Transaction" (section 4.1.23.10; see
[our post on Visa's agent rules](/blog/visa-agent-rules-cardholder-responsible)). The evidence change builds on that.

Say a cardholder's agent made purchases through the same agent account for
months, and none of them were disputed. Then the cardholder disputes a new
agent purchase as fraud. The merchant can now point to the agent account's
history, even if the earlier purchases were at other merchants.

Our reading: the fraud route for "my agent bought this, but I didn't" is getting
narrower. That fits Visa's model, where the agent is the cardholder.

## The part that doesn't fit cleanly

The anchor elements are still about the cardholder's own device and network. The
rule says the IP address "must be the Cardholder's public IP address". The device
ID must identify "the Cardholder's device". Many agents run on the platform's
servers, not the cardholder's phone. For those purchases, the IP address and
device belong to the agent platform.

Does a platform's IP count as the cardholder's for an agentic transaction,
given that the provider "is considered to be the Cardholder"? The rule text
doesn't answer that. We expect merchants and acquirers to test it in
representment before Visa clarifies it.

## Where agent disputes go instead

If the fraud route narrows, problems with agent purchases end up in the
consumer dispute conditions:

- 13.1 Merchandise/Services Not Received
- 13.3 Not as Described or Defective
- 13.6 Credit Not Processed

Those cases aren't decided by who clicked "buy". They're decided by what happened
afterwards. Did the goods arrive? Did the seller refund? When did the cardholder
try to sort it out with the merchant?

The useful evidence there is delivery status, refund status with dates, and the
messages exchanged with the seller. A buying agent sees all of that first. Some
agents check it and record it. Others take the seller's word and tell the user
it's resolved.

## What to do with this

- **If you run an agent platform:** treat your account login as a dispute data
  element. Visa now names it. The rule requires a value the cardholder recognizes,
  so a display name or email shown to the user is better than an internal UUID.
- **If you build buying agents:** design for consumer disputes, not fraud
  disputes. Log delivery checks, refund checks and seller messages with
  timestamps. Make sure the final report to the user reflects what the payment
  system shows, not what the seller said.
- **If you're a merchant:** agent-placed orders with a stable agent login and a
  clean history are now easier to defend against "I didn't do this". The
  "it never arrived" claim is unchanged.

## What we test

Chargeback OS tests the after-payment behaviour that consumer disputes depend on.
Does the agent verify delivery before asking for a refund? Does it check refund
status independently instead of trusting a seller's screenshot? Does its final
claim match what happened? It runs with synthetic money and your own agent. You
can see a scripted example at https://chargebackos.dev/example.

## Sources
- Visa Core Rules and Visa Product and Service Rules, 24 October 2026:
  Dispute Condition 10.4 Invalid Disputes (ID 0030254, both effective-date
  versions and footnote 9), section 4.1.23.10, glossary "Cardholder".
  https://usa.visa.com/dam/VCOM/download/about-visa/visa-rules-public.pdf
- This is our reading of public rule text, not legal advice.
