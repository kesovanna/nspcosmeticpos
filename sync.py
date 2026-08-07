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

# --- PILLAR 1 + 2 + 3: EXACTLY-ONCE TRANSACTIONAL ORDER PUSH ---
def push_order_transactionally(db, order_data):
    """
    Push one order to Firestore with EXACTLY-ONCE semantics.

    - PILLAR 2: The Firestore document ID is the ``tran_id`` (UUID) itself, so
      offline and online devices can never collide, and retries overwrite the
      same document instead of duplicating.
    - PILLAR 1: Creating the order document and deducting cloud stock happen in
      ONE atomic Firestore transaction. If the transaction aborts, neither
      happened; if it commits, both happened exactly once.
    - PILLAR 3: If the document already exists with a confirmed status
      (paid/completed), stock was already deducted — we only merge updates.
      If it exists as ``pending`` and is now confirmed, we deduct at that
      transition point (exactly once).
    """
    tran_id = order_data.get('tran_id')
    if not tran_id:
        return False, "Order has no tran_id; cannot push idempotently"

    doc_ref = db.collection('orders').document(tran_id)
    items = order_data.get('items', []) or []
    new_status = order_data.get('status')

    existing = doc_ref.get()
    if existing.exists:
        prev_status = existing.get('status')
        # Already confirmed on cloud -> stock was deducted -> just merge updates
        if prev_status in ('paid', 'completed') and new_status in ('paid', 'completed'):
            doc_ref.set(order_data, merge=True)
            return True, "order already confirmed; metadata merged"
        # Pending -> confirmed transition: deduct cloud stock now (exactly once)
        needs_deduction = new_status in ('paid', 'completed')
    else:
        # Brand new document: deduct only for confirmed sales
        needs_deduction = new_status in ('paid', 'completed')

    if not needs_deduction:
        # Pending/unconfirmed order: store record only, never touch stock
        doc_ref.set(order_data)
        return True, "order created (pending, no stock deduction)"

    @firestore.transactional
    def create_order_and_deduct_stock(transaction):
        # 1. Create/overwrite the order document inside the transaction
        transaction.set(doc_ref, order_data)
        # 2. Atomically read-update stock for every sold item
        for item in items:
            prod_id = item.get('id') or item.get('product_id')
            if not prod_id:
                continue
            qty_to_deduct = int(item.get('quantity', 0) or item.get('qty', 0))
            if qty_to_deduct <= 0:
                continue
            item_ref = db.collection('items').document(prod_id)
            snapshot = item_ref.get(transaction=transaction)
            if not snapshot.exists:
                # Product missing on cloud (may have been deleted); skip item
                continue
            current_stock = snapshot.get('stock_quantity') or 0
            # Clamp at 0: never allow negative cloud stock during reconciliation
            new_stock = max(0, current_stock - qty_to_deduct)
            if new_stock != current_stock:
                transaction.update(item_ref, {'stock_quantity': new_stock})

    transaction = db.transaction()
    create_order_and_deduct_stock(transaction)
    return True, "order created and cloud stock deducted atomically"

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

        # 2b. Prune local users that no longer exist in Firestore
        # (Prevents deleted accounts from resurrecting in the local DB)
        firestore_usernames = {u['username'] for u in users_list}
        local_usernames = {u['username'] for u in local_db.get_all_users()}
        for username in (local_usernames - firestore_usernames):
            local_db.delete_user_local(username)

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

        # 3. Push Orders (PILLAR 1+2+3: exactly-once, atomic, collision-free)
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

            # Exactly-once, transactional push (see push_order_transactionally)
            pushed_ok, pushed_msg = push_order_transactionally(db, order_data)

            if pushed_ok:
                # Mark as synced locally; firestore_id == tran_id (UUID doc id)
                local_db.mark_order_synced(order['local_id'], order['tran_id'])
                count += 1
            else:
                print(f"⚠️ Skipping order {order.get('tran_id')}: {pushed_msg}")

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
