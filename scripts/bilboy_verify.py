import sqlite3
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

db_path = os.path.join(os.path.dirname(__file__), '..', 'database', 'makolet.db')
conn = sqlite3.connect(db_path)

print('=== EXPENSES TABLE COLUMNS ===')
cols = [row[1] for row in conn.execute('PRAGMA table_info(expenses)').fetchall()]
print(cols)
print()

print('=== MARCH 2026 GOODS BY TYPE ===')
rows = conn.execute(
    "SELECT COALESCE(doc_type_name, 'legacy') as tn, doc_type, COUNT(*) as cnt, SUM(amount) as tot "
    "FROM expenses WHERE category='goods' AND date LIKE '2026-03%' "
    "GROUP BY tn ORDER BY tn"
).fetchall()
grand = 0
for r in rows:
    print('  %s (type=%s): %d docs, %.2f' % (r[0], r[1], r[2], r[3]))
    grand += r[3]
print('TOTAL: %.2f' % grand)
print('BilBoy UI: 144339.88')
print('DIFF: %.2f' % (grand - 144339.88))
print()

# Check if franchise supplier accounts for the gap
print('=== FRANCHISE SUPPLIER CHECK ===')
conn.close()

from dotenv import load_dotenv
load_dotenv()

import os, requests
token = os.environ.get('BILBOY_TOKEN', '')
if not token:
    print('BILBOY_TOKEN not set, skipping API check')
else:
    headers = {'Authorization': f'Bearer {token}'}
    API_BASE = 'https://app.billboy.co.il:5050/api'
    branches = requests.get(f'{API_BASE}/user/branches', headers=headers, timeout=30).json()
    branch_id = str(branches[0].get('branchId') or branches[0].get('id', ''))

    suppliers = requests.get(f'{API_BASE}/customer/suppliers', headers=headers,
        params={'customerBranchId': branch_id, 'all': 'true'}, timeout=30).json()
    sup_list = suppliers.get('suppliers', suppliers) if isinstance(suppliers, dict) else suppliers

    franchise_ids = []
    for s in sup_list:
        name = s.get('title') or s.get('name') or s.get('supplierName') or ''
        sid = str(s.get('id') or s.get('supplierId') or '')
        if 'זיכיונות המכולת' in name:
            franchise_ids.append(sid)
            print('  Franchise supplier: %s (id=%s)' % (name, sid))

    if franchise_ids:
        # Fetch franchise docs for March
        resp = requests.get(f'{API_BASE}/customer/docs/headers', headers=headers, params={
            'suppliers': ','.join(franchise_ids),
            'branches': branch_id,
            'from': '2026-03-01T00:00:00',
            'to': '2026-03-15T23:59:59',
        }, timeout=30).json()
        docs = resp if isinstance(resp, list) else resp.get('data') or resp.get('docs') or resp.get('headers') or []
        franchise_total = sum(d.get('totalWithVat', 0) or 0 for d in docs)
        print('  Franchise docs: %d, total: %.2f' % (len(docs), franchise_total))
        print('  DB total + franchise = %.2f' % (grand + franchise_total))
    else:
        print('  No franchise suppliers found')
