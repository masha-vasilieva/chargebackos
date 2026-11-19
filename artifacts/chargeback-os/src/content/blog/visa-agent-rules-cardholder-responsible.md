---
title: Visa's new agent rules make the cardholder answer for the agent. Here is what that means for disputes.
date: 2026-11-19
summary: Visa's agent rules make the cardholder answer for the agent's purchases, and add no new dispute path for agent mistakes.
---

Visa's public rulebook now has a section for AI agents that buy things. It
is section 4.1.23, "Agentic Platform Requirements", and it is still in the
24 October 2026 edition. Much of it is about onboarding: registration, tokens,
Visa Intelligent Commerce. The part that matters most for anyone building or
buying a purchasing agent is shorter. It is one sentence about
who is responsible when the agent acts.

## What Visa defined

The rules introduce two roles. An *Agentic Payment Provider* is the application a
cardholder uses to search for and buy things on their behalf. An *Agentic Payment
Enabler* connects that provider to Visa. An *Agentic Transaction* is an
e-commerce purchase the provider makes from the cardholder's instructions,
without the cardholder dealing with the merchant directly. That includes
purchases made later and repeated purchases.

Two definitions stand out. The glossary says the provider "is considered to be
the Cardholder for an Agentic Transaction". And it says initiators of agentic
transactions "are not considered Merchants". So in Visa's model the agent sits on
the buyer's side of the transaction, not the seller's.

## What the provider has to do

Before the first purchase (4.1.23.3), the provider must get the cardholder's
consent to use their card and their "Cardholder-defined payment instruction". It
must state when that instruction expires. It must verify the cardholder's
identity. And it must get the cardholder to acknowledge that they are
responsible for what the provider does.

During a purchase (4.1.23.4), the provider must "use only the Cardholder-defined
criteria" and pass the cardholder's name, address and email to the merchant.

After a purchase (4.1.23.8), the provider must keep an order confirmation
available for at least 120 days. It must show what was bought, how to contact
the merchant, the total, the currency and the refund policy.

The provider also accepts merchant terms on the cardholder's behalf (4.1.23.6):
limited refund policies, delayed charges, estimated amounts, upsells. Consent can
come at checkout, or in advance when the instructions are first captured.

## The sentence that matters

Section 4.1.23.10 reads: "A Cardholder is responsible for any actions taken by an
Agentic Payment Provider as part of an Agentic Transaction as if the Cardholder
initiated the Transaction."

Put that next to the provider definition, and the model is consistent. The agent
acts as the cardholder. Its purchases are the cardholder's purchases.

## What the rules don't add

There is no new dispute condition for agent purchases. Section 11 of the
rulebook, which governs disputes, has no agentic reason code. The only agentic
change there is to fraud evidence. We cover that change in a separate post.
A merchant still "must not require a Cardholder to waive the right to dispute a
Transaction, including an Agentic Transaction".

So agent purchases go through the same dispute conditions as everything else.
That works well for some problems and poorly for others.

It works well when the merchant is at fault. If the agent bought within its
instructions and the goods never arrived, that is Dispute Condition 13.1,
Merchandise/Services Not Received. If a refund was issued on paper but never
processed, that is 13.6, Credit Not Processed. Those conditions are about the
merchant's behaviour, and nothing about the agent changes them.

It works poorly when the agent is at fault. If the agent bought something
outside its instructions, the rule says the provider should not have done that
(4.1.23.4). But the cardholder is responsible "as if the Cardholder initiated
the Transaction". The dispute section offers no condition for "my agent
misread me". Visa can disqualify a provider (4.1.23.11). That is enforcement
against the provider. It doesn't refund the cardholder.

## What this means if you build a buying agent

Under these rules, the cardholder's remaining protections mostly depend on what
the merchant did after payment. Did the goods arrive? Did the refund actually
post? The cardholder can only use those protections if someone notices and
acts within the time limits. For 13.1 that is generally 120 days.

In an agent purchase, the first one to see the problem is often the agent. It
checked the order status and got a delivery failure. It received the seller's
message saying "refund complete". What the agent checks, what it records, and
what it tells the person decide whether the cardholder ever uses the rights
they still have.

One open question is worth raising with your acquirer or issuer. Before a 13.1
dispute, the cardholder "must attempt to resolve the Dispute with the Merchant".
If the provider "is considered to be the Cardholder", does the agent's contact
with the merchant count as that attempt? The rules don't say.

## What we test

Chargeback OS is a sandbox for this after-payment part. You connect your own
agent and it buys from a synthetic seller. Some sellers deliver honestly. Others
never deliver, claim a refund that never happened, or push for payment outside
checkout. We record whether the agent checked delivery, checked refund status
with the payment system rather than taking the seller's word, stayed within its
authority, and told you something true at the end. All money is synthetic, and
the results are observations of your runs, not a certification.

You can see a scripted example without any setup: https://chargebackos.dev/example.

## Sources
- Visa Core Rules and Visa Product and Service Rules, 24 October 2026:
  sections 4.1.23.1–4.1.23.11, glossary ("Agentic Payment Provider", "Agentic
  Transaction", "Cardholder"), ID 0008771, Dispute Conditions 13.1 and 13.6.
  https://usa.visa.com/dam/VCOM/download/about-visa/visa-rules-public.pdf
