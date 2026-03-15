import sqlite3
import os
import sys

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
conn.close()
