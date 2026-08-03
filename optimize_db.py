import sqlite3
import os

DB_PATH = 'pos_local.db'

# Set WAL mode for better concurrency
conn = sqlite3.connect(DB_PATH, timeout=30.0)
conn.execute('PRAGMA journal_mode = WAL')
conn.execute('PRAGMA synchronous = NORMAL')
conn.execute('PRAGMA busy_timeout = 30000')  # 30 seconds
conn.close()

print('Database optimized for concurrent access')
print('Journal mode:', sqlite3.connect(DB_PATH).execute('PRAGMA journal_mode').fetchone())
print('Busy timeout:', sqlite3.connect(DB_PATH).execute('PRAGMA busy_timeout').fetchone())
