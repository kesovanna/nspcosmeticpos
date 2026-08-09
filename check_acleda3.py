import sqlite3

conn = sqlite3.connect('pos_local.db')
cur = conn.cursor()
cur.execute("SELECT tran_id, status, has_receipt, created_at FROM orders WHERE status='paid by acleda' ORDER BY created_at DESC LIMIT 10")
for r in cur.fetchall():
    print(r)
conn.close()