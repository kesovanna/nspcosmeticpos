import sqlite3
import os

DB_PATH = 'pos_local.db'
print('DB exists:', os.path.exists(DB_PATH))
if os.path.exists(DB_PATH):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        print('Tables:', tables)
        conn.close()
    except Exception as e:
        print('Error:', e)