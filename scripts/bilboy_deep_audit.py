#!/usr/bin/env python3
"""
BilBoy Deep Audit — full forensic comparison between SQLite DB and BilBoy API.

Run AFTER the Saturday 02:30 reconciliation:
    python3 scripts/bilboy_deep_audit.py

Exit code 0 = all matched, non-zero = discrepancies found.
"""

import os
import sys
from datetime import date, datetime

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from database.db import get_connection

API_BASE = "https://app.billboy.co.il:5050/api"
FRANCHISE_FILTER = "זיכיונות המכולת"


def get_api_invoices(token: str) -> list[dict]:
    """Fetch all invoices for the current month from BilBoy API."""
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})

    def api_get(path, params=None):
        resp = session.get(f"{API_BASE}{path}", params=params, timeout=30)
        if resp.status_code == 401:
            raise PermissionError("BilBoy token expired — renew BILBOY_TOKEN in .env")
        resp.raise_for_status()
        return resp.json()

    # Get branch
    branches = api_get("/user/branches")
    first = branches[0] if isinstance(branches, list) else branches
    branch_id = str(first.get("branchId") or first.get("id") or first.get("branch_id", ""))

    # Get suppliers (filter out franchise)
    raw = api_get("/customer/suppliers", params={
        "customerBranchId": branch_id, "all": "true",
    })
    suppliers = raw.get("suppliers") if isinstance(raw, dict) else raw
    keep_ids = []
    for s in (suppliers or []):
        name = s.get("title") or s.get("name") or s.get("supplierName") or ""
        sid = str(s.get("id") or s.get("supplierId") or "")
        if FRANCHISE_FILTER in name:
            continue
        if sid:
            keep_ids.append(sid)
    suppliers_csv = ",".join(keep_ids)

    # Fetch invoices for current month
    today = date.today()
    from_date = today.replace(day=1).isoformat()
    to_date = today.isoformat()

    raw = api_get("/customer/docs/headers", params={
        "suppliers": suppliers_csv,
        "branches": branch_id,
        "from": f"{from_date}T00:00:00",
        "to": f"{to_date}T00:00:00",
    })
    docs = raw if isinstance(raw, list) else (
        raw.get("data") or raw.get("docs") or raw.get("headers") or []
    )

    invoices = []
    for doc in docs:
        supplier = doc.get("supplierName") or ""
        if FRANCHISE_FILTER in supplier:
            continue
        ref = str(doc.get("refNumber") or doc.get("number") or "")
        amount = float(doc.get("totalWithVat") or doc.get("totalAmount") or doc.get("amount") or 0)
        raw_date = str(doc.get("date") or doc.get("documentDate") or "")[:10]
        invoices.append({
            "ref": ref,
            "amount": amount,
            "supplier": supplier,
            "date": raw_date,
        })
    return invoices


def get_db_rows() -> list[dict]:
    """Fetch all goods expenses for the current month from DB."""
    today = date.today()
    month_str = f"{today.month:02d}"
    year_str = str(today.year)

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT date, amount, description, source FROM expenses "
            "WHERE category = 'goods' "
            "AND strftime('%m', date) = ? AND strftime('%Y', date) = ? "
            "ORDER BY date",
            (month_str, year_str),
        ).fetchall()

    return [
        {
            "ref": "",  # DB doesn't store ref_number separately
            "amount": row["amount"],
            "supplier": row["description"] or "",
            "date": row["date"],
        }
        for row in rows
    ]


def build_match_key(record: dict) -> str:
    """Match key: date + amount + supplier/description."""
    return f"{record['date']}|{record['amount']:.2f}|{record['supplier']}"


