import sqlite3
import os
from flask import jsonify

def get_db_connection():
    # Update this path to your actual local database path
    db_path = 'pos_local.db'
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def purge_test_data():
    """
    Safely purges unwanted test data entries and resets placeholders.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # 1. Delete products with 'test' in the name
        cursor.execute("DELETE FROM products WHERE name LIKE '%test%' OR name LIKE '%Test%'")
        deleted_products = cursor.rowcount
        
        # 2. Reset images to default.jpg if they aren't data:image strings or actual files
        # This handles cases where mixed aspect ratio test images might have been uploaded
        cursor.execute("""
            UPDATE products 
            SET image = 'default.jpg' 
            WHERE image IS NULL 
               OR image = '' 
               OR (image NOT LIKE 'data:image%' AND image NOT LIKE '%.jpg' AND image NOT LIKE '%.png')
        """)
        reset_images = cursor.rowcount
        
        # 3. Purge old orders (e.g., more than 30 days old test orders)
        # Note: In a production environment, you might want to archive instead.
        # cursor.execute("DELETE FROM orders WHERE status = 'pending' AND created_at < date('now', '-30 days')")
        
        conn.commit()
        return {
            "status": "success",
            "message": f"Purged {deleted_products} test products and reset {reset_images} image placeholders.",
            "details": {
                "deleted_products": deleted_products,
                "reset_images": reset_images
            }
        }
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

# Example Flask Route implementation
# @app.route('/api/admin/hygiene/purge-test', methods=['POST'])
# @admin_required
# def api_purge_test_data():
#     result = purge_test_data()
#     return jsonify(result)

if __name__ == "__main__":
    print("Starting Data Hygiene process...")
    result = purge_test_data()
    print(result['message'])
