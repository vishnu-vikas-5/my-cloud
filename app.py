from flask import Flask, request, jsonify, session, send_from_directory, send_file, render_template
from flask_cors import CORS
import os, hashlib, secrets, sys, json, shutil, socket, subprocess, re, threading, sqlite3
from werkzeug.utils import secure_filename
import mimetypes
from pathlib import Path
import psycopg2
import psycopg2.extras
from psycopg2 import IntegrityError
from psycopg2.pool import ThreadedConnectionPool
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

load_dotenv()

# Force UTF-8 encoding for stdout and stderr to prevent UnicodeEncodeErrors on Windows consoles (e.g. cp1252 charmap errors)
try:
    if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

DB_POOL = None

IS_FROZEN = getattr(sys, 'frozen', False)

def get_app_dir():
    if sys.platform == 'win32':
        app_dir = Path(os.environ.get('APPDATA', '~')).expanduser() / 'MyCloud'
    elif sys.platform == 'darwin':
        app_dir = Path('~/Library/Application Support/MyCloud').expanduser()
    else:
        app_dir = Path('~/.config/mycloud').expanduser()
    app_dir.mkdir(parents=True, exist_ok=True)
    return str(app_dir)

APP_DIR = get_app_dir()
DB_PATH = os.path.join(APP_DIR, 'mycloud.db')
CONFIG_PATH = os.path.join(APP_DIR, 'config.json')

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                return json.load(f)
        except:
            pass
    return None

def save_config(config_data):
    try:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config_data, f, indent=4)
    except Exception as e:
        print(f"Error saving config: {e}")

if IS_FROZEN:
    template_folder = os.path.join(sys._MEIPASS, 'templates')
    static_folder = os.path.join(sys._MEIPASS, 'static')
    app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)
else:
    app = Flask(__name__)

app.secret_key = secrets.token_hex(32)
CORS(app, supports_credentials=True)

# ─────────────────────────────────────────
# DATABASE SETUP
# ─────────────────────────────────────────
class PostgresWrapper:
    def __init__(self, conn):
        self.conn = conn
    def execute(self, query, params=()):
        query = query.replace('?', '%s')
        cur = self.conn.cursor()
        cur.execute(query, params)
        return cur
    def cursor(self):
        return self
    def commit(self):
        self.conn.commit()
    def close(self):
        global DB_POOL
        if DB_POOL and self.conn:
            DB_POOL.putconn(self.conn)
            self.conn = None
    def rollback(self):
        self.conn.rollback()

class SQLiteWrapper:
    def __init__(self, conn):
        self.conn = conn
    def execute(self, query, params=()):
        query = query.replace('SERIAL PRIMARY KEY', 'INTEGER PRIMARY KEY AUTOINCREMENT')
        query = query.replace('ADD COLUMN IF NOT EXISTS', 'ADD COLUMN')
        cur = self.conn.cursor()
        cur.execute(query, params)
        return cur
    def cursor(self):
        return self
    def commit(self):
        self.conn.commit()
    def close(self):
        self.conn.close()
    def rollback(self):
        try:
            self.conn.rollback()
        except:
            pass

USE_SQLITE = False

