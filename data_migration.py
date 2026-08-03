import local_db
import sys

def migrate_product_prices():
    print("Starting product price migration...")
    products = local_db.get_products()
    riel_rate = float(local_db.get_setting('exchange_rate', 4039))
    
    count = 0
    for prod in products:
        price = prod.get('price', 0)
        # If price is unreasonably high (> 1000), it's likely a raw Riel value
        if price > 1000:
            new_price = price / riel_rate
            print(f"Normalizing '{prod['name']}': {price} -> {new_price:.4f}")
            
            # Update local DB
            prod['price'] = new_price
            local_db.add_product(prod['id'], prod)
            count += 1
            
    print(f"Migration complete. Updated {count} products.")
    if count > 0:
        print("Please run 'Sync Now' in the POS to update the cloud database.")

if __name__ == "__main__":
    migrate_product_prices()
