import sqlite3

conn = sqlite3.connect('pos_local.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("TABLES:", [r[0] for r in cur.fetchall()])
try:
    cur.execute("SELECT username, role, status FROM users")
    for r in cur.fetchall():
        print("USER:", r)
except Exception as e:
    print("users query error:", e)
conn.close()