import sqlite3

conn = sqlite3.connect('pos_local.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print('=== ALL orders with has_receipt=0 (last 20) ===')
cur.execute("SELECT tran_id, status, has_receipt, created_at FROM orders WHERE has_receipt = 0 ORDER BY created_at DESC LIMIT 20")
for r in cur.fetchall():
    print(dict(r))

print()
print('=== Orders with status containing acleda ===')
cur.execute("SELECT tran_id, status, has_receipt, created_at FROM orders WHERE status LIKE '%acleda%' ORDER BY created_at DESC")
for r in cur.fetchall():
    print(dict(r))

print()
print('=== Orders with status containing aba ===')
cur.execute("SELECT tran_id, status, has_receipt, created_at FROM orders WHERE status LIKE '%aba%' ORDER BY created_at DESC")
for r in cur.fetchall():
    print(dict(r))

conn.close()