def get_db():
    global DB_POOL, USE_SQLITE
    if USE_SQLITE:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return SQLiteWrapper(conn)

    try:
        if DB_POOL is None:
            db_url = os.getenv("DATABASE_URL", "postgresql://neondb_owner:npg_9TpadKYDM8qI@ep-steep-fog-ain8sh9l-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require")
            DB_POOL = ThreadedConnectionPool(
                minconn=1, maxconn=10,
                dsn=db_url,
                cursor_factory=psycopg2.extras.RealDictCursor
            )
        
        conn = None
        for _ in range(3):
            try:
                conn = DB_POOL.getconn()
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                return PostgresWrapper(conn)
            except psycopg2.OperationalError:
                if conn:
                    DB_POOL.putconn(conn, close=True)
        
        raise Exception("OperationalError: failed to connect to PG pool")
    except Exception as e:
        print(f"Warning: PG Database connection failed: {e}. Falling back to local SQLite database at {DB_PATH}")
        USE_SQLITE = True
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return SQLiteWrapper(conn)

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS admins (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS clouds (
        id SERIAL PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        storage_path TEXT NOT NULL,
        limit_gb INTEGER DEFAULT 100,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        admin_owner TEXT DEFAULT 'admin'
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        connected_cloud TEXT DEFAULT NULL,
        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS files (
        id SERIAL PRIMARY KEY,
        cloud_name TEXT NOT NULL,
        filename TEXT NOT NULL,
        original_name TEXT NOT NULL,
        size INTEGER NOT NULL,
        file_type TEXT NOT NULL,
        uploaded_by TEXT NOT NULL,
        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Migration: Add admin_owner to clouds if not exists
    try:
        c.execute("ALTER TABLE clouds ADD COLUMN IF NOT EXISTS admin_owner TEXT DEFAULT 'admin'")
    except:
        conn.rollback()

    conn.commit()
    conn.close()

import sys
import getpass

def list_usb_drives():
    drives = []
    system = sys.platform
    if system == 'win32':
        import string
        from ctypes import windll
        bitmask = windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drive_path = f"{letter}:\\"
                drive_type = windll.kernel32.GetDriveTypeW(drive_path)
                if drive_type in [2, 3] and letter != 'C':
                    try:
                        label_buf = bytearray(260)
                        windll.kernel32.GetVolumeInformationW(
                            drive_path, label_buf, len(label_buf), None, None, None, None, 0
                        )
                        label = label_buf.decode('utf-16').strip('\x00')
                        name = f"{label} ({drive_path})" if label else f"Drive ({drive_path})"
                    except:
                        name = f"Drive ({drive_path})"
                    drives.append({'path': drive_path, 'name': name})
            bitmask >>= 1
    elif system == 'darwin':
        if os.path.exists('/Volumes'):
            for item in os.listdir('/Volumes'):
                path = os.path.join('/Volumes', item)
                if os.path.isdir(path) and not os.path.islink(path) and not item.startswith('.'):
                    if item.lower() not in ['macintosh hd', 'preboot']:
                        drives.append({'path': path, 'name': f"{item} (USB/External)"})
    else:
        try:
            username = getpass.getuser()
        except:
            username = 'root'
        paths_to_check = [
            f'/media/{username}',
            '/media',
            f'/run/media/{username}'
        ]
        for base_path in paths_to_check:
            if os.path.exists(base_path):
                try:
                    for item in os.listdir(base_path):
                        path = os.path.join(base_path, item)
                        if os.path.isdir(path) and not item.startswith('.'):
                            drives.append({'path': path, 'name': f"{item} (Removable)"})
                except:
                    pass
    return drives

def is_cloud_online(storage_path):
    if os.path.exists(storage_path):
        return os.access(storage_path, os.W_OK)
    parent = os.path.dirname(storage_path)
    if os.path.exists(parent):
        return os.access(parent, os.W_OK)
    return False

def hash_pass(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_file_type(filename):
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext in ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp']:
        return 'image'
    if ext in ['mp4', 'mov', 'webm', 'mkv', 'avi', 'm4v', '3gp']:
        return 'video'
    if ext in ['pdf', 'doc', 'docx', 'txt', 'ppt', 'pptx', 'xls', 'xlsx', 'csv']:
        return 'doc'
    return 'other'

# ─────────────────────────────────────────
# SERVE FRONTEND
# ─────────────────────────────────────────
@app.route('/')
@app.route('/landingpage')
@app.route('/landing')
def landing_page():
    return render_template('landingpage.html')

@app.route('/dashboard')
def index():
    return render_template('index.html')

# ─────────────────────────────────────────
# ADMIN ROUTES
# ─────────────────────────────────────────
@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    data = request.json
    conn = get_db()
    admin = conn.execute(
        "SELECT * FROM admins WHERE username=? AND password=?",
        (data['username'], hash_pass(data['password']))
    ).fetchone()
    conn.close()
    if admin:
        session['admin'] = data['username']
        return jsonify({'success': True, 'username': data['username']})
    return jsonify({'success': False, 'message': 'Wrong credentials'}), 401

@app.route('/api/admin/register', methods=['POST'])
def admin_register():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'success': False, 'message': 'All fields required'}), 400
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO admins (username, password) VALUES (?, ?)",
            (username, hash_pass(password))
        )
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'message': 'Admin username already taken'}), 400
    conn.close()
    session['admin'] = username
    return jsonify({'success': True, 'username': username})

