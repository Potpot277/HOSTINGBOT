import os
import json
import subprocess
import shlex
import shutil
import psutil
import time
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from datetime import timedelta
from werkzeug.utils import secure_filename
from functools import wraps

app = Flask(__name__)
app.secret_key = "tessl_super_secret_key_2024"
app.permanent_session_lifetime = timedelta(days=30)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024 * 1024  # 100GB max upload

# File paths
USERS_FILE = "users.json"
SERVERS_FILE = "servers.json"
SETTINGS_FILE = "settings.json"
STORAGE_DIR = "user_storage"

# Resource limits (can be scaled up)
MAX_RAM_MB = 128 * 1024  # 128GB
MAX_CPU_CORES = 32
MAX_DISK_MB = 500 * 1024  # 500GB
MAX_GPU_UNITS = 8

# Ensure directories exist
os.makedirs(STORAGE_DIR, exist_ok=True)

# ==================== HELPER FUNCTIONS ====================

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        default = {"registration_enabled": True, "splitter_enabled": True}
        with open(SETTINGS_FILE, "w") as f:
            json.dump(default, f, indent=4)
        return default
    with open(SETTINGS_FILE, "r") as f:
        return json.load(f)

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4)

def load_users():
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, "w") as f:
            json.dump([], f)
    with open(USERS_FILE, "r") as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=4)

def load_servers():
    if not os.path.exists(SERVERS_FILE):
        with open(SERVERS_FILE, "w") as f:
            json.dump([], f)
    with open(SERVERS_FILE, "r") as f:
        return json.load(f)

def save_servers(servers):
    with open(SERVERS_FILE, "w") as f:
        json.dump(servers, f, indent=4)

def get_user_storage(username):
    """Get user's storage usage in bytes"""
    user_dir = os.path.join(STORAGE_DIR, username)
    if not os.path.exists(user_dir):
        return 0
    total = 0
    for dirpath, _, filenames in os.walk(user_dir):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total += os.path.getsize(fp)
    return total

def format_bytes(bytes_val):
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024**2:
        return f"{bytes_val / 1024:.2f} KB"
    elif bytes_val < 1024**3:
        return f"{bytes_val / 1024**2:.2f} MB"
    else:
        return f"{bytes_val / 1024**3:.2f} GB"

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            flash('Please login first', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            flash('Please login first', 'warning')
            return redirect(url_for('login'))
        if session.get('role') != 'admin' and session['username'] != 'Antrax':
            flash('Admin access required', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

def ensure_admin():
    users = load_users()
    if not any(u["username"] == "Antrax" for u in users):
        users.append({"username": "Antrax", "password": "Antrax27", "role": "admin", "storage_limit": 100 * 1024**3})
        save_users(users)

# ==================== ROUTES ====================

@app.route("/")
def home():
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        users = load_users()
        for u in users:
            if u["username"] == username and u["password"] == password:
                session["username"] = username
                session["role"] = u.get("role", "user")
                session.permanent = True
                flash(f"Welcome back, {username}!", "success")
                if username == "Antrax" or session.get("role") == "admin":
                    return redirect(url_for("admin_dashboard"))
                return redirect(url_for("user_dashboard"))
        flash("Invalid credentials!", "danger")
    
    settings = load_settings()
    return render_template("login.html", registration_enabled=settings.get("registration_enabled", True))

@app.route("/register", methods=["POST"])
def register():
    settings = load_settings()
    if not settings.get("registration_enabled", True):
        flash("Registration is disabled", "danger")
        return redirect(url_for("login"))
    
    username = request.form["username"].strip()
    password = request.form["password"]
    confirm = request.form["confirm_password"]
    
    users = load_users()
    if any(u["username"] == username for u in users):
        flash("Username already exists", "danger")
    elif password != confirm:
        flash("Passwords do not match", "warning")
    elif len(password) < 6:
        flash("Password must be at least 6 characters", "warning")
    else:
        users.append({
            "username": username,
            "password": password,
            "role": "user",
            "storage_limit": 10 * 1024**3  # 10GB free tier
        })
        save_users(users)
        
        # Create user storage directory
        os.makedirs(os.path.join(STORAGE_DIR, username), exist_ok=True)
        flash("Account created! You can now login.", "success")
    
    return redirect(url_for("login"))

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully", "info")
    return redirect(url_for("login"))

# ==================== USER DASHBOARD ====================

@app.route("/user/dashboard")
@login_required
def user_dashboard():
    if session['username'] == 'Antrax' or session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))
    
    username = session['username']
    storage_used = get_user_storage(username)
    storage_limit = 10 * 1024**3  # 10GB for free users
    
    return render_template("user_dashboard.html", 
                         username=username,
                         storage_used=format_bytes(storage_used),
                         storage_limit=format_bytes(storage_limit),
                         storage_percent=(storage_used / storage_limit) * 100)

