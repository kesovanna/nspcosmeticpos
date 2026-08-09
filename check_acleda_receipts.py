import sqlite3

conn = sqlite3.connect('pos_local.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print('=== ACLEDA orders (ACL%) ===')
cur.execute("SELECT tran_id, status, has_receipt, created_at FROM orders WHERE tran_id LIKE 'ACL%' ORDER BY created_at DESC LIMIT 15")
for r in cur.fetchall():
    print(dict(r))

print()
print('=== ABA orders (TX%) ===')
cur.execute("SELECT tran_id, status, has_receipt, created_at FROM orders WHERE tran_id LIKE 'TX%' ORDER BY created_at DESC LIMIT 5")
for r in cur.fetchall():
    print(dict(r))

print()
print('=== Cash orders (CASH%) ===')
cur.execute("SELECT tran_id, status, has_receipt, created_at FROM orders WHERE tran_id LIKE 'CASH%' ORDER BY created_at DESC LIMIT 5")
for r in cur.fetchall():
    print(dict(r))

conn.close()