@app.route('/api/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('admin', None)
    return jsonify({'success': True})

@app.route('/api/admin/usb-drives', methods=['GET'])
def get_usb_drives():
    if 'admin' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        drives = list_usb_drives()
        return jsonify(drives)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/clouds', methods=['GET'])
def get_clouds():
    if 'admin' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    clouds = conn.execute("SELECT * FROM clouds WHERE admin_owner=?", (session['admin'],)).fetchall()
    result = []
    for c in clouds:
        files = conn.execute("SELECT COUNT(*) AS count, SUM(size) AS sum FROM files WHERE cloud_name=?", (c['name'],)).fetchone()
        online = is_cloud_online(c['storage_path'])
        result.append({
            'name': c['name'],
            'storage_path': c['storage_path'],
            'limit_gb': c['limit_gb'],
            'created_at': c['created_at'],
            'file_count': files['count'] or 0,
            'total_size': files['sum'] or 0,
            'is_online': online
        })
    conn.close()
    return jsonify(result)

@app.route('/api/admin/clouds', methods=['POST'])
def create_cloud():
    if 'admin' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json
    name = data.get('name', '').strip()
    password = data.get('password', '')
    path = data.get('storage_path', '').strip()
    limit = int(data.get('limit_gb', 100))

    if not name or not password or not path:
        return jsonify({'success': False, 'message': 'All fields required'}), 400

    if not os.path.exists(path):
        return jsonify({'success': False, 'message': f'Path does not exist: {path}. Please insert the USB drive first.'}), 400

    full_path = os.path.join(path, name.replace(' ', '_'))
    try:
        os.makedirs(full_path, exist_ok=True)
    except Exception as e:
        return jsonify({'success': False, 'message': f'Cannot create folder at path: {str(e)}'}), 400

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO clouds (name, password, storage_path, limit_gb, admin_owner) VALUES (?, ?, ?, ?, ?)",
            (name, hash_pass(password), full_path, limit, session['admin'])
        )
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'message': 'Cloud name already exists'}), 400
    conn.close()
    return jsonify({'success': True, 'message': f'Cloud "{name}" created at {full_path}'})

def get_cloud_storage_path(conn, cloud_name):
    cloud = conn.execute("SELECT storage_path FROM clouds WHERE name=?", (cloud_name,)).fetchone()
    return cloud['storage_path'] if cloud else None

def get_user_vault_path(conn, cloud_name, username):
    cloud_path = get_cloud_storage_path(conn, cloud_name)
    if not cloud_path or not is_cloud_online(cloud_path):
        return None
    vault_path = os.path.join(cloud_path, secure_filename(username or 'user'))
    try:
        os.makedirs(vault_path, exist_ok=True)
    except:
        return None
    return vault_path

@app.route('/api/admin/clouds/<name>', methods=['DELETE'])
def delete_cloud(name):
    if 'admin' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    conn.execute("DELETE FROM clouds WHERE name=?", (name,))
    conn.execute("DELETE FROM files WHERE cloud_name=?", (name,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/admin/users', methods=['GET'])
def get_users():
    if 'admin' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    users = conn.execute(
        "SELECT id, username, connected_cloud, joined_at FROM users WHERE connected_cloud IS NULL OR connected_cloud IN (SELECT name FROM clouds WHERE admin_owner=?)",
        (session['admin'],)
    ).fetchall()
    conn.close()
    return jsonify([dict(u) for u in users])

@app.route('/api/admin/users/<username>', methods=['DELETE'])
def delete_user(username):
    if 'admin' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    user = conn.execute("SELECT connected_cloud FROM users WHERE username=?", (username,)).fetchone()
    if user and user['connected_cloud']:
        cloud = conn.execute("SELECT admin_owner FROM clouds WHERE name=?", (user['connected_cloud'],)).fetchone()
        if cloud and cloud['admin_owner'] != session['admin']:
            conn.close()
            return jsonify({'error': 'Unauthorized to delete user of another admin'}), 403
    conn.execute("DELETE FROM users WHERE username=?", (username,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/admin/files', methods=['GET'])
def admin_get_files():
    if 'admin' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    cloud = request.args.get('cloud', '')
    conn = get_db()
    cloud_info = conn.execute("SELECT admin_owner FROM clouds WHERE name=?", (cloud,)).fetchone()
    if not cloud_info or cloud_info['admin_owner'] != session['admin']:
        conn.close()
        return jsonify({'error': 'Unauthorized'}), 401
        
    files = conn.execute(
        "SELECT * FROM files WHERE cloud_name=? ORDER BY uploaded_at DESC", (cloud,)
    ).fetchall()
    conn.close()
    return jsonify([dict(f) for f in files])

@app.route('/api/admin/files/<int:file_id>', methods=['DELETE'])
def admin_delete_file(file_id):
    if 'admin' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    f = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if f:
        cloud_info = conn.execute("SELECT admin_owner FROM clouds WHERE name=?", (f['cloud_name'],)).fetchone()
        if not cloud_info or cloud_info['admin_owner'] != session['admin']:
            conn.close()
            return jsonify({'error': 'Unauthorized'}), 401
        try:
            cloud_path = get_cloud_storage_path(conn, f['cloud_name'])
            if cloud_path:
                os.remove(os.path.join(cloud_path, f['filename']))
        except:
            pass
        conn.execute("DELETE FROM files WHERE id=?", (file_id,))
        conn.commit()
    conn.close()
    return jsonify({'success': True})

# ─────────────────────────────────────────
# USER ROUTES
# ─────────────────────────────────────────
@app.route('/api/user/register', methods=['POST'])
def user_register():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'success': False, 'message': 'All fields required'}), 400
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)",
            (username, hash_pass(password))
        )
        conn.commit()
    except IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'message': 'Username already taken'}), 400
    conn.close()
    session['user'] = username
    return jsonify({'success': True, 'username': username})

