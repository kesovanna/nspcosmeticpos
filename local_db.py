import sqlite3
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict

def is_cloud_runtime():
    """True on Firebase Cloud Functions / Cloud Run; False on desktop POS."""
    return bool(
        os.environ.get('K_SERVICE')
        or os.environ.get('FUNCTION_TARGET')
        or os.environ.get('FUNCTION_NAME')
    )

# Cambodia ICT = UTC+7 (fixed, no DST)
ICT_TZ = timezone(timedelta(hours=7), name='ICT')

# Canonical set of "paid / revenue-earning" statuses. Every revenue counter
# (dashboard stats + daily/monthly/annual sales reports) MUST use exactly
# this set so the summary cards can never disagree with the table filters.
# 'paid by cash' / 'paid by aba' / 'paid by acleda' / 'paid by amret' are the canonical
# payment-method statuses; 'paid' / 'completed' are the legacy bare forms.
PAID_STATUSES = ('paid by cash', 'paid by aba', 'paid by acleda', 'paid by amret', 'paid', 'completed')

# SQL fragment: case-insensitive, whitespace-trimmed status membership.
# Shared by every query that aggregates revenue / order counts.
PAID_STATUSES_SQL = (
    "LOWER(TRIM(status)) IN "
    "('paid by cash', 'paid by aba', 'paid by acleda', 'paid by amret', 'paid', 'completed')"
)

def cambodia_time(dt):
    """Normalize any datetime to Cambodia ICT (UTC+7).

    Naive datetimes are assumed to already be Cambodia local wall-clock
    (legacy rows written by datetime.now().isoformat()); aware datetimes
    (e.g. Firestore Timestamps with +00:00) are converted to ICT.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ICT_TZ)
    return dt.astimezone(ICT_TZ)

# --- PORTABLE PATH LOGIC ---
def get_resource_path(relative_path):
    """
    Get absolute path to resource, works for dev and for PyInstaller.
    """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

DB_PATH = get_resource_path('pos_local.db')

def get_connection():
    """Create a SQLite connection configured for concurrent access.
    Uses WAL journal mode and a busy timeout so background sync threads
    and Flask request threads do not hit 'database is locked' errors.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout = 30000')  # wait up to 30s for locks
    conn.execute('PRAGMA journal_mode = WAL')     # allow concurrent readers/writers
    conn.execute('PRAGMA synchronous = NORMAL')   # good balance of safety & speed
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # Users Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT,
            role TEXT,
            status TEXT,
            profile_image TEXT,
            cover_image TEXT
        )
    ''')
    
    # Products (Items) Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            name TEXT,
            price REAL,
            image TEXT,
            category TEXT,
            barcode TEXT,
            createdAt TEXT,
            stock_quantity INTEGER DEFAULT 0,
            expiry_date TEXT,
            cost_price REAL DEFAULT 0.0,
            low_stock_level INTEGER DEFAULT 5
        )
    ''')
    
    # Check if stock_quantity column exists, if not add it (for existing databases)
    cursor.execute("PRAGMA table_info(products)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'stock_quantity' not in columns:
        cursor.execute('ALTER TABLE products ADD COLUMN stock_quantity INTEGER DEFAULT 0')
    if 'expiry_date' not in columns:
        cursor.execute('ALTER TABLE products ADD COLUMN expiry_date TEXT')
    if 'cost_price' not in columns:
        cursor.execute('ALTER TABLE products ADD COLUMN cost_price REAL DEFAULT 0.0')
    if 'low_stock_level' not in columns:
        cursor.execute('ALTER TABLE products ADD COLUMN low_stock_level INTEGER DEFAULT 5')
    
    # Stock History Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT,
            change_amount INTEGER,
            reason TEXT,
            created_at TEXT,
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    ''')
    
    # Orders Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            local_id INTEGER PRIMARY KEY AUTOINCREMENT,
            firestore_id TEXT,
            items TEXT,
            total REAL,
            discount REAL DEFAULT 0,
            status TEXT,
            created_at TEXT,
            tran_id TEXT,
            user TEXT,
            synced INTEGER DEFAULT 0,
            has_receipt INTEGER DEFAULT 0
        )
    ''')
    
    # Check if discount column exists, if not add it
    cursor.execute("PRAGMA table_info(orders)")
    order_columns = [column[1] for column in cursor.fetchall()]
    if 'discount' not in order_columns:
        cursor.execute('ALTER TABLE orders ADD COLUMN discount REAL DEFAULT 0')
    if 'has_receipt' not in order_columns:
        cursor.execute('ALTER TABLE orders ADD COLUMN has_receipt INTEGER DEFAULT 0')

    # PILLAR 2 + PILLAR 3: Enforce UNIQUE tran_id (collision-free, idempotent retries)
    # Index name is stable; CREATE UNIQUE INDEX IF NOT EXISTS is a no-op on re-run,
    # but we must first purge any legacy duplicates so the unique index can be built.
    # SQLite unique indexes CANNOT contain NULLs, so skip legacy NULL tran_ids.
    cursor.execute('''
        DELETE FROM orders
        WHERE tran_id IS NOT NULL AND rowid NOT IN (
            SELECT MIN(rowid) FROM orders WHERE tran_id IS NOT NULL GROUP BY tran_id
        )
    ''')
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_tran_id
        ON orders (tran_id)
        WHERE tran_id IS NOT NULL
    ''')
    
    # Deletion Log Table (To track what to delete from Firestore during sync)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS deleted_products (
            id TEXT PRIMARY KEY,
            deleted_at TEXT
        )
    ''')

    # Settings Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')

    # Categories Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        )
    ''')

    # Cover Images Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cover_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            image_data TEXT,
            display_order INTEGER,
            FOREIGN KEY (username) REFERENCES users(username)
        )
    ''')
    
    conn.commit()
    conn.close()

