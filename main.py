import sys
import random
import os
import webbrowser            # <-- 1. Add this near your top imports
from threading import Timer  # <-- 2. Add this near your top imports
# Fix for PyInstaller --windowed mode crashing with Firebase/Google Cloud loggers
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')
import base64
import os
from dotenv import load_dotenv
import json
import smtplib
import urllib.request
import urllib.parse
import traceback
from datetime import datetime, timedelta
from functools import wraps
from email.mime.text import MIMEText

import firebase_admin
from firebase_admin import credentials, firestore
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, make_response, send_from_directory
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_wtf.csrf import CSRFProtect, generate_csrf
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from firebase_functions import https_fn
from weasyprint import HTML, CSS
from io import BytesIO

import local_db
import sync

# --- PORTABLE PATH LOGIC ---
def get_resource_path(relative_path):
    """
    Get absolute path to resource, works for dev and for PyInstaller.
    """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        # If not running in a bundle, use the directory of the current script
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, relative_path)

# Load environment variables from portable path
load_dotenv(get_resource_path('.env'))

# --- CONFIGURATION VALIDATION ---
REQUIRED_ENV_VARS = [
    'SECRET_KEY',
    'EMAIL_APP_PASSWORD',
    'OWNER_EMAIL',
    'MASTER_RECOVERY_PIN'
]

def validate_config():
    missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

validate_config()

# --- OWNER EMAIL SETTINGS ---
OWNER_EMAIL = os.environ.get("OWNER_EMAIL")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD")

def notify_owner_by_email(new_username):
    # ... (rest of the function)
    try:
        msg = MIMEText(f"មានអ្នកប្រើប្រាស់ថ្មីឈ្មោះ: {new_username} បានចុះឈ្មោះ។ សូមចូលទៅកាន់ Firebase ដើម្បីប្តូរ status ពី 'pending' ទៅ 'active' ដើម្បីអនុញ្ញាត។")
        msg['Subject'] = 'New POS User Signup Approval Required'
        msg['From'] = OWNER_EMAIL
        msg['To'] = OWNER_EMAIL

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(OWNER_EMAIL, EMAIL_APP_PASSWORD)
            server.send_message(msg)
    except Exception as e:
        print(f"Failed to send email: {e}")

# Initialize Firebase Admin
if not firebase_admin._apps:
    service_account_path = get_resource_path('serviceAccountKey.json')
    if os.path.exists(service_account_path):
        try:
            cred = credentials.Certificate(service_account_path)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            print(f"WARNING: Could not load {service_account_path}: {e}")
            firebase_admin.initialize_app()
    else:
        firebase_admin.initialize_app()

def get_db():
    return firestore.client()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY')

# --- CSRF PROTECTION ---
csrf = CSRFProtect(app)
# Exempt API endpoints that use JSON (they are protected by login_required + SameSite cookies)
# CSRF token is still required for all HTML form POST submissions
app.config['WTF_CSRF_CHECK_DEFAULT'] = True
app.config['WTF_CSRF_TIME_LIMIT'] = 3600  # 1 hour token validity

# --- SECURITY HEADERS ---
@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # Prevent caching of sensitive pages
    if request.endpoint and request.endpoint not in ['static']:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
    return response

# Make CSRF token available to all templates (for AJAX requests)
@app.context_processor
def inject_csrf_token():
    return dict(csrf_token=generate_csrf)
# Ensure reports directory exists
if not os.path.exists('reports'):
    os.makedirs('reports')

@app.route('/save-report', methods=['POST'])
@csrf.exempt # Exempting API endpoint, protected by other means if needed
def save_report():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file part"}), 400
    file = request.files['file']
    tran_id = request.form.get('tran_id')
    
    if file.filename == '':
        return jsonify({"status": "error", "message": "No selected file"}), 400
        
    if file:
        # SECURITY: Strict filename validation to prevent path traversal
        filename = secure_filename(file.filename)
        if not filename:
            return jsonify({"status": "error", "message": "Invalid filename"}), 400
            
        # Ensure reports directory exists
        reports_dir = os.path.abspath(os.path.join(os.getcwd(), 'reports'))
        if not os.path.exists(reports_dir):
            os.makedirs(reports_dir)
            
        # SECURITY: Ensure the final path is actually inside the reports directory
        final_path = os.path.abspath(os.path.join(reports_dir, filename))
        if not final_path.startswith(reports_dir):
            return jsonify({"status": "error", "message": "Path traversal detected"}), 403
            
        file.save(final_path)
        
        # Update database status if tran_id is provided
        if tran_id:
            local_db.update_order_receipt_status(tran_id, 1)
            
        return jsonify({"status": "success", "message": "Report archived and database updated"})
    return jsonify({"status": "error"}), 400

@app.route('/api/archive-pdf/<tran_id>', methods=['POST'])
@csrf.exempt # Exempting API endpoint
def archive_pdf(tran_id):
    """
    Generate PDF from success.html using weasyprint with proper Khmer text rendering (CTL support).
    Saves PDF to reports folder and updates database.
    """
    try:
        # SECURITY: Validate tran_id format (alphanumeric, dash, underscore only)
        if not isinstance(tran_id, str) or not all(c.isalnum() or c in '-_' for c in tran_id):
            return jsonify({"status": "error", "message": "Invalid transaction ID format"}), 400
        
        # Fetch the order from the database
        order = local_db.get_order(tran_id)
        if order is None:
            return jsonify({"status": "error", "message": f"Order not found: {tran_id}"}), 404
        
        # Get currency rate
        riel_rate = get_riel_rate()
        
        # Render the success.html template as a string with order data and PDF flag for absolute font paths
        html_content = render_template('success.html', order=order, RIEL_RATE=riel_rate, is_pdf=True)
        
        # Convert HTML to PDF using weasyprint (supports CTL for Khmer text)
        pdf_bytes = HTML(string=html_content, base_url=request.host_url).write_pdf()
        
        # SECURITY: Ensure reports directory exists with absolute path
        reports_dir = os.path.abspath(os.path.join(os.getcwd(), 'reports'))
        if not os.path.exists(reports_dir):
            os.makedirs(reports_dir)
        
        # SECURITY: Strict filename validation to prevent path traversal
        pdf_filename = secure_filename(f"invoice_{tran_id}.pdf")
        if not pdf_filename or not pdf_filename.endswith('.pdf'):
            return jsonify({"status": "error", "message": "Invalid filename generated"}), 400
        
        # SECURITY: Ensure final path is within reports directory
        pdf_filepath = os.path.abspath(os.path.join(reports_dir, pdf_filename))
        if not pdf_filepath.startswith(reports_dir):
            return jsonify({"status": "error", "message": "Path traversal detected"}), 403
        
        with open(pdf_filepath, 'wb') as f:
            f.write(pdf_bytes)
        
        # Update database to mark receipt as archived
        local_db.update_order_receipt_status(tran_id, 1)
        
        return jsonify({
            "status": "success",
            "message": "PDF archived successfully",
            "filename": pdf_filename
        })
        
    except Exception as e:
        print(f"❌ PDF Generation Error for {tran_id}: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"PDF generation failed: {str(e)}"
        }), 500
        
        with open(pdf_filepath, 'wb') as f:
            f.write(pdf_bytes)
        
        # Update database to mark receipt as archived
        local_db.update_order_receipt_status(tran_id, 1)
        
        return jsonify({
            "status": "success",
            "message": "PDF archived successfully",
            "filename": pdf_filename
        })
        
    except Exception as e:
        print(f"❌ PDF Generation Error for {tran_id}: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"PDF generation failed: {str(e)}"
        }), 500

app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
# Firebase Cloud Functions cookie handling
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_DEBUG', 'False').lower() != 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_NAME'] = '__session'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_SECURE'] = os.environ.get('FLASK_DEBUG', 'False').lower() != 'true'