@app.route('/api/user/login', methods=['POST'])
def user_login():
    data = request.json
    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE username=? AND password=?",
        (data['username'], hash_pass(data['password']))
    ).fetchone()
    conn.close()
    if user:
        session['user'] = data['username']
        return jsonify({'success': True, 'username': data['username'], 'connected_cloud': user['connected_cloud']})
    return jsonify({'success': False, 'message': 'Wrong credentials'}), 401

@app.route('/api/user/logout', methods=['POST'])
def user_logout():
    session.pop('user', None)
    return jsonify({'success': True})

@app.route('/api/user/profile', methods=['GET'])
def user_profile():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    row = conn.execute("SELECT username, joined_at, connected_cloud FROM users WHERE username=?", (session['user'],)).fetchone()
    conn.close()
    if row:
        return jsonify({
            'ok': True,
            'username': row['username'],
            'joined_at': row['joined_at'],
            'connected_cloud': row['connected_cloud']
        })
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/user/change_password', methods=['POST'])
def user_change_password():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    new_pass = data.get('password')
    if not new_pass:
        return jsonify({'error': 'Password is required'})
    
    conn = get_db()
    conn.execute("UPDATE users SET password=? WHERE username=?", (hash_pass(new_pass), session['user']))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/clouds/search', methods=['GET'])
def search_clouds():
    q = request.args.get('q', '').strip().lower()
    if not q:
        return jsonify([])
    conn = get_db()
    clouds = conn.execute(
        "SELECT name, limit_gb, created_at, storage_path FROM clouds WHERE LOWER(name) LIKE ?",
        (f'%{q}%',)
    ).fetchall()
    result = []
    for c in clouds:
        files = conn.execute("SELECT COUNT(*) AS count, SUM(size) AS sum FROM files WHERE cloud_name=?", (c['name'],)).fetchone()
        online = is_cloud_online(c['storage_path'])
        result.append({
            'name': c['name'],
            'limit_gb': c['limit_gb'],
            'file_count': files['count'] or 0,
            'total_size': files['sum'] or 0,
            'is_online': online
        })
    conn.close()
    return jsonify(result)

@app.route('/api/user/connect', methods=['POST'])
def connect_cloud():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json
    conn = get_db()
    cloud = conn.execute(
        "SELECT * FROM clouds WHERE name=? AND password=?",
        (data['cloud_name'], hash_pass(data['password']))
    ).fetchone()
    if not cloud:
        conn.close()
        return jsonify({'success': False, 'message': 'Wrong cloud name or password'}), 401
    conn.execute(
        "UPDATE users SET connected_cloud=? WHERE username=?",
        (data['cloud_name'], session['user'])
    )
    get_user_vault_path(conn, data['cloud_name'], session['user'])
    conn.commit()
    conn.close()
    session['cloud'] = data['cloud_name']
    return jsonify({'success': True, 'cloud_name': data['cloud_name']})

