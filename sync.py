import firebase_admin
from firebase_admin import firestore
import local_db
import traceback
from datetime import datetime, timezone, timedelta
import json
import threading
import time
import os

# --- Cambodia ICT = UTC+7 (fixed, no DST) ---
ICT_TZ = timezone(timedelta(hours=7), name='ICT')

# --- SYNC STATE MANAGEMENT ---
_sync_lock = threading.Lock()
_sync_timer = None
_is_syncing = False

sync_status = {
    'is_syncing': False,
    'last_sync': None
}

def get_firestore_db():
    try:
        # បង្ខំឱ្យប្រើ client() សុទ្ធសាធ ដោយគ្មានការបញ្ជាក់ database_id ណាមួយឡើយ 
        # ដើម្បីការពារកុំឱ្យ Google Cloud បម្លែងវារត់ទៅជា %28default%29
        return firestore.client()
    except Exception as e:
        print(f"Error initializing Firestore client: {e}")
        traceback.print_exc()
        return None

def push_order_transactionally(db, order_data):
    tran_id = order_data.get('tran_id')
    if not tran_id:
        return False, "Order has no tran_id; cannot push idempotently"

    doc_ref = db.collection('orders').document(tran_id)
    items = order_data.get('items', []) or []
    new_status = order_data.get('status')

    @firestore.transactional
    def create_order_and_deduct_stock(transaction):
        existing_snapshot = doc_ref.get(transaction=transaction)
        needs_deduction = False
        action_type = "set"
        
        if existing_snapshot.exists:
            prev_status = existing_snapshot.get('status')
            if prev_status in ('paid', 'completed') and new_status in ('paid', 'completed'):
                action_type = "merge"
            else:
                needs_deduction = new_status in ('paid', 'completed')
                action_type = "set"
        else:
            needs_deduction = new_status in ('paid', 'completed')
            action_type = "set"

        stock_snapshots = []
        if needs_deduction:
            for item in items:
                prod_id = item.get('id') or item.get('product_id')
                if not prod_id: continue
                
                try:
                    qty_to_deduct = int(item.get('quantity', 0) or item.get('qty', 0))
                except (ValueError, TypeError):
                    qty_to_deduct = 0
                    
                if qty_to_deduct <= 0: continue
                
                item_ref = db.collection('items').document(str(prod_id))
                product_snapshot = item_ref.get(transaction=transaction)
                stock_snapshots.append({
                    'ref': item_ref,
                    'snapshot': product_snapshot,
                    'qty': qty_to_deduct
                })

        if action_type == "merge":
            transaction.set(doc_ref, order_data, merge=True)
            return True

        transaction.set(doc_ref, order_data)
            
        if needs_deduction:
            for entry in stock_snapshots:
                item_ref = entry['ref']
                snapshot = entry['snapshot']
                qty_to_deduct = entry['qty']
                
                if not snapshot.exists: continue
                
                try:
                    current_stock = int(snapshot.get('stock_quantity') or 0)
                except (ValueError, TypeError):
                    current_stock = 0
                    
                new_stock = max(0, current_stock - qty_to_deduct)
                if new_stock != current_stock:
                    transaction.update(item_ref, {'stock_quantity': new_stock})
                    
        return True

    try:
        transaction_ref = db.transaction()
        create_order_and_deduct_stock(transaction_ref)
        return True, "Order pushed transactionally"
    except Exception as e:
        print(f"❌ Transaction Failed for {tran_id}: {e}")
        return False, str(e)

def pull_from_firestore():
    db = get_firestore_db()
    if not db:
        return False, "Could not connect to Firestore"

    try:
        items_ref = db.collection('items').stream()
        products_list = []
        
        raw_deleted = local_db.get_deleted_products() or []
        deleted_ids = {r[0] if isinstance(r, (tuple, list)) else r for r in raw_deleted}
        
        try:
            current_rate = float(local_db.get_setting('exchange_rate', 4039))
        except Exception:
            current_rate = 4039.0
            
        for doc in items_ref:
            try:
                if doc.id in deleted_ids: continue
                    
                p = doc.to_dict()
                p['id'] = doc.id
                
                # បង្ការ Error ពេលតម្លៃជា String ឬ None
                raw_price = p.get('price', 0)
                try:
                    price_val = float(raw_price) if raw_price else 0.0
                    if price_val > 1000:
                        p['price'] = price_val / current_rate
                    else:
                        p['price'] = price_val
                except (ValueError, TypeError):
                    p['price'] = 0.0

                if 'createdAt' in p and hasattr(p['createdAt'], 'isoformat'):
                    p['createdAt'] = p['createdAt'].isoformat()
                    
                products_list.append(p)
            except Exception as e:
                print(f"Error parsing product {doc.id}: {e}")
                traceback.print_exc()
        
        if products_list:
            local_db.save_products(products_list)

        users_ref = db.collection('users').stream()
        users_list = []
        for doc in users_ref:
            try:
                u = doc.to_dict()
                u['username'] = doc.id
                users_list.append(u)
            except Exception as e:
                print(f"Error parsing user {doc.id}: {e}")
                traceback.print_exc()
                
        if users_list:
            local_db.save_users(users_list)
            firestore_usernames = {u['username'] for u in users_list}
            local_usernames = {u.get('username') for u in local_db.get_all_users() if u.get('username')}
            for username in (local_usernames - firestore_usernames):
                local_db.delete_user_local(username)

        return True, "Successfully pulled data from Firestore"
    except Exception as e:
        print(f"Error in pull_from_firestore: {e}")
        traceback.print_exc()
        return False, f"Error pulling data: {str(e)}"

