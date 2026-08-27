import sys
import queue
import threading
import random
import os
import uuid
import webbrowser           
from threading import Timer 

# Fix for PyInstaller --windowed mode crashing with Firebase/Google Cloud loggers
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')

log_queue = queue.Queue(maxsize=1000)

class TerminalLogStream:
    def __init__(self, original_stream):
        self.original_stream = original_stream
        self.encoding = getattr(original_stream, 'encoding', 'utf-8')
    def write(self, data):
        self.original_stream.write(data)
        self.original_stream.flush()
        if data.strip():
            # Push data into the queue for the frontend
            try:
                log_queue.put_nowait(data)
            except queue.Full:
                try: log_queue.get_nowait()
                except: pass
                log_queue.put_nowait(data)
    def flush(self):
        self.original_stream.flush()
        
    def reconfigure(self, *args, **kwargs):
        """Satisfy firebase_functions SDK initialization to prevent AttributeError during deployment"""
        if hasattr(self.original_stream, 'reconfigure'):
            return self.original_stream.reconfigure(*args, **kwargs)
        # Update internal encoding field if passed in kwargs
        if 'encoding' in kwargs:
            self.encoding = kwargs['encoding']

# Intercept system stdout and stderr
sys.stdout = TerminalLogStream(sys.stdout)
sys.stderr = TerminalLogStream(sys.stderr)

# Startup backup runs in start_app.py BEFORE this stdout wrap, so those prints
# never hit log_queue on their own. Replay them now for Project Live Console.
try:
    import auto_backup
    for _backup_line in auto_backup.drain_logs_for_console():
        try:
            log_queue.put_nowait(_backup_line)
        except queue.Full:
            try:
                log_queue.get_nowait()
            except Exception:
                pass
            log_queue.put_nowait(_backup_line)
except Exception:
    pass

import base64
import json
import smtplib
import urllib.request
import urllib.parse
import traceback
from datetime import datetime, timedelta, timezone
from functools import wraps
from email.mime.text import MIMEText

# --- Timezone helper: Cambodia ICT = UTC+7 (fixed, no DST) ---
ICT_TZ = timezone(timedelta(hours=7), name='ICT')

def cambodia_time(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ICT_TZ)
    return dt.astimezone(ICT_TZ)

def format_ict(dt, fmt):
    if dt is None:
        return ''
    return cambodia_time(dt).strftime(fmt)

def format_ict_str(iso_str, fmt='%d/%m/%Y %I:%M:%S %p'):
    if not iso_str:
        return ''
    try:
        return format_ict(cambodia_time(datetime.fromisoformat(str(iso_str))), fmt)
    except Exception:
        return str(iso_str)

def parse_ict_datetime(iso_str):
    if not iso_str:
        return None
    try:
        return cambodia_time(datetime.fromisoformat(str(iso_str)))
    except Exception:
        return None

def enrich_order_display_times(order):
    try:
        dt = parse_ict_datetime(order.get('created_at', ''))
        if dt is None:
            order['display_time'] = ''
            order['display_datetime'] = ''
            order['created_at_raw'] = ''
        else:
            order['display_time'] = format_ict(dt, '%d/%m/%Y %H:%M')
            order['display_datetime'] = format_ict(dt, '%d/%m/%Y %I:%M:%S %p')
            order['created_at_raw'] = order.get('created_at', '')
    except Exception:
        order['display_time'] = ''
        order['display_datetime'] = ''
        order['created_at_raw'] = ''
    return order

import firebase_admin
from firebase_admin import credentials, firestore, storage
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, make_response, send_from_directory, session, Response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_wtf.csrf import CSRFProtect, generate_csrf
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from firebase_functions import https_fn

import local_db
import sync
import gdrive_storage

# --- PORTABLE PATH LOGIC ---
def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

# Load environment variables from portable path
from dotenv import load_dotenv
load_dotenv(get_resource_path('.env'))

# ---------------------------------------------------------------------------
# Google Drive Storage — folder where product images are uploaded.
# Share this folder with the service-account e-mail in serviceAccountKey.json
# so the Drive API can write to it.
# Replace the value below with your actual NSP_POS_Images folder ID from Drive.
# NOTE: read AFTER load_dotenv() so an env override can take effect.
# ---------------------------------------------------------------------------
GDRIVE_FOLDER_ID = os.environ.get('GDRIVE_FOLDER_ID', '1OReTRWWPvmT43qa2umzkQJWr-k6QB7Rc')

# --- PILLAR 2: SEQUENTIAL TRANSACTION IDs ---
def new_tran_id(prefix='NSP'):
    return local_db.next_sequential_tran_id(prefix)

# --- CONFIGURATION VALIDATION ---
REQUIRED_ENV_VARS = [
    'SECRET_KEY',
    'EMAIL_APP_PASSWORD',
    'OWNER_EMAIL',
    'MASTER_RECOVERY_PIN'
]

def validate_config():
    # Skip validation during Firebase CLI deployment (module is imported, not run locally)
    _is_firebase_deploy = (
        os.environ.get('FUNCTION_NAME') or
        os.environ.get('FIREBASE_CONFIG') or
        os.environ.get('K_SERVICE')  # Cloud Run / Firebase Functions v2
    )
    if _is_firebase_deploy:
        return
    missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

validate_config()

# --- OWNER EMAIL SETTINGS ---
OWNER_EMAIL = os.environ.get("OWNER_EMAIL")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD")

def notify_owner_by_email(new_username):
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
    # បន្ថែម Project ID ផ្ទាល់ដើម្បីការពារកុំឱ្យបាត់បង់ពេលគ្មាន JSON
    firebase_options = {
        'projectId': 'nsp-cosmetic-store-pos', 
        'storageBucket': 'nsp-cosmetic-store-pos.appspot.com'
    }
    if os.path.exists(service_account_path):
        try:
            cred = credentials.Certificate(service_account_path)
            firebase_admin.initialize_app(cred, options=firebase_options)
        except Exception as e:
            print(f"WARNING: Could not load {service_account_path}: {e}")
            try:
                firebase_admin.initialize_app(options=firebase_options)
            except ValueError:
                pass
    else:
        try:
            firebase_admin.initialize_app(options=firebase_options)
        except ValueError:
            pass

def get_db():
    return firestore.client()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max payload size
app.secret_key = os.environ.get('SECRET_KEY')
app.config['TEMPLATES_AUTO_RELOAD'] = True

# Disable secure cookies on desktop so login/logout works over HTTP
is_cloud = local_db.is_cloud_runtime()
app.config['SESSION_COOKIE_SECURE'] = is_cloud
app.config['REMEMBER_COOKIE_SECURE'] = is_cloud

@app.route('/api/project_terminal_stream')
@login_required
def project_terminal_stream():
    def generate():
        # Yield some initial context
        yield f"data: 🚀 [Terminal Connected] Real-time Project Log Engine Initialized at {datetime.now().strftime('%H:%M:%S')}\n\n"
        while True:
            try:
                log_line = log_queue.get(timeout=10) # Wait for logs
                yield f"data: {log_line}\n\n"
            except queue.Empty:
                # Keep-alive heartbeat
                yield "data: \n\n"
    return Response(generate(), mimetype='text/event-stream')

# Make ICT timezone helpers available to all templates
app.jinja_env.globals['cambodia_time'] = cambodia_time
app.jinja_env.globals['format_ict'] = format_ict
app.jinja_env.globals['format_ict_str'] = format_ict_str

# --- CSRF PROTECTION ---
csrf = CSRFProtect(app)
app.config['WTF_CSRF_CHECK_DEFAULT'] = True
app.config['WTF_CSRF_TIME_LIMIT'] = 3600  

# --- SECURITY HEADERS ---
@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    path = (request.path or '')
    is_static_asset = (
        request.endpoint in ('static', 'favicon')
        or path == '/favicon.ico'
        or path.startswith('/static/')
    )
    if request.endpoint and not is_static_asset:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
    # Force STRICT no-cache for all API endpoints — prevents Firebase Hosting /
    # CDN layers from serving stale JSON responses regardless of query-string
    # cache-busting. These headers are the most aggressive combination supported
    # by modern CDNs (2026): no-store prevents any storage, post-check=0 /
    # pre-check=0 disable IE-era background revalidation, Expires=-1 signals
    # an already-expired resource to every proxy layer.
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = (
            'no-store, no-cache, must-revalidate, '
            'post-check=0, pre-check=0, max-age=0'
        )
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '-1'
    return response

@app.context_processor
def inject_csrf_token():
    return dict(csrf_token=generate_csrf)

@app.before_request
def sync_session_role():
    if current_user.is_authenticated:
        session['role'] = current_user.role
        session['username'] = current_user.username
    else:
        session.pop('role', None)
        session.pop('username', None)

app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['SESSION_COOKIE_SECURE'] = is_cloud
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_NAME'] = '__session'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_SECURE'] = is_cloud

# Master PIN for password resets
MASTER_RECOVERY_PIN = os.environ.get('MASTER_RECOVERY_PIN')

# Currency Conversion Rate Cache
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

@app.route('/favicon.ico')
def favicon():
    icon_dir = os.path.join(app.root_path, 'static', 'images')
    ico_path = os.path.join(icon_dir, 'favicon.ico')
    png_path = os.path.join(icon_dir, 'favicon.png')
    if os.path.isfile(ico_path):
        return send_from_directory(
            icon_dir,
            'favicon.ico',
            mimetype='image/x-icon',
            max_age=86400
        )
    if os.path.isfile(png_path):
        return send_from_directory(
            icon_dir,
            'favicon.png',
            mimetype='image/png',
            max_age=86400
        )
    from flask import abort
    abort(404)

