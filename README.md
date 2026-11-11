# PayoutGuard — kill-test kit

Two tests to run **tonight** before building anything. If both pass, hand `BUILD_SPEC.md` to Claude Code / Codex.

## 0. Setup (5 min)
```bash
cp .env.example .env          # fill in sandbox PayPal creds + OpenAI key
pip install -r requirements.txt
```

## 1. PayPal Payouts in sandbox (~20 min)
```bash
python scripts/payouts_test.py --dry-run   # see the request
python scripts/payouts_test.py             # real sandbox batch, polls until done
```
PASS = batch `SUCCESS`, items `SUCCESS` or `UNCLAIMED`.
`UNCLAIMED` only means no sandbox account exists for that email yet. For the demo, create sandbox **personal** accounts
(Developer Dashboard → Testing Tools → Sandbox Accounts → Create account) using the emails in `data/vendor_master.json`,
or put your own sandbox personal emails in `PAYOUT_TEST_RECIPIENTS`.

## 2. AI audit (~20 min)
```bash
python scripts/audit_test.py                    # OpenAI (default)
python scripts/audit_test.py --provider gemini  # backup, optional
```
PASS = `Score: 8/8` and appeal `TOP_UP 650`. Run it 3 times; it must be stable.
Try `--no-guardrails` to see what the AI gets right on its own. Results land in `out/audit_results.json`
(Codex can build the UI against that file).

## Data (all fictional)
| File | Contractor | Trap |
|---|---|---|
| `scan_0042.pdf` | Priya Nair | $650 rush surcharge without PM pre-approval → short-pay $4,200 |
| `Document (3).pdf` | Diego Ramos | Re-numbered duplicate of INV-117 (paid 30 Oct) |
| `invoice.pdf` + `RE_ payment details update.eml` | Tom Okafor | Payee change to look-alike domain, $9,400 |
| 5 others (2 are phone photos) | — | Clean |
| `appeals/priya_reply.eml` | Priya | PM approval dated 30 Oct → $650 top-up |

Regenerate with `python tools/generate_data.py`.


## Backend (FastAPI)

```bash
pip install -r requirements.txt
cp .env.example .env            # fill in the sandbox + OpenAI keys
uvicorn app.main:app --reload --port 8000
```

`app/audit.py` holds the provider wrapper, schemas, prompts and guardrails (shared with
`scripts/audit_test.py`); `app/paypal.py` holds the token / create-batch / get-batch calls (shared with
`scripts/payouts_test.py`); `app/store.py` keeps run state as JSON in `runs/`.

`AUDIT_MODE=cached` (the default) serves a cached verdict when there is one and otherwise calls the AI and caches
it; `AUDIT_MODE=live` always calls the AI. Lookup order: `out/cache/<file-hash>-<prompt-scope>.json`, then the
committed `data/audit_cache.json` (keyed by file hash), then the AI. `POST /api/runs/{id}/audit?force=true` is the
"Re-audit live" button.

`data/audit_cache.json` is committed so a fresh deploy answers instantly with the verdicts we tested. Re-seed it
after changing a prompt or model — it only writes when all 8 invoices match `data/expected_labels.json` and the
appeal returns TOP_UP 650:

```bash
python tools/seed_cache.py            # one live run, then write the committed cache
python tools/seed_cache.py --dry-run  # score only
```

`GET /api/runs/current` returns the active run, creating one from `data/inbox` if there is none, and `web/dist` is
served at `/` with an SPA fallback when it has been built (`cd web && npm run build`). CORS is open to
`localhost:5173` for `npm run dev`.

### Deploy

`render.yaml` describes one free Docker web service with health check `/api/health`, `AUDIT_MODE=cached`, and
`PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `OPENAI_API_KEY`, `OPENAI_MODEL` left as dashboard secrets
(`sync: false`). The `Dockerfile` builds `web/` in a Node stage when it is present, then runs
`uvicorn app.main:app` on `$PORT`.

### Local flow

```bash
BASE=http://localhost:8000
RID=$(curl -s -X POST $BASE/api/runs | python -c 'import sys,json;print(json.load(sys.stdin)["run_id"])')
curl -s -X POST $BASE/api/runs/$RID/audit | python -m json.tool          # 8 verdicts
curl -s -X POST "$BASE/api/runs/$RID/approve?dry_run=true"               # inspect the Payouts body
curl -s -X POST $BASE/api/runs/$RID/approve                              # sends ONE real sandbox batch
curl -s "$BASE/api/runs/$RID?refresh=1"                                  # re-poll item statuses
curl -s -X POST "$BASE/api/runs/$RID/appeals/scan_0042.pdf?dry_run=true"  # appeal decision only
curl -s -X POST $BASE/api/runs/$RID/appeals/scan_0042.pdf                # TOP_UP -> second batch
curl -s -X POST $BASE/api/reset                                          # new run, new sender_batch_id
```

### Appeal loop

`POST /api/runs/{id}/appeals/{invoice-file}` re-checks a `SHORT_PAY` invoice against the contractor's reply in
`data/appeals/` (matched by contractor name, or name one with `?appeal_file=`). On `TOP_UP` it sends a second,
one-item Payouts batch for the withheld amount — capped at what was actually withheld — and the run's
`totals.protected` drops accordingly. `?dry_run=true` decides without sending. The run must have been approved
first, and a topped-up invoice cannot be appealed twice.

### Webhooks

`POST /webhooks/paypal` takes `PAYMENT.PAYOUTSBATCH.*` and `PAYMENT.PAYOUTS-ITEM.*` events and updates the stored
item/batch status for the main batch and any top-up batch. Register the URL at Developer Dashboard > your sandbox
app > Features > Sandbox Webhooks and put the webhook id in `PAYPAL_WEBHOOK_ID`: every event is then verified with
`/v1/notifications/verify-webhook-signature` and a failure changes nothing. Without that id an event cannot be
verified and is refused with 400 unless `PAYPAL_ALLOW_UNVERIFIED_WEBHOOKS=1` is set for local testing.
After any batch is created the server also polls `GET /v1/payments/payouts/{id}` every 5 s in a background thread
until the batch is final, so item statuses tick over even with no webhook registered; `GET /api/runs/{id}?refresh=1`
forces a re-read, and `GET /api/health` lists the batches currently being polled. On startup, if `PUBLIC_BASE_URL`
or `RENDER_EXTERNAL_URL` is set, the server finds-or-creates the sandbox webhook for `<url>/webhooks/paypal` over
the PayPal API and keeps its id in memory; any failure there is logged and polling carries the demo.

### Tests

```bash
pip install -r requirements-dev.txt
pytest -q        # guardrails, Payouts body, appeal loop, webhooks — no network
```

### Browser end-to-end

Drives the real UI through reset → audit → approve → appeal with Playwright, checking the
`data-testid` hooks, the counters and both PayPal batch ids. It sends two real sandbox batches.

```bash
playwright install chromium
cd web && npm run build && cd ..
uvicorn app.main:app --port 8000 &
python tools/e2e_browser.py --base http://localhost:8000     # --headed to watch, screenshots in out/e2e/
```

### Docker

```bash
docker build -t payoutguard:local .
docker run --rm -p 8200:8200 -e PORT=8200 --env-file .env payoutguard:local
```