@app.route('/api/user/disconnect', methods=['POST'])
def disconnect_cloud():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    conn.execute(
        "UPDATE users SET connected_cloud=NULL WHERE username=?",
        (session['user'],)
    )
    conn.commit()
    conn.close()
    session.pop('cloud', None)
    return jsonify({'success': True})

@app.route('/api/user/upload', methods=['POST'])
def upload_file():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username=?", (session['user'],)).fetchone()
    cloud_name = session.get('cloud') or (user['connected_cloud'] if user else None)
    if not cloud_name:
        conn.close()
        return jsonify({'success': False, 'message': 'Not connected to any cloud'}), 400
    cloud = conn.execute("SELECT * FROM clouds WHERE name=?", (cloud_name,)).fetchone()
    if not cloud:
        conn.close()
        return jsonify({'error': 'Cloud not found'}), 404

    # Check if the cloud is online (USB drive is plugged in)
    if not is_cloud_online(cloud['storage_path']):
        conn.close()
        return jsonify({'success': False, 'message': 'Cloud storage is offline (USB drive unplugged by admin)'}), 400

    # Check storage limit
    used = conn.execute("SELECT SUM(size) AS sum FROM files WHERE cloud_name=?", (cloud_name,)).fetchone()['sum'] or 0
    limit_bytes = cloud['limit_gb'] * 1024 * 1024 * 1024
    if used >= limit_bytes:
        conn.close()
        return jsonify({'success': False, 'message': 'Storage limit reached'}), 400

    files = request.files.getlist('files')
    uploaded = []
    vault_path = get_user_vault_path(conn, cloud_name, session['user'])
    if not vault_path:
        conn.close()
        return jsonify({'error': 'Cloud path not found'}), 404
    for f in files:
        original_name = f.filename
        safe_name = secrets.token_hex(8) + '_' + secure_filename(original_name or 'file')
        save_path = os.path.join(vault_path, safe_name)
        f.save(save_path)
        size = os.path.getsize(save_path)
        file_type = get_file_type(original_name)
        conn.execute(
            "INSERT INTO files (cloud_name, filename, original_name, size, file_type, uploaded_by) VALUES (?,?,?,?,?,?)",
            (cloud_name, safe_name, original_name, size, file_type, session['user'])
        )
        uploaded.append(original_name)

    conn.commit()
    conn.close()
    return jsonify({'success': True, 'uploaded': uploaded})

@app.route('/api/user/files', methods=['GET'])
def get_user_files():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username=?", (session['user'],)).fetchone()
    cloud_name = session.get('cloud') or (user['connected_cloud'] if user else None)
    if not cloud_name:
        conn.close()
        return jsonify([])
    files = conn.execute(
        "SELECT * FROM files WHERE cloud_name=? AND uploaded_by=? ORDER BY uploaded_at DESC",
        (cloud_name, session['user'])
    ).fetchall()
    conn.close()
    return jsonify([dict(f) for f in files])

@app.route('/api/user/storage-stats', methods=['GET'])
def user_storage_stats():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE username=?", (session['user'],)).fetchone()
    cloud_name = session.get('cloud') or (user['connected_cloud'] if user else None)
    if not cloud_name:
        conn.close()
        return jsonify({'error': 'Not connected to a cloud'}), 400
    cloud = conn.execute("SELECT * FROM clouds WHERE name=?", (cloud_name,)).fetchone()
    if not cloud:
        conn.close()
        return jsonify({'error': 'Cloud not found'}), 404

    online = is_cloud_online(cloud['storage_path'])
    cloud_used = conn.execute("SELECT SUM(size) AS sum FROM files WHERE cloud_name=?", (cloud_name,)).fetchone()['sum'] or 0
    user_used = conn.execute("SELECT SUM(size) AS sum FROM files WHERE cloud_name=? AND uploaded_by=?", (cloud_name, session['user'])).fetchone()['sum'] or 0
    cloud_file_count = conn.execute("SELECT COUNT(*) AS count FROM files WHERE cloud_name=?", (cloud_name,)).fetchone()['count'] or 0
    user_file_count = conn.execute("SELECT COUNT(*) AS count FROM files WHERE cloud_name=? AND uploaded_by=?", (cloud_name, session['user'])).fetchone()['count'] or 0
    conn.close()

    return jsonify({
        'cloud_name': cloud_name,
        'cloud_used': cloud_used,
        'cloud_limit': cloud['limit_gb'] * 1024 * 1024 * 1024,
        'cloud_file_count': cloud_file_count,
        'user_used': user_used,
        'user_file_count': user_file_count,
        'is_online': online
    })