# --- Cover Image Operations ---
def get_user_covers(username):
    conn = get_connection()
    rows = conn.execute('SELECT * FROM cover_images WHERE username = ? ORDER BY display_order ASC', (username,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def add_user_cover(username, image_data):
    conn = get_connection()
    cursor = conn.cursor()
    # Limit to 10
    count = cursor.execute('SELECT COUNT(*) FROM cover_images WHERE username = ?', (username,)).fetchone()[0]
    if count >= 10:
        conn.close()
        return False, "Maximum 10 cover images allowed."
    
    cursor.execute('INSERT INTO cover_images (username, image_data, display_order) VALUES (?, ?, ?)', 
                   (username, image_data, count))
    conn.commit()
    conn.close()
    return True, "Added successfully."

def delete_user_cover(cover_id, username):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM cover_images WHERE id = ? AND username = ?', (cover_id, username))
    # Re-order
    covers = cursor.execute('SELECT id FROM cover_images WHERE username = ? ORDER BY display_order ASC', (username,)).fetchall()
    for i, row in enumerate(covers):
        cursor.execute('UPDATE cover_images SET display_order = ? WHERE id = ?', (i, row['id']))
    conn.commit()
    conn.close()
    return True

def clear_user_covers(username):
    conn = get_connection()
    conn.execute('DELETE FROM cover_images WHERE username = ?', (username,))
    conn.commit()
    conn.close()

# --- Category Operations ---
def get_categories():
    """Cloud-First hybrid: Firestore `categories` (then unique `items.category`
    values) first, local SQLite fallback. Returns a list of name strings to
    match the existing SQLite SELECT mapping.
    """
    # 1) Cloud Firestore (primary in serverless environments)
    if is_cloud_runtime():
        try:
            from firebase_admin import firestore
            db = firestore.client()
            names = []
            seen = set()
            for doc in db.collection('categories').stream():
                data = doc.to_dict() or {}
                name = (data.get('name') or doc.id or '').strip()
                if name and name not in seen:
                    seen.add(name)
                    names.append(name)
            # Categories are often stored only as a field on items, not as a
            # dedicated collection. Derive unique names from products if needed.
            if not names:
                for doc in db.collection('items').stream():
                    data = doc.to_dict() or {}
                    name = (data.get('category') or '').strip()
                    if name and name not in seen:
                        seen.add(name)
                        names.append(name)
            if names:
                names.sort()
                return names
        except Exception:
            pass  # Firestore unavailable/not initialized -> fall through

    # 2) Local SQLite fallback (desktop / offline mode)
    try:
        conn = get_connection()
        try:
            rows = conn.execute('SELECT name FROM categories ORDER BY name ASC').fetchall()
            return [row['name'] for row in rows]
        finally:
            conn.close()
    except Exception:
        return []

def add_category(name):
    conn = get_connection()
    try:
        conn.execute('INSERT INTO categories (name) VALUES (?)', (name,))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False # Already exists
    finally:
        conn.close()

# --- Settings Operations ---
def get_setting(key, default=None):
    conn = get_connection()
    row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    conn.close()
    return row['value'] if row else default

def set_setting(key, value):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(value)))
    conn.commit()
    conn.close()

def update_setting(key, value):
    """
    Updates a setting in the database. Uses INSERT OR REPLACE to handle both
    new and existing keys.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(value)))
    conn.commit()
    conn.close()

# --- Deletion Tracking ---
def add_to_deletion_log(product_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO deleted_products (id, deleted_at) VALUES (?, ?)', 
                   (product_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_deleted_products():
    conn = get_connection()
    rows = conn.execute('SELECT id FROM deleted_products').fetchall()
    conn.close()
    return [row['id'] for row in rows]

def clear_deletion_log(product_ids):
    if not product_ids:
        return
    conn = get_connection()
    cursor = conn.cursor()
    placeholders = ','.join(['?'] * len(product_ids))
    cursor.execute(f'DELETE FROM deleted_products WHERE id IN ({placeholders})', product_ids)
    conn.commit()
    conn.close()

# --- User Operations ---
def save_users(users_list):
    """Bulk save/update users from Firestore"""
    conn = get_connection()
    cursor = conn.cursor()
    for user in users_list:
        cursor.execute('''
            INSERT OR REPLACE INTO users (username, password, role, status, profile_image, cover_image)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            user.get('username'),
            user.get('password'),
            user.get('role', 'user'),
            user.get('status', 'active'),
            user.get('profile_image'),
            user.get('cover_image')
        ))
    conn.commit()
    conn.close()

def get_user(username):
    """Cloud-First hybrid lookup: try Cloud Firestore first, then local SQLite.

    On Firebase Cloud Functions the filesystem is read-only and pos_local.db
    is excluded from the deployment, so a pure-SQLite lookup returns None.
    We therefore query the Firestore `users` collection first (the canonical
    source in the cloud), and fall back to the local SQLite database only if
    Firestore is unavailable or returns nothing (desktop / offline mode).
    """
    # 1) Cloud Firestore (primary in serverless environments)
    if is_cloud_runtime():
        try:
            from firebase_admin import firestore
            db = firestore.client()
            doc = db.collection('users').document(str(username)).get()
            if doc.exists:
                u = doc.to_dict()
                if u:
                    # Normalize Firestore fields to the SQLite schema
                    u['username'] = u.get('username') or doc.id
                    u.setdefault('password', '')
                    u.setdefault('role', 'user')
                    u.setdefault('status', 'active')
                    if u.get('profile_image') is None:
                        u['profile_image'] = u.get('profile_pic')
                    return u
        except Exception:
            pass  # Firestore unavailable/not initialized -> fall through

    # 2) Local SQLite fallback (desktop / offline mode)
    try:
        conn = get_connection()
        try:
            user = conn.execute(
                'SELECT * FROM users WHERE username = ?', (str(username),)
            ).fetchone()
            return dict(user) if user else None
        finally:
            conn.close()
    except Exception:
        return None

def get_user_by_id(user_id):
    """Look up a user by ID. In this app the username IS the user ID
    (Firestore document ID), so this delegates to the hybrid get_user()."""
    return get_user(user_id)

def get_user_profile(username):
    """Explicitly fetch profile and cover images for a specific user"""
    conn = get_connection()
    user = conn.execute('SELECT username, profile_image, cover_image FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()
    return dict(user) if user else None

def get_all_users():
    conn = get_connection()
    users = conn.execute('SELECT * FROM users').fetchall()
    conn.close()
    return [dict(u) for u in users]

def delete_user_local(username):
    """Delete a user from local SQLite. Returns True if a row was removed."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM users WHERE username = ?', (username,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted

def update_user_images(username, profile_image=None, cover_image=None):
    conn = get_connection()
    cursor = conn.cursor()
    if profile_image:
        cursor.execute('UPDATE users SET profile_image = ? WHERE username = ?', (profile_image, username))
    if cover_image:
        cursor.execute('UPDATE users SET cover_image = ? WHERE username = ?', (cover_image, username))
    conn.commit()
    conn.close()

