"""Seed the committed audit cache (data/audit_cache.json) from one live run.

  python tools/seed_cache.py              # live audit of all 8 invoices + the appeal, then write
  python tools/seed_cache.py --use-cache  # allow out/cache hits instead of calling the AI
  python tools/seed_cache.py --dry-run    # score only, write nothing

The committed cache is keyed by file hash (and "appeal-<file>"), so AUDIT_MODE=cached serves the
hosted demo with no AI calls and identical verdicts. Nothing is written unless every verdict matches
data/expected_labels.json and the appeal returns TOP_UP 650.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audit import (  # noqa: E402
    DATA, audit_appeal, audit_file, committed_write, context_text, file_hash, find_appeal_file,
    inbox_files, load_env, load_expected, load_history, load_vendor_master, make_provider,
)

SHORT_PAY_FILE = "scan_0042.pdf"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-cache", action="store_true", help="allow out/cache hits")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    load_env()

    provider = make_provider()
    ctx, vm, history, expected = context_text(), load_vendor_master(), load_history(), load_expected()
    mode = "cached" if args.use_cache else "live"
    print(f"Seeding from a {mode} run | provider {provider.name} | model {provider.model}\n")

    entries, audits, ok_count = {}, {}, 0
    for p in inbox_files():
        t0 = time.time()
        audit = audit_file(p, provider, mode=mode, ctx=ctx, vm=vm, history=history)
        cache = audit.pop("_cache", {}) or {}
        audit["file"] = p.name
        audits[p.name] = audit
        exp = expected.get(p.name)
        ok = bool(exp) and audit["decision"] == exp["decision"] \
            and abs(audit["approved_amount"] - exp["approved_amount"]) < 1
        ok_count += ok
        print(f"[{'PASS' if ok else 'FAIL'}] {p.name:<22} {audit['decision']:<17}"
              f"{audit['approved_amount']:>9,.2f}  ({time.time() - t0:.1f}s, {cache.get('source', 'ai')})")
        if not ok and exp:
            print(f"       expected {exp['decision']} {exp['approved_amount']:,.2f}")
        entries[file_hash(p)] = audit

    appeal_ok = False
    short = audits.get(SHORT_PAY_FILE)
    if short and short["decision"] == "SHORT_PAY":
        reply = find_appeal_file(short.get("contractor_name", ""))
        res = audit_appeal(short, reply.read_text(errors="replace"), provider, vm, mode=mode)
        res.pop("_cache", None)
        res["_for_file"] = SHORT_PAY_FILE
        appeal_ok = res["decision"] == "TOP_UP" and abs(res["top_up_amount"] - 650) < 1
        print(f"\n[{'PASS' if appeal_ok else 'FAIL'}] appeal {reply.name:<15} {res['decision']} "
              f"{res['top_up_amount']:,.2f}")
        entries[f"appeal-{SHORT_PAY_FILE}"] = res
    else:
        print(f"\n[FAIL] {SHORT_PAY_FILE} is not SHORT_PAY, so there is no appeal to seed")

    total = len(audits)
    print(f"\nScore: {ok_count}/{total} invoices, appeal {'PASS' if appeal_ok else 'FAIL'}")
    if ok_count != total or not appeal_ok:
        print("NOT SEEDED — fix the FAIL lines first; the committed cache must match the ground truth.")
        return 1
    if args.dry_run:
        print("DRY RUN — nothing written.")
        return 0
    committed_write(entries)
    out = DATA / "audit_cache.json"
    print(f"Wrote {len(entries)} entries to {out.relative_to(out.parent.parent)} "
          f"({out.stat().st_size / 1024:.1f} KiB). Commit it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
