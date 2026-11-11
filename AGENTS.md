# PayoutGuard — Build Spec (PayPal AI Hackathon)

> Hand this file to Claude Code / Codex as the source of truth. Copy it to `CLAUDE.md` and `AGENTS.md` at the repo root.

**Deadline:** Thu 12 Nov 2026 12:00 PT = **Fri 13 Nov 04:00 SGT**. Internal target: submitted by **Fri 01:00 SGT**.
**One-liner:** PayPal moves the money. PayoutGuard makes sure it's the right amount, to the right person, before it leaves.

## 1. The story (what the video must show)
Brightloom Studio (fictional) pays 8 freelance contractors every Friday via **PayPal Payouts**. This week's run: 8 invoices, **$25,580** billed, messy filenames (`scan_0042.pdf`, `IMG_1234.jpg`, `Document (3).pdf`).

PayoutGuard audits every invoice against the contract terms, the rate card, payout history and the AP inbox, then:

| Invoice | Contractor | Billed | Verdict | Paid | Why |
|---|---|---|---|---|---|
| scan_0042.pdf | Priya Nair | 4,850 | SHORT_PAY | 4,200 | $650 rush surcharge has no written PM pre-approval (cl. 4.3) |
| Document (3).pdf | Diego Ramos | 1,440 | HOLD_DUPLICATE | 0 | Same work as INV-117 paid 30 Oct, re-numbered INV-0117 (cl. 3) |
| invoice.pdf | Tom Okafor | 9,400 | QUARANTINE_PAYEE | 0 | Payee changed by email to look-alike domain okafor-edits.co, ~3–4× usual amount, urgency (cl. 5) |
| 5 others | — | 9,890 | PAY | 9,890 | Clean |

**Batch:** $14,090 paid via one PayPal Payouts batch, **$11,490 stopped**.
**Twist (appeal):** Priya replies with a forwarded PM email dated *before* the work approving a flat $650 → AI re-checks clause 4.3 → **automatic $650 top-up payout**. The agent is fair in both directions.

Ground truth: `data/expected_labels.json`. Everything in `data/` is synthetic and fictional — say so on screen.

## 2. Acceptance criteria (Must)
1. **Audit:** one click audits all 8 inbox files with OpenAI (multimodal PDF + JPG) and produces the verdicts above (8/8 correct, stable on 3 consecutive runs).
2. **Evidence:** every non-PAY verdict shows clause number, plain-English reason and quoted evidence; source document viewable beside it.
3. **Guardrails:** deterministic checks (payee email ≠ vendor master, duplicate = same amount + same invoice number/description, line-sum ≠ total) can only *raise* severity. Shown as "Rule check" badges next to "AI finding".
4. **Human approval:** "Approve & pay" button. Nothing moves before it.
5. **Real PayPal Payouts:** one sandbox batch (`POST /v1/payments/payouts`), each item with `note` = remittance note. Item statuses visible in the UI.
6. **Status updates:** PayPal **webhook** listener (`PAYMENT.PAYOUTSBATCH.SUCCESS`, `PAYMENT.PAYOUTS-ITEM.SUCCEEDED`, `.UNCLAIMED`, `.FAILED`) updates UI; fallback poll `GET /v1/payments/payouts/{id}` every 5 s if no webhook.
7. **Appeal loop:** "Contractor replied" event loads `data/appeals/priya_reply.eml` → AI appeal check → TOP_UP → second Payouts batch (1 item, $650) → card turns "Paid in full after evidence".
8. **Counters:** "Billed / Paying / Stopped" + after appeal "Protected: $10,840".
9. **Judge-runnable:** hosted URL, no login, "Reset demo" button (new `sender_batch_id` each run). README with local run steps and `.env.example`. MIT licence visible in repo About. No secrets committed.

**Should** (only after Must is green): upload your own invoice; streaming verdicts one-by-one; short audit trail export (CSV).
**Won't:** auth, ERP integrations, real money, multi-currency, AG Grid, Vault, disputes.

## 3. Architecture (keep it boring)
- **Backend:** Python 3.11+, FastAPI. Reuse logic from `scripts/audit_test.py` (Provider wrapper, schemas, prompts, guardrails) and `scripts/payouts_test.py` (token, create batch, get batch). Move into `app/audit.py`, `app/paypal.py`.
- **State:** SQLite or a JSON file per run (`runs/{run_id}.json`). One run at a time is fine.
- **AI:** OpenAI Responses API (`client.responses.parse` with Pydantic `text_format`, PDFs as `input_file`, photos as `input_image`). Model from `OPENAI_MODEL`. Keep the `Provider` wrapper so `AI_PROVIDER=gemini` still works as a backup.
- **Cache:** cache audit results per file hash so re-runs in the video are fast and identical; "Re-audit live" button bypasses cache.
- **Frontend:** React + Vite + Tailwind, built to static and served by FastAPI. One page.
- **Hosting:** Render (also makes us eligible for the Render sponsor prize) or Cloud Run. Webhook URL = `https://<host>/webhooks/paypal`; register it on the sandbox REST app (Features → Sandbox Webhooks).

