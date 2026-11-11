# PayoutGuard web

Use Node.js 22.12 or newer. From the Git repository root:

```sh
cd web && npm ci
VITE_USE_MOCK=true npm run dev
```

Open the URL printed by Vite. Click **Audit this week's run**, open a verdict, then click **Approve & pay with PayPal**. Once the batch settles, select **Review evidence** on Priya's reply and watch the $650 top-up settle. **Reset** starts a fresh run.

Mock mode uses `src/mock/audit_results.json` and serves the synthetic source documents under `/files/`. Batch IDs and payout statuses are simulated.

Run `npm run build` to produce `dist/`. Production always calls the same-origin FastAPI endpoints. Vite proxies those same paths to port 8000 during development when mock mode is off.
