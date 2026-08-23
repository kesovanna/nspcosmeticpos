import os
import re
import sys
import local_db
from telethon import TelegramClient, events
from dotenv import load_dotenv

# --- PORTABLE PATH LOGIC ---
def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller."""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

# Load environment variables
load_dotenv(get_resource_path('.env'))

# --- SECURITY: Load Telegram credentials from environment variables ---
API_ID = int(os.environ.get('TELEGRAM_API_ID', '0'))
API_HASH = os.environ.get('TELEGRAM_API_HASH', '')
SESSION_NAME = os.environ.get('TELEGRAM_SESSION_NAME', 'aba_pos_session')
EXCHANGE_RATE = int(os.environ.get('TELEGRAM_EXCHANGE_RATE', '4000'))

# Validate that required credentials are set
if not API_ID or not API_HASH:
    raise RuntimeError(
        "CRITICAL: Telegram credentials not found in .env file. "
        "Please ensure TELEGRAM_API_ID and TELEGRAM_API_HASH are set."
    )

client = TelegramClient(SESSION_NAME, API_ID, API_HASH) 

def update_pos_database(amount, currency, tran_id):
    try:
        # 1. Fetch only the newest pending order directly
        # Instead of sorting all, query the database for the max RowID where status='pending'
        newest_order = local_db.get_latest_pending_order()

        if not newest_order:
            print("WARNING: No pending orders found for ABA payment validation.")
            return

        # 2. Extract values for comparison
        order_amount_usd = float(newest_order['total'])
        row_id = newest_order['local_id']

        # 3. Match Logic
        is_match = False
        if currency == '៛':
            if abs(amount - (order_amount_usd * EXCHANGE_RATE)) < 500:
                is_match = True
        elif currency == '$':
            if abs(amount - order_amount_usd) < 0.01:
                is_match = True

        # 4. Final Execution
        if is_match:
            local_db.update_order_status_by_local_id(row_id, 'paid')
            print(f"SUCCESS: Order {row_id} updated to PAID.")
        else:
            print(f"CRITICAL: Payment of {amount} {currency} does not match newest order {row_id} ({order_amount_usd} USD).")

    except Exception as e:
        print(f"❌ Database update failed: {e}")
@client.on(events.NewMessage)
async def catch_incoming_message(event):
    try:
        message_text = event.message.text or ""
        sender = await event.get_sender()
        sender_username = getattr(sender, 'username', '') or ''

        # ABA Logic
        if "paid by" in message_text and "Trx. ID:" in message_text:
            print("\n🔔 VALID ABA BANK NOTIFICATION IDENTIFIED")
            print("-" * 50)
            print(message_text.strip())
            print("-" * 50)

            ref_match = re.search(r'Trx\.\s*ID:\s*([0-9]+)', message_text)
            amount_match = re.search(r'([\$៛])([0-9,]+\.?[0-9]*)', message_text)

            if amount_match and ref_match:
                currency = amount_match.group(1) 
                raw_amount = amount_match.group(2) 
                ref_id = ref_match.group(1) 
                
                clean_amount = float(raw_amount.replace(',', ''))
                print(f"🔍 Extracted Values -> Currency: {currency} | Amount: {clean_amount} | Trx ID: {ref_id}")
                update_pos_database(clean_amount, currency, ref_id)
                
        # Amret Logic
        elif sender_username.lower() == 'amretplcbot' or "Amret" in message_text:
            print("\n🔔 VALID AMRET BANK NOTIFICATION IDENTIFIED")
            print("-" * 50)
            print(message_text.strip())
            print("-" * 50)
            
            # Example: "500 KHR បានទទួលពីគណនីលេខ 086966244 (REAM KESOVANNA)..."
            amount_match = re.search(r'([\d,]+(?:\.\d+)?)\s*(KHR|USD)\s*បានទទួល', message_text)
            
            if amount_match:
                raw_amount = amount_match.group(1)
                currency_str = amount_match.group(2)
                
                currency = '$' if currency_str == 'USD' else '៛'
                clean_amount = float(raw_amount.replace(',', ''))
                
                # Amret might not have a clear Trx ID in the same format, use a placeholder or extract if available
                ref_match = re.search(r'(?:Ref|Trx\.\s*ID|Transaction ID)[\s:]*([0-9a-zA-Z]+)', message_text, re.IGNORECASE)
                ref_id = ref_match.group(1) if ref_match else "AMRET_AUTO"
                
                print(f"🔍 Extracted Amret Values -> Currency: {currency} | Amount: {clean_amount} | Trx ID: {ref_id}")
                update_pos_database(clean_amount, currency, ref_id)

    except Exception as e:
        print(f"❌ Error monitoring incoming message: {e}")

async def main():
    try:
        print("🚀 Initializing ABA Automation Userbot...")
        await client.start()
        print("✅ System fully active and listening for live PayWay alerts!")
        await client.run_until_disconnected()
    finally:
        await client.disconnect()
        print("\n✅ ABA Listener gracefully disconnected. Session released.")

if __name__ == '__main__':
    import asyncio
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⚠️  Received Ctrl+C. Shutting down cleanly...")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")