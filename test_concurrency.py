import sqlite3
import threading
import time
import local_db

# Test concurrent access
results = []

def writer_thread(name):
    try:
        conn = local_db.get_connection()
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f'test_{name}', str(time.time())))
        conn.commit()
        conn.close()
        results.append(f'{name}: OK')
    except Exception as e:
        results.append(f'{name}: FAIL {e}')

def reader_thread(name):
    try:
        conn = local_db.get_connection()
        conn.execute("SELECT COUNT(*) FROM products").fetchone()
        conn.close()
        results.append(f'{name}: OK')
    except Exception as e:
        results.append(f'{name}: FAIL {e}')

threads = []
for i in range(10):
    t = threading.Thread(target=writer_thread, args=(f'writer{i}',))
    threads.append(t)
    t.start()
for i in range(10):
    t = threading.Thread(target=reader_thread, args=(f'reader{i}',))
    threads.append(t)
    t.start()

for t in threads:
    t.join()

for r in results:
    print(r)
print('All tests passed:', all('OK' in r for r in results))