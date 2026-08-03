import firebase_admin
from firebase_admin import credentials, firestore

# Initialize Firebase
cred = credentials.Certificate('serviceAccountKey.json')
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

# The 2 ghost IDs identified by your diagnostic tool
ghost_ids = ["EHKVR3yZIj25HdoZ04qx", "f244i6Ul5YyrZG4teYd4"]

print("--- Force Evicting Ghost Products from Firestore ---")
for product_id in ghost_ids:
    doc_ref = db.collection('items').document(product_id)
    if doc_ref.get().exists:
        doc_ref.delete()
        print(f"🗑️ Successfully deleted {product_id} from Cloud Firestore!")
    else:
        print(f"ℹ️ {product_id} was already gone or not found.")

print("\nAll done! Refresh your live website dashboard now.")