@app.route('/api/user/files/<int:file_id>', methods=['DELETE'])
def delete_file(file_id):
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    f = conn.execute("SELECT * FROM files WHERE id=? AND uploaded_by=?", (file_id, session['user'])).fetchone()
    if f:
        cloud_path = get_cloud_storage_path(conn, f['cloud_name'])
        if not cloud_path or not is_cloud_online(cloud_path):
            conn.close()
            return jsonify({'error': 'Cloud storage is offline (USB drive unplugged by admin)'}), 400
        try:
            vault_path = os.path.join(cloud_path, secure_filename(session['user'] or 'user'))
            os.remove(os.path.join(vault_path, f['filename']))
        except:
            pass
        conn.execute("DELETE FROM files WHERE id=?", (file_id,))
        conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/user/files/<int:file_id>/download')
def download_file(file_id):
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    f = conn.execute("SELECT * FROM files WHERE id=? AND uploaded_by=?", (file_id, session['user'])).fetchone()
    if not f:
        conn.close()
        return jsonify({'error': 'File not found'}), 404
    cloud = conn.execute("SELECT storage_path FROM clouds WHERE name=?", (f['cloud_name'],)).fetchone()
    conn.close()
    if not cloud:
        return jsonify({'error': 'Cloud not found'}), 404
    if not is_cloud_online(cloud['storage_path']):
        return jsonify({'error': 'Cloud storage is offline (USB drive unplugged by admin)'}), 400
    return send_from_directory(
        os.path.join(cloud['storage_path'], secure_filename(session['user'] or 'user')),
        f['filename'],
        as_attachment=True,
        download_name=f['original_name']
    )

@app.route('/api/user/files/<int:file_id>/preview')
def preview_file(file_id):
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    f = conn.execute("SELECT * FROM files WHERE id=? AND uploaded_by=?", (file_id, session['user'])).fetchone()
    if not f:
        conn.close()
        return jsonify({'error': 'File not found'}), 404
    cloud = conn.execute("SELECT storage_path FROM clouds WHERE name=?", (f['cloud_name'],)).fetchone()
    conn.close()
    if not cloud:
        return jsonify({'error': 'Cloud not found'}), 404
    if not is_cloud_online(cloud['storage_path']):
        return jsonify({'error': 'Cloud storage is offline (USB drive unplugged by admin)'}), 400
    file_path = os.path.join(cloud['storage_path'], secure_filename(session['user'] or 'user'), f['filename'])
    if not os.path.exists(file_path):
        return jsonify({'error': 'File missing on disk'}), 404
    mime_type, _ = mimetypes.guess_type(f['original_name'])
    return send_file(
        file_path,
        mimetype=mime_type or 'application/octet-stream',
        as_attachment=False,
        download_name=f['original_name'],
        conditional=True,
        max_age=0
    )

@app.route('/api/user/files/<int:file_id>/preview-meta')
def preview_meta(file_id):
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    f = conn.execute("SELECT id, original_name, file_type, size, uploaded_at FROM files WHERE id=? AND uploaded_by=?", (file_id, session['user'])).fetchone()
    conn.close()
    if not f:
        return jsonify({'error': 'File not found'}), 404
    return jsonify(dict(f))

@app.route('/download')
def download_portal():
    return render_template('download.html')

@app.route('/setup')
def setup_wizard():
    return render_template('setup.html')


@app.route('/api/server/status')
def server_status():
    config = load_config() or {}
    return jsonify({
        'local_ip': f"http://{get_local_ip()}:5001",
        'tunnel_url': tunnel_url,
        'storage_path': config.get('storage_path', os.path.abspath('.'))
    })

@app.route('/api/tunnel/start', methods=['POST'])
def trigger_tunnel_start():
    import time
    global tunnel_url, tunnel_process
    cf_path = get_cloudflared_path()
    if not cf_path:
        return jsonify({
            'success': False,
            'message': 'cloudflared binary not found on this system.'
        }), 400
    
    if not tunnel_url:
        start_cloudflare_tunnel(5001)
        # Wait up to 8 seconds for the tunnel process to boot and capture URL
        for _ in range(16):
            if tunnel_url:
                break
            time.sleep(0.5)
            
    if tunnel_url:
        return jsonify({
            'success': True,
            'tunnel_url': tunnel_url
        })
    else:
        return jsonify({
            'success': False,
            'message': 'Tunnel process launched, but URL acquisition timed out. Please check status again shortly.'
        })

