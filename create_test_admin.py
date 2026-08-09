import sqlite3
from werkzeug.security import generate_password_hash

conn = sqlite3.connect('pos_local.db')
cur = conn.cursor()
# Create a temporary test admin (only if not exists)
cur.execute("SELECT username FROM users WHERE username='testadmin'")
if cur.fetchone() is None:
    cur.execute(
        "INSERT INTO users (username, password, role, status) VALUES (?, ?, ?, ?)",
        ('testadmin', generate_password_hash('test123456'), 'admin', 'active')
    )
    conn.commit()
    print("Created testadmin")
else:
    print("testadmin already exists")
conn.close()