def run_audit():
    token = os.getenv("BILBOY_TOKEN", "")
    if not token:
        print("ERROR: BILBOY_TOKEN not set in .env")
        return 1

    # Fetch data
    try:
        api_invoices = get_api_invoices(token)
    except Exception as e:
        print(f"ERROR fetching from API: {e}")
        return 1

    db_rows = get_db_rows()

    # Build lookup dicts by match key
    api_by_key = {}
    for inv in api_invoices:
        key = build_match_key(inv)
        api_by_key[key] = inv

    db_by_key = {}
    for row in db_rows:
        key = build_match_key(row)
        db_by_key[key] = row

    api_keys = set(api_by_key.keys())
    db_keys = set(db_by_key.keys())

    # Compare
    matched_keys = api_keys & db_keys
    in_api_not_db = api_keys - db_keys
    in_db_not_api = db_keys - api_keys

    # Amount mismatches (already caught by exact key matching on amount,
    # so also do a fuzzy match by date+supplier only)
    amount_mismatches = []
    api_by_ds = {}
    for inv in api_invoices:
        ds_key = f"{inv['date']}|{inv['supplier']}"
        api_by_ds.setdefault(ds_key, []).append(inv)

    db_by_ds = {}
    for row in db_rows:
        ds_key = f"{row['date']}|{row['supplier']}"
        db_by_ds.setdefault(ds_key, []).append(row)

    for ds_key in set(api_by_ds.keys()) & set(db_by_ds.keys()):
        for api_inv in api_by_ds[ds_key]:
            for db_row in db_by_ds[ds_key]:
                diff = abs(api_inv["amount"] - db_row["amount"])
                if diff > 0.01:
                    amount_mismatches.append({
                        "ref": api_inv["ref"] or ds_key,
                        "api_amt": api_inv["amount"],
                        "db_amt": db_row["amount"],
                        "diff": diff,
                    })

    # Totals
    api_total = sum(inv["amount"] for inv in api_invoices)
    db_total = sum(row["amount"] for row in db_rows)
    gap = api_total - db_total

    now = datetime.now()

    # Print report
    print("══════════════════════════════════════════")
    print(f"BilBoy Deep Audit — {now.strftime('%Y-%m-%d')} {now.strftime('%H:%M:%S')}")
    print("══════════════════════════════════════════")
    print()
    print("📊 TOTALS:")
    print(f"  API total:  ₪{api_total:,.2f}  ({len(api_invoices)} invoices)")
    print(f"  DB total:   ₪{db_total:,.2f}   ({len(db_rows)} rows)")
    print(f"  Gap:        ₪{gap:,.2f}")
    print()
    print(f"✅ MATCHED: {len(matched_keys)} invoices match perfectly")
    print()

    # In API, missing from DB
    print(f"🔴 IN API, MISSING FROM DB ({len(in_api_not_db)}):")
    if in_api_not_db:
        for key in sorted(in_api_not_db):
            inv = api_by_key[key]
            print(f"  {inv['ref'] or '-'} | {inv['supplier']} | ₪{inv['amount']:,.2f} | {inv['date']}")
    else:
        print("  (none)")
    print()

    # In DB, not in API (ghost rows)
    print(f"👻 IN DB, NOT IN API ({len(in_db_not_api)}):")
    if in_db_not_api:
        for key in sorted(in_db_not_api):
            row = db_by_key[key]
            print(f"  {row['ref'] or '-'} | {row['supplier']} | ₪{row['amount']:,.2f} | {row['date']}")
    else:
        print("  (none)")
    print()

    # Amount mismatches
    print(f"⚠️  AMOUNT MISMATCHES ({len(amount_mismatches)}):")
    if amount_mismatches:
        for m in amount_mismatches:
            print(f"  {m['ref']} | API: ₪{m['api_amt']:,.2f} | DB: ₪{m['db_amt']:,.2f} | diff: ₪{m['diff']:,.2f}")
    else:
        print("  (none)")
    print()
    print("══════════════════════════════════════════")

    # Exit code: 0 if clean, 1 if discrepancies
    has_issues = in_api_not_db or in_db_not_api or amount_mismatches
    return 1 if has_issues else 0


if __name__ == "__main__":
    sys.exit(run_audit())
