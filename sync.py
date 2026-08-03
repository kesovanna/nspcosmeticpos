import firebase_admin
from firebase_admin import firestore
import local_db
import traceback
from datetime import datetime
import json
import threading
import time

# --- SYNC STATE MANAGEMENT ---
_sync_lock = threading.Lock()
_sync_timer = None
_is_syncing = False

# Global sync status for the UI
sync_status = {
    'is_syncing': False,
    'last_sync': None
}

def get_firestore_db():
    try:
        return firestore.client()
    except Exception as e:
        print(f"Error initializing Firestore client: {e}")
        traceback.print_exc()
        return None

def pull_from_firestore():
    """Pull products and users from Firestore to local SQLite"""
    db = get_firestore_db()
    if not db:
        return False, "Could not connect to Firestore"

    try:
        # 1. Sync Products
        items_ref = db.collection('items').stream()
        products_list = []
        deleted_ids = set(local_db.get_deleted_products()) # Track what we want deleted
        
        for doc in items_ref:
            if doc.id in deleted_ids:
                continue # Skip items we are trying to delete
                
            p = doc.to_dict()
            p['id'] = doc.id
            
            # Audit: Ensure price normalization (Safety check for inflated cloud data)
            # Pull current rate from settings if available, else default
            current_rate = float(local_db.get_setting('exchange_rate', 4039))
            if p.get('price', 0) > 1000:
                p['price'] = p['price'] / current_rate

            # Handle createdAt conversion if it's a Firestore Timestamp
            if 'createdAt' in p and hasattr(p['createdAt'], 'isoformat'):
                p['createdAt'] = p['createdAt'].isoformat()
            products_list.append(p)
        
        local_db.save_products(products_list)

        # 2. Sync Users
        users_ref = db.collection('users').stream()
        users_list = []
        for doc in users_ref:
            u = doc.to_dict()
            u['username'] = doc.id # Username is the document ID
            users_list.append(u)
        local_db.save_users(users_list)

        return True, "Successfully pulled data from Firestore"
    except Exception as e:
        print(f"Error in pull_from_firestore: {e}")
        traceback.print_exc()
        return False, f"Error pulling data: {str(e)}"

def push_to_firestore():
    """Push unsynced local orders, products, and user profiles to Firestore"""
    db = get_firestore_db()
    print(f"DEBUG push_to_firestore local_db={getattr(local_db, '__file__', None)} has_get_unsynced={hasattr(local_db, 'get_unsynced_orders')}")
    if not db:
        return False, "Could not connect to Firestore"

    try:
        count = 0

        # 1. Process Deletions (Delete from Firestore if marked locally)
        deleted_ids = local_db.get_deleted_products()
        if deleted_ids:
            print(f"Syncing {len(deleted_ids)} deletions to Firestore...")
            successfully_deleted = []
            for prod_id in deleted_ids:
                try:
                    db.collection('items').document(prod_id).delete()
                    successfully_deleted.append(prod_id)
                    count += 1
                except Exception as e:
                    print(f"Failed to delete {prod_id} from Firestore: {e}")
            
            # Clear log only for IDs that were successfully deleted from Firestore
            if successfully_deleted:
                local_db.clear_deletion_log(successfully_deleted)

        # 2. Push Products (Overwrite/Update Firestore with latest local data)
        products = local_db.get_products()
        for prod in products:
            prod_data = dict(prod)
            prod_id = prod_data.pop('id', None)
            if prod_id:
                db.collection('items').document(prod_id).set(prod_data)
                count += 1

        # 3. Push User Profiles (Sync all local users to Firestore)
        try:
            users = local_db.get_all_users()
            for user in users:
                # Audit: Specifically call a function to get user images
                profile_data = local_db.get_user_profile(user['username'])
                if not profile_data:
                    continue

                user_data = {
                    'profile_image': profile_data.get('profile_image'),
                    'cover_image': profile_data.get('cover_image')
                }

                # Only push if they have images
                if user_data['profile_image'] or user_data['cover_image']:
                    print(f"Syncing images for user: {user.get('username')}") # Debug log
                    # Use set(merge=True) so it works even if the document doesn't exist yet
                    db.collection('users').document(user['username']).set(user_data, merge=True)
                    count += 1
        except Exception as e:
            print(f"Error syncing user profile images: {e}")
            traceback.print_exc()
            return False, f"Error syncing user profile images: {str(e)}"

        # 3. Push Orders
        unsynced = local_db.get_unsynced_orders()
        for order in unsynced:
            # Prepare data for Firestore
            order_data = {
                'items': json.loads(order['items']),
                'total': order['total'],
                'status': order['status'],
                'tran_id': order['tran_id'],
                'user': order['user']
            }
            # Handle created_at
            if order['created_at']:
                try:
                    order_data['created_at'] = datetime.fromisoformat(order['created_at'])
                except:
                    order_data['created_at'] = datetime.now()
            else:
                order_data['created_at'] = datetime.now()

            # Push to Firestore
            _, doc_ref = db.collection('orders').add(order_data)
            
            # Mark as synced locally
            local_db.mark_order_synced(order['local_id'], doc_ref.id)
            count += 1

        return True, f"Successfully pushed {count} records to Firestore"
    except Exception as e:
        print(f"Error in push_to_firestore: {e}")
        traceback.print_exc()
        return False, f"Error pushing data: {str(e)}"

def sync_all():
    """Perform a full sync (Push then Pull)"""
    global _is_syncing
    with _sync_lock:
        if _is_syncing:
            return {'success': False, 'message': 'Sync already in progress'}
        _is_syncing = True
        sync_status['is_syncing'] = True

    try:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Syncing started...")
        push_ok, push_msg = push_to_firestore()
        pull_ok, pull_msg = pull_from_firestore()
        
        success = pull_ok and push_ok
        sync_status['last_sync'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Sync finished. Success: {success}")
        
        return {
            'success': success,
            'pull': pull_msg,
            'push': push_msg
        }
    finally:
        with _sync_lock:
            _is_syncing = False
            sync_status['is_syncing'] = False

def trigger_auto_sync(delay=5):
    """
    Schedules a sync to happen after 'delay' seconds.
    If called again before the delay expires, the timer resets.
    """
    global _sync_timer
    with _sync_lock:
        if _sync_timer is not None:
            _sync_timer.cancel()
        
        _sync_timer = threading.Timer(delay, sync_all)
        _sync_timer.daemon = True
        _sync_timer.start()
        print(f"Auto-sync scheduled in {delay} seconds...")

def start_background_sync(interval=30):
    """
    Starts a background thread that syncs every 'interval' seconds (default 30s).
    """
    def sync_loop():
        while True:
            time.sleep(interval)
            sync_all()

    thread = threading.Thread(target=sync_loop, daemon=True)
    thread.name = "BackgroundSyncThread"
    thread.start()
    print(f"Background sync service started (Interval: {interval}s)")
