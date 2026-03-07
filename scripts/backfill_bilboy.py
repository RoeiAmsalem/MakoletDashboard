"""
Backfill BilBoy goods expenses for a date range.

Usage:
    python3 scripts/backfill_bilboy.py                     # defaults: 2026-03-01 to today
    python3 scripts/backfill_bilboy.py 2026-02-01 2026-02-28   # custom range
"""

import logging
import os
import sys
from datetime import date

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db import get_connection, init_db, insert_expense

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(message)s")

API_BASE = "https://app.billboy.co.il:5050/api"


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
        end = date.today()

    print(f"Backfilling BilBoy: {start} to {end}\n")

    # ── Step 1: Get branch ID ──
    resp = session.get(f"{API_BASE}/user/branches", timeout=30)
    if resp.status_code == 401:
        print("ERROR: BilBoy token expired (401)")
        return
    resp.raise_for_status()
    branches = resp.json()
    first = branches[0] if isinstance(branches, list) else branches
    branch_id = str(first.get("branchId") or first.get("id") or "")
    print(f"Branch ID: {branch_id}")

    # ── Step 2: Get supplier IDs (filter out franchise) ──
    resp = session.get(
        f"{API_BASE}/customer/suppliers",
        params={"customerBranchId": branch_id, "all": "true"},
        timeout=30,
    )
    resp.raise_for_status()
    raw_suppliers = resp.json()
    suppliers_list = raw_suppliers.get("suppliers") if isinstance(raw_suppliers, dict) else raw_suppliers
    if not suppliers_list:
        print("ERROR: No suppliers returned from API")
        return

    keep_ids = []
    skipped_franchise = 0
    for s in suppliers_list:
        name = s.get("title") or s.get("name") or s.get("supplierName") or ""
        sid = str(s.get("id") or s.get("supplierId") or "")
        if "זיכיונות המכולת" in name:
            skipped_franchise += 1
            print(f"  SKIP supplier: {name} (franchise)")
            continue
        if sid:
            keep_ids.append(sid)
    suppliers_csv = ",".join(keep_ids)
    print(f"Suppliers: {len(keep_ids)} active, {skipped_franchise} franchise filtered\n")

    # ── Step 3: Fetch invoices for the full range ──
    resp = session.get(
        f"{API_BASE}/customer/docs/headers",
        params={
            "suppliers": suppliers_csv,
            "branches": branch_id,
            "from": f"{start.isoformat()}T00:00:00",
            "to": f"{end.isoformat()}T00:00:00",
        },
        timeout=30,
    )
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
    skipped_dup = 0

    for inv in all_invoices:
        raw_date = inv.get("date") or inv.get("documentDate") or ""
        inv_date = str(raw_date)[:10]
        supplier = inv.get("supplierName") or ""
        amount = float(inv.get("totalWithVat") or inv.get("totalAmount") or inv.get("amount") or 0)
        doc_number = str(inv.get("refNumber") or inv.get("number") or "")
        description = supplier or doc_number or "BilBoy invoice"

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
        print(f"  SAVED: {inv_date} | {supplier:<30s} | {amount:>10,.2f} | #{doc_number}")

    # Summary
    print(f"\n{'='*70}")
    print(f"SUMMARY: {saved} saved, {skipped_dup} duplicates skipped")
    print(f"Total invoices from API: {len(all_invoices)}")


if __name__ == "__main__":
    main()