# ==================== ADMIN DASHBOARD ====================

@app.route("/admin")
@admin_required
def admin_dashboard():
    users = load_users()
    servers = load_servers()
    
    # Calculate stats
    total_ram = sum(int(s.get("ram", 0)) for s in servers)
    total_cpu = sum(int(s.get("cpu", 0)) for s in servers)
    total_disk = sum(int(s.get("disk", 0)) for s in servers)
    
    # Get storage for each user
    for user in users:
        user['storage_used'] = format_bytes(get_user_storage(user['username']))
    
    stats = {
        'total_ram': format_bytes(total_ram * 1024**2),
        'max_ram': format_bytes(MAX_RAM_MB * 1024**2),
        'ram_percent': (total_ram / MAX_RAM_MB) * 100 if MAX_RAM_MB > 0 else 0,
        'total_cpu': total_cpu,
        'max_cpu': MAX_CPU_CORES,
        'cpu_percent': (total_cpu / MAX_CPU_CORES) * 100 if MAX_CPU_CORES > 0 else 0,
        'total_disk': format_bytes(total_disk * 1024**2),
        'max_disk': format_bytes(MAX_DISK_MB * 1024**2),
        'disk_percent': (total_disk / MAX_DISK_MB) * 100 if MAX_DISK_MB > 0 else 0,
        'active_users': len([u for u in users if u.get('role') != 'admin']),
        'total_users': len(users)
    }
    
    settings = load_settings()
    
    return render_template("admin_dashboard.html", 
                         users=users,
                         servers=servers,
                         stats=stats,
                         registration_enabled=settings.get("registration_enabled", True),
                         splitter_enabled=settings.get("splitter_enabled", True),
                         username=session['username'])

# ==================== TERMINAL API ====================

