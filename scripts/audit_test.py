"""Kill test #2 — can the AI audit messy invoices against the contract and history?

Usage
  pip install -r requirements.txt
  python scripts/audit_test.py                  # audit all 8 invoices + run the appeal, score vs ground truth
  python scripts/audit_test.py --no-guardrails  # pure AI, no deterministic checks
  python scripts/audit_test.py --only scan_0042.pdf
  python scripts/audit_test.py --provider gemini   # backup provider
  python scripts/audit_test.py --no-cache          # ignore out/cache, always call the AI

Provider (in .env or the environment):
  AI_PROVIDER=openai (default) with OPENAI_API_KEY=...  OPENAI_MODEL=<newest model you have that reads PDFs + images>
  AI_PROVIDER=gemini with GEMINI_API_KEY=...  GEMINI_MODEL=...   (or the Vertex env vars)

PASS = all 8 decisions + approved amounts match data/expected_labels.json, and the appeal returns TOP_UP $650.
Results are saved to out/audit_results.json for the app to reuse.

All the logic lives in app/audit.py — this script is just the scoreboard.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audit import (  # noqa: E402
    DATA, OUT, apply_guardrails, audit_appeal, audit_file, context_text, guardrails, inbox_files,
    load_env, load_expected, load_history, load_vendor_master, make_provider,
)

__all__ = ["apply_guardrails", "guardrails", "context_text"]  # importable from the old location


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-guardrails", action="store_true")
    ap.add_argument("--only", help="audit a single inbox file name")
    ap.add_argument("--model", default=None)
    ap.add_argument("--provider", choices=["openai", "gemini"], default=None)
    ap.add_argument("--no-cache", action="store_true", help="ignore out/cache and re-audit live")
    args = ap.parse_args()
    load_env()

    provider = make_provider(args.provider, args.model)
    ctx = context_text()
    vm = load_vendor_master()
    history = load_history()
    expected = load_expected()

    files = inbox_files()
    if args.only:
        files = [p for p in files if p.name == args.only]
    print(f"Provider: {provider.name} | model: {provider.model} | guardrails: "
          f"{'off' if args.no_guardrails else 'on'} | invoices: {len(files)}\n")

    results, passed = {}, 0
    for p in files:
        t0 = time.time()
        audit = audit_file(p, provider, use_guardrails=not args.no_guardrails, mode="live",
                           ctx=ctx, vm=vm, history=history, force=args.no_cache)
        cached = (audit.pop("_cache", None) or {}).get("hit")
        audit["file"] = p.name
        results[p.name] = audit
        exp = expected.get(p.name)
        ok = exp and audit["decision"] == exp["decision"] and abs(audit["approved_amount"] - exp["approved_amount"]) < 1
        passed += bool(ok)
        flag = "PASS" if ok else "FAIL"
        print(f"[{flag}] {p.name:<22} {audit['contractor_name']:<14} billed {audit['billed_total']:>9,.2f}  "
              f"-> {audit['decision']:<16} {audit['approved_amount']:>9,.2f}   "
              f"({time.time() - t0:.1f}s{', cached' if cached else ''})")
        if not ok and exp:
            print(f"       expected {exp['decision']} {exp['approved_amount']:,.2f}")
        for f in audit["findings"]:
            print(f"       - {f['code']} (cl. {f.get('clause')}): {f['explanation']}")
        for g in audit.get("guardrail_flags", []):
            print(f"       ! guardrail: {g}")

    # ---- appeal round for the short-paid invoice
    appeal_ok = None
    if "scan_0042.pdf" in results and results["scan_0042.pdf"]["decision"] == "SHORT_PAY":
        a = results["scan_0042.pdf"]
        appeal = (DATA / "appeals" / "priya_reply.eml").read_text()
        res = audit_appeal(a, appeal, provider, vm)
        results["_appeal_scan_0042.pdf"] = res
        appeal_ok = res["decision"] == "TOP_UP" and abs(res["top_up_amount"] - 650) < 1
        print(f"\n[{'PASS' if appeal_ok else 'FAIL'}] appeal (Priya)        -> {res['decision']} "
              f"{res['top_up_amount']:,.2f}\n       {res['reasoning']}")

    OUT.mkdir(exist_ok=True)
    out_file = OUT / "audit_results.json"
    # A --only run must not truncate the file: the app falls back to it in AUDIT_MODE=cached.
    merged = {}
    if args.only and out_file.exists():
        try:
            merged = json.loads(out_file.read_text())
        except json.JSONDecodeError:
            merged = {}
    merged.update(results)
    out_file.write_text(json.dumps(merged, indent=2))
    total = len(files)
    print(f"\nScore: {passed}/{total} invoices correct; appeal: "
          f"{'n/a' if appeal_ok is None else ('PASS' if appeal_ok else 'FAIL')}")
    print("Saved out/audit_results.json")
    all_ok = passed == total and (appeal_ok in (True, None) if args.only else appeal_ok)
    print("\nKILL TEST:", "PASS" if all_ok else "NOT YET — check FAIL lines; tighten prompt or rely on guardrails")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
