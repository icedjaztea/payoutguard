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

`AUDIT_MODE=live` calls the AI on a cache miss and writes the result to `out/cache/<file-hash>-<prompt-scope>.json`.
`AUDIT_MODE=cached` never calls the AI: it reuses `out/cache`, falling back to `out/audit_results.json` from the
kill test. `POST /api/runs/{id}/audit?force=true` is the "Re-audit live" button.

### Local flow

```bash
BASE=http://localhost:8000
RID=$(curl -s -X POST $BASE/api/runs | python -c 'import sys,json;print(json.load(sys.stdin)["run_id"])')
curl -s -X POST $BASE/api/runs/$RID/audit | python -m json.tool          # 8 verdicts
curl -s -X POST "$BASE/api/runs/$RID/approve?dry_run=true"               # inspect the Payouts body
curl -s -X POST $BASE/api/runs/$RID/approve                              # sends ONE real sandbox batch
curl -s "$BASE/api/runs/$RID?refresh=1"                                  # re-poll item statuses
curl -s -X POST $BASE/api/reset                                          # new run, new sender_batch_id
```

### Tests

```bash
pytest -q        # guardrails + Payouts request body, no network
```