def push_to_firestore():
    db = get_firestore_db()
    if not db:
        return False, "Could not connect to Firestore", []

    synced_product_names = []
    count = 0

    try:
        deleted_ids = local_db.get_deleted_products()
        if deleted_ids:
            successfully_deleted = []
            for prod_id in deleted_ids:
                try:
                    db.collection('items').document(str(prod_id)).delete()
                    successfully_deleted.append(prod_id)
                    count += 1
                except Exception as e:
                    print(f"Error deleting product {prod_id}: {e}")
                    traceback.print_exc()
            if successfully_deleted:
                local_db.clear_deletion_log(successfully_deleted)

        products = local_db.get_products() or []
        for prod in products:
            try:
                prod_data = dict(prod)
                prod_id = prod_data.pop('id', None)
                if not prod_id: continue
                
                image_val = prod_data.get('image', '')
                if image_val and not str(image_val).startswith('http') and not str(image_val).startswith('data:'):
                    prod_data['image'] = 'default.jpg'

                db.collection('items').document(str(prod_id)).set(prod_data)
                synced_product_names.append(prod_data.get('name', 'Unknown'))
                count += 1
            except Exception as e:
                print(f"Error pushing product: {e}")
                traceback.print_exc()

        try:
            users = local_db.get_all_users() or []
            for user in users:
                username = user.get('username')
                if not username: continue
                try:
                    profile_data = local_db.get_user_profile(username) or {}
                    user_data = {
                        'username': username,
                        'password': user.get('password'),
                        'role': user.get('role', 'user'),
                        'status': user.get('status', 'active'),
                        'profile_image': profile_data.get('profile_image'),
                        'cover_image': profile_data.get('cover_image')
                    }
                    db.collection('users').document(username).set(user_data, merge=True)
                except Exception as e:
                    print(f"Error syncing user {username}: {e}")
                    traceback.print_exc()
        except Exception as e:
            print(f"Error syncing users: {e}")
            traceback.print_exc()

        # បំពាក់ប្រព័ន្ធការពារកំហុសសម្រាប់ Orders (កុំឱ្យគាំងពេល json.loads)
        unsynced = local_db.get_unsynced_orders() or []
        for order in unsynced:
            try:
                order_dict = dict(order)
                raw_items = order_dict.get('items', '[]')
                
                if isinstance(raw_items, str):
                    try:
                        parsed_items = json.loads(raw_items)
                    except json.JSONDecodeError:
                        parsed_items = []
                else:
                    parsed_items = raw_items

                order_data = {
                    'items': parsed_items,
                    'total': order_dict.get('total', 0),
                    'status': order_dict.get('status', 'pending'),
                    'tran_id': order_dict.get('tran_id'),
                    'user': order_dict.get('user', 'unknown')
                }
                
                raw_date = order_dict.get('created_at')
                if raw_date:
                    try:
                        created_dt = datetime.fromisoformat(str(raw_date))
                        if created_dt.tzinfo is None:
                            created_dt = created_dt.replace(tzinfo=ICT_TZ)
                        else:
                            created_dt = created_dt.astimezone(ICT_TZ)
                        order_data['created_at'] = created_dt
                    except Exception:
                        order_data['created_at'] = datetime.now(ICT_TZ)
                else:
                    order_data['created_at'] = datetime.now(ICT_TZ)

                pushed_ok, pushed_msg = push_order_transactionally(db, order_data)
                if pushed_ok:
                    local_db.mark_order_synced(order_dict.get('local_id'), order_data['tran_id'])
                    count += 1
            except Exception as order_e:
                print(f"Error pushing order {order.get('tran_id')}: {order_e}")
                traceback.print_exc()

        return True, f"Successfully pushed {count} records", synced_product_names
    except Exception as e:
        print(f"Error in push_to_firestore: {e}")
        traceback.print_exc()
        return False, f"Error pushing data: {str(e)}", []

def sync_all():
    global _is_syncing
    with _sync_lock:
        if _is_syncing:
            return {'success': False, 'message': 'Sync already in progress'}
        _is_syncing = True
        sync_status['is_syncing'] = True

    try:
        push_ok, push_msg, synced_products = push_to_firestore()
        pull_ok, pull_msg = pull_from_firestore()
        
        success = pull_ok and push_ok
        sync_status['last_sync'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        return {
            'success': success,
            'pull': pull_msg,
            'push': push_msg,
            'synced_products': synced_products
        }
    except Exception as e:
        print(f"Error in sync_all: {e}")
        traceback.print_exc()
        return {
            'success': False,
            'pull': f"Error: {str(e)}",
            'push': f"Error: {str(e)}",
            'synced_products': []
        }
    finally:
        with _sync_lock:
            _is_syncing = False
            sync_status['is_syncing'] = False

def trigger_auto_sync(delay=5):
    global _sync_timer
    with _sync_lock:
        if _sync_timer is not None:
            _sync_timer.cancel()
        _sync_timer = threading.Timer(delay, sync_all)
        _sync_timer.daemon = True
        _sync_timer.start()

def start_background_sync(interval=30):
    def sync_loop():
        while True:
            time.sleep(interval)
            sync_all()

    thread = threading.Thread(target=sync_loop, daemon=True)
    thread.name = "BackgroundSyncThread"
    thread.start()