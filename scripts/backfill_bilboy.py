"""
Backfill BilBoy goods expenses for a date range.

Usage:
    python3 scripts/backfill_bilboy.py                     # defaults: 2026-03-01 to yesterday
    python3 scripts/backfill_bilboy.py 2026-02-01 2026-02-28   # custom range
"""

import logging
import os
import sys
from datetime import date, timedelta

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db import get_connection, init_db, insert_expense

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(message)s")

API_BASE = "https://app.billboy.co.il:5050/api"
SKIP_SUPPLIERS = ["זיכיונות המכולת"]


def main():
    import requests

    init_db()

    token = os.getenv("BILBOY_TOKEN", "")
    if not token:
        print("ERROR: BILBOY_TOKEN not set in .env")
        return

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"

    # Parse args or use defaults
    if len(sys.argv) >= 3:
        start = date.fromisoformat(sys.argv[1])
        end = date.fromisoformat(sys.argv[2])
    else:
        start = date(2026, 3, 1)
        end = date.today() - timedelta(days=1)

    print(f"Backfilling BilBoy: {start} to {end}\n")

    # Get branch ID and supplier IDs
    resp = session.get(f"{API_BASE}/user/branches", timeout=30)
    if resp.status_code == 401:
        print("ERROR: BilBoy token expired (401)")
        return
    resp.raise_for_status()
    branches = resp.json()
    first = branches[0] if isinstance(branches, list) else branches
    branch_id = str(first.get("branchId") or first.get("id") or "")
    supplier_ids = [str(s) for s in (first.get("suppliers") or [])]
    print(f"Branch ID: {branch_id} ({len(supplier_ids)} suppliers)")

    # Fetch invoices for the full range
    params = [("branches", branch_id), ("fromDate", start.isoformat()), ("toDate", end.isoformat())]
    for sid in supplier_ids:
        params.append(("suppliers", sid))

    resp = session.get(f"{API_BASE}/customer/docs/headers", params=params, timeout=30)
    if resp.status_code == 401:
        print("ERROR: BilBoy token expired (401)")
        return
    if not resp.ok:
        print(f"ERROR: API returned {resp.status_code}: {resp.text[:300]}")
        return
    raw = resp.json()
    all_invoices = raw if isinstance(raw, list) else (raw.get("data") or raw.get("docs") or raw.get("headers") or [])
    print(f"Fetched {len(all_invoices)} invoice(s) from API\n")

    saved = 0
    skipped_supplier = 0
    skipped_dup = 0

    for inv in all_invoices:
        supplier = inv.get("supplierName") or inv.get("description") or ""
        if any(s in supplier for s in SKIP_SUPPLIERS):
            skipped_supplier += 1
            continue

        raw_date = (inv.get("documentDate") or inv.get("date") or inv.get("docDate") or "")
        inv_date = str(raw_date)[:10]
        amount = float(inv.get("totalAmount") or inv.get("total") or inv.get("amount") or 0)
        description = supplier or inv.get("docNumber") or "BilBoy invoice"

        # Check for duplicate
        with get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM expenses WHERE date=? AND source='bilboy' AND amount=? AND description=?",
                (inv_date, amount, description),
            ).fetchone()[0]
        if count > 0:
            skipped_dup += 1
            continue

        insert_expense(
            date=inv_date,
            category="goods",
            amount=amount,
            description=description,
            source="bilboy",
        )
        saved += 1
        print(f"  SAVED: {inv_date} | {amount:>10,.2f} | {description}")

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY: {saved} saved, {skipped_dup} duplicates skipped, {skipped_supplier} franchise skipped")
    print(f"Total invoices from API: {len(all_invoices)}")


if __name__ == "__main__":
    main()
