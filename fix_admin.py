import sqlite3
from werkzeug.security import generate_password_hash

def fix_admin_account():
    try:
        # តភ្ជាប់ទៅកាន់ Local Database ផ្ទាល់
        conn = sqlite3.connect('pos_local.db')
        cursor = conn.cursor()
        
        # បង្កើតលេខសម្ងាត់ថ្មី (Hash) ដែលត្រឹមត្រូវតាមស្តង់ដារ ធានាមិនគាំង .split()
        new_password = generate_password_hash('admin123')
        
        # ដំឡើងសិទ្ធិគណនី reamkesovanna ទៅជា Admin ពេញសិទ្ធិ
        cursor.execute("""
            UPDATE users 
            SET role = 'admin', password = ?, status = 'active'
            WHERE username = 'reamkesovanna'
        """, (new_password,))
        
        # ប្រសិនបើរកមិនឃើញគណនីនេះទេ គឺបង្កើតថ្មីយកតែម្ដង
        if cursor.rowcount == 0:
            cursor.execute("""
                INSERT INTO users (username, password, role, status) 
                VALUES ('reamkesovanna', ?, 'admin', 'active')
            """, (new_password,))
            
        conn.commit()
        conn.close()
        print("✅ ជោគជ័យ! គណនី 'reamkesovanna' ក្លាយជា Admin ពេញសិទ្ធិ!")
        print("👉 សូម Login ដោយប្រើលេខសម្ងាត់: admin123")
    except Exception as e:
        print(f"❌ មានបញ្ហា: {e}")

if __name__ == '__main__':
    fix_admin_account()