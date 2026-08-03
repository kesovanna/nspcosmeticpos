import local_db
import sys

def migrate_barcodes():
    print("=== Sequential Barcode Standardizer ===")
    print("This script will re-assign sequential barcodes (0001, 0002, ...) to all products.")
    print("Logic: Sort by creation date (oldest first) and assign 4-digit padded numbers.")
    print("-" * 40)
    
    # Get all products
    products = local_db.get_products()
    
    # local_db.get_products() returns sorted by createdAt DESC by default.
    # We want oldest products to have lower numbers, so we reverse the list.
    products.reverse()
    
    total = len(products)
    if total == 0:
        print("No products found in the database. Nothing to migrate.")
        return

    print(f"Found {total} products.")
    print("\n[DRY RUN LOG]")
    
    updates = []
    for i, prod in enumerate(products, 1):
        old_barcode = prod.get('barcode') or "N/A"
        new_barcode = f"{i:04d}"
        print(f"Updating: '{prod['name'][:30]:<30}' | {old_barcode} -> {new_barcode}")
        
        # Prepare the updated product object
        # We need to pass the same structure back to local_db.add_product
        updates.append({
            'id': prod['id'],
            'data': {
                'name': prod['name'],
                'price': prod['price'],
                'image': prod['image'],
                'category': prod['category'],
                'barcode': new_barcode,
                'createdAt': prod['createdAt'],
                'stock_quantity': prod.get('stock_quantity', 0)
            }
        })

    print("-" * 40)
    if len(sys.argv) > 1 and sys.argv[1] == "--confirm":
        confirm = "yes"
    else:
        confirm = input(f"\nProceed with updating {total} products in 'pos_local.db'? (yes/no): ").strip().lower()
    
    if confirm == 'yes':
        print("\nCommitting changes...")
        count = 0
        for uprod in updates:
            try:
                local_db.add_product(uprod['id'], uprod['data'])
                count += 1
            except Exception as e:
                print(f"Error updating product {uprod['id']}: {e}")
        
        print(f"\nSuccess! {count} products updated locally.")
        print("\n" + "!" * 40)
        print("IMPORTANT NEXT STEPS:")
        print("1. Start your POS application.")
        print("2. Run 'Sync Now' from the Manager/Settings to push these new barcodes to Firebase.")
        print("3. Go to 'Print Barcodes' page to verify and print your new labels.")
        print("!" * 40)
    else:
        print("\nMigration cancelled. No changes were made.")

if __name__ == "__main__":
    try:
        migrate_barcodes()
    except KeyboardInterrupt:
        print("\nMigration aborted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        sys.exit(1)