@app.route("/api/terminal/execute", methods=["POST"])
@admin_required
def execute_terminal():
    command = request.json.get("command", "").strip()
    
    if not command:
        return jsonify({"error": "No command provided"}), 400
    
    # Security: Block dangerous commands
    dangerous = ["rm -rf /", "mkfs", "dd if=/dev/zero", "sudo", "chmod 777", ":(){ :|:& };:"]
    for d in dangerous:
        if d in command:
            return jsonify({"error": f"Command blocked: {d}"}), 403
    
    try:
        # Use shell for built-in commands like cd, ls, etc.
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=os.path.expanduser("~")
        )
        
        output = result.stdout + result.stderr
        if not output:
            output = f"Command executed successfully (exit code: {result.returncode})"
        
        return jsonify({
            "output": output,
            "returncode": result.returncode
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Command timed out (30s limit)"}), 408
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==================== FILE MANAGEMENT API ====================

@app.route("/api/files/list")
@login_required
def list_files():
    path = request.args.get("path", "")
    username = session['username']
    base_dir = os.path.join(STORAGE_DIR, username)
    
    # Security: prevent directory traversal
    safe_path = os.path.normpath(os.path.join(base_dir, path))
    if not safe_path.startswith(base_dir):
        return jsonify({"error": "Access denied"}), 403
    
    if not os.path.exists(safe_path):
        return jsonify({"error": "Path not found"}), 404
    
    items = []
    try:
        for item in os.listdir(safe_path):
            item_path = os.path.join(safe_path, item)
            items.append({
                "name": item,
                "is_dir": os.path.isdir(item_path),
                "size": os.path.getsize(item_path) if os.path.isfile(item_path) else 0,
                "size_formatted": format_bytes(os.path.getsize(item_path)) if os.path.isfile(item_path) else "-",
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(item_path)))
            })
        return jsonify({"success": True, "items": items, "current_path": path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/files/upload", methods=["POST"])
@login_required
def upload_file():
    username = session['username']
    user_dir = os.path.join(STORAGE_DIR, username)
    os.makedirs(user_dir, exist_ok=True)
    
    current_storage = get_user_storage(username)
    storage_limit = 100 * 1024**3 if session.get('role') == 'admin' else 10 * 1024**3
    
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    
    # Check storage limit
    if current_storage + len(file.read()) > storage_limit:
        file.seek(0)
        return jsonify({"error": f"Storage limit exceeded! Max: {format_bytes(storage_limit)}"}), 413
    file.seek(0)
    
    filename = secure_filename(file.filename)
    path = request.form.get("path", "")
    target_dir = os.path.normpath(os.path.join(user_dir, path))
    
    if not target_dir.startswith(user_dir):
        return jsonify({"error": "Access denied"}), 403
    
    os.makedirs(target_dir, exist_ok=True)
    file.save(os.path.join(target_dir, filename))
    
    return jsonify({"success": True, "message": f"{filename} uploaded successfully"})

@app.route("/api/files/delete", methods=["POST"])
@login_required
def delete_file():
    username = session['username']
    path = request.json.get("path", "")
    item_name = request.json.get("name", "")
    
    user_dir = os.path.join(STORAGE_DIR, username)
    target = os.path.normpath(os.path.join(user_dir, path, item_name))
    
    if not target.startswith(user_dir):
        return jsonify({"error": "Access denied"}), 403
    
    try:
        if os.path.isfile(target):
            os.remove(target)
        elif os.path.isdir(target):
            shutil.rmtree(target)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/files/create_folder", methods=["POST"])
@login_required
def create_folder():
    username = session['username']
    path = request.json.get("path", "")
    folder_name = request.json.get("name", "")
    
    user_dir = os.path.join(STORAGE_DIR, username)
    target = os.path.normpath(os.path.join(user_dir, path, folder_name))
    
    if not target.startswith(user_dir):
        return jsonify({"error": "Access denied"}), 403
    
    try:
        os.makedirs(target, exist_ok=True)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==================== SYSTEM STATS API ====================

@app.route("/api/system/stats")
@admin_required
def system_stats():
    cpu_percent = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    return jsonify({
        "cpu": cpu_percent,
        "cpu_cores": psutil.cpu_count(),
        "ram_used": format_bytes(ram.used),
        "ram_total": format_bytes(ram.total),
        "ram_percent": ram.percent,
        "disk_used": format_bytes(disk.used),
        "disk_total": format_bytes(disk.total),
        "disk_percent": disk.percent
    })

# ==================== USER MANAGEMENT API ====================

@app.route("/api/admin/users", methods=["GET", "POST", "DELETE"])
@admin_required
def manage_users():
    users = load_users()
    
    if request.method == "GET":
        return jsonify(users)
    
    elif request.method == "POST":
        data = request.json
        username = data.get("username")
        password = data.get("password")
        role = data.get("role", "user")
        
        if any(u["username"] == username for u in users):
            return jsonify({"error": "Username exists"}), 400
        
        users.append({
            "username": username,
            "password": password,
            "role": role,
            "storage_limit": 100 * 1024**3 if role == "admin" else 10 * 1024**3
        })
        save_users(users)
        os.makedirs(os.path.join(STORAGE_DIR, username), exist_ok=True)
        return jsonify({"success": True})
    
    elif request.method == "DELETE":
        data = request.json
        username = data.get("username")
        
        if username == "Antrax":
            return jsonify({"error": "Cannot delete main admin"}), 403
        
        users = [u for u in users if u["username"] != username]
        save_users(users)
        
        # Delete user storage
        user_storage = os.path.join(STORAGE_DIR, username)
        if os.path.exists(user_storage):
            shutil.rmtree(user_storage)
        
        return jsonify({"success": True})

# ==================== ACCOUNT & SETTINGS ====================

@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    username = session["username"]
    
    if request.method == "POST":
        current = request.form.get("current_password")
        new = request.form.get("new_password")
        confirm = request.form.get("confirm_password")
        
        users = load_users()
        user = next((u for u in users if u["username"] == username), None)
        
        if not user or user["password"] != current:
            flash("Current password is incorrect", "danger")
        elif new != confirm:
            flash("New passwords do not match", "warning")
        elif len(new) < 6:
            flash("Password must be at least 6 characters", "warning")
        else:
            user["password"] = new
            save_users(users)
            flash("Password updated successfully!", "success")
        
        return redirect(url_for("account"))
    
    return render_template("account.html", username=username)

@app.route("/settings")
@login_required
def settings():
    username = session["username"]
    storage_used = get_user_storage(username)
    storage_limit = 100 * 1024**3 if session.get('role') == 'admin' else 10 * 1024**3
    
    return render_template("settings.html", 
                         username=username,
                         storage_used=format_bytes(storage_used),
                         storage_limit=format_bytes(storage_limit),
                         storage_percent=(storage_used / storage_limit) * 100)

# ==================== COMPATIBILITY ROUTES ====================

@app.route("/dashboard")
@login_required
def dashboard():
    if session['username'] == 'Antrax' or session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('user_dashboard'))

@app.route("/admin/settings", methods=["POST"])
@admin_required
def admin_settings():
    settings = load_settings()
    settings['registration_enabled'] = 'registration_enabled' in request.form
    settings['splitter_enabled'] = 'splitter_enabled' in request.form
    save_settings(settings)
    flash("Settings updated!", "success")
    return redirect(url_for('admin_dashboard'))

# ==================== MAIN ====================

if __name__ == "__main__":
    ensure_admin()
    app.run(host="0.0.0.0", port=5000, debug=True)