# Master PIN for password resets
MASTER_RECOVERY_PIN = os.environ.get('MASTER_RECOVERY_PIN')

# Currency Conversion Rate
_cached_riel_rate = 4100
_last_fetch_time = None

def get_acleda_riel_rate():
    try:
        import re
        url = "https://www.acledabank.com.kh/assets/unity/exchangerate"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=3) as response:
            html = response.read().decode('utf-8')
            match = re.search(r'USD</td><td[^>]*>KHR ([\d,.]+)</td><td[^>]*>KHR ([\d,.]+)', html)
            if match:
                ask = match.group(2).replace(',', '')
                return int(float(ask))
    except Exception as e:
        print(f"Error fetching ACLEDA rate: {e}")
    return None

def get_riel_rate():
    # 1. Check local DB settings for a manually set rate
    manual_rate = local_db.get_setting('exchange_rate')
    if manual_rate:
        try:
            return int(float(manual_rate))
        except:
            pass

    global _cached_riel_rate, _last_fetch_time
    now = datetime.now()
    if _last_fetch_time is None or (now - _last_fetch_time).total_seconds() > 3600:
        new_rate = get_acleda_riel_rate()
        if new_rate:
            _cached_riel_rate = new_rate
        _last_fetch_time = now
    return _cached_riel_rate

@app.route('/add_category_endpoint', methods=['POST'])
@csrf.exempt  # SECURITY: API endpoint
@login_required
def add_custom_category():
    data = request.get_json()
    category_name = data.get('name', '').strip()
    if not category_name:
        return jsonify({"success": False, "message": "Category name cannot be empty"}), 400
        
    try:
        success = local_db.add_category(category_name)
        if success:
            return jsonify({"success": True, "category": category_name})
        else:
            return jsonify({"success": False, "message": "Category already exists"}), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/settings/exchange-rate', methods=['POST'])
@csrf.exempt  # SECURITY: API endpoint, CSRF not applicable to programmatic clients
@login_required
def update_exchange_rate():
    if current_user.role != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized privileges"}), 403

    data = request.get_json()
    if not data or 'exchange_rate' not in data:
        return jsonify({"status": "error", "message": "Missing exchange_rate parameters in request body"}), 400

    try:
        new_rate = int(data['exchange_rate'])
        if new_rate <= 0:
            return jsonify({"status": "error", "message": "Rate magnitude must be greater than zero"}), 400

        # Execute data write layer
        local_db.update_setting('exchange_rate', str(new_rate))
        return jsonify({"status": "success", "message": "Exchange rate updated successfully", "new_rate": new_rate})
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid number format provided"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Database transactional crash: {str(e)}"}), 500
@app.context_processor
def inject_riel_rate():
    rate = get_riel_rate()
    return dict(riel_rate=rate, RIEL_RATE=rate)

# --- AUTH CONFIG ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.session_protection = 'basic'

class User(UserMixin):
    def __init__(self, id, username, role, profile_image=None, cover_image=None): 
        self.id = id
        self.username = username
        self.role = role
        self.profile_image = profile_image
        self.cover_image = cover_image

@login_manager.user_loader
def load_user(user_id):
    try:
        user_data = local_db.get_user(user_id)
        if user_data:
            return User(
                id=user_id, 
                username=user_data.get('username'), 
                role=user_data.get('role'),
                profile_image=user_data.get('profile_image'), 
                cover_image=user_data.get('cover_image')
            )
    except Exception as e:
        print(f"Error loading user: {e}")
    return None

@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith('/api/') or request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"status": "error", "message": "សូមចូលប្រើប្រាស់ជាមុនសិន (Please login)"}), 401
    return redirect(url_for('login'))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Access Denied: Administrators only.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/setup-admin')
def setup_admin():
    # SECURITY: Require the master PIN to run setup
    pin = request.args.get('pin')
    if not pin or pin != MASTER_RECOVERY_PIN:
        return "Unauthorized", 403
        
    db = get_db()
    users_ref = db.collection('users')
    existing = users_ref.document('admin').get()
    if not existing.exists:
        # SECURITY: Generate a random password instead of hardcoding
        import secrets
        import string
        alphabet = string.ascii_letters + string.digits
        random_password = ''.join(secrets.choice(alphabet) for i in range(12))
        
        admin_data = {
            'username': 'admin',
            'password': generate_password_hash(random_password),
            'role': 'admin',
            'status': 'active'
        }
        users_ref.document('admin').set(admin_data)
        local_db.save_users([admin_data])
        return f"Admin user created: admin / {random_password} (Please save this password!)"
    return "Admin already exists."

