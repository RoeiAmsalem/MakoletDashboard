"""
BilBoy March 2026 audit — READ-ONLY diagnostic.
Connects to database/makolet.db and prints detailed breakdown.
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "database", "makolet.db")
MONTH_START = "2026-03-01"
MONTH_END = "2026-03-31"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row


def section(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


# 1. TOTAL SUMMARY
section("1. TOTAL SUMMARY — March 2026 goods")
row = conn.execute(
    "SELECT COUNT(*) as n, COALESCE(SUM(amount),0) as total_with_vat, "
    "COALESCE(SUM(total_without_vat),0) as total_without_vat "
    "FROM expenses WHERE category='goods' AND date BETWEEN ? AND ?",
    (MONTH_START, MONTH_END),
).fetchone()
print(f"  Rows:              {row['n']}")
print(f"  SUM(amount):       {row['total_with_vat']:.2f}  (with VAT)")
print(f"  SUM(no VAT):       {row['total_without_vat']:.2f}")


# 2. BREAKDOWN BY DOC TYPE
section("2. BREAKDOWN BY DOC TYPE")
rows = conn.execute(
    "SELECT doc_type, doc_type_name, COUNT(*) as n, "
    "SUM(amount) as tot, SUM(total_without_vat) as tot_nv "
    "FROM expenses WHERE category='goods' AND date BETWEEN ? AND ? "
    "GROUP BY doc_type, doc_type_name ORDER BY doc_type",
    (MONTH_START, MONTH_END),
).fetchall()
for r in rows:
    print(f"  type={r['doc_type']}  ({r['doc_type_name'] or '?'}):  "
          f"{r['n']} docs,  amount={r['tot']:.2f},  no_vat={r['tot_nv']:.2f}")


# 3. ZERO / NULL ROWS
section("3. ZERO / NULL AMOUNT ROWS")
rows = conn.execute(
    "SELECT id, date, description, ref_number, doc_type, doc_type_name, amount, total_without_vat "
    "FROM expenses WHERE category='goods' AND date BETWEEN ? AND ? "
    "AND (amount = 0 OR amount IS NULL) AND (total_without_vat = 0 OR total_without_vat IS NULL)",
    (MONTH_START, MONTH_END),
).fetchall()
if rows:
    for r in rows:
        print(f"  id={r['id']}  date={r['date']}  ref={r['ref_number']}  "
              f"type={r['doc_type']}  desc={r['description']}  "
              f"amt={r['amount']}  nv={r['total_without_vat']}")
else:
    print("  (none)")

section("3b. NULL / EMPTY REF_NUMBER ROWS")
rows = conn.execute(
    "SELECT id, date, description, ref_number, doc_type, doc_type_name, amount, total_without_vat "
    "FROM expenses WHERE category='goods' AND date BETWEEN ? AND ? "
    "AND (ref_number IS NULL OR ref_number = '')",
    (MONTH_START, MONTH_END),
).fetchall()
if rows:
    for r in rows:
        print(f"  id={r['id']}  date={r['date']}  ref={r['ref_number']!r}  "
              f"type={r['doc_type']}  desc={r['description']}  "
              f"amt={r['amount']}  nv={r['total_without_vat']}")
else:
    print("  (none)")


# 4. DUPLICATE REF_NUMBERS
section("4. DUPLICATE REF_NUMBERS")
rows = conn.execute(
    "SELECT ref_number, COUNT(*) as n, GROUP_CONCAT(id) as ids, "
    "GROUP_CONCAT(amount) as amounts, GROUP_CONCAT(doc_type) as types, "
    "GROUP_CONCAT(description, ' | ') as descs "
    "FROM expenses WHERE category='goods' AND date BETWEEN ? AND ? "
    "AND ref_number IS NOT NULL AND ref_number != '' "
    "GROUP BY ref_number HAVING n > 1 ORDER BY n DESC",
    (MONTH_START, MONTH_END),
).fetchall()
if rows:
    for r in rows:
        print(f"  ref={r['ref_number']}  count={r['n']}  ids=[{r['ids']}]  "
              f"amounts=[{r['amounts']}]  types=[{r['types']}]")
        print(f"    descs: {r['descs']}")
else:
    print("  (none)")


# 5. TOP SUPPLIERS
section("5. TOP SUPPLIERS BY TOTAL AMOUNT")
rows = conn.execute(
    "SELECT description, COUNT(*) as n, SUM(amount) as tot "
    "FROM expenses WHERE category='goods' AND date BETWEEN ? AND ? "
    "GROUP BY description ORDER BY tot DESC",
    (MONTH_START, MONTH_END),
).fetchall()
for r in rows:
    print(f"  {r['tot']:>10.2f}  ({r['n']:>3d} docs)  {r['description']}")


# 6. MOST RECENT 15 ROWS
section("6. MOST RECENT 15 ROWS")
rows = conn.execute(
    "SELECT id, date, description, ref_number, doc_type, doc_type_name, amount, total_without_vat "
    "FROM expenses WHERE category='goods' AND date BETWEEN ? AND ? "
    "ORDER BY date DESC, id DESC LIMIT 15",
    (MONTH_START, MONTH_END),
).fetchall()
for r in rows:
    print(f"  id={r['id']}  {r['date']}  ref={r['ref_number']}  "
          f"type={r['doc_type']}({r['doc_type_name']})  "
          f"amt={r['amount']:.2f}  nv={r['total_without_vat']:.2f}  "
          f"{r['description']}")


# 7. FULL DATE RANGE CHECK
section("7. FULL DATE RANGE — ALL GOODS ROWS (any month)")
row = conn.execute(
    "SELECT MIN(date) as mn, MAX(date) as mx, COUNT(*) as n, SUM(amount) as tot "
    "FROM expenses WHERE category='goods'"
).fetchone()
print(f"  MIN(date): {row['mn']}")
print(f"  MAX(date): {row['mx']}")
print(f"  COUNT:     {row['n']}")
print(f"  SUM:       {row['tot']:.2f}")

# Check for rows outside March 2026
row2 = conn.execute(
    "SELECT COUNT(*) as n, COALESCE(SUM(amount),0) as tot "
    "FROM expenses WHERE category='goods' AND (date < ? OR date > ?)",
    (MONTH_START, MONTH_END),
).fetchone()
print(f"  Outside March 2026: {row2['n']} rows, amount={row2['tot']:.2f}")


conn.close()
print("\n--- audit complete ---")