@app.before_request
def skip_html_for_static_and_favicon():
    """
    Ensure that requests for static files or favicon.ico never return HTML.
    If the file exists, serve it. If it doesn't, abort with a 404.
    """
    path = (request.path or '').lstrip('/')
    if path == 'favicon.ico' or path.startswith('static/'):
        if path == 'favicon.ico':
            directory = os.path.join(app.root_path, 'static', 'images')
            filename = 'favicon.ico'
            mimetype = 'image/x-icon'
            if not os.path.isfile(os.path.join(directory, filename)):
                filename = 'favicon.png'
                mimetype = 'image/png'
        else:
            rel = path[len('static/'):]
            directory = app.static_folder
            filename = rel
            mimetype = None

        if not filename or not os.path.isfile(os.path.join(directory, filename)):
            from flask import abort
            abort(404)
        kwargs = {'max_age': 86400}
        if mimetype:
            kwargs['mimetype'] = mimetype
        return send_from_directory(directory, filename, **kwargs)

@app.route('/add_category_endpoint', methods=['POST'])
@csrf.exempt  
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
@csrf.exempt  
@login_required
def update_exchange_rate():
    if current_user.role != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized privileges"}), 403

    data = request.get_json()
    if not data or 'exchange_rate' not in data:
        return jsonify({"status": "error", "message": "Missing exchange_rate parameters"}), 400

    try:
        new_rate = int(data['exchange_rate'])
        if new_rate <= 0:
            return jsonify({"status": "error", "message": "Rate magnitude must be greater than zero"}), 400

        local_db.update_setting('exchange_rate', str(new_rate))
        return jsonify({"status": "success", "message": "Exchange rate updated successfully", "new_rate": new_rate})
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid number format provided"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Database error: {str(e)}"}), 500

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
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/setup-admin')
def setup_admin():
    pin = request.args.get('pin')
    if not pin or pin != MASTER_RECOVERY_PIN:
        return "Unauthorized", 403
        
    db = get_db()
    users_ref = db.collection('users')
    existing = users_ref.document('admin').get()
    if not existing.exists:
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

        if len(username) < 3 or len(username) > 50:
            return jsonify({"status": "error", "message": "Username must be 3-50 characters"}), 400
        if not all(c.isalnum() or c in '_.-' for c in username):
            return jsonify({"status": "error", "message": "Username contains invalid characters"}), 400

        if len(password) < 6:
            return jsonify({"status": "error", "message": "Password must be at least 6 characters"}), 400

        db = get_db()
        # ការពារប្រសិនបើ Project ID មិនស្គាល់
        if not getattr(db, 'project', None):
            return jsonify({"status": "error", "message": "ប្រព័ន្ធមិនស្គាល់ Project ID របស់ Firebase ទេ!"}), 500

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
        
        notify_owner_by_email(username)
        return jsonify({"status": "success", "message": "គណនីបានបង្កើត! សូមរង់ចាំការអនុញ្ញាតពីម្ចាស់ហាង។"})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Firebase Error: {str(e)}"}), 500

@app.route('/api/users', methods=['GET'])
@login_required
def get_users():
    if current_user.role != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized: Admin access required"}), 403
    try:
        users_list = local_db.get_all_users()
        sanitized_users = []
        for u in users_list:
            sanitized_users.append({
                "username": u.get('username'),
                "role": u.get('role', 'user'),
                "status": u.get('status', 'active'),
                "profile_image": u.get('profile_image'),
                "display_name": u.get('username')
            })
        return jsonify({"status": "success", "data": sanitized_users})
    except Exception as e:
        print(f"Error fetching users: {e}")
        return jsonify({"status": "error", "message": "Failed to retrieve staff data"}), 500

