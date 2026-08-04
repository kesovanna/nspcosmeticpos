import sqlite3
import json
import os
import sys
from datetime import datetime

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
    conn = get_connection()
    rows = conn.execute('SELECT name FROM categories ORDER BY name ASC').fetchall()
    conn.close()
    return [row['name'] for row in rows]

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
    conn = get_connection()
    user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()
    return dict(user) if user else None

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

def get_products():
    conn = get_connection()
    prods = conn.execute('SELECT * FROM products ORDER BY createdAt DESC').fetchall()
    conn.close()
    return [dict(p) for p in prods]

def get_product(prod_id):
    conn = get_connection()
    prod = conn.execute('SELECT * FROM products WHERE id = ?', (prod_id,)).fetchone()
    conn.close()
    return dict(prod) if prod else None

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
def add_order(order_data):
    """Save order locally with synced=0"""
    conn = get_connection()
    cursor = conn.cursor()
    
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
        order_data.get('tran_id'),
        order_data.get('user')
    ))
    local_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return local_id
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
    """
    conn = get_connection()
    # Find orders for that date. Store uses ISO string for created_at.
    # We use LIKE 'target_date%' to match the date part.
    orders = conn.execute(
        "SELECT * FROM orders WHERE created_at LIKE ?",
        (f"{target_date}%",)
    ).fetchall()
    conn.close()

    total_revenue = 0
    total_orders = 0
    item_summary = {}

    for o in orders:
        if o['status'] in ('paid', 'completed'):
            total_revenue += float(o['total'] or 0)
            total_orders += 1
            items = json.loads(o['items'])
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
        "orders": [dict(o) for o in orders]
    }

def get_monthly_sales_report(target_month):
    """
    Get revenue, total orders, and top products for a specific month (YYYY-MM).
    Also returns all orders for the table display.
    """
    conn = get_connection()
    orders = conn.execute(
        "SELECT * FROM orders WHERE created_at LIKE ?",
        (f"{target_month}%",)
    ).fetchall()
    conn.close()

    total_revenue = 0
    total_orders = 0
    item_summary = {}

    for o in orders:
        if o['status'] in ('paid', 'completed'):
            total_revenue += float(o['total'] or 0)
            total_orders += 1
            items = json.loads(o['items'])
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
        "orders": [dict(o) for o in orders]
    }

def get_annual_sales_report(target_year):
    """
    Get revenue, total orders, and top products for a specific year (YYYY).
    Also returns all orders for the table display.
    """
    conn = get_connection()
    orders = conn.execute(
        "SELECT * FROM orders WHERE created_at LIKE ?",
        (f"{target_year}%",)
    ).fetchall()
    conn.close()

    total_revenue = 0
    total_orders = 0
    item_summary = {}

    for o in orders:
        if o['status'] in ('paid', 'completed'):
            total_revenue += float(o['total'] or 0)
            total_orders += 1
            items = json.loads(o['items'])
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
        "orders": [dict(o) for o in orders]
    }

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
                
                # Convert created_at to datetime if it's a string
                if isinstance(created_at, str):
                    order_time = datetime.fromisoformat(created_at)
                else:
                    order_time = created_at
                
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
                    event_time = datetime.fromisoformat(created_at)
                else:
                    event_time = created_at
                    
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
        for activity in activities:
            time_diff = datetime.now() - activity['time_obj']
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

# Initialize DB on import
init_db()
