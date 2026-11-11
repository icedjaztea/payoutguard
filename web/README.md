# PayoutGuard web

Use Node.js 22.12 or newer. From the Git repository root:

```sh
cd web && npm ci
npm run dev
```

Open the local URL printed by Vite. Click **Audit run**, open a flagged invoice, then click **Approve & pay**. Once all six items settle, click **Review reply** in the contractor notification and watch the $650 top-up settle. **Reset demo** starts a new run.

The default mode reads the synthetic verdicts and appeal from `../out/audit_results.json`. Fresh checkouts use the bundled snapshot of that file because `out/` is Git-ignored. Payout statuses and batch IDs in this mode are simulated.

## FastAPI mode

Set `VITE_API_BASE=http://127.0.0.1:8000` in `web/.env.local` and restart Vite. Set `VITE_API_BASE=/` when FastAPI serves the frontend on the same origin. The value is built into the bundle, so run `npm run build` after changing it.

The adapter uses the run creation, audit, approval, appeal, run read, reset and file endpoints in AGENTS.md. It reads the FastAPI run's `invoices` map and PayPal batch status, and requests a refresh every five seconds while payouts are outstanding. The backend receives PayPal webhooks and owns payout creation. The appeal UI expects a decision and, for `TOP_UP`, the top-up batch in the run state.