### API
| Method | Path | Does |
|---|---|---|
| POST | `/api/runs` | create run from `data/inbox`, returns run_id |
| POST | `/api/runs/{id}/audit` | audit all invoices (SSE stream or poll) |
| POST | `/api/runs/{id}/approve` | create Payouts batch for PAY + SHORT_PAY items |
| POST | `/api/runs/{id}/appeals/{file}` | run appeal; on TOP_UP, send 1-item batch |
| GET | `/api/runs/{id}` | full state for UI |
| POST | `/webhooks/paypal` | verify (`/v1/notifications/verify-webhook-signature`) → update item status |
| POST | `/api/reset` | new run |
| GET | `/files/{name}` | serve source docs for preview |

## 4. UI — one screen, three moments (design brief for Jaz)
Not a generic dashboard. It should feel like a **payment run**: documents physically become payments.
1. **Inbox → audit:** left: stack of 8 document thumbnails (real messy filenames). Click "Audit run" → each turns into a verdict card (green PAY, amber SHORT_PAY, red HOLD/QUARANTINE). Top bar: Billed $25,580 · Paying · Stopped (animate as cards resolve).
2. **Card detail:** source doc on the left, findings on the right: clause chip, evidence quote highlighted, AI finding vs Rule check badges, remittance note preview.
3. **Approve & pay:** batch tray shows the 6 payable items → PayPal batch ID → item statuses tick to SUCCESS live (webhook). Then "Contractor replied" toast → appeal reasoning → $650 top-up ticks to SUCCESS → "Protected: $10,840".
Small footer: "Synthetic data · PayPal sandbox · OpenAI".

## 5. Video (≤ 3:00)
| Time | Beat |
|---|---|
| 0:00–0:15 | Hook: "Every Friday, platforms send thousands of PayPal payouts. 1.5% of disbursements go out duplicate or wrong (APQC median). This is the run where one of them is a scammer." |
| 0:15–0:30 | Who: finance ops at agencies/marketplaces paying contractors via PayPal Payouts. |
| 0:30–1:30 | Audit run: 8 messy files → verdicts. Zoom on the scam (look-alike domain + email), the duplicate, the $650 short-pay with clause. |
| 1:30–2:05 | Approve → real PayPal Payouts batch → statuses go SUCCESS → money visible in a sandbox recipient account. |
| 2:05–2:40 | Priya appeals → AI checks the PM approval date + amount → $650 top-up lands. "Not a blocker. A fair auditor." |
| 2:40–3:00 | Architecture in one frame (OpenAI + guardrails + Payouts + webhooks), what's real vs synthetic, next step. |

## 6. Timeline (SGT)
| When | Who | What |
|---|---|---|
| Tue 10 Nov night | Jaz | Run both kill tests (`scripts/`). Create sandbox personal accounts for recipient emails (at least 3). |
| Tue night | Claude Code | Scaffold repo: FastAPI + audit/paypal modules + run state + API. |
| Wed 11 Nov evening | Claude Code | Approve → Payouts, webhook + poll fallback, appeal endpoint. Deploy to Render early. |
| Wed evening | Codex | Frontend against mocked `out/audit_results.json` (parallel). |
| Thu 12 Nov day | Jaz + both | Wire UI to API, polish, 3× stable runs, README, licence, Devpost text. |
| Thu 20:00 | Jaz | Record video (have a pre-recorded fallback take). |
| Thu 23:00 | Jaz | **Upload to YouTube** (processing takes hours). Submit Devpost draft. |
| Fri 01:00 | Jaz | Final submit. Stop touching everything. |

**Cut order if behind:** Should items → streaming → webhook (keep poll) → appeal UI polish (keep appeal logic) → never cut real Payouts batch or the evidence view.

## 7. Risks
| Risk | Mitigation |
|---|---|
| Payouts blocked in sandbox | Kill test tonight. Fallback: contractors' invoices via Invoicing API paid from sandbox buyer (weaker). |
| AI misreads a photo / flaky verdict | structured outputs, guardrails, cache per file hash, 3-run stability check. |
| Webhooks need public URL | Deploy early; poll fallback. |
| "It's just AP software" (Innovation) | Lead with appeal loop + PayPal-native payee-change quarantine; say incumbents serve ERP AP, we serve platforms paying via Payouts. |
| Judges don't run it | Video carries it; hosted URL works anyway. |

## 8. Devpost checklist
- [ ] Text: problem, who, how it works, PayPal APIs (Payouts batch, Payouts item status, Webhooks, OAuth), AI (OpenAI multimodal + structured output + guardrails), what's real vs synthetic.
- [ ] Tools section: OpenAI (which model, how used); coding assistants disclosed.
- [ ] Public repo, MIT licence in About, README run steps, `.env.example`, no secrets.
- [ ] Hosted demo URL works in an incognito window.
- [ ] Public YouTube video < 3:00.