@app.route('/api/server/config')
def server_config():
    config = load_config() or {}
    return jsonify(config)

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def get_cloudflared_path():
    if getattr(sys, 'frozen', False):
        binary_name = 'cloudflared.exe' if sys.platform == 'win32' else 'cloudflared'
        bundled_path = os.path.join(sys._MEIPASS, binary_name)
        if os.path.exists(bundled_path):
            return bundled_path
            
    local_path = './cloudflared.exe' if sys.platform == 'win32' else './cloudflared'
    if os.path.exists(local_path):
        return os.path.abspath(local_path)
        
    import shutil
    system_path = shutil.which('cloudflared')
    if system_path:
        return system_path
        
    return None

tunnel_url = None
tunnel_process = None

def start_cloudflare_tunnel(port=5001):
    global tunnel_url, tunnel_process
    cf_path = get_cloudflared_path()
    if not cf_path:
        print("Warning: cloudflared binary not found. Cloudflare Tunnel is disabled.")
        return
        
    def run_tunnel():
        global tunnel_url, tunnel_process
        cmd = [cf_path, 'tunnel', '--url', f'http://localhost:{port}']
        print(f"Starting Cloudflare Tunnel: {' '.join(cmd)}")
        try:
            # Prevent console window on Windows
            cf_flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            tunnel_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=cf_flags
            )
            
            for line in iter(tunnel_process.stdout.readline, ''):
                print(f"[cloudflared] {line.strip()}")
                match = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', line)
                if match:
                    tunnel_url = match.group(0)
                    print(f"Cloudflare Tunnel URL: {tunnel_url}")
            tunnel_process.stdout.close()
            tunnel_process.wait()
        except Exception as e:
            print(f"Error running cloudflared: {e}")
            
    t = threading.Thread(target=run_tunnel)
    t.daemon = True
    t.start()

def free_port(port=5001):
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        pass
        
    print(f"Port {port} in use, attempting to release...")
    if sys.platform == 'win32':
        try:
            out = subprocess.check_output(f"netstat -ano | findstr LISTENING | findstr :{port}", shell=True, text=True)
            for line in out.strip().split('\n'):
                parts = line.strip().split()
                if parts and parts[1].endswith(f":{port}"):
                    pid = parts[-1]
                    subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
        except Exception as e:
            print(f"Failed to free port on Windows: {e}")
    else:
        try:
            out = subprocess.check_output(["lsof", "-t", "-i", f"tcp:{port}"], text=True)
            for pid in out.strip().split():
                if pid:
                    subprocess.run(["kill", "-9", pid], capture_output=True)
        except Exception as e:
            print(f"Failed to free port: {e}")

def create_windows_shortcut(exe_path):
    start_menu = Path(os.environ.get('APPDATA', '~')).expanduser() / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs'
    start_menu.mkdir(parents=True, exist_ok=True)
    shortcut_path = start_menu / 'MyCloud.lnk'
    
    ps_command = f"""
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut("{shortcut_path}")
    $Shortcut.TargetPath = "{exe_path}"
    $Shortcut.WorkingDirectory = "{exe_path.parent}"
    $Shortcut.Description = "MyCloud Private Cloud Server"
    $Shortcut.IconLocation = "{exe_path}"
    $Shortcut.Save()
    """
    subprocess.run(["powershell", "-Command", ps_command], capture_output=True, text=True, check=True)