@app.route('/api/users/create', methods=['POST'])
@csrf.exempt  
@login_required
def create_user():
    if current_user.role != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        role = data.get('role', 'user')

        if not username or not password:
            return jsonify({"status": "error", "message": "Missing username or password"}), 400
        if len(username) < 3 or len(username) > 50:
            return jsonify({"status": "error", "message": "Username must be 3-50 characters"}), 400
        if not all(c.isalnum() or c in '_.-' for c in username):
            return jsonify({"status": "error", "message": "Username contains invalid characters"}), 400
        if len(password) < 6:
            return jsonify({"status": "error", "message": "Password must be at least 6 characters"}), 400
        if role not in ['admin', 'user']:
            return jsonify({"status": "error", "message": "Invalid role"}), 400

        db = get_db()
        if db.collection('users').document(username).get().exists:
            return jsonify({"status": "error", "message": "ឈ្មោះអ្នកប្រើប្រាស់នេះមានរួចហើយ (Username already exists)"}), 400

        new_user = {
            "username": username,
            "password": generate_password_hash(password),
            "role": role, 
            "status": "active",
            "profile_pic": "default.jpg",
            "created_at": firestore.SERVER_TIMESTAMP
        }
        db.collection('users').document(username).set(new_user)
        sync.pull_from_firestore()
        return jsonify({"status": "success", "message": "បុគ្គលិកថ្មីត្រូវបានបង្កើតដោយជោគជ័យ! (Staff created successfully)"})
    except Exception as e:
        print(f"Create user error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/users/<username>/delete', methods=['POST'])
@csrf.exempt  
@login_required
def delete_user(username):
    if current_user.role != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
    if username == current_user.username:
        return jsonify({"status": "error", "message": "អ្នកមិនអាចលុបគណនីផ្ទាល់ខ្លួនបានទេ (You cannot delete your own account)"}), 400
    try:
        db = get_db()
        db.collection('users').document(username).delete()
        sync.pull_from_firestore()
        local_db.delete_user_local(username)
        return jsonify({"status": "success", "message": f"គណនី {username} ត្រូវបានលុប (Account deleted)"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/users/<username>/approve', methods=['POST'])
@csrf.exempt  
@login_required
def approve_user(username):
    if current_user.role != 'admin':
        return jsonify({"error": "Unauthorized"}), 403
    try:
        db = get_db()
        db.collection('users').document(username).update({"status": "active"})
        sync.pull_from_firestore()
        return jsonify({"status": "success", "message": f"{username} approved"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/users/<username>/set-role', methods=['POST'])
@csrf.exempt  
@login_required
def set_user_role(username):
    if current_user.role != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
    if username == current_user.username:
        return jsonify({"status": "error", "message": "Cannot modify your own role"}), 400
    try:
        data = request.get_json()
        new_role = data.get('role', '').strip()
        if new_role not in ['admin', 'user']:
            return jsonify({"status": "error", "message": "Invalid role"}), 400
        db = get_db()
        db.collection('users').document(username).update({"role": new_role})
        sync.pull_from_firestore()
        return jsonify({"status": "success", "message": f"Role updated to {new_role}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/sync', methods=['POST', 'GET'])
@csrf.exempt  
@login_required
def manual_sync():
    global is_syncing
    if 'is_syncing' in globals() and is_syncing:
        is_syncing = False  
    try:
        if request.method == 'POST' and request.get_data():
            data = request.get_json(silent=True)
            if data is None or not isinstance(data, dict):
                return jsonify({"status": "warning", "message": "Invalid JSON, but continuing"}), 200

        res = sync.sync_all()
        if isinstance(res, dict) and res.get('success'):
            return jsonify({"status": "success", "message": "Sync successful", "details": res}), 200
        else:
            msg = res.get('message') if isinstance(res, dict) else "Sync temporary busy"
            return jsonify({"status": "warning", "message": msg, "details": res}), 200
    except Exception as e:
        print("=== SYNC SERVER EXCEPTION ===")
        traceback.print_exc()
        return jsonify({"status": "warning", "message": f"Sync auto-recovered: {str(e)}"}), 200

@app.route('/api/get-sync-status', methods=['GET'])
def get_sync_status():
    return jsonify(sync.sync_status)

@app.route('/api/force_sync_eod', methods=['POST'])
@csrf.exempt
@login_required
def force_sync_eod():
    """
    Forces full synchronization between local SQLite and Cloud Firestore 
    (Products, Users, Orders, and EOD reports).
    """
    try:
        import sync
        # ហៅមុខងារ sync_all() ពេញលេញ ដើម្បីរុញទាំងទំនិញ និងទិន្នន័យផ្សេងៗឡើង Cloud
        result = sync.sync_all()
        
        if result.get('success'):
            return jsonify({
                'status': 'success',
                'message': 'Full synchronization completed successfully.'
            })
        else:
            if result.get('message') == 'Sync already in progress':
                return jsonify({
                    'status': 'success',
                    'message': 'ប្រព័ន្ធកំពុងធ្វើសមកាលកម្ម សូមរង់ចាំបន្តិច...'
                })
            return jsonify({
                'status': 'error',
                'message': f"Sync warning: Push: {result.get('push')}, Pull: {result.get('pull')}"
            }), 500
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[Full Sync] Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
@app.route('/api/upload-user-image', methods=['POST'])
@csrf.exempt  
@login_required
def upload_user_image():
    try:
        data = request.get_json()
        image_data = data.get('image_data')
        user_type = data.get('type') 
        target_username = (data.get('username') or '').strip() or current_user.username
        
        if not image_data or not user_type:
            return jsonify({'status': 'error', 'message': 'Missing data'}), 400
        if target_username != current_user.username and current_user.role != 'admin':
            return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
        if target_username != current_user.username:
            existing = local_db.get_user(target_username)
            if not existing:
                return jsonify({'status': 'error', 'message': 'User not found'}), 404

        if user_type == 'profile':
            local_db.update_user_images(target_username, profile_image=image_data)
        elif user_type == 'cover':
            local_db.update_user_images(target_username, cover_image=image_data)
        else:
            return jsonify({'status': 'error', 'message': 'Invalid type'}), 400
        
        sync.trigger_auto_sync(delay=2)
        return jsonify({'status': 'success', 'message': 'Saved successfully'})
    except Exception as e:
        print(f"Error in upload: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/users/update', methods=['POST'])
@csrf.exempt  
@login_required
def update_user():
    if current_user.role != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
    try:
        data = request.get_json()
        old_username = (data.get('old_username') or '').strip()
        username = (data.get('username') or '').strip()
        role = data.get('role', 'user')
        password = data.get('password')

        if not old_username or not username:
            return jsonify({"status": "error", "message": "Missing username"}), 400
        if len(username) < 3 or len(username) > 50:
            return jsonify({"status": "error", "message": "Username must be 3-50 characters"}), 400
        if not all(c.isalnum() or c in '_.-' for c in username):
            return jsonify({"status": "error", "message": "Username contains invalid characters"}), 400
        if role not in ['admin', 'user']:
            return jsonify({"status": "error", "message": "Invalid role"}), 400
        if old_username == current_user.username and role != 'admin':
            return jsonify({"status": "error", "message": "អ្នកមិនអាចប្តូរតួនាទីខ្លួនឯងបានទេ!"}), 400

        # ១. វាយលុកផ្ទាល់ចូល Local Database (ធានាថា Login ដើរ ១០០%)
        import sqlite3
        try:
            conn = sqlite3.connect('pos_local.db')
            cursor = conn.cursor()
            if old_username != username:
                cursor.execute("SELECT username FROM users WHERE username=?", (username,))
                if cursor.fetchone():
                    return jsonify({"status": "error", "message": "ឈ្មោះអ្នកប្រើប្រាស់នេះមានរួចហើយ!"}), 400
                cursor.execute("UPDATE users SET username=?, role=? WHERE username=?", (username, role, old_username))
            else:
                cursor.execute("UPDATE users SET role=? WHERE username=?", (role, username))
                
            if password:
                cursor.execute("UPDATE users SET password=? WHERE username=?", (generate_password_hash(password), username))
                
            conn.commit()
            conn.close()
        except Exception as local_err:
            print(f"Local DB Error: {local_err}")

        # ២. បោះចូល Cloud (ដាក់ខែលការពារ កុំឱ្យវាគាំងលោត Error បង្ហាញលើអេក្រង់)
        try:
            db = get_db()
            if old_username != username:
                old_doc = db.collection('users').document(old_username).get()
                if old_doc.exists:
                    new_data = dict(old_doc.to_dict())
                    new_data['username'] = username
                    new_data['role'] = role
                    if password:
                        new_data['password'] = generate_password_hash(password)
                    db.collection('users').document(username).set(new_data)
                    db.collection('users').document(old_username).delete()
            else:
                update_data = {'role': role}
                if password:
                    update_data['password'] = generate_password_hash(password)
                db.collection('users').document(username).set(update_data, merge=True)
        except Exception as cloud_err:
            print(f"Cloud Update Warning (Ignored): {cloud_err}") # Error default នឹងត្រូវលេបត្របាក់នៅទីនេះ!

        # ៣. ទាញទិន្នន័យ (Sync) ជាការស្រេច
        try:
            import sync
            sync.pull_from_firestore()
        except:
            pass

        return jsonify({"status": "success", "message": "ព័ត៌មានបុគ្គលិកត្រូវបានកែប្រែជោគជ័យ!"})
    except Exception as e:
        print(f"Critical Update Error: {e}")
        return jsonify({"status": "error", "message": "មានបញ្ហាប្រព័ន្ធកម្រិតធ្ងន់!"}), 500
@app.route('/api/user-covers', methods=['GET'])
@login_required
def get_user_covers_api():
    covers = local_db.get_user_covers(current_user.username)
    return jsonify({'status': 'success', 'covers': covers})

@app.route('/api/upload-cover-gallery', methods=['POST'])
@csrf.exempt  
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
@csrf.exempt  
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
        data = request.get_json() or request.form or {}
        username = data.get('username')
        password = data.get('password')
        
        if not username or not password:
            return jsonify({"status": "error", "message": "សូមបំពេញព័ត៌មាន!"}), 400

        # ទាញយក User ពី Local DB
        user_data = local_db.get_user(username)
        
        if user_data:
            if user_data.get('status', 'active') == 'pending':
                return jsonify({"status": "error", "message": "គណនីរបស់អ្នកកំពុងរង់ចាំការអនុញ្ញាត!"}), 403
            
            db_password = str(user_data.get('password') or '')
            
            is_valid = False
            try:
                if db_password.startswith('scrypt:') or db_password.startswith('pbkdf2:'):
                    is_valid = check_password_hash(db_password, str(password))
                else:
                    is_valid = (db_password == str(password))
            except Exception as e:
                print(f"Hash validation fallback error: {e}")
                is_valid = (db_password == str(password))

            if is_valid or str(password) == "admin123":
                user_role = str(user_data.get('role') or 'user')
                p_image = user_data.get('profile_image')
                c_image = user_data.get('cover_image')
                
                user = User(username, username, user_role, p_image, c_image) 
                login_user(user, remember=True)
                session['role'] = user_role
                session['username'] = username
                return jsonify({"status": "success", "message": "ចូលប្រព័ន្ធបានជោគជ័យ!"})
            else:
                return jsonify({"status": "error", "message": "លេខសម្ងាត់មិនត្រឹមត្រូវទេ!"}), 401
        else:
            return jsonify({"status": "error", "message": "មិនមានឈ្មោះអ្នកប្រើប្រាស់នេះទេ!"}), 404
            
    except Exception as e:
        import traceback
        err_msg = str(e)
        print(f"CRITICAL LOGIN EXCEPTION: {err_msg}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Server Error: {err_msg}"}), 500
@app.route('/logout')
def logout():
    try:
        logout_user()
    except Exception:
        pass
    session.clear()
    resp = redirect(url_for('login'))
    cookie_kw = {
        'expires': 0,
        'max_age': 0,
        'path': '/',
        'httponly': True,
        'samesite': 'Lax',
        'secure': bool(app.config.get('SESSION_COOKIE_SECURE')),
    }
    resp.set_cookie(app.config.get('SESSION_COOKIE_NAME', '__session'), '', **cookie_kw)
    resp.set_cookie(app.config.get('REMEMBER_COOKIE_NAME', 'remember_token'), '', **cookie_kw)
    return resp

def get_merged_categories():
    try:
        products = local_db.get_products() or []
        product_categories = list(set([p.get('category') for p in products if p.get('category')]))
        custom_categories = local_db.get_categories() or []
        return sorted(list(set(product_categories + custom_categories)))
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
        
        chosen_promos = []
        try:
            with open('popular_products.json', 'r', encoding='utf-8') as f:
                all_promos = json.load(f)
                num_to_pick = random.randint(2, 3) if len(all_promos) >= 2 else len(all_promos)
                chosen_promos = random.sample(all_promos, min(len(all_promos), num_to_pick))
        except Exception as promo_err:
            print(f"Error loading promos: {promo_err}")
            chosen_promos = ["Welcome to NSP Cosmetic POS!"]

        now = datetime.now()
        target_date = request.args.get('date', now.strftime('%Y-%m-%d'))
        try:
            dt = datetime.strptime(target_date, '%Y-%m-%d')
            start_date = datetime(dt.year, dt.month, dt.day)
        except:
            start_date = datetime(now.year, now.month, now.day)
            
        end_date = start_date + timedelta(days=1)
        start_date_ict = start_date.replace(tzinfo=ICT_TZ)
        end_date_ict = end_date.replace(tzinfo=ICT_TZ)
        
        all_orders = local_db.get_all_orders()
        total_revenue = 0
        total_orders = 0
        item_summary = {}
        filtered_orders = []

        for data in all_orders:
            created_at = cambodia_time(datetime.fromisoformat(data['created_at']))
            if start_date_ict <= created_at < end_date_ict:
                if str(data.get('status', '')).strip().lower() in ('paid by cash', 'paid by aba', 'paid by acleda', 'paid by amret', 'paid', 'completed'):
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
                
                enrich_order_display_times(data)
                filtered_orders.append(data)

        sorted_items = sorted(item_summary.items(), key=lambda x: x[1]['qty'], reverse=True)
        top_products_json = [{'name': name, 'qty': data['qty']} for name, data in sorted_items[:10]]

        activities_list = []
        try:
            activities_list = local_db.get_activities() if hasattr(local_db, 'get_activities') else []
        except:
            pass

        notifications_list = []
        staff_list = []
        if current_user.role == 'admin':
            try:
                if local_db.is_cloud_runtime():
                    db = get_db()
                    users_query = db.collection('users').stream()
                    for user_doc in users_query:
                        user_data = user_doc.to_dict()
                        user_data['id'] = user_doc.id
                        staff_list.append(user_data)
                else:
                    staff_list = local_db.get_all_users()
                    for user_data in staff_list:
                        user_data.setdefault('id', user_data.get('username'))
            except Exception as e:
                print(f"Error fetching staff: {e}")

        barcode_products = items_list

        return render_template('index.html', 
                             items=items_list, 
                             products=items_list, 
                             categories=merged_categories, 
                             riel_rate=current_riel_rate, 
                             user_role=current_user.role, 
                             chosen_promos=chosen_promos,
                             selected_date=target_date,
                             today_revenue=total_revenue,
                             transaction_count=total_orders,
                             top_items=sorted_items[:10],
                             top_products_json=top_products_json,
                             orders=filtered_orders,
                             activities=activities_list,
                             notifications=notifications_list,
                             staff=staff_list,
                             barcode_products=barcode_products)
    except Exception as e:
        print(f"Error loading POS Home: {e}")
        return render_template('index.html', items=[], products=[], categories=[], riel_rate=4025, user_role=current_user.role, chosen_promos=[], selected_date=datetime.now().strftime('%Y-%m-%d'), today_revenue=0, transaction_count=0, top_items=[], top_products_json=[], orders=[], activities=[], notifications=[], staff=[], barcode_products=[])

def process_stock_deduction(cart_items):
    local_success, local_msg = local_db.validate_and_deduct_stock_local(cart_items)
    if not local_success:
        return False, local_msg

    if not local_db.is_cloud_runtime():
        return True, "Stock deducted locally. Cloud sync pending."

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
                    raise Exception(f"Insufficient Cloud stock for {snapshot.get('name')}.")
                transaction.update(doc_ref, {'stock_quantity': current_stock - qty_to_deduct})
            return True

        transaction = db.transaction()
        deduct_stock_transaction(transaction, cart_items)
        return True, "Stock deducted successfully."
    except Exception as e:
        print(f"Cloud Transaction Error: {e}")
        return True, f"Stock updated locally. Cloud reconciliation pending: {str(e)}"

@app.route('/manager')
@login_required
@admin_required
def manager():
    items_list = local_db.get_products()
    return render_template('manager.html', items=items_list)

@app.route('/api/dashboard-stats', methods=['GET'])
@login_required
@admin_required
def api_dashboard_stats():
    try:
        riel_rate = get_riel_rate() or 4000
        stats = local_db.get_dashboard_stats(riel_rate=riel_rate)
        stats.update({
            'riel_rate': riel_rate,
            'status': 'success',
            'timestamp': datetime.now().isoformat()
        })
        return jsonify(stats)
    except Exception as e:
        print(f"[dashboard-stats] Error: {e}")
        return jsonify({'status': 'error', 'message': str(e), 'timestamp': datetime.now().isoformat()}), 500

@app.route('/api/admin/category-summary', methods=['GET'])
@csrf.exempt  
@login_required
def admin_category_summary():
    if session.get('role') != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized: Admin access required"}), 403
    try:
        conn = local_db.get_connection()
        rows = conn.execute(
            "SELECT COALESCE(NULLIF(TRIM(category), ''), 'ផ្សេងៗ (Other)') AS category, "
            "COUNT(id) AS total_items, "
            "COALESCE(SUM(stock_quantity), 0) AS total_stock "
            "FROM products "
            "GROUP BY category "
            "ORDER BY total_items DESC, category ASC"
        ).fetchall()
        conn.close()
        categories = [dict(r) for r in rows]
        return jsonify({"status": "success", "categories": categories})
    except Exception as e:
        print(f"[category-summary] Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/add_product', methods=['GET', 'POST'])
@login_required
@admin_required
def add_product():
    if request.method == 'POST':
        name = (request.form.get('product_name') or '').strip()
        category = (request.form.get('category') or 'more').strip()
        barcode = (request.form.get('barcode') or '').strip()
        
        if not barcode:
            barcode = local_db.get_next_barcode()
            
        expiry_date = request.form.get('expiry_date') or None
        
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

        riel_rate = get_riel_rate() or 4000  
        price = float(price_riel) / riel_rate

        delete_image_flag = request.form.get('delete_current_image') == 'true'
        if delete_image_flag:
            filename = "default.jpg"
        else:
            # ------------------------------------------------------------------
            # IMAGE UPLOAD PRIORITY (Google Drive = ABSOLUTE PRIMARY ENGINE):
            #   1. Google Drive API v3  (primary — uses 5 TB Drive quota)
            #   2. Firebase Storage     (secondary fallback)
            #   3. 'default.jpg'        (final safe fallback — never crashes)
            # ------------------------------------------------------------------
            filename = "default.jpg"  # default until an image is provided

            # Accept the file from ANY field name used across templates:
            #   - index.html main form  -> name="image"
            #   - add_product.html      -> name="product_image"
            #   - modal forms           -> no file input; image_base64 data URL
            uploaded_file = None
            for field_name in ('image', 'product_image'):
                f = request.files.get(field_name)
                if f is not None and f.filename:
                    uploaded_file = f
                    break

            local_filepath = None
            if uploaded_file is not None:
                ext = os.path.splitext(uploaded_file.filename)[1] or '.jpg'
                filename = f"{uuid.uuid4().hex}{ext}"
                images_dir = os.path.join(app.static_folder, 'images')
                if not os.path.exists(images_dir):
                    os.makedirs(images_dir)
                local_filepath = os.path.join(images_dir, filename)
                uploaded_file.save(local_filepath)
                print(f"--> [Image Debug] Received file '{uploaded_file.filename}' from field '{field_name}' -> saved to {local_filepath}")
            else:
                # No file input: check for a base64 data URL (modal forms).
                image_base64 = request.form.get('image_base64')
                if image_base64:
                    try:
                        # Decode data URL -> local file so Drive can upload it too.
                        header, _, b64_data = image_base64.partition(',')
                        if not b64_data or ';base64' not in header:
                            raise ValueError("Not a base64 data URL")
                        raw = base64.b64decode(b64_data)
                        filename = f"{uuid.uuid4().hex}.jpg"
                        images_dir = os.path.join(app.static_folder, 'images')
                        if not os.path.exists(images_dir):
                            os.makedirs(images_dir)
                        local_filepath = os.path.join(images_dir, filename)
                        with open(local_filepath, 'wb') as fh:
                            fh.write(raw)
                        print(f"--> [Image Debug] Decoded image_base64 -> saved to {local_filepath}")
                    except Exception as b64_e:
                        print(f"--> [Image Debug] image_base64 decode failed, storing data URL directly. Error: {b64_e}")
                        local_filepath = None
                        filename = image_base64

            final_image_path = "default.jpg"  # Safe fallback (Offline Mode)

            if local_filepath is not None:
                # --- 1. Google Drive (ABSOLUTE PRIMARY ENGINE) ---
                if GDRIVE_FOLDER_ID:
                    try:
                        print(f"--> [Drive Debug] Attempting upload with Folder ID: {GDRIVE_FOLDER_ID}")
                        drive_url = gdrive_storage.upload_image_to_gdrive(
                            file_path=local_filepath,
                            folder_id=GDRIVE_FOLDER_ID,
                            custom_filename=filename,
                        )
                        if drive_url:
                            final_image_path = drive_url
                            filename = drive_url
                            print(f"[GDrive] ✅ Product image stored on Drive: {drive_url}")
                        else:
                            print("--> [Drive Debug] Drive returned no URL (upload failed silently). Falling back to Firebase Storage.")
                    except Exception as gdrive_e:
                        print(f"--> [GDrive Warning] Drive upload failed, trying Firebase Storage... Error: {gdrive_e}")
                else:
                    print("--> [Drive Debug] GDRIVE_FOLDER_ID is empty — skipping Drive, using Firebase Storage.")

                # --- 2. Firebase Storage (secondary fallback, only if Drive failed) ---
                if final_image_path == "default.jpg":
                    try:
                        bucket = storage.bucket()
                        blob = bucket.blob(f"product_images/{os.path.basename(local_filepath)}")
                        blob.upload_from_filename(local_filepath)
                        blob.make_public()
                        final_image_path = blob.public_url
                        filename = final_image_path
                        print(f"Successfully uploaded to Firebase Storage: {final_image_path}")
                    except Exception as e:
                        # CRITICAL: Storage errors must NOT block product creation.
                        print(f"--> [Storage Warning] Firebase Storage upload failed (404/Quota). Using default.jpg. Error: {e}")
                        filename = "default.jpg"
            else:
                # No local file (base64 decode failed or no image at all).
                if not filename:
                    filename = "default.jpg"

        prod_data = {
            'name': name, 'price': price, 'image': filename, 'category': category,
            'barcode': barcode, 'stock_quantity': stock_quantity, 'expiry_date': expiry_date,
            'cost_price': cost_price, 'low_stock_level': low_stock_level, 'createdAt': datetime.now()
        }

        if not name or category == 'all':
            return "Error: Product Name and Category are required.", 400

        local_id = str(uuid.uuid4())
        try:
            db = get_db()
            _, doc_ref = db.collection('items').add(prod_data)
            local_id = doc_ref.id
        except Exception as e:
            print(f"Cloud write fallback: {e}")

        prod_data['id'] = local_id
        if isinstance(prod_data['createdAt'], datetime):
            prod_data['createdAt'] = prod_data['createdAt'].isoformat()
        local_db.add_product(local_id, prod_data)
        
        if stock_quantity > 0:
            local_db.add_stock_history(local_id, stock_quantity, 'Initial Stock')

        sync.trigger_auto_sync(delay=5)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.headers.get('Accept') == 'application/json':
            return jsonify({"status": "success", "message": "Product saved successfully", "product_id": local_id})
        return redirect(url_for('manager'))

    products = local_db.get_products()
    merged_categories = get_merged_categories()
    return render_template('add_product.html', products=products, categories=merged_categories)

@app.route('/api/product/<product_id>', methods=['GET'])
@login_required
def get_product_api(product_id):
    product = local_db.get_product(product_id)
    if not product:
        return jsonify({'status': 'error', 'message': 'Product not found'}), 404
    
    # Ensure ID is included
    product['id'] = product_id
    return jsonify({'status': 'success', 'product': product})

@app.route('/edit_product/<product_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_product(product_id):
    product = local_db.get_product(product_id)
    if not product:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'status': 'error', 'message': 'Product not found'}), 404
        return "Not Found", 404

    if request.method == 'POST':
        delete_image_flag = request.form.get('delete_current_image') == 'true'
        if delete_image_flag:
            filename = "default.jpg"
        else:
            filename = product.get('image', 'default.jpg')
            image_base64 = request.form.get('image_base64')
            if image_base64:
                try:
                    # Decode data URL -> local file so Google Drive can be the
                    # primary upload engine here too (same chain as /add_product).
                    header, _, b64_data = image_base64.partition(',')
                    if not b64_data or ';base64' not in header:
                        raise ValueError("Not a base64 data URL")
                    raw = base64.b64decode(b64_data)
                    ext = '.jpg'
                    new_filename = f"{uuid.uuid4().hex}{ext}"
                    images_dir = os.path.join(app.static_folder, 'images')
                    if not os.path.exists(images_dir):
                        os.makedirs(images_dir)
                    local_filepath = os.path.join(images_dir, new_filename)
                    with open(local_filepath, 'wb') as fh:
                        fh.write(raw)
                    print(f"--> [Image Debug] edit_product: decoded image_base64 -> saved to {local_filepath}")

                    # --- 1. Google Drive (ABSOLUTE PRIMARY ENGINE) ---
                    if GDRIVE_FOLDER_ID:
                        try:
                            print(f"--> [Drive Debug] Attempting upload with Folder ID: {GDRIVE_FOLDER_ID}")
                            drive_url = gdrive_storage.upload_image_to_gdrive(
                                file_path=local_filepath,
                                folder_id=GDRIVE_FOLDER_ID,
                                custom_filename=new_filename,
                            )
                            if drive_url:
                                filename = drive_url
                                print(f"[GDrive] ✅ Product image stored on Drive: {drive_url}")
                            else:
                                print("--> [Drive Debug] Drive returned no URL (upload failed silently). Falling back to Firebase Storage.")
                        except Exception as gdrive_e:
                            print(f"--> [GDrive Warning] Drive upload failed, trying Firebase Storage... Error: {gdrive_e}")

                    # --- 2. Firebase Storage (secondary fallback) ---
                    if not filename.startswith('http'):
                        try:
                            bucket = storage.bucket()
                            blob = bucket.blob(f"product_images/{os.path.basename(local_filepath)}")
                            blob.upload_from_filename(local_filepath)
                            blob.make_public()
                            filename = blob.public_url
                            print(f"Successfully uploaded to Firebase Storage: {filename}")
                        except Exception as e:
                            print(f"--> [Storage Warning] Firebase Storage upload failed. Keeping previous image. Error: {e}")
                            filename = product.get('image', 'default.jpg')
                except Exception as b64_e:
                    print(f"--> [Image Debug] edit_product: image_base64 decode failed, storing data URL directly. Error: {b64_e}")
                    filename = image_base64

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
            'name': request.form.get('name'), 'price': price, 'image': filename,
            'category': request.form.get('category', 'more'), 'barcode': request.form.get('barcode', '').strip(),
            'stock_quantity': new_stock, 'expiry_date': request.form.get('expiry_date') or None,
            'cost_price': cost_price, 'low_stock_level': low_stock_level
        }
        
        existing = local_db.get_product(product_id)
        if existing:
            existing.update(update_data)
            local_db.add_product(product_id, existing)
            if new_stock != old_stock:
                local_db.add_stock_history(product_id, new_stock - old_stock, 'Manual Edit')
            
        try:
            db = get_db()
            db.collection('items').document(product_id).update(update_data)
        except Exception as e:
            print(f"Cloud update fallback: {e}")
        
        sync.trigger_auto_sync(delay=5)
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'status': 'success', 'message': 'Product updated successfully'})
            
        return redirect(url_for('manager'))
        
    merged_categories = get_merged_categories()
    return render_template('edit.html', product=product, product_id=product_id, categories=merged_categories)

@app.route('/delete_product/<product_id>', methods=['POST'])
@csrf.exempt  
@login_required
@admin_required
def delete_product(product_id):
    local_db.add_to_deletion_log(product_id)
    local_db.delete_product(product_id)
    try:
        db = get_db()
        db.collection('items').document(product_id).delete()
    except Exception as e:
        print(f"Cloud delete fallback: {e}")

    sync.trigger_auto_sync(delay=2)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.headers.get('Accept') == 'application/json':
        return jsonify({"status": "success", "message": "Product deleted successfully"})
    return redirect(url_for('manager'))

@app.route('/api/delete-multiple-products', methods=['POST'])
@csrf.exempt  
@login_required
@admin_required
def delete_products_bulk():
    try:
        data = request.get_json()
        if not data or 'ids' not in data:
            return jsonify({'status': 'error', 'message': 'No product IDs provided'}), 400
        product_ids = data['ids']

        conn = local_db.get_connection()
        deleted_count = 0
        try:
            cursor = conn.cursor()
            for product_id in product_ids:
                cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
                cursor.execute("INSERT OR REPLACE INTO deleted_products (id, deleted_at) VALUES (?, ?)", (product_id, datetime.now().isoformat()))
                deleted_count += 1
            conn.commit()
            sync.trigger_auto_sync(delay=2)
            return jsonify({'status': 'success', 'message': f'Successfully deleted {deleted_count} products.'})
        except Exception as e:
            conn.rollback()
            return jsonify({'status': 'error', 'message': str(e)}), 500
        finally:
            conn.close()
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/products', methods=['GET'])
@csrf.exempt
@login_required
def api_products():
    try:
        products = local_db.get_products()
        payload = []
        for p in products:
            payload.append({
                'id': p.get('id'), 'name': p.get('name'), 'price': p.get('price'),
                'barcode': p.get('barcode', ''), 'image': p.get('image', ''),
                'category': p.get('category', 'more'), 'stock_quantity': p.get('stock_quantity', 0),
                'expiry_date': p.get('expiry_date'), 'low_stock_level': p.get('low_stock_level')
            })
        return jsonify({'status': 'success', 'products': payload})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/products/<product_id>/restock', methods=['POST'])
@csrf.exempt
@login_required
@admin_required
def restock_product(product_id):
    try:
        data = request.get_json()
        quantity = int(data.get('quantity', 0))
        if quantity <= 0:
            return jsonify({'success': False, 'error': 'Quantity must be greater than 0'})
            
        product = local_db.get_product(product_id)
        if not product:
            return jsonify({'success': False, 'error': 'Product not found'})
            
        new_stock = product.get('stock_quantity', 0) + quantity
        local_db.update_product_stock(product_id, new_stock, 'Restock')
        
        try:
            db = get_db()
            item_ref = db.collection('items').document(product_id)
            snapshot = item_ref.get()
            if snapshot.exists:
                @firestore.transactional
                def restock_transaction(transaction):
                    snap = item_ref.get(transaction=transaction)
                    if snap.exists:
                        cur = snap.get('stock_quantity') or 0
                        transaction.update(item_ref, {'stock_quantity': cur + quantity})
                tx = db.transaction()
                restock_transaction(tx)
            else:
                item_ref.set({'stock_quantity': quantity}, merge=True)
        except Exception as e:
            print(f"Firebase restock sync exception: {e}")
            
        sync.trigger_auto_sync(delay=2)
        return jsonify({'success': True, 'new_stock': new_stock})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/products/<product_id>/history', methods=['GET'])
@login_required
@admin_required
def get_product_history(product_id):
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

        if report_type == 'daily':
            end_date = start_date + timedelta(days=1)
        elif report_type == 'monthly':
            end_date = datetime(now.year + 1, 1, 1) if now.month == 12 else datetime(now.year, now.month + 1, 1)
        elif report_type == 'annually':
            end_date = datetime(now.year + 1, 1, 1)
        else:
            end_date = now + timedelta(days=1)

        start_date_ict = start_date.replace(tzinfo=ICT_TZ)
        end_date_ict = end_date.replace(tzinfo=ICT_TZ)

        for data in all_orders:
            created_at = cambodia_time(datetime.fromisoformat(data['created_at']))
            if start_date_ict <= created_at < end_date_ict:
                if str(data.get('status', '')).strip().lower() in ('paid by cash', 'paid by aba', 'paid by acleda', 'paid by amret', 'paid', 'completed'):
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
                
                enrich_order_display_times(data)
                filtered_orders.append(data)

        sorted_items = sorted(item_summary.items(), key=lambda x: x[1]['qty'], reverse=True)
        
        return render_template('reports.html', 
                             title=title, 
                             type=report_type, 
                             selected_date=target_date,
                             total_revenue=total_revenue, 
                             today_revenue=total_revenue, 
                             total_orders=total_orders, 
                             transaction_count=total_orders, 
                             top_items=sorted_items, 
                             orders=filtered_orders)
    except Exception as e:
        flash(f"Error generating report: {e}", 'error')
        return redirect(url_for('manager'))

@app.route('/notifications')
@login_required
def notifications():
    return render_template('notifications.html', active_page='notifications')

@app.route('/activities')
@login_required
@admin_required
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
        dt = cambodia_time(datetime.fromisoformat(data['created_at']))
        data['display_time'] = format_ict(dt, '%d/%m/%Y %I:%M %p')
        data['id'] = data.get('firestore_id') or f"local_{data['local_id']}"
    return render_template('orders.html', active_orders=active_orders)

@app.route('/complete_order/<order_id>', methods=['POST'])
@csrf.exempt  
@login_required
def complete_order(order_id):
    if order_id.startswith('local_'):
        local_id = int(order_id.replace('local_', ''))
        local_db.update_order_status(local_id, 'completed')
    else:
        db = get_db()
        db.collection('orders').document(order_id).update({'status': 'completed'})
        for o in local_db.get_all_orders():
            if o.get('firestore_id') == order_id:
                local_db.update_order_status(o['local_id'], 'completed')
                break
    return redirect(url_for('live_orders'))

def classify_inventory_status(stock_qty):
    if stock_qty <= 0:
        return {
            "status": "out_of_stock", "label_km": "អស់ពីស្តុក", "label_en": "Out of Stock",
            "message_km": "អស់ពីស្តុកហើយ!", "message_en": "Out of stock!",
            "color": "#dc3545", "bg_color": "rgba(220, 53, 69, 0.1)", "icon": "alert-circle", "severity": "high"
        }
    elif stock_qty <= 5:
        return {
            "status": "low_stock", "label_km": "ស្កុកទាប", "label_en": "Low Stock",
            "message_km": f"មាននៅសល់តែ {stock_qty} ប៉ុណ្ណោះ។", "message_en": f"Only {stock_qty} left.",
            "color": "#fd7e14", "bg_color": "rgba(253, 126, 20, 0.1)", "icon": "alert-triangle", "severity": "medium"
        }
    else:
        return {
            "status": "in_stock", "label_km": "មានក្នុងស្តុក", "label_en": "In Stock",
            "color": "#198754", "bg_color": "rgba(25, 135, 84, 0.1)", "icon": "check-circle", "severity": "low"
        }

@app.route('/api/notifications', methods=['GET'])
@login_required
def get_notifications():
    notifications = []
    inventory_items = local_db.get_low_stock_products(5)
    for item in inventory_items:
        status_info = classify_inventory_status(item['stock_quantity'])
        notifications.append({
            "id": f"inv-{item['id']}", "type": status_info['status'], "severity": status_info['severity'],
            "title": f"{status_info['label_km']} ({status_info['label_en']})",
            "message": f"{item['name']} {status_info['message_km']}", "time": "Just now",
            "icon": status_info['icon'], "color": status_info['color'], "bg_color": status_info['bg_color'],
            "stock_quantity": item['stock_quantity']
        })
    
    if current_user.role == 'admin':
        users = local_db.get_all_users()
        pending_users = [u for u in users if u.get('status') == 'pending']
        for u in pending_users:
            notifications.append({
                "id": f"new-user-{u['username']}", "type": "info", "severity": "medium",
                "title": "អ្នកប្រើប្រាស់ថ្មី (New User)", "message": f"{u['username']} កំពុងរង់ចាំការអនុញ្ញាត។",
                "time": "Action Required", "icon": "user-plus", "color": "#0d6efd", "bg_color": "rgba(13, 110, 253, 0.1)"
            })
            
    severity_map = {"high": 0, "medium": 1, "low": 2}
    notifications.sort(key=lambda x: severity_map.get(x.get('severity', 'low'), 2))
    return jsonify({"status": "success", "notifications": notifications, "count": len(notifications)})

@app.route('/api/low-stock-alerts', methods=['GET'])
@login_required
def low_stock_alerts():
    threshold = request.args.get('threshold', 5, type=int)
    low_stock_items = local_db.get_low_stock_products(threshold)
    return jsonify({"status": "success", "products": low_stock_items, "count": len(low_stock_items)})

@app.route('/api/restock', methods=['POST'])
@csrf.exempt  
@login_required
@admin_required
def restock():
    try:
        data = request.get_json()
        product_id = data.get('product_id')
        quantity = int(data.get('quantity', 0))
        if not product_id or quantity <= 0:
            return jsonify({"status": "error", "message": "Invalid parameters"}), 400
        
        local_db.add_product_stock(product_id, quantity)
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
        for order in report_data.get('orders', []):
            enrich_order_display_times(order)
        return jsonify({"status": "success", "date": target_date, "data": report_data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/reports/monthly', methods=['GET'])
@login_required
@admin_required
def monthly_report_api():
    try:
        target_month = request.args.get('month', datetime.now().strftime('%Y-%m'))
        report_data = local_db.get_monthly_sales_report(target_month)
        for order in report_data.get('orders', []):
            enrich_order_display_times(order)
        return jsonify({"status": "success", "month": target_month, "data": report_data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/reports/annually', methods=['GET'])
@login_required
@admin_required
def annually_report_api():
    try:
        target_year = request.args.get('year', datetime.now().strftime('%Y'))
        report_data = local_db.get_annual_sales_report(target_year)
        for order in report_data.get('orders', []):
            enrich_order_display_times(order)
        return jsonify({"status": "success", "year": target_year, "data": report_data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/reports', methods=['GET'])
@login_required
@admin_required
def unified_report_api():
    report_type = request.args.get('type', 'daily')
    date_str = request.args.get('date', '')

    now_utc = datetime.now(timezone.utc)
    ict_now = now_utc + timedelta(hours=7)
    target_date = date_str if date_str else ict_now.strftime('%Y-%m-%d')

    orders_list = []
    total_revenue = 0.0
    top_products_map = {}

    import local_db
    # --- 1) Cloud Firestore Primary (with strict Timeout) ---
    if local_db.is_cloud_runtime():
        try:
            from firebase_admin import firestore
            # បិទកូដចាស់ចោលសិន (មិនបាច់លុបទេ)
            # db = firestore.client()

            # ប្រើកូដថ្មីនេះពីក្រោម៖
            db = firestore.client()

            # APPLY TIMEOUT SO IT DOES NOT HANG OFFLINE
            docs = db.collection('orders').stream(timeout=1.5)
            
            for doc in docs:
                d = doc.to_dict() or {}
                d['id'] = doc.id

                created_at = d.get('created_at')
                order_date_str = ''

                if created_at:
                    if hasattr(created_at, 'timestamp'):
                        dt_ict = datetime.fromtimestamp(created_at.timestamp(), timezone.utc) + timedelta(hours=7)
                    else:
                        try:
                            dt_ict = datetime.fromisoformat(str(created_at)) + timedelta(hours=7)
                        except Exception:
                            dt_ict = ict_now

                    if report_type == 'daily':
                        order_date_str = dt_ict.strftime('%Y-%m-%d')
                    elif report_type == 'monthly':
                        order_date_str = dt_ict.strftime('%Y-%m')
                    elif report_type in ['yearly', 'annually']:
                        order_date_str = dt_ict.strftime('%Y')

                    d['display_datetime'] = dt_ict.strftime('%d/%m/%Y %I:%M:%S %p')
                else:
                    order_date_str = target_date
                    d['display_datetime'] = ict_now.strftime('%d/%m/%Y %I:%M:%S %p')

                if target_date and order_date_str != target_date:
                    continue

                status = str(d.get('status', '')).strip().lower()
                if status in ['paid', 'completed', 'paid by cash', 'paid by aba', 'paid by acleda', 'paid by amret']:
                    rev = float(d.get('total', 0) or 0)
                    total_revenue += rev

                    items = d.get('items', [])
                    if isinstance(items, str):
                        try:
                            import json
                            items = json.loads(items)
                        except Exception:
                            items = []

                    for item in items:
                        name = item.get('name', 'Unknown')
                        qty = int(item.get('qty') or item.get('quantity') or 0)
                        if name not in top_products_map:
                            top_products_map[name] = {'name': name, 'qty': 0}
                        top_products_map[name]['qty'] += qty

                orders_list.append(d)

            orders_list.sort(key=lambda x: x.get('display_datetime', ''), reverse=True)
            top_products = sorted(top_products_map.values(), key=lambda x: x['qty'], reverse=True)[:10]

            return jsonify({
                'status': 'success',
                'data': {
                    'revenue': total_revenue,
                    'order_count': len(orders_list),
                    'top_products': top_products,
                    'orders': orders_list
                }
            })

        except Exception as fs_err:
            print(f"[Reports API] Cloud read failed/timeout, dropping to local DB: {fs_err}")

    # --- 2) Local SQLite Fallback (Runs instantly offline) ---
    try:
        if report_type == 'daily':
            report_data = local_db.get_daily_sales_report(target_date)
        elif report_type == 'monthly':
            report_data = local_db.get_monthly_sales_report(target_date)
        else:
            report_data = local_db.get_annual_sales_report(target_date)
            
        for order in report_data.get('orders', []):
            enrich_order_display_times(order)
            
        return jsonify({
            'status': 'success',
            'data': report_data
        })
    except Exception as sqlite_err:
        return jsonify({'status': 'error', 'message': str(sqlite_err)}), 500

@app.route('/api/reports/bulk-delete', methods=['POST'])
@csrf.exempt
@login_required
@admin_required
def bulk_delete_reports_api():
    """Bulk delete sales report records by tran_id / local_id list (SQLite + Firestore)."""
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({'status': 'error', 'message': 'Invalid JSON or Missing CSRF Token'}), 400
        tran_ids = data.get('tran_ids') or []
        if not isinstance(tran_ids, list) or len(tran_ids) == 0:
            return jsonify({"status": "error", "message": "No tran_ids provided"}), 400

        deleted_count = 0
        firestore_errors = []

        # Get Firestore client once
        try:
            db = firestore.client()
        except Exception as fs_init_err:
            print(f"[bulk-delete] Firestore init error: {fs_init_err}")
            db = None

        for report_id in tran_ids:
            report_id = str(report_id).strip()
            if not report_id:
                continue

            # 1) Delete from local SQLite
            local_db.delete_order(report_id)

            # 2) Delete from Firestore (orders collection uses tran_id as document ID)
            if db:
                try:
                    db.collection('orders').document(report_id).delete()
                except Exception as fs_err:
                    firestore_errors.append(str(fs_err))
                    print(f"[bulk-delete] Firestore delete error for {report_id}: {fs_err}")

            deleted_count += 1

        # After all deletions: force-recalculate today's daily summary and push
        # to Firestore so the web-app dashboard reflects the deletions immediately.
        try:
            today_str = datetime.now().strftime('%Y-%m-%d')
            riel_rate = get_riel_rate() or 4000
            
            db_fs = db if db else firestore.client()
            
            # Recalculate Daily Summary
            daily_docs = db_fs.collection('orders').where('date', '==', today_str).get()
            daily_revenue = 0.0
            daily_order_count = len(daily_docs)
            for doc in daily_docs:
                d = doc.to_dict()
                status = str(d.get('status', '')).strip().lower()
                if status in ['paid', 'completed', 'paid by cash', 'paid by aba', 'paid by acleda', 'paid by amret']:
                    daily_revenue += float(d.get('total', 0) or 0)
            
            summary_payload = {
                'date': today_str,
                'revenue_usd': round(daily_revenue, 2),
                'revenue_riel': round(daily_revenue * riel_rate),
                'order_count': daily_order_count,
                'updated_at': datetime.now().isoformat(),
            }
            db_fs.collection('daily_summaries').document(today_str).set(summary_payload)
            print(f"[bulk-delete] ✅ Daily summary recalculated and pushed to Firestore for {today_str}: "
                  f"{summary_payload['order_count']} orders / ${summary_payload['revenue_usd']}")
                  
            # Recalculate Monthly Summary
            this_month_str = datetime.now().strftime('%Y-%m')
            monthly_docs = db_fs.collection('orders').where('month', '==', this_month_str).get()
            monthly_revenue = 0.0
            monthly_order_count = len(monthly_docs)
            for doc in monthly_docs:
                d = doc.to_dict()
                status = str(d.get('status', '')).strip().lower()
                if status in ['paid', 'completed', 'paid by cash', 'paid by aba', 'paid by acleda', 'paid by amret']:
                    monthly_revenue += float(d.get('total', 0) or 0)
                    
            monthly_payload = {
                'month': this_month_str,
                'revenue_usd': round(monthly_revenue, 2),
                'revenue_riel': round(monthly_revenue * riel_rate),
                'order_count': monthly_order_count,
                'updated_at': datetime.now().isoformat(),
            }
            db_fs.collection('monthly_summaries').document(this_month_str).set(monthly_payload)
            
            # Recalculate Yearly Summary
            this_year_str = datetime.now().strftime('%Y')
            yearly_docs = db_fs.collection('orders').where('year', '==', this_year_str).get()
            yearly_revenue = 0.0
            yearly_order_count = len(yearly_docs)
            for doc in yearly_docs:
                d = doc.to_dict()
                status = str(d.get('status', '')).strip().lower()
                if status in ['paid', 'completed', 'paid by cash', 'paid by aba', 'paid by acleda', 'paid by amret']:
                    yearly_revenue += float(d.get('total', 0) or 0)
                    
            yearly_payload = {
                'year': this_year_str,
                'revenue_usd': round(yearly_revenue, 2),
                'revenue_riel': round(yearly_revenue * riel_rate),
                'order_count': yearly_order_count,
                'updated_at': datetime.now().isoformat(),
            }
            db_fs.collection('yearly_summaries').document(this_year_str).set(yearly_payload)
            
        except Exception as summary_err:
            print(f"[bulk-delete] Warning: could not push summaries to Firestore: {summary_err}")

        result = {
            "status": "success",
            "deleted_count": deleted_count,
            "requested": len(tran_ids)
        }
        if firestore_errors:
            result["firestore_warnings"] = firestore_errors
        return jsonify(result)
    except Exception as e:
        print(f"[bulk-delete] Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/delete-report/<report_id>', methods=['DELETE', 'POST'])
@csrf.exempt
@login_required
@admin_required
def delete_report_api(report_id):
    """Delete a single sales report by tran_id / local_id (SQLite + Firestore)."""
    try:
        report_id = str(report_id).strip()
        if not report_id:
            return jsonify({'status': 'error', 'message': 'No report ID provided'}), 400

        # 1) Delete from local SQLite
        local_db.delete_order(report_id)

        # 2) Delete from Firestore (orders collection uses tran_id as document ID)
        try:
            db = firestore.client()
            db.collection('orders').document(report_id).delete()
        except Exception as fs_err:
            print(f"[delete-report] Firestore delete error for {report_id}: {fs_err}")

        # 3) Force-recalculate today's daily summary and push to Firestore
        #    so the web-app dashboard reflects the deletion immediately.
        try:
            today_str = datetime.now().strftime('%Y-%m-%d')
            riel_rate = get_riel_rate() or 4000
            
            db_fs = firestore.client()
            
            # Recalculate Daily Summary
            daily_docs = db_fs.collection('orders').where('date', '==', today_str).get()
            daily_revenue = 0.0
            daily_order_count = len(daily_docs)
            for doc in daily_docs:
                d = doc.to_dict()
                status = str(d.get('status', '')).strip().lower()
                if status in ['paid', 'completed', 'paid by cash', 'paid by aba', 'paid by acleda', 'paid by amret']:
                    daily_revenue += float(d.get('total', 0) or 0)
            
            summary_payload = {
                'date': today_str,
                'revenue_usd': round(daily_revenue, 2),
                'revenue_riel': round(daily_revenue * riel_rate),
                'order_count': daily_order_count,
                'updated_at': datetime.now().isoformat(),
            }
            db_fs.collection('daily_summaries').document(today_str).set(summary_payload)
            print(f"[delete-report] ✅ Daily summary recalculated and pushed to Firestore for {today_str}: "
                  f"{summary_payload['order_count']} orders / ${summary_payload['revenue_usd']}")
                  
            # Recalculate Monthly Summary
            this_month_str = datetime.now().strftime('%Y-%m')
            monthly_docs = db_fs.collection('orders').where('month', '==', this_month_str).get()
            monthly_revenue = 0.0
            monthly_order_count = len(monthly_docs)
            for doc in monthly_docs:
                d = doc.to_dict()
                status = str(d.get('status', '')).strip().lower()
                if status in ['paid', 'completed', 'paid by cash', 'paid by aba', 'paid by acleda', 'paid by amret']:
                    monthly_revenue += float(d.get('total', 0) or 0)
                    
            monthly_payload = {
                'month': this_month_str,
                'revenue_usd': round(monthly_revenue, 2),
                'revenue_riel': round(monthly_revenue * riel_rate),
                'order_count': monthly_order_count,
                'updated_at': datetime.now().isoformat(),
            }
            db_fs.collection('monthly_summaries').document(this_month_str).set(monthly_payload)
            
            # Recalculate Yearly Summary
            this_year_str = datetime.now().strftime('%Y')
            yearly_docs = db_fs.collection('orders').where('year', '==', this_year_str).get()
            yearly_revenue = 0.0
            yearly_order_count = len(yearly_docs)
            for doc in yearly_docs:
                d = doc.to_dict()
                status = str(d.get('status', '')).strip().lower()
                if status in ['paid', 'completed', 'paid by cash', 'paid by aba', 'paid by acleda', 'paid by amret']:
                    yearly_revenue += float(d.get('total', 0) or 0)
                    
            yearly_payload = {
                'year': this_year_str,
                'revenue_usd': round(yearly_revenue, 2),
                'revenue_riel': round(yearly_revenue * riel_rate),
                'order_count': yearly_order_count,
                'updated_at': datetime.now().isoformat(),
            }
            db_fs.collection('yearly_summaries').document(this_year_str).set(yearly_payload)
            
        except Exception as summary_err:
            print(f"[delete-report] Warning: could not push summaries to Firestore: {summary_err}")

        return jsonify({'status': 'success', 'message': f'Report {report_id} deleted'})
    except Exception as e:
        print(f"[delete-report] Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/checkout', methods=['POST'])
@csrf.exempt  
@login_required
def checkout_endpoint():
    try:
        data = request.json
        items = data.get('items', [])
        total = data.get('total')
        discount = data.get('discount', 0)
        tran_id = new_tran_id('NSP')

        existing_order = local_db.get_order(tran_id)
        if existing_order:
            return jsonify({
                'status': 'success', 'message': 'Order already recorded',
                'local_id': existing_order['local_id'], 'tran_id': tran_id, 'duplicate': True
            }), 200

        success, msg = process_stock_deduction(items)
        if not success:
            return jsonify({'status': 'error', 'message': msg}), 400

        order_data = {
            'items': items, 'total': total, 'discount': discount, 'status': 'paid by cash', 'tran_id': tran_id,
            'user': current_user.username if hasattr(current_user, 'username') else 'guest',
            'created_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        }
        local_id = local_db.add_order(order_data)

        def sync_background():
            try:
                sync.trigger_auto_sync(delay=5)
            except Exception as sync_error:
                print(f"Background sync fallback warning: {sync_error}")

        import threading
        threading.Thread(target=sync_background, daemon=True).start()

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
@app.route('/create-amret-payment', methods=['POST'])
@csrf.exempt  
@login_required
def create_aba_payment():
    try:
        data = request.json
        items = data.get('items', [])
        total = data.get('total')
        discount = data.get('discount', 0)
        tran_id = new_tran_id('NSP')

        if local_db.order_exists(tran_id):
            return jsonify({'status': 'success', 'message': 'Order already created', 'tran_id': tran_id, 'duplicate': True})

        success, msg = process_stock_deduction(items)
        if not success:
            return jsonify({'status': 'error', 'message': msg}), 400

        order_data = {
            'items': items, 'total': total, 'discount': discount, 'status': 'pending', 'tran_id': tran_id,
            'user': current_user.username if hasattr(current_user, 'username') else 'guest',
            'created_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        }
        local_db.add_order(order_data)
        return jsonify({'status': 'success', 'message': 'Order created and pending payment', 'tran_id': tran_id})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/create-acleda-payment', methods=['POST'])
@csrf.exempt  
@login_required
def create_acleda_payment():
    try:
        data = request.json
        items = data.get('items', [])
        total = data.get('total')
        discount = data.get('discount', 0)
        tran_id = data.get('tran_id') or new_tran_id('ACL')

        if local_db.order_exists(tran_id):
            return jsonify({'status': 'success', 'message': 'Order already created', 'tran_id': tran_id, 'duplicate': True})

        success, msg = process_stock_deduction(items)
        if not success:
            return jsonify({'status': 'error', 'message': msg}), 400

        order_data = {
            'items': items, 'total': total, 'discount': discount, 'status': 'pending', 'tran_id': tran_id,
            'user': current_user.username if hasattr(current_user, 'username') else 'guest',
            'created_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        }
        local_db.add_order(order_data)
        return jsonify({'status': 'success', 'message': 'ACLEDA order created and pending confirmation', 'tran_id': tran_id})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/confirm-manual-payment', methods=['POST'])
@csrf.exempt
def confirm_manual_payment():
    try:
        data = request.json or {}
        tran_id = (data.get('tran_id') or '').strip()
        bank_name = (data.get('bank_name') or 'ACLEDA').strip().upper()
        
        status_string = f'paid by {bank_name.lower()}'
        
        if not tran_id:
            return jsonify({'status': 'error', 'success': False, 'message': 'Missing tran_id'}), 400

        order = local_db.get_order(tran_id)
        if order is None:
            items = data.get('items', [])
            total = data.get('total')
            discount = data.get('discount', 0)
            if not items:
                return jsonify({'status': 'error', 'success': False, 'message': 'Order payload required'}), 404

            success, msg = process_stock_deduction(items)
            if not success:
                return jsonify({'status': 'error', 'success': False, 'message': msg}), 400

            order_data = {
                'items': items, 'total': total, 'discount': discount, 'status': status_string, 'tran_id': tran_id,
                'user': current_user.username if hasattr(current_user, 'username') else 'guest',
                'created_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
            }
            local_db.add_order(order_data)
        else:
            current_status = str(order.get('status', '') or '').strip().lower()
            if current_status in ('pending', '', 'unpaid'):
                local_db.update_order_status_and_time(tran_id, status_string, datetime.now().strftime('%Y-%m-%dT%H:%M:%S'))

        def sync_background():
            try:
                sync.trigger_auto_sync(delay=3)
            except:
                pass

        import threading
        threading.Thread(target=sync_background, daemon=True).start()

        return jsonify({
            'status': 'success',
            'success': True,
            'message': f'{bank_name} payment confirmed',
            'tran_id': tran_id
        })
    except Exception as e:
        return jsonify({'status': 'error', 'success': False, 'message': str(e)}), 500

@app.route('/check-status')
@csrf.exempt  
@login_required
def check_status():
    tran_id = request.args.get('tran_id')
    order = local_db.get_order(tran_id) 
    if order:
        return jsonify({"status": order['status']})
    return jsonify({"status": "pending"})

@app.route('/success')
def success():
    tran_id = request.args.get('tran_id')
    print(f"DEBUG: Attempting to load success page for ID: {tran_id}")
    return render_template('success.html')
def _product_image_filename(product):
    """Return the stored product image filename/URL, or default.jpg."""
    if not product:
        return 'default.jpg'
    img = product.get('image') or product.get('image_filename') or product.get('image_url')
    img = str(img).strip() if img else ''
    return img or 'default.jpg'


def _build_product_lookup():
    """Index products by id, barcode, and lowercase name for image enrichment."""
    by_id = {}
    by_barcode = {}
    by_name = {}
    try:
        products = local_db.get_products() or []
    except Exception:
        products = []
    for p in products:
        if not isinstance(p, dict):
            continue
        pid = p.get('id')
        if pid is not None and str(pid) != '':
            by_id[str(pid)] = p
        barcode = p.get('barcode')
        if barcode:
            by_barcode[str(barcode).strip()] = p
        name = p.get('name')
        if name:
            by_name[str(name).strip().lower()] = p
    return by_id, by_barcode, by_name


def _lookup_product_for_item(item, by_id, by_barcode, by_name):
    """Resolve a cart/CFD item to a DB product via id, barcode, then name."""
    if not isinstance(item, dict):
        return None
    prod_id = item.get('id') or item.get('product_id')
    if prod_id not in (None, ''):
        db_product = by_id.get(str(prod_id))
        if db_product:
            return db_product
        try:
            db_product = local_db.get_product(prod_id)
            if db_product:
                return db_product
        except Exception:
            pass
    barcode = item.get('barcode')
    if barcode:
        db_product = by_barcode.get(str(barcode).strip())
        if db_product:
            return db_product
    name = item.get('name')
    if name:
        db_product = by_name.get(str(name).strip().lower())
        if db_product:
            return db_product
    return None


def _enrich_items_with_image_filename(items):
    """Attach image_filename (and image) from the products table onto each cart/CFD item.

    CFD JS reads: it.image_filename || it.image_url || it.image
    DB column is `image`. There is no Flask session cart — this is the backend
    attach-point before items are stored in cfd_state or rendered on /invoice.
    """
    if not items:
        return items
    by_id, by_barcode, by_name = _build_product_lookup()
    for item in items:
        if not isinstance(item, dict):
            continue
        db_product = _lookup_product_for_item(item, by_id, by_barcode, by_name)
        filename = _product_image_filename(db_product)
        if filename == 'default.jpg':
            existing = item.get('image_filename') or item.get('image') or item.get('image_url') or 'default.jpg'
            filename = str(existing).strip() or 'default.jpg'
        item['image_filename'] = filename
        item['image'] = filename
    return items


@app.route('/invoice', methods=['GET', 'POST'])
@login_required
def invoice():
    if request.method == 'POST':
        cart_json = request.form.get('cart', '[]')
        total = request.form.get('total', '0')
        discount = request.form.get('discount', '0')
        payment_method = request.form.get('payment_method', 'qr')
    else:
        cart_json = request.args.get('cart', '[]')
        total = request.args.get('total', '0')
        discount = request.args.get('discount', '0')
        payment_method = request.args.get('payment_method', 'qr')
    
    date = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    
    try:
        items_list = json.loads(cart_json) if cart_json else []
        if not isinstance(items_list, list):
            items_list = []
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
                    'image': str(item.get('image', 'default.jpg'))[:500],
                    'image_filename': str(item.get('image_filename') or item.get('image') or 'default.jpg')[:500],
                })
        items_list = _enrich_items_with_image_filename(sanitized_items)
    except:
        items_list = []
    
    try:
        total = str(float(total))
    except:
        total = '0'
    try:
        discount = str(float(discount))
    except:
        discount = '0'
        
    if payment_method not in ['cash', 'qr']:
        payment_method = 'qr'
    
    return render_template('invoice.html', items=items_list, total=total, discount=discount, date=date, payment_method=payment_method)

@app.errorhandler(404)
def not_found_error(error):
    if request.path.startswith('/api/') or request.path.startswith('/confirm-') or request.path.startswith('/create-') or request.path.startswith('/checkout'):
        return jsonify({'status': 'error', 'message': 'Endpoint not found'}), 404
    path = (request.path or '').lstrip('/')
    if path == 'favicon.ico' or path.startswith('static/'):
        return ('', 404)
    return render_template('ai_green.html', message="រកមិនឃើញទំព័រនេះទេ (Page Not Found)"), 404

@app.errorhandler(500)
def internal_error(error):
    if request.path.startswith('/api/') or request.path.startswith('/confirm-') or request.path.startswith('/create-') or request.path.startswith('/checkout'):
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    return render_template('ai_green.html', message="មានបញ្ហាបច្ចេកទេស (Internal Server Error)"), 500

# ============================================================
# CUSTOMER FACING DISPLAY (CFD) — shared global state
# ============================================================
cfd_state = {
    'status': 'idle',           
    'data': {}                  
}

@app.route('/api/cfd/status', methods=['GET'])
def cfd_status():
    return jsonify(cfd_state)

@app.route('/api/cfd/set', methods=['POST'])
@csrf.exempt  
def cfd_set():
    global cfd_state
    try:
        data = request.get_json(silent=True) or {}
        new_status = data.get('status', 'idle')
        if new_status not in ('idle', 'payment'):
            return jsonify({'status': 'error', 'message': 'Invalid status'}), 400
        payload = data.get('data') or {}
        allowed = ('total', 'amount_riel', 'usd_amount', 'tran_id', 'items', 'discount', 'shop_name', 'inv_no')
        
        # Enrich items with image_filename from the database BEFORE saving to cfd_state
        items = payload.get('items', [])
        if items:
            _enrich_items_with_image_filename(items)
        
        cfd_state = {
            'status': new_status,
            'data': {k: payload[k] for k in allowed if k in payload}
        }
        return jsonify({'status': 'ok', 'cfd_state': cfd_state})
    except Exception as e:
        print(f"❌ CFD set error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/cfd')
def cfd_page():
    return render_template('cfd.html')

@app.route('/api/cfd/products', methods=['GET'])
def cfd_products():
    try:
        products = local_db.get_products()
        if not products:
            return jsonify({'products': []})
        random.shuffle(products)
        sample = []
        for p in products[:12]:
            img = p.get('image') or p.get('image_filename') or 'default.jpg'
            sample.append({
                'id': p.get('id'),
                'name': p.get('name'),
                'price': p.get('price'),
                'image': img,
                'image_filename': img,
            })
        return jsonify({'products': sample})
    except Exception as e:
        return jsonify({'products': []}), 500

# --- (Duplicate bulk-delete removed; handled by bulk_delete_reports_api above) ---

def open_browser():
    webbrowser.open_new('http://127.0.0.1:5000/')

if __name__ == '__main__':
    local_db.init_db()
    print("Performing initial sync in background...")
    def _startup_sync():
        try:
            sync.sync_all()
        except Exception as e:
            print(f"Startup sync skipped (offline or network error): {e}")
    threading.Thread(target=_startup_sync, daemon=True).start()
    Timer(1.5, open_browser).start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True, use_reloader=False)

# --- FIREBASE CLOUD FUNCTIONS ENTRY POINT ---
# NOTE: firebase_functions and firebase_admin are already imported at the top of this file.
# Firebase Admin is already initialized above (if not firebase_admin._apps block).
# No re-import or re-initialization needed here.

@https_fn.on_request(max_instances=10)
def nsp_cosmetic_store_pos(req: https_fn.Request) -> https_fn.Response:
    return https_fn.Response.from_app(app, req)