@app.route('/signup', methods=['POST'])
def signup():
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()

        if not username or not password:
            return jsonify({"status": "error", "message": "Missing info"}), 400

        # SECURITY: Validate username format (alphanumeric, underscore, dash, dot only)
        if len(username) < 3 or len(username) > 50:
            return jsonify({"status": "error", "message": "Username must be 3-50 characters"}), 400
        if not all(c.isalnum() or c in '_.-' for c in username):
            return jsonify({"status": "error", "message": "Username contains invalid characters"}), 400

        # SECURITY: Validate password strength
        if len(password) < 6:
            return jsonify({"status": "error", "message": "Password must be at least 6 characters"}), 400

        db = get_db()
        if db.collection('users').document(username).get().exists:
            return jsonify({"status": "error", "message": "ឈ្មោះនេះមានគេប្រើរួចហើយ!"}), 400

        new_user = {
            "username": username,
            "password": generate_password_hash(password),
            "role": "user", 
            "status": "pending",
            "profile_pic": "default.jpg",
            "created_at": firestore.SERVER_TIMESTAMP
        }
        db.collection('users').document(username).set(new_user)
        
        # We don't save to local_db yet, as we only pull approved users during sync
        notify_owner_by_email(username)
        
        return jsonify({"status": "success", "message": "គណនីបានបង្កើត! សូមរង់ចាំការអនុញ្ញាតពីម្ចាស់ហាង។"})
    except Exception as e:
        print(f"Signup error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/users', methods=['GET'])
@login_required
def get_users():
    if current_user.role != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized: Admin access required"}), 403
    
    try:
        users_list = local_db.get_all_users()
        # Sanitize: Remove sensitive information and format for the frontend
        sanitized_users = []
        for u in users_list:
            sanitized_users.append({
                "username": u.get('username'),
                "role": u.get('role', 'user'),
                "status": u.get('status', 'active'),
                "profile_image": u.get('profile_image'),
                "display_name": u.get('username') # Using username as display name as no separate field exists
            })
        
        return jsonify({"status": "success", "data": sanitized_users})
    except Exception as e:
        print(f"Error fetching users: {e}")
        return jsonify({"status": "error", "message": "Failed to retrieve staff data"}), 500

@app.route('/api/users/create', methods=['POST'])
@csrf.exempt  # SECURITY: API endpoint
@login_required
def create_user():
    if current_user.role != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
    
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        role = data.get('role', 'user')

        # SECURITY: Validate inputs
        if not username or not password:
            return jsonify({"status": "error", "message": "Missing username or password"}), 400

        if len(username) < 3 or len(username) > 50:
            return jsonify({"status": "error", "message": "Username must be 3-50 characters"}), 400
        if not all(c.isalnum() or c in '_.-' for c in username):
            return jsonify({"status": "error", "message": "Username contains invalid characters"}), 400

        if len(password) < 6:
            return jsonify({"status": "error", "message": "Password must be at least 6 characters"}), 400

        # SECURITY: Whitelist allowed roles
        if role not in ['admin', 'user']:
            return jsonify({"status": "error", "message": "Invalid role"}), 400

        db = get_db()
        if db.collection('users').document(username).get().exists:
            return jsonify({"status": "error", "message": "ឈ្មោះអ្នកប្រើប្រាស់នេះមានរួចហើយ (Username already exists)"}), 400

        new_user = {
            "username": username,
            "password": generate_password_hash(password),
            "role": role, 
            "status": "active", # Admins create active users
            "profile_pic": "default.jpg",
            "created_at": firestore.SERVER_TIMESTAMP
        }
        db.collection('users').document(username).set(new_user)
        
        # Sync locally to update SQLite
        sync.pull_from_firestore()
        
        return jsonify({"status": "success", "message": "បុគ្គលិកថ្មីត្រូវបានបង្កើតដោយជោគជ័យ! (Staff created successfully)"})
    except Exception as e:
        print(f"Create user error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/users/<username>/delete', methods=['POST'])
@csrf.exempt  # SECURITY: API endpoint
@login_required
def delete_user(username):
    if current_user.role != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
    if username == current_user.username:
        return jsonify({"status": "error", "message": "អ្នកមិនអាចលុបគណនីផ្ទាល់ខ្លួនបានទេ (You cannot delete your own account)"}), 400
    
    try:
        db = get_db()
        db.collection('users').document(username).delete()
        # Sync locally
        sync.pull_from_firestore()
        return jsonify({"status": "success", "message": f"គណនី {username} ត្រូវបានលុប (Account deleted)"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
@app.route('/api/users/<username>/approve', methods=['POST'])
@csrf.exempt  # SECURITY: API endpoint
@login_required
def approve_user(username):
    if current_user.role != 'admin':
        return jsonify({"error": "Unauthorized"}), 403
    try:
        # Update Firestore
        db = get_db()
        db.collection('users').document(username).update({"status": "active"})
        # Sync locally
        sync.pull_from_firestore()
        return jsonify({"status": "success", "message": f"{username} approved"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/users/<username>/set-role', methods=['POST'])
@csrf.exempt  # SECURITY: API endpoint
@login_required
def set_user_role(username):
    if current_user.role != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
    
    # SECURITY: Prevent admins from changing their own role
    if username == current_user.username:
        return jsonify({"status": "error", "message": "Cannot modify your own role"}), 400
    
    try:
        data = request.get_json()
        new_role = data.get('role', '').strip()
        
        # SECURITY: Whitelist allowed roles
        if new_role not in ['admin', 'user']:
            return jsonify({"status": "error", "message": "Invalid role"}), 400
        
        # Update Firestore
        db = get_db()
        db.collection('users').document(username).update({"role": new_role})
        # Sync locally
        sync.pull_from_firestore()
        return jsonify({"status": "success", "message": f"Role updated to {new_role}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/sync', methods=['POST'])
@csrf.exempt  # SECURITY: API endpoint
@login_required
def manual_sync():
    try:
        res = sync.sync_all()
        if res['success']:
            return jsonify({"status": "success", "message": "Sync successful", "details": res})
        else:
            return jsonify({"status": "error", "message": "Sync failed", "details": res}), 500
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/get-sync-status', methods=['GET'])
def get_sync_status():
    """Returns the current background sync status"""
    return jsonify(sync.sync_status)

@app.route('/api/upload-user-image', methods=['POST'])
@csrf.exempt  # SECURITY: API endpoint
@login_required
def upload_user_image():
    try:
        data = request.get_json()
        image_data = data.get('image_data')
        user_type = data.get('type') # 'profile' or 'cover'
        
        if not image_data or not user_type:
            return jsonify({'status': 'error', 'message': 'Missing data'}), 400

        # Update local database
        if user_type == 'profile':
            local_db.update_user_images(current_user.username, profile_image=image_data)
        elif user_type == 'cover':
            local_db.update_user_images(current_user.username, cover_image=image_data)
        else:
            return jsonify({'status': 'error', 'message': 'Invalid type'}), 400
        
        # Schedule auto-sync to push to Firestore
        sync.trigger_auto_sync(delay=2)
        
        return jsonify({'status': 'success', 'message': 'Saved successfully'})
    except Exception as e:
        print(f"Error in upload: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/user-covers', methods=['GET'])
@login_required
def get_user_covers_api():
    covers = local_db.get_user_covers(current_user.username)
    return jsonify({'status': 'success', 'covers': covers})

@app.route('/api/upload-cover-gallery', methods=['POST'])
@csrf.exempt  # SECURITY: API endpoint
@login_required
def upload_cover_gallery():
    try:
        data = request.get_json()
        image_data = data.get('image_data')
        if not image_data:
            return jsonify({'status': 'error', 'message': 'Missing image data'}), 400
        
        success, message = local_db.add_user_cover(current_user.username, image_data)
        if success:
            return jsonify({'status': 'success', 'message': message})
        else:
            return jsonify({'status': 'error', 'message': message}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/delete-cover/<int:cover_id>', methods=['POST'])
@csrf.exempt  # SECURITY: API endpoint
@login_required
def delete_cover_api(cover_id):
    try:
        local_db.delete_user_cover(cover_id, current_user.username)
        return jsonify({'status': 'success', 'message': 'Cover deleted'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')
    try:
        data = request.get_json() or request.form
        username = data.get('username')
        password = data.get('password')
        if not username or not password:
            return jsonify({"status": "error", "message": "សូមបំពេញព័ត៌មាន!"}), 400

        # SECURITY: Validate username format (prevent injection)
        if not all(c.isalnum() or c in '_.-@' for c in username):
            return jsonify({"status": "error", "message": "Invalid username format"}), 400

        user_data = local_db.get_user(username)
        if user_data:
            if user_data.get('status', 'active') == 'pending':
                return jsonify({"status": "error", "message": "គណនីរបស់អ្នកកំពុងរង់ចាំការអនុញ្ញាត (Pending Approval)!"}), 403
            if check_password_hash(user_data.get('password', ''), password):
                user = User(username, username, user_data.get('role', 'user'), user_data.get('profile_image'), user_data.get('cover_image')) 
                login_user(user, remember=True)
                return jsonify({"status": "success", "message": "ចូលប្រព័ន្ធបានជោគជ័យ!"})
            else:
                return jsonify({"status": "error", "message": "លេខសម្ងាត់មិនត្រឹមត្រូវទេ!"}), 401
        else:
            return jsonify({"status": "error", "message": "មិនមានឈ្មោះអ្នកប្រើប្រាស់នេះទេ!"}), 404
    except Exception as e:
        print(f"Server Login Error: {e}")
        return jsonify({"status": "error", "message": f"Server Error: {str(e)}"}), 500

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

def get_merged_categories():
    """Fetches categories from both products and custom categories table, then merges, deduplicates, and sorts them."""
    try:
        products = local_db.get_products() or []
        product_categories = list(set([p.get('category') for p in products if p.get('category')]))

        custom_categories = local_db.get_categories() or []
        # assuming custom_categories is a list of strings based on local_db.get_categories() implementation
        # row['name'] for row in rows
        
        # Merge, deduplicate, and sort
        merged_categories = sorted(list(set(product_categories + custom_categories)))
        return merged_categories
    except Exception as e:
        print(f"Error merging categories: {e}")
        return []

@app.route('/')
@login_required
def home():
    try:
        items_list = local_db.get_products()
        current_riel_rate = get_riel_rate()
        merged_categories = get_merged_categories()
        
        # --- NEW: Dynamic Popular Product Promotions ---
        chosen_promos = []
        try:
            with open('popular_products.json', 'r', encoding='utf-8') as f:
                all_promos = json.load(f)
                # Pick 2 or 3 random promos
                num_to_pick = random.randint(2, 3) if len(all_promos) >= 2 else len(all_promos)
                chosen_promos = random.sample(all_promos, min(len(all_promos), num_to_pick))
        except Exception as promo_err:
            print(f"Error loading promos: {promo_err}")
            chosen_promos = ["Welcome to NSP Cosmetic POS!"]

        # --- NEW: Fetch Daily Report Data for Integrated View ---
        now = datetime.now()
        target_date = request.args.get('date', now.strftime('%Y-%m-%d'))
        try:
            dt = datetime.strptime(target_date, '%Y-%m-%d')
            start_date = datetime(dt.year, dt.month, dt.day)
        except:
            start_date = datetime(now.year, now.month, now.day)
            
        end_date = start_date + timedelta(days=1)
        
        all_orders = local_db.get_all_orders()
        total_revenue = 0
        total_orders = 0
        item_summary = {}
        filtered_orders = []

        for data in all_orders:
            created_at = datetime.fromisoformat(data['created_at'])
            if start_date <= created_at < end_date:
                if data.get('status') in ['paid', 'completed']:
                    total_revenue += float(data.get('total', 0))
                    total_orders += 1
                    for item in data.get('items', []):
                        name = item.get('name', 'Unknown')
                        qty = int(item.get('quantity', 0) or item.get('qty', 0))
                        price = float(item.get('price', 0))
                        if name in item_summary:
                            item_summary[name]['qty'] += qty
                            item_summary[name]['total'] += (qty * price)
                        else:
                            item_summary[name] = {'qty': qty, 'total': (qty * price)}
                    data['display_time'] = created_at.strftime('%d/%m/%Y %H:%M')
                    filtered_orders.append(data)

        sorted_items = sorted(item_summary.items(), key=lambda x: x[1]['qty'], reverse=True)

        # JSON-safe top products for the SPA (list of dicts, avoids Jinja-in-template syntax issues)
        top_products_json = [{'name': name, 'qty': data['qty']} for name, data in sorted_items[:10]]

        # --- NEW: SPA Data - Fetch all page data for integrated views ---
        # Activities data
        try:
            activities_list = local_db.get_activities() if hasattr(local_db, 'get_activities') else []
        except:
            activities_list = []

        # Notifications data (placeholder - can be enhanced later)
        notifications_list = []

        # Users/Staff data (for admin only)
        staff_list = []
        if current_user.role == 'admin':
            try:
                db = get_db()
                users_query = db.collection('users').stream()
                for user_doc in users_query:
                    user_data = user_doc.to_dict()
                    user_data['id'] = user_doc.id
                    staff_list.append(user_data)
            except Exception as e:
                print(f"Error fetching staff: {e}")
                staff_list = []

        # Print barcodes data
        barcode_products = items_list

        return render_template('index.html', 
                             items=items_list, 
                             products=items_list, 
                             categories=merged_categories, 
                             riel_rate=current_riel_rate, 
                             user_role=current_user.role, 
                             chosen_promos=chosen_promos,
                             # Report data
                             selected_date=target_date,
                             today_revenue=total_revenue,
                             transaction_count=total_orders,
                             top_items=sorted_items[:10],
                             top_products_json=top_products_json,
                             orders=filtered_orders,
                             # SPA integrated views data
                             activities=activities_list,
                             notifications=notifications_list,
                             staff=staff_list,
                             barcode_products=barcode_products)
    except Exception as e:
        print(f"Error loading POS Home: {e}")
        return render_template('index.html', items=[], products=[], categories=[], riel_rate=4025, user_role=current_user.role, chosen_promos=[], selected_date=datetime.now().strftime('%Y-%m-%d'), today_revenue=0, transaction_count=0, top_items=[], top_products_json=[], orders=[], activities=activities_list, notifications=[], staff=[], barcode_products=[])

def process_stock_deduction(cart_items):
    """
    Handles stock deduction for both SQLite and Firestore.
    Uses an atomic Firestore transaction to ensure data integrity
    across concurrent sales from multiple devices.
    """
    # 1. Update SQLite locally (Primary source for the local register)
    # This includes the safety check for sufficient local stock.
    local_success, local_msg = local_db.validate_and_deduct_stock_local(cart_items)
    if not local_success:
        return False, local_msg

    # 2. Update Firestore using a Transactional Read-Update pattern
    try:
        db = get_db()
        
        @firestore.transactional
        def deduct_stock_transaction(transaction, items):
            for item in items:
                prod_id = item.get('id')
                qty_to_deduct = int(item.get('quantity', 0) or item.get('qty', 0))
                
                doc_ref = db.collection('items').document(prod_id)
                snapshot = doc_ref.get(transaction=transaction)
                
                if not snapshot.exists:
                    raise Exception(f"Product {prod_id} not found in Firestore.")
                
                current_stock = snapshot.get('stock_quantity') or 0
                
                if current_stock < qty_to_deduct:
                    # This raises an exception which automatically rolls back the transaction
                    raise Exception(f"Insufficient Cloud stock for {snapshot.get('name')}. Available: {current_stock}")
                
                # Apply the deduction
                transaction.update(doc_ref, {
                    'stock_quantity': current_stock - qty_to_deduct
                })
            
            return True

        # Execute the transaction
        transaction = db.transaction()
        deduct_stock_transaction(transaction, cart_items)
        
        return True, "Stock deducted successfully across both databases."

    except Exception as e:
        print(f"Cloud Transaction Error: {e}")
        # In a hybrid system, if local DB is updated but cloud fails, 
        # we log the error but allow the sale to proceed locally.
        # The 'sync.py' logic can later reconcile the cloud state.
        return True, f"Stock updated locally. Cloud reconciliation pending: {str(e)}"

@app.route('/manager')
@login_required
@admin_required
def manager():
    items_list = local_db.get_products()
    return render_template('manager.html', items=items_list)

@app.route('/add_product', methods=['GET', 'POST'])
@login_required
@admin_required
def add_product():
    if request.method == 'POST':
        # 1. Defensive String Extraction & Cleaning
        name = (request.form.get('product_name') or '').strip()
        category = (request.form.get('category') or 'more').strip()
        barcode = (request.form.get('barcode') or '').strip()
        
        # Auto-generate barcode if empty
        if not barcode:
            barcode = local_db.get_next_barcode()
            
        expiry_date = request.form.get('expiry_date') or None
        
        # 2. Secure Numeric Parsing with Defaults
        try:
            raw_price = request.form.get('price', '0')
            price_riel = int(float(raw_price)) if raw_price else 0
        except (ValueError, TypeError):
            price_riel = 0
            
        try:
            stock_quantity = int(request.form.get('stock_quantity', '0') or 0)
        except (ValueError, TypeError):
            stock_quantity = 0
            
        try:
            cost_price = float(request.form.get('cost_price', '0.0') or 0.0)
        except (ValueError, TypeError):
            cost_price = 0.0
            
        try:
            low_stock_level = int(request.form.get('low_stock_level', '5') or 5)
        except (ValueError, TypeError):
            low_stock_level = 5

        # 3. Currency Conversion (Riel to USD base)
        riel_rate = get_riel_rate() or 4000  # Fallback to 4000 if rate is 0 or None
        price = float(price_riel) / riel_rate

        # 4. Image Payload Handling
        delete_image_flag = request.form.get('delete_current_image') == 'true'
        if delete_image_flag:
            filename = "default.jpg"
        else:
            image_base64 = request.form.get('image_base64')
            filename = image_base64 if image_base64 else "default.jpg"

        # 5. Data Structuring
        prod_data = {
            'name': name,
            'price': price,
            'image': filename,
            'category': category,
            'barcode': barcode,
            'stock_quantity': stock_quantity,
            'expiry_date': expiry_date,
            'cost_price': cost_price,
            'low_stock_level': low_stock_level,
            'createdAt': datetime.now()
        }

        # Validate mandatory fields
        if not name or category == 'all':
            # Basic validation failure handling
            return "Error: Product Name and a valid Category are required.", 400

        import uuid
        local_id = str(uuid.uuid4())

        # 6. Storage & Sync Execution
        try:
            db = get_db()
            _, doc_ref = db.collection('items').add(prod_data)
            local_id = doc_ref.id
        except Exception as e:
            print(f"Immediate Firebase update failed, background sync will retry: {e}")

        # Add to Local DB immediately for instant update
        prod_data['id'] = local_id
        if isinstance(prod_data['createdAt'], datetime):
            prod_data['createdAt'] = prod_data['createdAt'].isoformat()
        local_db.add_product(local_id, prod_data)
        
        # Log initial stock if > 0
        if stock_quantity > 0:
            local_db.add_stock_history(local_id, stock_quantity, 'Initial Stock')

        # Schedule auto-sync to Firebase
        sync.trigger_auto_sync(delay=5)

        return redirect(url_for('manager'))

    products = local_db.get_products()
    merged_categories = get_merged_categories()
    return render_template('add_product.html', products=products, categories=merged_categories)

@app.route('/edit_product/<product_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_product(product_id):
    product = local_db.get_product(product_id)
    if not product:
        return "Not Found", 404

    if request.method == 'POST':
        # ... (rest of the post logic)
        delete_image_flag = request.form.get('delete_current_image') == 'true'
        if delete_image_flag:
            filename = "default.jpg"
        else:
            filename = product.get('image', 'default.jpg')
            image_base64 = request.form.get('image_base64')
            if image_base64:
                filename = image_base64

        # Convert Riel input to USD base
        raw_price = request.form.get('price', '0')
        price_riel = int(float(raw_price))
        riel_rate = get_riel_rate()
        price = price_riel / riel_rate
        
        try:
            cost_price = float(request.form.get('cost_price', '0.0') or 0.0)
        except (ValueError, TypeError):
            cost_price = 0.0
            
        try:
            low_stock_level = int(request.form.get('low_stock_level', '5') or 5)
        except (ValueError, TypeError):
            low_stock_level = 5
            
        new_stock = int(request.form.get('stock_quantity', '0') or 0)
        old_stock = product.get('stock_quantity', 0)

        update_data = {
            'name': request.form.get('name'),
            'price': price,
            'image': filename,
            'category': request.form.get('category', 'more'),
            'barcode': request.form.get('barcode', '').strip(),
            'stock_quantity': new_stock,
            'expiry_date': request.form.get('expiry_date') or None,
            'cost_price': cost_price,
            'low_stock_level': low_stock_level
        }
        
        # 1. Update Local DB immediately for offline support
        existing = local_db.get_product(product_id)
        if existing:
            existing.update(update_data)
            local_db.add_product(product_id, existing)
            
            # Log stock change if it was modified manually
            if new_stock != old_stock:
                change = new_stock - old_stock
                local_db.add_stock_history(product_id, change, 'Manual Edit')
            
        # 2. Update Firestore
        try:
            db = get_db()
            db.collection('items').document(product_id).update(update_data)
        except Exception as e:
            print(f"Immediate Firebase update failed, background sync will retry: {e}")
        
        # Schedule auto-sync to pull changes back locally and ensure consistency
        sync.trigger_auto_sync(delay=5)
        
        return redirect(url_for('manager'))
        
    merged_categories = get_merged_categories()
    return render_template('edit.html', product=product, product_id=product_id, categories=merged_categories)

@app.route('/delete_product/<product_id>', methods=['POST'])
@csrf.exempt  # SECURITY: API endpoint
@login_required
@admin_required
def delete_product(product_id):
    # 1. Track deletion in local log (for sync retry if offline)
    local_db.add_to_deletion_log(product_id)

    # 2. Delete Local SQLite immediately (for UI responsiveness)
    local_db.delete_product(product_id)

    # 3. Attempt immediate Firestore delete
    try:
        db = get_db()
        db.collection('items').document(product_id).delete()
        # If successful, we could optionally remove from log, 
        # but the sync process will handle it either way.
    except Exception as e:
        print(f"Immediate Firestore delete failed, sync will retry: {e}")

    # Schedule auto-sync to ensure everything is consistent
    sync.trigger_auto_sync(delay=2)

    return redirect(url_for('manager'))

@app.route('/api/delete-multiple-products', methods=['POST'])
@csrf.exempt  # SECURITY: API endpoint
@login_required
@admin_required
def delete_products_bulk():
    """
    Bulk delete products from the local SQLite database and log them 
    for background synchronization to Cloud Firestore.
    """
    try:
        data = request.get_json()
        if not data or 'ids' not in data:
            return jsonify({'status': 'error', 'message': 'Invalid request: No product IDs provided'}), 400

        product_ids = data['ids']
        if not isinstance(product_ids, list):
            return jsonify({'status': 'error', 'message': 'Invalid request: "ids" must be a list'}), 400

        # We use standard sqlite3 execution calls within a single transaction
        conn = local_db.get_connection()
        deleted_count = 0
        
        try:
            cursor = conn.cursor()
            # The transaction ensures that either ALL deletions/logs succeed or NONE do.
            for product_id in product_ids:
                # 1. Execute the SQL command to delete the product from the local products table.
                cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
                
                # 2. Execute a secondary SQL command to insert a record into the deleted_products table.
                # We use the existing schema (id, deleted_at) found in local_db.py to maintain sync compatibility.
                cursor.execute(
                    "INSERT OR REPLACE INTO deleted_products (id, deleted_at) VALUES (?, ?)",
                    (product_id, datetime.now().isoformat())
                )
                deleted_count += 1
            
            # Commit the transaction only after all items have been successfully processed and logged.
            conn.commit()
            
            # Trigger background sync to update Firestore
            sync.trigger_auto_sync(delay=2)
            
            return jsonify({
                'status': 'success',
                'message': f'Successfully deleted and logged {deleted_count} products for sync.'
            })
            
        except Exception as e:
            conn.rollback()
            print(f"Bulk delete transaction failed: {e}")
            return jsonify({'status': 'error', 'message': f'Database error: {str(e)}'}), 500
        finally:
            conn.close()
            
    except Exception as e:
        print(f"Bulk delete error: {e}")
        return jsonify({'status': 'error', 'message': f'Server error: {str(e)}'}), 500

# --- INVENTORY API ROUTES ---
@app.route('/api/products/<product_id>/restock', methods=['POST'])
@csrf.exempt
@login_required
@admin_required
def restock_product(product_id):
    """Restock a product and log the transaction"""
    try:
        data = request.get_json()
        quantity = int(data.get('quantity', 0))
        
        if quantity <= 0:
            return jsonify({'success': False, 'error': 'Quantity must be greater than 0'})
            
        product = local_db.get_product(product_id)
        if not product:
            return jsonify({'success': False, 'error': 'Product not found'})
            
        new_stock = product.get('stock_quantity', 0) + quantity
        
        # Update local DB and log history
        local_db.update_product_stock(product_id, new_stock, 'Restock')
        
        # Update Firestore
        try:
            db = get_db()
            db.collection('items').document(product_id).update({'stock_quantity': new_stock})
        except Exception as e:
            print(f"Firebase update failed for restock: {e}")
            
        # Trigger sync
        sync.trigger_auto_sync(delay=2)
        
        return jsonify({'success': True, 'new_stock': new_stock})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/products/<product_id>/history', methods=['GET'])
@login_required
@admin_required
def get_product_history(product_id):
    """Get stock history for a product"""
    try:
        history = local_db.get_stock_history(product_id)
        return jsonify({'success': True, 'history': history})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/reports')
@login_required
@admin_required
def reports():
    try:
        report_type = request.args.get('type', 'daily')
        target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
        now = datetime.now()
        
        if report_type == 'daily':
            try:
                dt = datetime.strptime(target_date, '%Y-%m-%d')
                start_date = datetime(dt.year, dt.month, dt.day)
            except:
                start_date = datetime(now.year, now.month, now.day)
            title = f"របាយការណ៍ថ្ងៃទី {target_date} (Daily Report)"
        elif report_type == 'monthly':
            start_date = datetime(now.year, now.month, 1)
            title = "របាយការណ៍ប្រចាំខែ (Monthly Report)"
        elif report_type == 'annually':
            start_date = datetime(now.year, 1, 1)
            title = "របាយការណ៍ប្រចាំឆ្នាំ (Annual Report)"
        else:
            start_date = datetime(now.year, now.month, now.day)
            title = "របាយការណ៍ (Report)"

        all_orders = local_db.get_all_orders()
        total_revenue = 0
        total_orders = 0
        item_summary = {}
        filtered_orders = []

        # End date for filtering
        if report_type == 'daily':
            end_date = start_date + timedelta(days=1)
        elif report_type == 'monthly':
            if now.month == 12:
                end_date = datetime(now.year + 1, 1, 1)
            else:
                end_date = datetime(now.year, now.month + 1, 1)
        elif report_type == 'annually':
            end_date = datetime(now.year + 1, 1, 1)
        else:
            end_date = now + timedelta(days=1)

        for data in all_orders:
            created_at = datetime.fromisoformat(data['created_at'])
            if start_date <= created_at < end_date:
                if data.get('status') in ['paid', 'completed']:
                    total_revenue += float(data.get('total', 0))
                    total_orders += 1
                    for item in data.get('items', []):
                        name = item.get('name', 'Unknown')
                        qty = int(item.get('quantity', 0) or item.get('qty', 0))
                        price = float(item.get('price', 0))
                        if name in item_summary:
                            item_summary[name]['qty'] += qty
                            item_summary[name]['total'] += (qty * price)
                        else:
                            item_summary[name] = {'qty': qty, 'total': (qty * price)}
                    data['display_time'] = created_at.strftime('%d/%m/%Y %H:%M')
                    filtered_orders.append(data)

        sorted_items = sorted(item_summary.items(), key=lambda x: x[1]['qty'], reverse=True)
        
        # Define report_files to avoid NameError
        report_files = os.listdir('reports') if os.path.exists('reports') else []
        
        # We provide both the requested names and the existing ones to maintain compatibility
        return render_template('reports.html', 
                             title=title, 
                             type=report_type, 
                             selected_date=target_date,
                             total_revenue=total_revenue, 
                             today_revenue=total_revenue, # Requested name
                             total_orders=total_orders, 
                             transaction_count=total_orders, # Requested name
                             top_items=sorted_items, 
                             orders=filtered_orders,
                             report_files=report_files)
    except Exception as e:
        print(f"Report Error: {e}")
        flash(f"Error generating report: {e}", 'error')
        return redirect(url_for('manager'))

# This route allows the browser to actually download/view the file
@app.route('/reports/view/<filename>')
@login_required
@admin_required
def view_report(filename):
    # SECURITY: Validate filename to prevent path traversal
    filename = secure_filename(filename)
    if not filename or '..' in filename or filename.startswith('/'):
        return "Invalid filename", 400
    
    # Use absolute path to the 'reports' directory
    reports_dir = os.path.abspath(os.path.join(os.getcwd(), 'reports'))
    
    # SECURITY: Ensure the requested file is actually inside reports_dir
    requested_path = os.path.abspath(os.path.join(reports_dir, filename))
    if not requested_path.startswith(reports_dir):
        return "Access denied", 403
    
    # Check if file exists before trying to send
    if not os.path.exists(requested_path):
        return "File not found", 404
        
    return send_from_directory(reports_dir, filename)

@app.route('/api/delete-report/<report_id>', methods=['DELETE'])
@csrf.exempt
@login_required
@admin_required
def delete_report(report_id):
    """Delete a report/transaction by its ID"""
    try:
        print(f"[DELETE REPORT] Attempting to delete report_id: {report_id}")
        
        # Validate report_id is not empty
        if not report_id or report_id.strip() == '':
            print(f"[DELETE REPORT] Error: Empty report_id provided")
            return jsonify({
                'status': 'error',
                'message': 'Report ID cannot be empty'
            }), 400
        
        # Attempt to delete from local database
        success = local_db.delete_order(report_id)
        print(f"[DELETE REPORT] Delete result: success={success}")
        
        if success:
            return jsonify({
                'status': 'success',
                'message': f'Report {report_id} deleted successfully'
            }), 200
        else:
            return jsonify({
                'status': 'error',
                'message': f'Report {report_id} not found or already deleted'
            }), 404
    except Exception as e:
        print(f"[DELETE REPORT] Exception error deleting report {report_id}: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'status': 'error',
            'message': f'Error deleting report: {str(e)}'
        }), 500

@app.route('/api/edit-report/<report_id>', methods=['PUT', 'POST'])
@csrf.exempt
@login_required
@admin_required
def edit_report(report_id):
    """Update a report/transaction details"""
    try:
        print(f"[EDIT REPORT] Attempting to edit report_id: {report_id}")
        print(f"[EDIT REPORT] Request method: {request.method}")
        print(f"[EDIT REPORT] Content-Type: {request.content_type}")
        print(f"[EDIT REPORT] Request data raw: {request.data}")
        
        # Validate report_id is not empty
        if not report_id or report_id.strip() == '':
            print(f"[EDIT REPORT] Error: Empty report_id provided")
            return jsonify({
                'status': 'error',
                'message': 'Report ID cannot be empty'
            }), 400
        
        # Parse JSON data with silent=True to prevent raising 400 before we log
        data = request.get_json(silent=True)
        print(f"[EDIT REPORT] Parsed JSON data: {data}")
        
        if not data:
            print(f"[EDIT REPORT] Error: No JSON data provided")
            return jsonify({
                'status': 'error',
                'message': 'No data provided or invalid JSON format'
            }), 400
        
        success, message = local_db.update_order(report_id, data)
        print(f"[EDIT REPORT] Update result: success={success}, message={message}")
        
        if success:
            return jsonify({
                'status': 'success',
                'message': message
            }), 200
        else:
            return jsonify({
                'status': 'error',
                'message': message
            }), 400
    except Exception as e:
        print(f"[EDIT REPORT] Exception error editing report {report_id}: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'status': 'error',
            'message': f'Error editing report: {str(e)}'
        }), 500

@app.route('/notifications')
@login_required
def notifications():
    return render_template('notifications.html', active_page='notifications')

@app.route('/activities')
@login_required
def activities():
    return render_template('activities.html', active_page='activities')

@app.route('/users')
@login_required
@admin_required
def users():
    return render_template('users.html', active_page='users')

@app.route('/print_barcodes', methods=['GET', 'POST'])
@login_required
@admin_required
def print_barcodes():
    if request.method == 'POST':
        product_ids = request.form.getlist('product_ids')
        all_products = local_db.get_products()
        selected_products = [p for p in all_products if p['id'] in product_ids]
        
        # Add riel price for display
        riel_rate = get_riel_rate()
        for p in selected_products:
            p['price_riel'] = int(p['price'] * riel_rate)
            
        return render_template('print_barcodes.html', print_mode=True, products=selected_products)
    
    products = local_db.get_products()
    return render_template('print_barcodes.html', print_mode=False, products=products)

@app.route('/cosmets')
@login_required
def cosmets():
    items_list = local_db.get_products()
    return render_template('cosmets.html', active_page='products', items=items_list)

@app.route('/terminology')
@login_required
def terminology():
    return render_template('terminology.html', active_page='terminology')

@app.route('/live-orders')
@login_required
def live_orders():
    all_orders = local_db.get_all_orders()
    active_orders = [o for o in all_orders if o['status'] == 'pending']
    for data in active_orders:
        dt = datetime.fromisoformat(data['created_at'])
        data['display_time'] = dt.strftime('%d/%m/%Y %I:%M %p')
        data['id'] = data.get('firestore_id') or f"local_{data['local_id']}"
    return render_template('orders.html', active_orders=active_orders)

@app.route('/complete_order/<order_id>', methods=['POST'])
@csrf.exempt  # SECURITY: API endpoint
@login_required
def complete_order(order_id):
    if order_id.startswith('local_'):
        local_id = int(order_id.replace('local_', ''))
        local_db.update_order_status(local_id, 'completed')
    else:
        db = get_db()
        db.collection('orders').document(order_id).update({'status': 'completed'})
        # Find local and update too
        for o in local_db.get_all_orders():
            if o.get('firestore_id') == order_id:
                local_db.update_order_status(o['local_id'], 'completed')
                break
    return redirect(url_for('live_orders'))

def classify_inventory_status(stock_qty):
    """
    Dynamically maps each item's current stock count to a status payload 
    containing Khmer/English labels, color tokens, and warning icons.
    """
    if stock_qty <= 0:
        return {
            "status": "out_of_stock",
            "label_km": "អស់ពីស្តុក",
            "label_en": "Out of Stock",
            "message_km": "អស់ពីស្តុកហើយ!",
            "message_en": "Out of stock!",
            "color": "#dc3545", # High-severity Red
            "bg_color": "rgba(220, 53, 69, 0.1)",
            "icon": "alert-circle",
            "severity": "high"
        }
    elif stock_qty <= 5:
        return {
            "status": "low_stock",
            "label_km": "ស្កុកទាប",
            "label_en": "Low Stock",
            "message_km": f"មាននៅសល់តែ {stock_qty} ប៉ុណ្ណោះ។",
            "message_en": f"Only {stock_qty} left in stock.",
            "color": "#fd7e14", # Warning Amber/Orange
            "bg_color": "rgba(253, 126, 20, 0.1)",
            "icon": "alert-triangle",
            "severity": "medium"
        }
    else:
        return {
            "status": "in_stock",
            "label_km": "មានក្នុងស្តុក",
            "label_en": "In Stock",
            "color": "#198754",
            "bg_color": "rgba(25, 135, 84, 0.1)",
            "icon": "check-circle",
            "severity": "low"
        }

@app.route('/api/notifications', methods=['GET'])
@login_required
def get_notifications():
    """Aggregates all active system notifications/alerts."""
    notifications = []
    
    # 1. Inventory Alerts (Out of Stock & Low Stock)
    inventory_items = local_db.get_low_stock_products(5)
    for item in inventory_items:
        status_info = classify_inventory_status(item['stock_quantity'])
        notifications.append({
            "id": f"inv-{item['id']}",
            "type": status_info['status'],
            "severity": status_info['severity'],
            "title": f"{status_info['label_km']} ({status_info['label_en']})",
            "message": f"{item['name']} {status_info['message_km']}",
            "time": "Just now",
            "icon": status_info['icon'],
            "color": status_info['color'],
            "bg_color": status_info['bg_color'],
            "stock_quantity": item['stock_quantity']
        })
    
    # 2. Info: New User Pending (Admin only)
    if current_user.role == 'admin':
        users = local_db.get_all_users()
        pending_users = [u for u in users if u.get('status') == 'pending']
        for u in pending_users:
            notifications.append({
                "id": f"new-user-{u['username']}",
                "type": "info",
                "severity": "medium",
                "title": "អ្នកប្រើប្រាស់ថ្មី (New User)",
                "message": f"{u['username']} កំពុងរង់ចាំការអនុញ្ញាត។",
                "time": "Action Required",
                "icon": "user-plus",
                "color": "#0d6efd",
                "bg_color": "rgba(13, 110, 253, 0.1)"
            })
            
    # Success Placeholder removed or kept as low priority
    # Sort: high severity first
    severity_map = {"high": 0, "medium": 1, "low": 2}
    notifications.sort(key=lambda x: severity_map.get(x.get('severity', 'low'), 2))

    return jsonify({
        "status": "success",
        "notifications": notifications,
        "count": len(notifications)
    })

@app.route('/api/low-stock-alerts', methods=['GET'])
@login_required
def low_stock_alerts():
    threshold = request.args.get('threshold', 5, type=int)
    low_stock_items = local_db.get_low_stock_products(threshold)
    return jsonify({
        "status": "success",
        "products": low_stock_items,
        "count": len(low_stock_items)
    })

@app.route('/api/restock', methods=['POST'])
@csrf.exempt  # SECURITY: API endpoint
@login_required
@admin_required
def restock():
    try:
        data = request.get_json()
        product_id = data.get('product_id')
        quantity = int(data.get('quantity', 0))
        if not product_id or quantity <= 0:
            return jsonify({"status": "error", "message": "Invalid data (product_id or quantity missing)"}), 400
        
        local_db.add_product_stock(product_id, quantity)
        # Schedule auto-sync to push updated stock to Firestore
        sync.trigger_auto_sync(delay=5)
        return jsonify({"status": "success", "message": f"Successfully added {quantity} units to stock."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/reports/daily', methods=['GET'])
@login_required
@admin_required
def daily_report_api():
    try:
        target_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
        report_data = local_db.get_daily_sales_report(target_date)
        return jsonify({
            "status": "success",
            "date": target_date,
            "data": report_data
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/reports/monthly', methods=['GET'])
@login_required
@admin_required
def monthly_report_api():
    try:
        target_month = request.args.get('month', datetime.now().strftime('%Y-%m'))
        report_data = local_db.get_monthly_sales_report(target_month)
        return jsonify({
            "status": "success",
            "month": target_month,
            "data": report_data
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/reports/annually', methods=['GET'])
@login_required
@admin_required
def annually_report_api():
    try:
        target_year = request.args.get('year', datetime.now().strftime('%Y'))
        report_data = local_db.get_annual_sales_report(target_year)
        return jsonify({
            "status": "success",
            "year": target_year,
            "data": report_data
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/checkout', methods=['POST'])
@csrf.exempt  # SECURITY: API endpoint
@login_required
def checkout_endpoint():
    try:
        data = request.json
        items = data.get('items', [])
        total = data.get('total')
        discount = data.get('discount', 0)
        tran_id = data.get('tran_id') or f"CASH-{int(datetime.now().timestamp())}"

        # 1. Deduct Stock Locally
        success, msg = local_db.validate_and_deduct_stock_local(items)
        if not success:
            return jsonify({'status': 'error', 'message': msg}), 400

        # 2. Save to local database with 'paid' status
        order_data = {
            'items': items,
            'total': total,
            'discount': discount,
            'status': 'paid',
            'tran_id': tran_id,
            'user': current_user.username if hasattr(current_user, 'username') else 'guest',
            'created_at': datetime.now().isoformat()
        }

        local_id = local_db.add_order(order_data)

        # 3. Generate and archive PDF receipt immediately (same as QR flow)
        try:
            # Get currency rate for PDF
            riel_rate = get_riel_rate()
            
            # Fetch the order from the database
            order = local_db.get_order(tran_id)
            if order is None:
                order = local_db.get_order(local_id)
            
            if order:
                # Render the invoice template as a string with order data and PDF flag
                html_content = render_template('invoice.html', 
                                             items=order.get('items', []),
                                             total=order.get('total', 0),
                                             discount=order.get('discount', 0),
                                             date=datetime.fromisoformat(order.get('created_at', datetime.now().isoformat())).strftime("%d/%m/%Y %H:%M:%S"),
                                             payment_method='cash',
                                             RIEL_RATE=riel_rate,
                                             is_pdf=True)
                
                # Convert HTML to PDF using weasyprint (supports CTL for Khmer text)
                pdf_bytes = HTML(string=html_content, base_url=request.host_url).write_pdf()
                
                # SECURITY: Ensure reports directory exists with absolute path
                reports_dir = os.path.abspath(os.path.join(os.getcwd(), 'reports'))
                if not os.path.exists(reports_dir):
                    os.makedirs(reports_dir)
                
                # SECURITY: Strict filename validation to prevent path traversal
                pdf_filename = secure_filename(f"invoice_{tran_id}.pdf")
                if pdf_filename and pdf_filename.endswith('.pdf'):
                    # SECURITY: Ensure final path is within reports directory
                    pdf_filepath = os.path.abspath(os.path.join(reports_dir, pdf_filename))
                    if pdf_filepath.startswith(reports_dir):
                        with open(pdf_filepath, 'wb') as f:
                            f.write(pdf_bytes)
                        
                        # Update database to mark receipt as archived
                        local_db.update_order_receipt_status(tran_id, 1)
                        print(f"✅ PDF receipt generated for cash order: {tran_id}")
        except Exception as pdf_error:
            print(f"⚠️ PDF generation warning for {tran_id}: {str(pdf_error)}")
            # Don't fail the checkout if PDF generation fails, just log the warning

        # 4. Trigger Auto-Sync
        sync.trigger_auto_sync(delay=5)

        return jsonify({
            'status': 'success',
            'message': 'Checkout successful',
            'local_id': local_id,
            'tran_id': tran_id
        })

    except Exception as e:
        print(f"❗ Checkout Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/create-aba-payment', methods=['POST'])
@csrf.exempt  # SECURITY: API endpoint
@login_required
def create_aba_payment():
    try:
        data = request.json
        items = data.get('items', [])
        total = data.get('total')
        discount = data.get('discount', 0)
        # Use provided tran_id from frontend (e.g. TX123456789)
        tran_id = data.get('tran_id') or f"NSP-{int(datetime.now().timestamp())}"
        
        # Save to local database with 'pending' status
        # aba_listener.py will look for 'pending' orders with matching amount
        order_data = {
            'items': items,
            'total': total,
            'discount': discount,
            'status': 'pending',
            'tran_id': tran_id,
            'user': current_user.username if hasattr(current_user, 'username') else 'guest',
            'created_at': datetime.now().isoformat()
        }
        
        local_db.add_order(order_data)
        
        print(f"✅ Order saved as pending locally. Tran ID: {tran_id} | Amount: ${total}")
        
        return jsonify({
            'status': 'success',
            'message': 'Order created and pending payment',
            'tran_id': tran_id
        })

    except Exception as e:
        print(f"❌ Backend Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/check-status')
@csrf.exempt  # SECURITY: API endpoint
@login_required
def check_status():
    tran_id = request.args.get('tran_id')
    # Fixed: Use get_order(tran_id) to look up by the string transaction ID
    order = local_db.get_order(tran_id) 
    
    if order:
        return jsonify({"status": order['status']})
    
    # Crucial: Always return JSON, even if not found
    return jsonify({"status": "pending"})

@app.route('/success')
def success():
    tran_id = request.args.get('tran_id')
    print(f"DEBUG: Attempting to load success page for ID: {tran_id}")
    
    try:
        # SECURITY: Validate tran_id to prevent XSS via error messages
        if not tran_id or not all(c.isalnum() or c in '-_' for c in tran_id):
            return render_template('ai_green.html', message="Invalid transaction ID"), 400
        
        # Fetch the order from the database
        order = local_db.get_order(tran_id)
        
        if order is None:
            # SECURITY: Don't reflect raw user input in error response
            return render_template('ai_green.html', message="Order not found"), 404
            
        riel_rate = get_riel_rate()
        
        # If this line is the problem, the terminal will tell us
        return render_template('success.html', order=order, RIEL_RATE=riel_rate)
        
    except Exception as e:
        # This print statement is the most important part
        print(f"❌ CRASH DETECTED: {e}") 
        # SECURITY: Don't expose internal error details to the user
        return render_template('ai_green.html', message="System Error: Unable to load order"), 500

@app.route('/invoice', methods=['GET', 'POST'])
@login_required
def invoice():
    # SECURITY: Extract data from GET or POST
    if request.method == 'POST':
        # CSRF token validation is automatic with Flask-WTF when using render_template
        cart_json = request.form.get('cart', '[]')
        total = request.form.get('total', '0')
        discount = request.form.get('discount', '0')
        payment_method = request.form.get('payment_method', 'qr')
    else:
        # GET request (backward compatibility)
        cart_json = request.args.get('cart', '[]')
        total = request.args.get('total', '0')
        discount = request.args.get('discount', '0')
        payment_method = request.args.get('payment_method', 'qr')
    
    date = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    
    # SECURITY: Safely parse JSON and validate structure
    try:
        items_list = json.loads(cart_json) if cart_json else []
        if not isinstance(items_list, list):
            items_list = []
        # Sanitize each item to only allow expected fields
        sanitized_items = []
        for item in items_list:
            if isinstance(item, dict):
                sanitized_items.append({
                    'id': item.get('id') or item.get('product_id'),
                    'barcode': item.get('barcode', ''),
                    'name': str(item.get('name', 'Unknown'))[:200],
                    'price': float(item.get('price', 0)),
                    'quantity': int(item.get('quantity', 0) or item.get('qty', 0)),
                    'qty': int(item.get('quantity', 0) or item.get('qty', 0)),
                    'image': str(item.get('image', 'default.jpg'))[:500]
                })
        items_list = sanitized_items
    except (json.JSONDecodeError, ValueError, TypeError):
        items_list = []
    
    # SECURITY: Validate total and discount are numbers
    try:
        total = str(float(total))
    except (ValueError, TypeError):
        total = '0'
    
    try:
        discount = str(float(discount))
    except (ValueError, TypeError):
        discount = '0'
    
    # SECURITY: Validate payment_method is whitelisted
    if payment_method not in ['cash', 'qr']:
        payment_method = 'qr'
    
    return render_template('invoice.html', items=items_list, total=total, discount=discount, date=date, payment_method=payment_method)

@app.errorhandler(404)
def not_found_error(error):
    return render_template('ai_green.html', message="រកមិនឃើញទំព័រនេះទេ (Page Not Found)"), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('ai_green.html', message="មានបញ្ហាបច្ចេកទេស (Internal Server Error)"), 500

# Firebase Cloud Function export
# Replace your previous nsp_cosmetic_store_pos line with this:

@https_fn.on_request()
def nsp_cosmetic_store_pos(req: https_fn.Request) -> https_fn.Response:
    with app.request_context(req.environ):
        return app.full_dispatch_request()

def open_browser():
    webbrowser.open_new('http://127.0.0.1:5000/')
if __name__ == '__main__':
    # Initialize the local database
    local_db.init_db()
    
    # Initial Sync
    print("Performing initial sync...")
    sync.sync_all()
    
    # Start background sync service (every 30 seconds)
    # sync.start_background_sync(interval=30)
    
    # Wait 1.5 seconds for the server to start, then open the browser
    Timer(1.5, open_browser).start()
    
    # Start the server (debug=False is best for your final .exe)
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
# --- FIREBASE CLOUD FUNCTIONS WRAPPER ---
from firebase_functions import https_fn
from firebase_admin import initialize_app

# Initialize the Firebase Admin SDK (safely)
try:
    initialize_app()
except ValueError:
    pass 

# Expose the Flask app to Firebase HTTPS routing
@https_fn.on_request()
def nsp_pos_server(req: https_fn.Request) -> https_fn.Response:
    return https_fn.wsgi_app(app, req)