import firebase_admin
from firebase_admin import firestore
import local_db
import traceback
from datetime import datetime, timezone, timedelta
import json
import threading
import time
import os
from werkzeug.utils import secure_filename

# --- Cambodia ICT = UTC+7 (fixed, no DST) ---
# Used when pushing order created_at to Firestore so the SDK stores the
# correct UTC instant instead of misinterpreting a naive local string as UTC.
ICT_TZ = timezone(timedelta(hours=7), name='ICT')

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
        return firestore.client(database_id='default')
    except Exception as e:
        print(f"Error initializing Firestore client: {e}")
        traceback.print_exc()
        return None

# --- PILLAR 1 + 2 + 3: EXACTLY-ONCE TRANSACTIONAL ORDER PUSH ---
def push_order_transactionally(db, order_data):
    """
    Push one order to Firestore with EXACTLY-ONCE semantics.
    Fixes ReadAfterWriteError by moving ALL reads (including the order doc check) 
    strictly inside Phase 1 of the transaction boundary.
    """
    tran_id = order_data.get('tran_id')
    if not tran_id:
        return False, "Order has no tran_id; cannot push idempotently"

    doc_ref = db.collection('orders').document(tran_id)
    items = order_data.get('items', []) or []
    new_status = order_data.get('status')

    @firestore.transactional
    def create_order_and_deduct_stock(transaction):
        # ------------------------------------------------------------
        # PHASE 1: ALL READS FIRST (Strictly required by Firestore)
        # ------------------------------------------------------------
        # 1. Read the order document snapshot FIRST inside the transaction
        existing_snapshot = doc_ref.get(transaction=transaction)
        
        needs_deduction = False
        action_type = "set" # Default operation style
        
        if existing_snapshot.exists:
            prev_status = existing_snapshot.get('status')
            # Already confirmed on cloud -> stock was deducted -> just merge updates
            if prev_status in ('paid', 'completed') and new_status in ('paid', 'completed'):
                action_type = "merge"
            else:
                # Pending -> confirmed transition: deduct cloud stock now
                needs_deduction = new_status in ('paid', 'completed')
                action_type = "set"
        else:
            # Brand new cloud document: deduct only for confirmed sales
            needs_deduction = new_status in ('paid', 'completed')
            action_type = "set"

        # 2. Gather all product stock snapshots upfront within the transaction
        stock_snapshots = []
        if needs_deduction:
            for item in items:
                prod_id = item.get('id') or item.get('product_id')
                if not prod_id:
                    continue
                qty_to_deduct = int(item.get('quantity', 0) or item.get('qty', 0))
                if qty_to_deduct <= 0:
                    continue
                
                item_ref = db.collection('items').document(prod_id)
                # READ statement inside transaction
                product_snapshot = item_ref.get(transaction=transaction)
                stock_snapshots.append({
                    'ref': item_ref,
                    'snapshot': product_snapshot,
                    'qty': qty_to_deduct
                })

        # ------------------------------------------------------------
        # PHASE 2: ALL WRITES NEXT (No more reads allowed after this)
        # ------------------------------------------------------------
        # 1. Write the Order Document based on inferred action type
        if action_type == "merge":
            transaction.set(doc_ref, order_data, merge=True)
            # Early return within transaction context for merged metadata
            return True

        transaction.set(doc_ref, order_data)
            
        # 2. Atomically update stock for every sold item using pre-fetched snapshots
        if needs_deduction:
            for entry in stock_snapshots:
                item_ref = entry['ref']
                snapshot = entry['snapshot']
                qty_to_deduct = entry['qty']
                
                if not snapshot.exists:
                    continue
                    
                current_stock = snapshot.get('stock_quantity') or 0
                # Clamp at 0: never allow negative cloud stock during reconciliation
                new_stock = max(0, current_stock - qty_to_deduct)
                
                if new_stock != current_stock:
                    transaction.update(item_ref, {'stock_quantity': new_stock})
                    
        return True

    try:
        transaction_ref = db.transaction()
        create_order_and_deduct_stock(transaction_ref)
        return True, "Order pushed and cloud stock calculated transactionally"
    except Exception as e:
        print(f"❌ Firestore Transaction Failed for {tran_id}: {e}")
        return False, str(e)

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
        return False, "Could not connect to Firestore", []

    synced_product_names = []

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
            
            if successfully_deleted:
                local_db.clear_deletion_log(successfully_deleted)

        # 2. Push Products — FULL PARITY SYNC (unconditional, no timestamp/flag filters)
        # -----------------------------------------------------------------------
        # Fetches ALL products from the local SQLite DB and writes every one to
        # Firestore using .set() (overwrite semantics).  Each product is wrapped
        # in its own try/except so a single bad record NEVER aborts the loop.
        #
        # IMAGE HANDLING:
        #   - Google Drive URLs (https://drive.google.com/uc?...) are PRESERVED
        #     as-is — they are permanent public links managed by gdrive_storage.
        #   - Firebase Storage URLs (https://storage.googleapis.com/...) are
        #     also preserved.
        #   - Any other http(s) URL or data: URI is kept verbatim.
        #   - Local filesystem paths (no scheme) are replaced with 'default.jpg'
        #     so the cloud-hosted web app never receives a broken local path.
        # -----------------------------------------------------------------------
        products = local_db.get_products()
        print(f"[Product Sync] Starting full parity sync for {len(products)} local products...")
        for prod in products:
            prod_data = dict(prod)
            prod_id = prod_data.pop('id', None)
            if not prod_id:
                print(f"--> [Product Sync Warning] Skipping product with no ID: {prod_data.get('name', '?')}")
                continue
            try:
                # Sanitize image field: keep cloud URLs, replace local paths.
                image_val = prod_data.get('image', '')
                if image_val and not str(image_val).startswith('http') and not str(image_val).startswith('data:'):
                    print(f"--> [Product Sync] Product '{prod_data.get('name')}' has local image path '{image_val}'. Replacing with 'default.jpg' for cloud sync.")
                    prod_data['image'] = 'default.jpg'

                db.collection('items').document(prod_id).set(prod_data)
                synced_product_names.append(prod_data.get('name', 'Unknown Product'))
                count += 1
            except Exception as e:
                print(f"--> [Product Sync Warning] Failed on {prod_id} ('{prod_data.get('name', '?')}'): {e}")
                continue

        # 3. Push User Profiles (Sync all local users to Firestore)
        # CRITICAL: errors here must NEVER abort the function — orders (Section 4)
        # must always be pushed regardless of image sync failures.
        try:
            users = local_db.get_all_users()
            for user in users:
                profile_data = local_db.get_user_profile(user['username'])
                if not profile_data:
                    continue

                user_data = {
                    'profile_image': profile_data.get('profile_image'),
                    'cover_image': profile_data.get('cover_image')
                }

                if user_data['profile_image'] or user_data['cover_image']:
                    print(f"Syncing images for user: {user.get('username')}")
                    try:
                        db.collection('users').document(user['username']).set(user_data, merge=True)
                        count += 1
                    except Exception as img_e:
                        print(f"--> [Storage Warning] Image upload failed (404/Quota). Skipping image but continuing data sync... Error: {img_e}")
        except Exception as e:
            # Log the error but DO NOT return early — orders must still be pushed.
            print(f"--> [Storage Warning] Image upload failed (404/Quota). Skipping image but continuing data sync... Error: {e}")
            traceback.print_exc()

        # 4. Push Orders (PILLAR 1+2+3: exactly-once, atomic, collision-free)
        unsynced = local_db.get_unsynced_orders()
        for order in unsynced:
            order_data = {
                'items': json.loads(order['items']),
                'total': order['total'],
                'status': order['status'],
                'tran_id': order['tran_id'],
                'user': order['user']
            }
            if order['created_at']:
                try:
                    # ------------------------------------------------------------------
                    # TIMEZONE FIX: preserve the exact Cambodia wall-clock instant.
                    #
                    # The local DB stores NAIVE ICT strings like
                    # '2026-08-08T11:39:36' (no offset). Passing that to Firestore
                    # as a naive datetime makes the SDK serialize it as UTC+00:00,
                    # so reading it back and converting to ICT (+7) shifted it to
                    # 18:39 (the "06:39 PM instead of 11:40 AM" bug).
                    #
                    # Fix: attach the ICT timezone (+07:00) to the parsed datetime
                    # BEFORE the write. Firestore then stores the correct UTC
                    # instant (04:39:36Z), and the read-back cambodia_time()
                    # conversion yields exactly 11:39:36 ICT. Round-trip verified.
                    # ------------------------------------------------------------------
                    created_dt = datetime.fromisoformat(order['created_at'])
                    if created_dt.tzinfo is None:
                        # Naive local string -> assume it is already Cambodia wall-clock
                        created_dt = created_dt.replace(tzinfo=ICT_TZ)
                    else:
                        # Already aware (e.g. +00:00 from Firestore read-back) -> normalize
                        created_dt = created_dt.astimezone(ICT_TZ)
                    order_data['created_at'] = created_dt
                except:
                    order_data['created_at'] = datetime.now(ICT_TZ)
            else:
                order_data['created_at'] = datetime.now(ICT_TZ)

            pushed_ok, pushed_msg = push_order_transactionally(db, order_data)

            if pushed_ok:
                local_db.mark_order_synced(order['local_id'], order['tran_id'])
                count += 1
            else:
                print(f"⚠️ Skipping order {order.get('tran_id')}: {pushed_msg}")

        return True, f"Successfully pushed {count} records to Firestore", synced_product_names
    except Exception as e:
        print(f"Error in push_to_firestore: {e}")
        traceback.print_exc()
        return False, f"Error pushing data: {str(e)}", []

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
        push_ok, push_msg, synced_products = push_to_firestore()
        pull_ok, pull_msg = pull_from_firestore()
        
        success = pull_ok and push_ok
        sync_status['last_sync'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Sync finished. Success: {success}")
        
        return {
            'success': success,
            'pull': pull_msg,
            'push': push_msg,
            'synced_products': synced_products
        }
    finally:
        with _sync_lock:
            _is_syncing = False
            sync_status['is_syncing'] = False

def trigger_auto_sync(delay=5):
    """Schedules a sync to happen after 'delay' seconds."""
    global _sync_timer
    with _sync_lock:
        if _sync_timer is not None:
            _sync_timer.cancel()
        
        _sync_timer = threading.Timer(delay, sync_all)
        _sync_timer.daemon = True
        _sync_timer.start()
        print(f"Auto-sync scheduled in {delay} seconds...")

def start_background_sync(interval=30):
    """Starts a background thread that syncs periodically."""
    def sync_loop():
        while True:
            time.sleep(interval)
            sync_all()

    thread = threading.Thread(target=sync_loop, daemon=True)
    thread.name = "BackgroundSyncThread"
    thread.start()
    print(f"Background sync service started (Interval: {interval}s)")