# --- Product Operations ---
def save_products(products_list):
    """Bulk save/update products from Firestore"""
    conn = get_connection()
    cursor = conn.cursor()
    for prod in products_list:
        cursor.execute('''
            INSERT OR REPLACE INTO products (id, name, price, image, category, barcode, createdAt, stock_quantity, expiry_date, cost_price, low_stock_level)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            prod.get('id'),
            prod.get('name'),
            prod.get('price'),
            prod.get('image'),
            prod.get('category'),
            prod.get('barcode'),
            prod.get('createdAt'),
            prod.get('stock_quantity', 0),
            prod.get('expiry_date'),
            prod.get('cost_price', 0.0),
            prod.get('low_stock_level', 5)
        ))
    conn.commit()
    conn.close()

def add_product(prod_id, prod_data):
    """Save a single product locally"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO products (id, name, price, image, category, barcode, createdAt, stock_quantity, expiry_date, cost_price, low_stock_level)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        prod_id,
        prod_data.get('name'),
        prod_data.get('price'),
        prod_data.get('image'),
        prod_data.get('category'),
        prod_data.get('barcode'),
        prod_data.get('createdAt'),
        prod_data.get('stock_quantity', 0),
        prod_data.get('expiry_date'),
        prod_data.get('cost_price', 0.0),
        prod_data.get('low_stock_level', 5)
    ))
    conn.commit()
    conn.close()

def _normalize_product_doc(doc_id, data):
    """Map a Firestore item document onto the SQLite products-row dict keys."""
    created_at = data.get('createdAt')
    if created_at is not None and hasattr(created_at, 'isoformat'):
        created_at = created_at.isoformat()
    return {
        'id': data.get('id') or doc_id,
        'name': data.get('name'),
        'price': data.get('price'),
        'image': data.get('image'),
        'category': data.get('category'),
        'barcode': data.get('barcode'),
        'createdAt': created_at,
        'stock_quantity': data.get('stock_quantity', 0),
        'expiry_date': data.get('expiry_date'),
        'cost_price': data.get('cost_price', 0.0),
        'low_stock_level': data.get('low_stock_level', 5),
    }

def get_products():
    """Cloud-First hybrid: Firestore `items` first, local SQLite fallback.
    Returns a list of dicts with the same keys as `SELECT * FROM products`.
    """
    # 1) Cloud Firestore (primary in serverless environments)
    if is_cloud_runtime():
        try:
            from firebase_admin import firestore
            db = firestore.client()
            products = []
            for doc in db.collection('items').stream():
                products.append(_normalize_product_doc(doc.id, doc.to_dict() or {}))
            if products:
                products.sort(key=lambda p: p.get('createdAt') or '', reverse=True)
                return products
        except Exception:
            pass  # Firestore unavailable/not initialized -> fall through

    # 2) Local SQLite fallback (desktop / offline mode)
    try:
        conn = get_connection()
        try:
            prods = conn.execute('SELECT * FROM products ORDER BY createdAt DESC').fetchall()
            return [dict(p) for p in prods]
        finally:
            conn.close()
    except Exception:
        return []

def get_product(prod_id):
    """Cloud-First hybrid: Firestore `items/{id}` first, local SQLite fallback."""
    # 1) Cloud Firestore (primary in serverless environments)
    if is_cloud_runtime():
        try:
            from firebase_admin import firestore
            db = firestore.client()
            doc = db.collection('items').document(str(prod_id)).get()
            if doc.exists:
                return _normalize_product_doc(doc.id, doc.to_dict() or {})
        except Exception:
            pass  # Firestore unavailable/not initialized -> fall through

    # 2) Local SQLite fallback (desktop / offline mode)
    try:
        conn = get_connection()
        try:
            prod = conn.execute('SELECT * FROM products WHERE id = ?', (prod_id,)).fetchone()
            return dict(prod) if prod else None
        finally:
            conn.close()
    except Exception:
        return None

def get_next_barcode():
    """
    Queries the database for all existing barcodes, filters for purely numeric strings,
    finds the maximum value, and returns the next number formatted with 4 leading zeros.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT barcode FROM products")
    rows = cursor.fetchall()
    conn.close()
    
    max_barcode = 0
    for row in rows:
        barcode = row['barcode']
        if barcode and barcode.isdigit():
            try:
                max_barcode = max(max_barcode, int(barcode))
            except (ValueError, TypeError):
                continue
    
    next_barcode = max_barcode + 1
    return f"{next_barcode:04d}"

def delete_product(prod_id):
    conn = get_connection()
    conn.execute('DELETE FROM products WHERE id = ?', (prod_id,))
    conn.commit()
    conn.close()

# --- Stock History Operations ---
def add_stock_history(product_id, change_amount, reason):
    """Log a stock change event"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO stock_history (product_id, change_amount, reason, created_at)
        VALUES (?, ?, ?, ?)
    ''', (
        product_id,
        change_amount,
        reason,
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()

def get_stock_history(product_id):
    """Retrieve stock history for a product"""
    conn = get_connection()
    rows = conn.execute('''
        SELECT id, product_id, change_amount, reason, created_at
        FROM stock_history
        WHERE product_id = ?
        ORDER BY created_at DESC
    ''', (product_id,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def update_product_stock(product_id, new_stock, reason='Manual Edit'):
    """Update stock and log the change"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get current stock
    current = cursor.execute('SELECT stock_quantity FROM products WHERE id = ?', (product_id,)).fetchone()
    if not current:
        conn.close()
        return False
    
    current_stock = current['stock_quantity']
    change_amount = new_stock - current_stock
    
    # Update stock
    cursor.execute('UPDATE products SET stock_quantity = ? WHERE id = ?', (new_stock, product_id))
    
    # Log the change
    cursor.execute('''
        INSERT INTO stock_history (product_id, change_amount, reason, created_at)
        VALUES (?, ?, ?, ?)
    ''', (
        product_id,
        change_amount,
        reason,
        datetime.now().isoformat()
    ))
    
    conn.commit()
    conn.close()
    return True

def validate_and_deduct_stock_local(cart_items):
    """
    Handles atomic stock deduction in SQLite.
    Follows a "Read-Validate-Update" pattern within a single transaction
     to ensure inventory consistency (all-or-nothing).
    """
    conn = get_connection()
    try:
        # 'with conn:' in sqlite3 automatically starts a transaction.
        # It will COMMIT if the block completes successfully, 
        # or ROLLBACK if an exception is raised.
        with conn:
            cursor = conn.cursor()
            
            # 1. Validation Phase: Check all items before modifying anything
            for item in cart_items:
                prod_id = item.get('id')
                qty_requested = int(item.get('quantity', 0) or item.get('qty', 0))
                
                # Fetch current stock (using a write lock hint via transaction if needed)
                cursor.execute(
                    "SELECT stock_quantity, name FROM products WHERE id = ?", 
                    (prod_id,)
                )
                row = cursor.fetchone()
                
                if not row:
                    return False, f"Product ID {prod_id} not found in local database."
                
                current_stock = row['stock_quantity']
                product_name = row['name']
                
                if current_stock < qty_requested:
                    return False, f"Insufficient stock for '{product_name}'. (Required: {qty_requested}, Available: {current_stock})"
            
            # 2. Update Phase: Deduct stock for all items
            # This part only runs if ALL items passed the validation above.
            for item in cart_items:
                prod_id = item.get('id')
                qty_to_deduct = int(item.get('quantity', 0) or item.get('qty', 0))
                
                cursor.execute(
                    "UPDATE products SET stock_quantity = stock_quantity - ? WHERE id = ?",
                    (qty_to_deduct, prod_id)
                )
        
        # Transaction committed automatically here
        return True, "Inventory updated successfully."

    except sqlite3.OperationalError as e:
        # Handles 'database is locked' or other SQLite operational issues
        return False, f"Database error: {str(e)}"
    except Exception as e:
        # Catch-all for unexpected logic or data errors
        return False, f"Unexpected error during stock deduction: {str(e)}"
    finally:
        # Always close the connection to prevent memory leaks and locks
        conn.close()

# --- Order Operations ---
def next_sequential_tran_id(prefix='nsp'):
    """Generate the next sequential transaction ID: ``{prefix}-{7-digit}``.

    Queries the ``orders`` table for the current maximum numeric suffix
    among IDs matching ``{prefix}-%``, increments it, and formats the
    result zero-padded to 7 digits (e.g. ``nsp-0000001``).

    Concurrency backstop: ``add_order()`` enforces the UNIQUE index on
    ``tran_id`` (``idx_orders_tran_id``), so even if two requests race and
    mint the same ID, only one row is created and the loser is served the
    existing ``local_id``.
    """
    prefix = str(prefix or 'nsp').strip().lower()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        # MAX on the numeric suffix of matching IDs: extract the digits
        # after the last '-' and cast to integer (safe — only digits).
        cursor.execute(
            "SELECT MAX(CAST(SUBSTR(tran_id, ?) AS INTEGER)) AS max_seq "
            "FROM orders WHERE tran_id LIKE ?",
            (len(prefix) + 2, f"{prefix}-%")
        )
        row = cursor.fetchone()
        max_seq = row['max_seq'] if row and row['max_seq'] is not None else 0
        next_seq = int(max_seq) + 1
        # Safety cap: wrap around instead of producing a longer string.
        if next_seq > 9999999:
            next_seq = 1
        return f"{prefix}-{next_seq:07d}"
    finally:
        conn.close()

def add_order(order_data):
    """Save order locally with synced=0.

    PILLAR 3 (IDEMPOTENCY): If an order with the same ``tran_id`` already
    exists, we do NOT create a duplicate. We simply return the existing
    order's ``local_id`` so retried POSTs (e.g. timeout + retry) cannot
    double-charge or create duplicated records.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        tran_id = order_data.get('tran_id')
        # Strict idempotency guard: dedupe on the same transaction ID
        if tran_id:
            cursor.execute("SELECT local_id FROM orders WHERE tran_id = ?", (tran_id,))
            existing = cursor.fetchone()
            if existing:
                conn.close()
                return existing['local_id']

        items_json = json.dumps(order_data.get('items', []))
        created_at = order_data.get('created_at')
        if isinstance(created_at, datetime):
            created_at = created_at.isoformat()
        elif not created_at:
            created_at = datetime.now().isoformat()

        cursor.execute('''
            INSERT INTO orders (items, total, discount, status, created_at, tran_id, user, synced)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        ''', (
            items_json,
            order_data.get('total'),
            order_data.get('discount', 0),
            order_data.get('status', 'pending'),
            created_at,
            tran_id,
            order_data.get('user')
        ))
        local_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return local_id
    except sqlite3.IntegrityError:
        # Unique index on tran_id fired (concurrent duplicate insert)
        conn.rollback()
        if tran_id:
            cursor = conn.cursor()
            cursor.execute("SELECT local_id FROM orders WHERE tran_id = ?", (tran_id,))
            existing = cursor.fetchone()
            conn.close()
            if existing:
                return existing['local_id']
        conn.close()
        raise
    except Exception:
        conn.rollback()
        conn.close()
        raise
def get_order(tran_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE tran_id = ?", (tran_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        order = dict(row)
        try:
            order['items'] = json.loads(order['items'])
        except (json.JSONDecodeError, TypeError):
            order['items'] = []
        return order
    return None

def order_exists(tran_id):
    """PILLAR 3 (IDEMPOTENCY): True if an order with this tran_id already exists.

    Used by the sync layer to make Firestore pushes idempotent —
    never create a second cloud document for a retried order.
    """
    if not tran_id:
        return False
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM orders WHERE tran_id = ? LIMIT 1", (tran_id,))
        exists = cursor.fetchone() is not None
    finally:
        conn.close()
    return exists

def get_order_by_local_id(local_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE local_id = ?", (local_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        order = dict(row)
        try:
            order['items'] = json.loads(order['items'])
        except (json.JSONDecodeError, TypeError):
            order['items'] = []
        return order
    return None

def get_latest_paid_order():
    """Returns the most recent order with status 'paid'."""
    conn = get_connection()
    cursor = conn.cursor()
    # This queries for the most recent order that is 'paid'
    cursor.execute("SELECT * FROM orders WHERE status = 'paid' ORDER BY rowid DESC LIMIT 1")
    order = cursor.fetchone()
    conn.close()
    return dict(order) if order else None

def get_latest_pending_order():
    """Returns the most recent order with status 'pending'."""
    conn = get_connection()
    cursor = conn.cursor()
    # This queries for the most recent order that is 'pending'
    cursor.execute("SELECT * FROM orders WHERE status = 'pending' ORDER BY local_id DESC LIMIT 1")
    order = cursor.fetchone()
    conn.close()
    return dict(order) if order else None

def update_order_status(tran_id, status):
    conn = get_connection()
    conn.execute('UPDATE orders SET status = ? WHERE tran_id = ?', (status, tran_id))
    conn.commit()
    conn.close()

def update_order_status_by_local_id(local_id, status):
    conn = get_connection()
    conn.execute('UPDATE orders SET status = ? WHERE local_id = ?', (status, local_id))
    conn.commit()
    conn.close()

def update_order_status_and_time(tran_id, status, created_at):
    """Update an order's status AND created_at in one atomic UPDATE.

    Used by ACLEDA manual confirmation: flips the EXISTING pending row to
    'paid by acleda' and re-stamps its timestamp with the Cambodia ICT
    value, so the row never shifts +7 hours and never duplicates.
    """
    conn = get_connection()
    try:
        conn.execute(
            'UPDATE orders SET status = ?, created_at = ? WHERE tran_id = ?',
            (status, created_at, tran_id)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def update_order_receipt_status(tran_id, has_receipt=1):
    conn = get_connection()
    # Try by tran_id first
    cursor = conn.execute('UPDATE orders SET has_receipt = ? WHERE tran_id = ?', (has_receipt, tran_id))
    if cursor.rowcount == 0:
        # If not found, try by local_id (in case tran_id was local_id)
        conn.execute('UPDATE orders SET has_receipt = ? WHERE local_id = ?', (has_receipt, tran_id))
    conn.commit()
    conn.close()

def delete_order(report_id):
    """Delete an order by local_id or tran_id from the database"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Try deleting by local_id first (numeric)
        cursor.execute('DELETE FROM orders WHERE local_id = ?', (report_id,))
        if cursor.rowcount == 0:
            # If not found by local_id, try by tran_id (string)
            cursor.execute('DELETE FROM orders WHERE tran_id = ?', (report_id,))
        
        conn.commit()
        success = cursor.rowcount > 0
        return success
    except Exception as e:
        conn.rollback()
        print(f"Error deleting order: {e}")
        return False
    finally:
        conn.close()

def update_order(report_id, update_data):
    """Update an order's items/details by local_id or tran_id"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Prepare update fields
        items_json = None
        if 'items' in update_data:
            items_json = json.dumps(update_data['items'])
        
        total = update_data.get('total')
        discount = update_data.get('discount')
        status = update_data.get('status')
        
        # Build dynamic UPDATE query
        updates = []
        params = []
        
        if items_json:
            updates.append('items = ?')
            params.append(items_json)
        if total is not None:
            updates.append('total = ?')
            params.append(total)
        if discount is not None:
            updates.append('discount = ?')
            params.append(discount)
        if status:
            updates.append('status = ?')
            params.append(status)
        
        if not updates:
            return False, "No valid fields to update"
        
        # Try updating by local_id first
        params.append(report_id)
        query = f"UPDATE orders SET {', '.join(updates)} WHERE local_id = ?"
        cursor.execute(query, params)
        
        if cursor.rowcount == 0:
            # If not found by local_id, try by tran_id
            params[-1] = report_id
            query = f"UPDATE orders SET {', '.join(updates)} WHERE tran_id = ?"
            cursor.execute(query, params)
        
        conn.commit()
        success = cursor.rowcount > 0
        return success, "Order updated successfully" if success else "Order not found"
    except Exception as e:
        conn.rollback()
        print(f"Error updating order: {e}")
        return False, f"Error updating order: {str(e)}"
    finally:
        conn.close()

def get_all_pending_orders():
    conn = get_connection()
    orders = conn.execute("SELECT * FROM orders WHERE status = 'pending'").fetchall()
    conn.close()
    
    result = []
    for o in orders:
        d = dict(o)
        try:
            d['items'] = json.loads(d['items'])
        except (json.JSONDecodeError, TypeError):
            d['items'] = []
        result.append(d)
    return result

def get_all_orders():
    conn = get_connection()
    orders = conn.execute('SELECT * FROM orders ORDER BY created_at DESC').fetchall()
    conn.close()
    
    result = []
    for o in orders:
        d = dict(o)
        d['items'] = json.loads(d['items'])
        result.append(d)
    return result

def get_unsynced_orders():
    """Return orders that have not been pushed to Firestore yet.

    Returns a list of dicts matching the DB columns. The `items` field
    is returned as the raw JSON string (sync.py expects to call json.loads on it).
    """
    conn = get_connection()
    orders = conn.execute('SELECT * FROM orders WHERE synced = 0').fetchall()
    conn.close()

    result = []
    for o in orders:
        d = dict(o)
        # keep items as raw JSON string to allow caller to control parsing
        result.append(d)
    return result

def mark_order_synced(local_id, firestore_id=None):
    """Mark a local order as synced and store the Firestore document id.

    `local_id` may be an integer or string representation. If `firestore_id`
    is provided, it will be saved to `firestore_id` column.
    """
    conn = get_connection()
    cursor = conn.cursor()
    if firestore_id:
        cursor.execute('UPDATE orders SET synced = 1, firestore_id = ? WHERE local_id = ?', (firestore_id, local_id))
    else:
        cursor.execute('UPDATE orders SET synced = 1 WHERE local_id = ?', (local_id,))
    conn.commit()
    conn.close()

def get_low_stock_products(threshold=5):
    """Fetch products where stock_quantity is <= threshold"""
    conn = get_connection()
    prods = conn.execute(
        'SELECT id, name, stock_quantity FROM products WHERE stock_quantity <= ? ORDER BY stock_quantity ASC', 
        (threshold,)
    ).fetchall()
    conn.close()
    return [dict(p) for p in prods]

def add_product_stock(product_id, added_quantity):
    """Increase stock for a product"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE products SET stock_quantity = stock_quantity + ? WHERE id = ?',
        (added_quantity, product_id)
    )
    conn.commit()
    conn.close()
    return True

def get_daily_sales_report(target_date):
    """
    Get revenue, total orders, and top products for a specific date (YYYY-MM-DD).
    Also returns all orders for the table display.

    NOTE: Rows are filtered in SQL via strftime('%Y-%m-%d', created_at).
    All local timestamps are stored as naive ICT wall-clock strings, so plain
    strftime bucketing is correct (no timezone modifier needed).
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM orders WHERE strftime('%Y-%m-%d', created_at) = ?",
        (target_date,)
    ).fetchall()
    conn.close()

    total_revenue = 0
    total_orders = 0
    item_summary = {}
    day_orders = []

    for o in rows:
        day_orders.append(o)
        if o['status'].strip().lower() in PAID_STATUSES:
            total_revenue += float(o['total'] or 0)
            total_orders += 1
            try:
                items = json.loads(o['items'])
            except (json.JSONDecodeError, TypeError):
                items = []
            for item in items:
                name = item.get('name', 'Unknown')
                qty = int(item.get('quantity', 0) or item.get('qty', 0))
                if name in item_summary:
                    item_summary[name] += qty
                else:
                    item_summary[name] = qty

    # Sort items by quantity and get top 10
    sorted_items = sorted(item_summary.items(), key=lambda x: x[1], reverse=True)
    top_items = [{"name": name, "qty": qty} for name, qty in sorted_items[:10]]

    return {
        "revenue": total_revenue,
        "order_count": total_orders,
        "top_products": top_items,
        "orders": [dict(o) for o in day_orders]
    }

def get_monthly_sales_report(target_month):
    """
    Get revenue, total orders, and top products for a specific month (YYYY-MM).
    Also returns all orders for the table display.

    NOTE: Rows are filtered in SQL via strftime('%Y-%m', created_at).
    All local timestamps are stored as naive ICT wall-clock strings, so plain
    strftime bucketing is correct (no timezone modifier needed).
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM orders WHERE strftime('%Y-%m', created_at) = ?",
        (target_month,)
    ).fetchall()
    conn.close()

    total_revenue = 0
    total_orders = 0
    item_summary = {}
    month_orders = []

    for o in rows:
        month_orders.append(o)
        if o['status'].strip().lower() in PAID_STATUSES:
            total_revenue += float(o['total'] or 0)
            total_orders += 1
            try:
                items = json.loads(o['items'])
            except (json.JSONDecodeError, TypeError):
                items = []
            for item in items:
                name = item.get('name', 'Unknown')
                qty = int(item.get('quantity', 0) or item.get('qty', 0))
                if name in item_summary:
                    item_summary[name] += qty
                else:
                    item_summary[name] = qty

    sorted_items = sorted(item_summary.items(), key=lambda x: x[1], reverse=True)
    top_10 = [{"name": name, "qty": qty} for name, qty in sorted_items[:10]]

    return {
        "revenue": total_revenue,
        "order_count": total_orders,
        "top_products": top_10,
        "orders": [dict(o) for o in month_orders]
    }

def get_annual_sales_report(target_year):
    """
    Get revenue, total orders, and top products for a specific year (YYYY).
    Also returns all orders for the table display.

    NOTE: Rows are filtered in SQL via strftime('%Y', created_at).
    All local timestamps are stored as naive ICT wall-clock strings, so plain
    strftime bucketing is correct (no timezone modifier needed).
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM orders WHERE strftime('%Y', created_at) = ?",
        (str(target_year),)
    ).fetchall()
    conn.close()

    total_revenue = 0
    total_orders = 0
    item_summary = {}
    year_orders = []

    for o in rows:
        year_orders.append(o)
        if o['status'].strip().lower() in PAID_STATUSES:
            total_revenue += float(o['total'] or 0)
            total_orders += 1
            try:
                items = json.loads(o['items'])
            except (json.JSONDecodeError, TypeError):
                items = []
            for item in items:
                name = item.get('name', 'Unknown')
                qty = int(item.get('quantity', 0) or item.get('qty', 0))
                if name in item_summary:
                    item_summary[name] += qty
                else:
                    item_summary[name] = qty

    sorted_items = sorted(item_summary.items(), key=lambda x: x[1], reverse=True)
    top_items = [{"name": name, "qty": qty} for name, qty in sorted_items[:10]]

    return {
        "revenue": total_revenue,
        "order_count": total_orders,
        "top_products": top_items,
        "orders": [dict(o) for o in year_orders]
    }

def push_sales_to_firestore():
    """
    Push all sales/invoices for the current day to Firestore.
    This ensures the web dashboard has the latest sales data.
    """
    from datetime import datetime
    import json
    import traceback
    
    try:
        # Import here to avoid circular imports if any
        from sync import get_firestore_db, ICT_TZ
        db = get_firestore_db()
        if not db:
            print("Could not connect to Firestore to push sales.")
            return False
            
        today_str = datetime.now(ICT_TZ).strftime('%Y-%m-%d')
        
        # Fetch ALL raw order records for today directly from the database
        # We do not filter by is_synced to ensure a forceful overwrite
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM orders 
            WHERE date(created_at) = ? OR created_at LIKE ?
        ''', (today_str, f"{today_str}%"))
        
        orders = cursor.fetchall()
        conn.close()
        
        print(f"--> [Sales Sync] Force pushing {len(orders)} orders for today to Firestore...")
        
        count = 0
        for order in orders:
            # Prepare order data for Firestore
            try:
                items = json.loads(order['items']) if isinstance(order['items'], str) else order['items']
            except:
                items = []
                
            order_data = {
                'items': items,
                'total': order['total'],
                'status': order['status'],
                'tran_id': order['tran_id'],
                'user': order['user']
            }
            
            # Handle timestamp
            if order['created_at']:
                try:
                    created_dt = datetime.fromisoformat(order['created_at'])
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=ICT_TZ)
                    else:
                        created_dt = created_dt.astimezone(ICT_TZ)
                    order_data['created_at'] = created_dt
                except:
                    order_data['created_at'] = datetime.now(ICT_TZ)
            else:
                order_data['created_at'] = datetime.now(ICT_TZ)
                
            # Use tran_id as document ID if available, else local_id
            doc_id = order['tran_id'] if order['tran_id'] else f"local_{order['id']}"
            
            # Push to 'orders' collection
            db.collection('orders').document(doc_id).set(order_data, merge=True)
            count += 1
            
        print(f"Successfully pushed {count} sales records to Firestore for {today_str}")
        return True
    except Exception as e:
        print(f"Error pushing sales to Firestore: {e}")
        traceback.print_exc()
        return False

def get_activities(limit=20):
    """
    Retrieve recent activities from orders and stock history
    Returns a list of activity dictionaries with title, description, time, icon, and color
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        activities = []
        
        # Get recent orders with details
        cursor.execute("""
            SELECT local_id, user, created_at, items, total, status 
            FROM orders 
            ORDER BY created_at DESC 
            LIMIT ?
        """, (limit,))
        
        orders = cursor.fetchall()
        
        for order in orders:
            order_id, username, created_at, items_json, total_amount, status = order
            
            try:
                # Parse items JSON
                items = json.loads(items_json) if items_json else []
                
                # Convert created_at to datetime if it's a string (normalize to ICT)
                if isinstance(created_at, str):
                    order_time = cambodia_time(datetime.fromisoformat(created_at))
                else:
                    order_time = cambodia_time(created_at)
                
                # Build item description
                item_names = []
                for item in items[:3]:  # Show first 3 items
                    item_names.append(f"{item.get('name', 'មិនស្គាល់')} x{item.get('qty', 1)}")
                
                if len(items) > 3:
                    item_description = ", ".join(item_names) + f" និងផលិតផលផ្សេងទៀត {len(items) - 3} ផលិតផល"
                else:
                    item_description = ", ".join(item_names)
                
                # Determine activity color and icon based on status
                if status == 'completed' or status == 'paid':
                    color = '#059669'  # Green
                    icon = 'check-circle'
                    title = 'ការលក់ពេញលេញ (Completed Sale)'
                elif status == 'pending':
                    color = '#f59e0b'  # Amber
                    icon = 'clock'
                    title = 'ការលក់រង់ចាំ (Pending Sale)'
                else:
                    color = '#6366f1'  # Indigo
                    icon = 'shopping-bag'
                    title = 'ការលក់ថ្មី (New Sale)'
                
                # Format description with username and items
                description = f"បុគ្គលិក <strong>{username}</strong> បានលក់ {item_description}។"
                
                activities.append({
                    'title': title,
                    'description': description,
                    'time_obj': order_time,
                    'icon': icon,
                    'color': color,
                    'order_id': order_id,
                    'status': status
                })
            except Exception as e:
                print(f"Error processing order {order_id}: {e}")
                continue
                
        # Get recent stock history
        cursor.execute("""
            SELECT sh.id, sh.product_id, sh.change_amount, sh.reason, sh.created_at, p.name
            FROM stock_history sh
            LEFT JOIN products p ON sh.product_id = p.id
            ORDER BY sh.created_at DESC
            LIMIT ?
        """, (limit,))
        
        stock_events = cursor.fetchall()
        
        for event in stock_events:
            event_id, product_id, change_amount, reason, created_at, product_name = event
            
            try:
                if isinstance(created_at, str):
                    event_time = cambodia_time(datetime.fromisoformat(created_at))
                else:
                    event_time = cambodia_time(created_at)
                    
                product_name = product_name or "ផលិតផលមិនស្គាល់"
                
                if change_amount > 0:
                    title = 'បន្ថែមស្តុក (Restock)'
                    color = '#3b82f6'  # Blue
                    icon = 'package-plus'
                    description = f"បានបន្ថែមស្តុក <strong>{product_name}</strong> ចំនួន {change_amount} ឯកតា។ មូលហេតុ: {reason}"
                else:
                    title = 'កាត់ស្តុក (Stock Reduction)'
                    color = '#ef4444'  # Red
                    icon = 'package-minus'
                    description = f"បានកាត់ស្តុក <strong>{product_name}</strong> ចំនួន {abs(change_amount)} ឯកតា។ មូលហេតុ: {reason}"
                    
                activities.append({
                    'title': title,
                    'description': description,
                    'time_obj': event_time,
                    'icon': icon,
                    'color': color,
                    'event_id': event_id,
                    'type': 'stock'
                })
            except Exception as e:
                print(f"Error processing stock event {event_id}: {e}")
                continue
                
        # Sort all activities by time descending
        activities.sort(key=lambda x: x['time_obj'], reverse=True)
        
        # Keep only the requested limit
        activities = activities[:limit]
        
        # Format time strings for the final list
        now_ict = cambodia_time(datetime.now())
        for activity in activities:
            time_diff = now_ict - activity['time_obj']
            if time_diff.total_seconds() < 60:
                time_str = "ម៉ោងនេះ"
            elif time_diff.total_seconds() < 3600:
                mins = int(time_diff.total_seconds() / 60)
                time_str = f"{mins} នាទីមុន"
            elif time_diff.total_seconds() < 86400:
                hours = int(time_diff.total_seconds() / 3600)
                time_str = f"{hours} ម៉ោងមុន"
            else:
                time_str = activity['time_obj'].strftime('%d/%m/%Y %H:%M')
                
            activity['time'] = time_str
            # Remove the datetime object as it's not JSON serializable
            del activity['time_obj']
        
        conn.close()
        return activities
        
    except Exception as e:
        print(f"Error fetching activities: {e}")
        return []

# --- EXECUTIVE DASHBOARD HELPERS (managerView / /api/dashboard-stats) ---

def get_dashboard_stats(riel_rate=4000):
    """
    Aggregate executive-dashboard metrics in a single optimized pass.

    Returns a dict with:
      - today_revenue_usd / today_revenue_riel
      - today_gross_profit_usd / today_gross_profit_riel
      - today_order_count
      - low_stock_count
      - revenue_7d            : [{date, label, usd, riel}, ...] oldest -> newest
      - top_categories        : [{category, qty, revenue_usd}, ...] top 5
      - recent_orders         : top 5 paid/completed orders
      - recent_stock_movements: top 5 stock_history entries w/ product name

    Gross profit is computed per sold item as:
        qty * (item_price - products.cost_price)
    The order items JSON stores selling price + qty, while cost_price
    lives on the products table (joined at query time).
    """
    now_ict = cambodia_time(datetime.now())
    today_start_dt = cambodia_time(datetime(now_ict.year, now_ict.month, now_ict.day))
    week_ago_dt = today_start_dt - timedelta(days=6)

    # 1. Today's + 7-day paid/completed orders (raw, for revenue + profit).
    #    Uses the module-level canonical PAID set (PAID_STATUSES_SQL) so the
    #    dashboard can never disagree with the sales-report revenue counters.
    #    SQL-level DATE() filter limits the scan to the last 7 days instead of
    #    pulling the entire orders table — more efficient and guarantees the
    #    query hits the live rows, not any stale cache or materialized view.
    today_str = today_start_dt.strftime('%Y-%m-%d')
    week_ago_str = week_ago_dt.strftime('%Y-%m-%d')
    conn = get_connection()
    all_rows = conn.execute(
        f"""SELECT * FROM orders
            WHERE {PAID_STATUSES_SQL}
              AND DATE(created_at) >= DATE(?)""",
        (week_ago_str,)
    ).fetchall()
    today_rows = []
    revenue_rows = []
    for o in all_rows:
        try:
            ts = cambodia_time(datetime.fromisoformat(o['created_at']))
        except Exception:
            continue
        if today_start_dt <= ts:
            today_rows.append(o)
        if week_ago_dt <= ts:
            revenue_rows.append((ts, o))
    # 2. Product cost lookup (single query, reused across all orders)
    cost_map = {}
    prod_rows = conn.execute(
        "SELECT id, cost_price FROM products WHERE cost_price IS NOT NULL"
    ).fetchall()
    for row in prod_rows:
        cost_map[row['id']] = float(row['cost_price'] or 0.0)

    # 3. Low stock count (stock_quantity <= 5 per requirement)
    low_stock = conn.execute(
        "SELECT COUNT(*) AS c FROM products WHERE stock_quantity <= ?", (5,)
    ).fetchone()

    # 4. 7-day revenue series (sum total of paid/completed orders per ICT calendar day)
    revenue_by_day = {}
    for ts, o in revenue_rows:
        day = ts.strftime('%Y-%m-%d')
        revenue_by_day[day] = revenue_by_day.get(day, 0.0) + float(o['total'] or 0)

    # 5. Top selling categories (qty + revenue from paid/completed orders in last 7 days)
    cat_agg = {}
    for ts, o in revenue_rows:
        try:
            item_list = json.loads(o['items'] or '[]')
        except (json.JSONDecodeError, TypeError):
            item_list = []
        for item in item_list:
            qty = int(item.get('quantity', 0) or item.get('qty', 0))
            price = float(item.get('price', 0) or 0)
            cat = item.get('category') or 'other'
            entry = cat_agg.setdefault(cat, {'qty': 0, 'revenue': 0.0})
            entry['qty'] += qty
            entry['revenue'] += qty * price
    top_categories = sorted(
        cat_agg.items(), key=lambda kv: kv[1]['qty'], reverse=True
    )[:5]
    top_categories = [
        {'category': cat, 'qty': agg['qty'], 'revenue_usd': round(agg['revenue'], 2)}
        for cat, agg in top_categories
    ]

    # 6. Recent invoices (top 5 paid/completed by created_at desc, ICT-normalized)
    recent_rows = conn.execute(
        f"""SELECT tran_id, local_id, total, user, status, created_at
            FROM orders
            WHERE {PAID_STATUSES_SQL}"""
    ).fetchall()

    # 6b. LIVE FALLBACK GUARD (anti-desync)
    # --------------------------------
    # The numbers above are computed from rows that were paid at the moment
    # this function started. If those orders were subsequently deleted (or
    # their status changed away from the paid set) while this call was in
    # flight, the summary cards would show stale revenue/counts even though
    # the sales-report table (which lists ALL order rows) shows 0 rows.
    #
    # So we re-query the orders table directly and count TODAY's PAID rows
    # only -- the exact canonical paid set, matching the revenue pipeline
    # (pending/ABA rows are NOT included because they contribute $0 until
    # the Telegram listener flips them to 'paid'; the reports page has its
    # own pending filter for visibility). Status matching is
    # case-insensitive (LOWER + TRIM) so 'Paid'/'PAID'/'paid by cash' rows
    # never slip through. The date-boundary comparison mirrors
    # get_daily_sales_report(): created_at is normalized with cambodia_time()
    # (naive local rows are treated as ICT, +07:00-aware rows are converted).
    # If zero paid rows remain today, the guard hard-forces the dashboard
    # numbers to zero so the cards can never disagree with an empty table.
    live_today_count = 0
    for row in conn.execute(
        f"""SELECT created_at FROM orders
            WHERE {PAID_STATUSES_SQL}"""
    ).fetchall():
        try:
            ts = cambodia_time(datetime.fromisoformat(str(row['created_at'])))
        except Exception:
            continue
        if today_start_dt <= ts < (today_start_dt + timedelta(days=1)):
            live_today_count += 1

    # 7. Recent stock movements (top 5 by created_at desc, join product name)
    stock_rows = conn.execute(
        """SELECT sh.product_id, sh.change_amount, sh.reason, sh.created_at, p.name AS product_name
           FROM stock_history sh
           LEFT JOIN products p ON sh.product_id = p.id"""
    ).fetchall()

    conn.close()

    # Normalize + sort in Python so naive (ICT) and aware (UTC) mix correctly
    def _sort_key(created_at):
        try:
            return cambodia_time(datetime.fromisoformat(str(created_at)))
        except Exception:
            return now_ict
    recent_rows = sorted(recent_rows, key=lambda r: _sort_key(r['created_at']), reverse=True)[:5]
    stock_rows = sorted(stock_rows, key=lambda s: _sort_key(s['created_at']), reverse=True)[:5]

    # --- Compute today's revenue + gross profit from today_rows ---
    today_revenue = 0.0
    today_profit = 0.0
    today_orders = 0
    for o in today_rows:
        today_revenue += float(o['total'] or 0)
        today_orders += 1
        try:
            item_list = json.loads(o['items'] or '[]')
        except (json.JSONDecodeError, TypeError):
            item_list = []
        for item in item_list:
            qty = int(item.get('quantity', 0) or item.get('qty', 0))
            price = float(item.get('price', 0) or 0)
            cost = cost_map.get(item.get('id'), 0.0)
            today_profit += qty * (price - cost)

    # --- Build the 7-day series (fill missing days with 0) ---
    revenue_7d = []
    for i in range(6, -1, -1):
        day = (now_ict - timedelta(days=i)).strftime('%Y-%m-%d')
        usd = revenue_by_day.get(day, 0.0)
        revenue_7d.append({
            'date': day,
            'label': day[5:],  # MM-DD
            'usd': round(usd, 2),
            'riel': round(usd * riel_rate)
        })

    # --- LIVE FALLBACK GUARD (anti-desync) ---
    # The metrics above were computed from rows that were paid at the moment
    # this function started. If those orders were subsequently deleted (or
    # their status changed away from the paid set) while this call was in
    # flight, the summary cards would show stale revenue/counts even though
    # the sales-report table (which lists ALL order rows) shows 0 rows. The
    # guard re-queries the orders table directly for today's date range; if
    # zero PAID orders remain, it hard-forces the dashboard numbers to zero
    # so the cards can never disagree with an empty table.
    if live_today_count == 0 and today_orders > 0:
        print("[dashboard-stats] Live guard: today's paid orders are gone "
              f"(computed {today_orders} stale) -> forcing today's metrics to 0")
        today_revenue = 0.0
        today_profit = 0.0
        today_orders = 0
        today_key = now_ict.strftime('%Y-%m-%d')
        revenue_by_day[today_key] = 0.0
        top_categories = []
        recent_rows = []

    # --- Recent orders (already sorted desc, keep top 5) ---
    recent_orders = []
    for r in recent_rows:
        recent_orders.append({
            'tran_id': r['tran_id'] or f"LOCAL-{r['local_id']}",
            'total_usd': round(float(r['total'] or 0), 2),
            'total_riel': round(float(r['total'] or 0) * riel_rate),
            'user': r['user'] or 'guest',
            'status': r['status'],
            'created_at': r['created_at']
        })

    # --- Recent stock movements (delta type: Sale vs Restock) ---
    recent_stock = []
    for s in stock_rows:
        delta = int(s['change_amount'] or 0)
        recent_stock.append({
            'product_name': s['product_name'] or 'Unknown',
            'product_id': s['product_id'],
            'delta': delta,
            'delta_type': 'Restock' if delta > 0 else 'Sale',
            'quantity': abs(delta),
            'reason': s['reason'] or '',
            'created_at': s['created_at']
        })

    return {
        'today_revenue_usd': round(today_revenue, 2),
        'today_revenue_riel': round(today_revenue * riel_rate),
        'today_gross_profit_usd': round(today_profit, 2),
        'today_gross_profit_riel': round(today_profit * riel_rate),
        'today_order_count': today_orders,
        'low_stock_count': int(low_stock['c'] if low_stock else 0),
        'revenue_7d': revenue_7d,
        'top_categories': top_categories,
        'recent_orders': recent_orders,
        'recent_stock_movements': recent_stock
    }


def get_recent_orders(limit=5):
    """Return the most recent paid/completed orders (used by dashboard tables)."""
    return get_dashboard_stats().get('recent_orders', [])[:limit]


def get_recent_stock_movements(limit=5):
    """Return the most recent stock history entries (used by dashboard tables)."""
    return get_dashboard_stats().get('recent_stock_movements', [])[:limit]


# Initialize DB on import
init_db()
