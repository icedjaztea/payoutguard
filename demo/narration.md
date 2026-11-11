# PayoutGuard demo narration

One line per scene, in order, as spoken in `demo/demo-payoutguard.json`. Numbers are spelled out
because the narration is synthesized — the voice reads "$25,580" badly and "twenty five thousand,
five hundred and eighty dollars" correctly. Burned-in captions are transcribed from the audio, so
they follow these words.

Every claim here is checkable against the app: the verdicts and clause numbers come from
`data/audit_cache.json` (seeded from a live audit that matches `data/expected_labels.json`), and the
two batch ids shown on screen are real PayPal sandbox Payouts batches.

## 1 — Hook (0:00–0:15)

> Every Friday, platforms pay hundreds of contractors through PayPal Payouts.

> The median company loses about one and a half percent of its disbursements to duplicate or wrong
> payments. That is A P Q C, twenty twenty four. This week's run has a scammer in it.

## 2 — Who it's for (0:15–0:25)

> This is built for finance operations at agencies and marketplaces, the people who pay contractors
> through PayPal Payouts. Eight documents came in this week.

## 3 — The audit (0:25–1:20)

> I'll run the audit. Every invoice is checked against the contract, the payee list, and the payment
> history.

> Eight verdicts come back. Five are clean. Three are exceptions, and each one names the clause it
> failed.

> Start with the biggest one. Tom's invoice asks to be paid at a new address, and the request arrived
> by email, from a look-alike domain.

> No payee change is on file, so clause five quarantines the payee. Nine thousand four hundred
> dollars, held.

> Next, the same October blog series, billed a second time. The invoice number gained a zero, but
> clause three still catches the duplicate.

> Priya's invoice is mostly right. Her hours and her rate check out. But a six hundred and fifty
> dollar rush surcharge has no written approval, so clause four point three short pays it.

> Twenty five thousand, five hundred and eighty dollars billed this week. Fourteen thousand and
> ninety of it is payable.

## 4 — Approve and pay (1:20–1:55)

> Now the payout. One click sends a single PayPal Payouts batch, carrying only the invoices that
> passed.

> That is a real batch id from the PayPal Payouts API. Six items, each one carrying its own
> remittance note.

> PayPal's webhooks report back, and every item turns to success. Fourteen thousand and ninety
> dollars paid, in one batch. Eleven thousand four hundred and ninety stopped before it left.

## 5 — The appeal (1:55–2:25)

> Then Priya replies. Her project manager approved the rush in writing, and the message is dated
> before the work started.

> The audit reads that reply against clause four point three, accepts it, and releases a six hundred
> and fifty dollar top up. A second real batch.

> Not a blocker. A fair auditor. Ten thousand eight hundred and forty dollars protected.

## 6 — Close (2:25–2:40)

> PayPal moves the money. PayoutGuard makes sure it is the right amount, to the right person.

> Built with OpenAI, PayPal Payouts and webhooks. Synthetic data, on the PayPal sandbox.

## Source for the one statistic

The 1.5% figure is APQC's 2024 Open Standards Benchmarking median for duplicate or erroneous
payments as a share of annual disbursements (top performers ~0.8%, bottom ~2.0%). It was checked
against a secondary source that cites that APQC study, not against the APQC publication itself.
