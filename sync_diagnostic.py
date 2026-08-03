import sqlite3
import firebase_admin
from firebase_admin import credentials, firestore
import os
import sys

# --- CONFIGURATION ---
DB_PATH = 'pos_local.db'
SERVICE_ACCOUNT_PATH = 'serviceAccountKey.json'

def get_local_connection():
    if not os.path.exists(DB_PATH):
        print(f"Error: Local database file '{DB_PATH}' not found.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_firebase():
    if not os.path.exists(SERVICE_ACCOUNT_PATH):
        print(f"Error: Firebase service account key '{SERVICE_ACCOUNT_PATH}' not found.")
        sys.exit(1)
    
    if not firebase_admin._apps:
        cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
        firebase_admin.initialize_app(cred)
    return firestore.client()

def run_diagnostic():
    print("--- NSP Cosmetic POS Sync Diagnostic ---")
    
    # 1. Local Totals
    conn = get_local_connection()
    local_products = conn.execute("SELECT id FROM products").fetchall()
    local_ids = set(row['id'] for row in local_products)
    local_count = len(local_ids)
    
    print(f"\n[Local Database]")
    print(f"Total products: {local_count}")
    
    # 2. Cloud Totals
    db = init_firebase()
    cloud_items = db.collection('items').stream()
    cloud_ids = set()
    for doc in cloud_items:
        cloud_ids.add(doc.id)
    cloud_count = len(cloud_ids)
    
    print(f"[Cloud Firestore]")
    print(f"Total documents (items): {cloud_count}")
    
    # 3. Compare the Two
    print(f"\n[Comparison]")
    mismatch_found = False
    
    only_local = local_ids - cloud_ids
    only_cloud = cloud_ids - local_ids
    
    if only_local:
        print(f"Mismatch: {len(only_local)} IDs exist locally but not in Cloud:")
        for idx, lid in enumerate(list(only_local)[:10]):
            print(f"  - {lid}")
        if len(only_local) > 10:
            print(f"  ... and {len(only_local) - 10} more")
        mismatch_found = True
    
    if only_cloud:
        print(f"Mismatch: {len(only_cloud)} IDs exist in Cloud but not locally:")
        for idx, cid in enumerate(list(only_cloud)[:10]):
            print(f"  - {cid}")
        if len(only_cloud) > 10:
            print(f"  ... and {len(only_cloud) - 10} more")
        mismatch_found = True
        
    if not only_local and not only_cloud:
        print("Product IDs match perfectly between Local and Cloud.")

    # 4. Checks Sync Logs (Orders and Deletions)
    print(f"\n[Sync Status Logs]")
    
    unsynced_orders = conn.execute("SELECT local_id FROM orders WHERE synced = 0").fetchall()
    pending_deletions = conn.execute("SELECT id FROM deleted_products").fetchall()
    
    if unsynced_orders:
        print(f"Pending: {len(unsynced_orders)} orders have not been synced to Cloud.")
        mismatch_found = True
    else:
        print("All local orders are synced.")
        
    if pending_deletions:
        print(f"Pending: {len(pending_deletions)} products are marked for deletion in Cloud.")
        mismatch_found = True
    else:
        print("No pending product deletions.")

    # Final Status
    print("\n" + "="*40)
    if mismatch_found:
        print("STATUS: Sync Mismatch Found / Operations Pending")
    else:
        print("STATUS: Sync OK")
    print("="*40)

    conn.close()

if __name__ == "__main__":
    try:
        run_diagnostic()
    except Exception as e:
        print(f"\nAn error occurred during diagnostic: {e}")
        import traceback
        traceback.print_exc()