def install_app_files():
    from pathlib import Path
    if not getattr(sys, 'frozen', False):
        return True, "Dev mode - no install needed"
        
    current_path = Path(sys.executable).resolve()
    
    if sys.platform == 'win32':
        install_dir = Path(os.environ.get('LOCALAPPDATA', '~')).expanduser() / 'Programs' / 'MyCloud'
        install_dir.mkdir(parents=True, exist_ok=True)
        dest_exe = install_dir / 'MyCloud.exe'
        
        try:
            shutil.copy2(current_path, dest_exe)
        except Exception as e:
            return False, f"Failed to copy executable: {str(e)}"
            
        try:
            create_windows_shortcut(dest_exe)
        except Exception as e:
            print(f"Warning: Failed to create Start Menu shortcut: {e}")
            
        return True, str(dest_exe)
        
    elif sys.platform == 'darwin':
        app_bundle = current_path.parents[2]
        if app_bundle.suffix != '.app':
            return False, f"Executable is not inside a .app bundle (path: {app_bundle})"
            
        dest_dir = Path('~/Applications').expanduser()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_app = dest_dir / 'MyCloud.app'
        
        if dest_app.exists():
            try:
                shutil.rmtree(dest_app)
            except Exception as e:
                return False, f"Failed to overwrite existing application: {str(e)}"
                
        try:
            shutil.copytree(app_bundle, dest_app)
        except Exception as e:
            return False, f"Failed to copy application bundle: {str(e)}"
            
        return True, str(dest_app)
        
    return True, "Unsupported OS for auto-install"

class SetupApi:
    def select_folder(self):
        try:
            import webview
            result = webview.active_window().create_file_dialog(webview.FOLDER_DIALOG)
            if result:
                return result[0]
        except Exception as e:
            print(f"Error selecting folder: {e}")
        return None
        
    def install_and_configure(self, storage_path, create_shortcut):
        try:
            config = {
                "storage_path": storage_path,
                "installed": True
            }
            save_config(config)
            
            success, msg_or_path = install_app_files()
            if not success:
                return {"success": False, "message": msg_or_path}
                
            # Launch the installed copy
            if sys.platform == 'win32':
                subprocess.Popen([msg_or_path])
            elif sys.platform == 'darwin':
                subprocess.Popen(["open", msg_or_path])
                
            # Shutdown current installer instance shortly
            def exit_app():
                import time
                time.sleep(1.0)
                os._exit(0)
            threading.Thread(target=exit_app).start()
            
            return {"success": True}
        except Exception as e:
            return {"success": False, "message": str(e)}

def wait_for_port(port=5001, timeout=5):
    import time
    import socket
    start = time.time()
    while time.time() - start < timeout:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port))
            s.close()
            return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            pass
        time.sleep(0.1)
    return False

if __name__ == '__main__':
    config = load_config()
    
    if not IS_FROZEN:
        # Development mode
        if not config:
            config = {"storage_path": os.path.abspath('.'), "installed": True}
            save_config(config)
            
        free_port(5001)
        init_db()
        
        if '--server' in sys.argv:
            # Server-only mode
            print("\nMyCloud server started!")
            print("Open http://127.0.0.1:5001 in your browser\n")
            app.run(debug=True, host='0.0.0.0', port=5001)
        else:
            # GUI window desktop mode
            import threading
            import webview
            import logging
            
            log = logging.getLogger('werkzeug')
            log.setLevel(logging.ERROR)
            
            t = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5001, debug=False, use_reloader=False))
            t.daemon = True
            t.start()
            
            print("\nMyCloud Desktop App (Dev) started!")
            print("Serving at http://127.0.0.1:5001 inside native frame\n")
            
            wait_for_port(5001)
            webview.create_window("MyCloud Desktop (Dev)", "http://127.0.0.1:5001", width=1200, height=800)
            webview.start()
    else:
        # Frozen (Packaged) Mode
        free_port(5001)
        init_db()
        
        if not config or not config.get('installed'):
            # First time install wizard setup
            import threading
            import webview
            import logging
            
            log = logging.getLogger('werkzeug')
            log.setLevel(logging.ERROR)
            
            # Start flask locally to serve setup page
            t = threading.Thread(target=lambda: app.run(host='127.0.0.1', port=5001, debug=False, use_reloader=False))
            t.daemon = True
            t.start()
            
            wait_for_port(5001)
            api_instance = SetupApi()
            webview.create_window("MyCloud Setup Wizard", "http://127.0.0.1:5001/setup", width=700, height=500, resizable=False, js_api=api_instance)
            webview.start()
        else:
            # Normal run mode (Installed app)
            import threading
            import webview
            import logging
            
            log = logging.getLogger('werkzeug')
            log.setLevel(logging.ERROR)
            
            # Start tunnel client
            start_cloudflare_tunnel(5001)
            
            # Start flask server bound to 0.0.0.0 (sharing server)
            t = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5001, debug=False, use_reloader=False))
            t.daemon = True
            t.start()
            
            wait_for_port(5001)
            webview.create_window("MyCloud Desktop", "http://127.0.0.1:5001", width=1200, height=800)
            webview.start()