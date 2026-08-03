import sys
sys.path.insert(0, '.')
from local_db import get_daily_sales_report, get_all_orders
from datetime import datetime, timedelta

target_date = sys.argv[1] if len(sys.argv) > 1 else '2026-08-01'

# Method 1: API method
api_result = get_daily_sales_report(target_date)
print("API Result (get_daily_sales_report):")
print(f"  Top products: {api_result['top_products']}")

# Method 2: Dashboard method
dt = datetime.strptime(target_date, '%Y-%m-%d')
start_date = datetime(dt.year, dt.month, dt.day)
end_date = start_date + timedelta(days=1)

all_orders = get_all_orders()
item_summary = {}

for data in all_orders:
    created_at = datetime.fromisoformat(data['created_at'])
    if start_date <= created_at < end_date:
        if data.get('status') in ['paid', 'completed']:
            for item in data.get('items', []):
                name = item.get('name', 'Unknown')
                qty = int(item.get('quantity', 0) or item.get('qty', 0))
                price = float(item.get('price', 0))
                if name in item_summary:
                    item_summary[name]['qty'] += qty
                    item_summary[name]['total'] += (qty * price)
                else:
                    item_summary[name] = {'qty': qty, 'total': (qty * price)}

sorted_items = sorted(item_summary.items(), key=lambda x: x[1]['qty'], reverse=True)
print("\nDashboard Result (main.py method):")
print(f"  Top products: {sorted_items[:3]}")

print("\nComparison:")
print(f"  API top 1: {api_result['top_products'][0] if api_result['top_products'] else 'None'}")
print(f"  Dashboard top 1: {sorted_items[0] if sorted_items else 'None'}")
