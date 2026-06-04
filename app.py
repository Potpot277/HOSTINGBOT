import os
import json
import subprocess
import shlex
import shutil
import psutil
import time
import threading
import zipfile
import tarfile
import uuid
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory, Response
from datetime import timedelta, datetime
from werkzeug.utils import secure_filename
from functools import wraps
from pathlib import Path

app = Flask(__name__)
app.secret_key = "tessl_super_secret_key_2024"
app.permanent_session_lifetime = timedelta(days=30)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024 * 1024  # 100GB max
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'py', 'js', 'html', 'css', 'zip', 'tar', 'gz', 'json', 'xml', 'md', 'sh', 'bat', 'exe', 'dll', 'so', 'bin', 'dat', 'log', 'csv', 'xlsx', 'docx', 'pptx'}

# File paths
USERS_FILE = "users.json"
SERVERS_FILE = "servers.json"
SETTINGS_FILE = "settings.json"
STORAGE_DIR = "user_storage"
SERVERS_BASE_DIR = "servers"
UPLOAD_DIR = "uploads"
LOGS_DIR = "logs"
BACKUP_DIR = "backups"

# Resource limits
MAX_RAM_MB = 128 * 1024  # 128GB
MAX_CPU_PERCENT = 800     # 800%
MAX_DISK_MB = 500 * 1024  # 500GB
MAX_GPU_UNITS = 8         # 8 GPU units

# Server process tracking
server_processes = {}
server_monitoring = {}
console_logs = {}
server_stats_cache = {}
MONITOR_INTERVAL = 2

# Create directories
for directory in [STORAGE_DIR, SERVERS_BASE_DIR, UPLOAD_DIR, LOGS_DIR, BACKUP_DIR]:
    os.makedirs(directory, exist_ok=True)

# ==================== HELPER FUNCTIONS ====================

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        default = {"registration_enabled": True, "splitter_enabled": True, "backup_enabled": True, "auto_backup_interval": 24, "maintenance_mode": False}
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
    if bytes_val is None or bytes_val == 0:
        return "0 B"
    bytes_val = float(bytes_val)
    if bytes_val < 1024:
        return f"{bytes_val:.0f} B"
    elif bytes_val < 1024**2:
        return f"{bytes_val / 1024:.2f} KB"
    elif bytes_val < 1024**3:
        return f"{bytes_val / 1024**2:.2f} MB"
    elif bytes_val < 1024**4:
        return f"{bytes_val / 1024**3:.2f} GB"
    else:
        return f"{bytes_val / 1024**4:.2f} TB"

def format_ram(mb):
    mb = float(mb)
    if mb < 1024:
        return f"{mb:.0f} MB"
    else:
        return f"{mb / 1024:.2f} GB"

def format_uptime(seconds):
    if seconds is None:
        return "N/A"
    seconds = int(seconds)
    days = seconds // (24 * 3600)
    hours = (seconds % (24 * 3600)) // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            flash('Please login first', 'warning')
            return redirect(url_for('login'))
        settings = load_settings()
        if settings.get('maintenance_mode', False) and session.get('role') != 'admin' and session['username'] != 'Antrax':
            flash('System is under maintenance. Please try again later.', 'warning')
            return redirect(url_for('logout'))
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
            return redirect(url_for('user_dashboard'))
        return f(*args, **kwargs)
    return decorated

def ensure_admin():
    users = load_users()
    if not any(u["username"] == "Antrax" for u in users):
        users.append({"username": "Antrax", "password": "Antrax27", "role": "admin", "email": "admin@tessl.com", "created_at": datetime.now().isoformat()})
        save_users(users)

def server_folder(owner, server_name):
    path = os.path.join(SERVERS_BASE_DIR, owner, server_name)
    os.makedirs(path, exist_ok=True)
    return path

def get_folder_size(path):
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.exists(fp):
                total += os.path.getsize(fp)
    return total / (1024 * 1024)

def safe_net_io_counters():
    try:
        return psutil.net_io_counters()
    except (PermissionError, FileNotFoundError):
        class DummyNetIO:
            bytes_recv = 0
            bytes_sent = 0
            packets_recv = 0
            packets_sent = 0
            errin = 0
            errout = 0
            dropin = 0
            dropout = 0
        return DummyNetIO()

def safe_disk_io_counters():
    try:
        return psutil.disk_io_counters()
    except (PermissionError, FileNotFoundError):
        class DummyDiskIO:
            read_bytes = 0
            write_bytes = 0
            read_count = 0
            write_count = 0
            read_time = 0
            write_time = 0
        return DummyDiskIO()

def safe_getloadavg():
    try:
        return psutil.getloadavg()
    except (OSError, AttributeError):
        return (0.0, 0.0, 0.0)

# ==================== SERVER MANAGEMENT ====================

def monitor_server(owner, server_name, ram_limit, cpu_limit, disk_limit):
    key = f"{owner}_{server_name}"
    folder = server_folder(owner, server_name)
    time.sleep(MONITOR_INTERVAL)
    
    while key in server_processes:
        process = server_processes.get(key)
        if not process or not isinstance(process, subprocess.Popen) or process.poll() is not None:
            break
        
        try:
            ps_proc = psutil.Process(process.pid)
            ram_used = ps_proc.memory_info().rss // (1024 * 1024)
            
            if ram_limit > 0 and ram_used >= ram_limit:
                console_logs.get(key, []).append(f"[!] RAM limit reached ({ram_used}MB / {ram_limit}MB). Stopping server...")
                stop_server(owner, server_name)
                break
            
            disk_used = get_folder_size(folder)
            if disk_limit > 0 and disk_used >= disk_limit:
                console_logs.get(key, []).append(f"[!] Disk limit reached ({disk_used:.2f}MB / {disk_limit}MB). Stopping server...")
                stop_server(owner, server_name)
                break

            total_cpu_usage = ps_proc.cpu_percent(interval=1)
            for child in ps_proc.children(recursive=True):
                try:
                    total_cpu_usage += child.cpu_percent(interval=None)
                except psutil.NoSuchProcess:
                    continue

            server_processes[key]['cpu_usage'] = total_cpu_usage
            server_processes[key]['ram_used'] = ram_used
            server_processes[key]['disk_used'] = disk_used

            if cpu_limit > 0:
                if total_cpu_usage > cpu_limit:
                    if not server_processes[key].get('throttled', False):
                        try:
                            if os.name == 'nt':
                                ps_proc.nice(psutil.IDLE_PRIORITY_CLASS)
                            else:
                                try:
                                    ps_proc.nice(10 if ps_proc.nice() <= 0 else ps_proc.nice() + 5)
                                except:
                                    ps_proc.nice(10)
                            console_logs.get(key, []).append(f"[!] High CPU ({total_cpu_usage:.1f}%) - Throttling process...")
                            server_processes[key]['throttled'] = True
                        except Exception as e:
                            console_logs.get(key, []).append(f"[!] Could not throttle: {e}")
                elif server_processes[key].get('throttled', False) and total_cpu_usage < max(cpu_limit - 10, 0):
                    try:
                        ps_proc.nice(0)
                        console_logs.get(key, []).append(f"[✓] CPU back to normal ({total_cpu_usage:.1f}%). Restored priority.")
                        server_processes[key]['throttled'] = False
                    except:
                        pass

        except psutil.NoSuchProcess:
            break
        except Exception as e:
            console_logs.get(key, []).append(f"[!] Monitor error: {e}")
            break
        
        time.sleep(MONITOR_INTERVAL)

def run_server(owner, server_name):
    key = f"{owner}_{server_name}"
    folder = server_folder(owner, server_name)
    
    servers = load_servers()
    server = next((s for s in servers if s["owner"] == owner and s["name"] == server_name), None)
    if not server:
        return
    
    ram_limit = int(server.get("ram", 0))
    cpu_limit = int(server.get("cpu", 0))
    disk_limit = int(server.get("disk", 0))
    
    disk_usage = get_folder_size(folder)
    if disk_limit > 0 and disk_usage >= disk_limit:
        console_logs.get(key, []).append(f"[!] Disk limit exceeded ({disk_usage:.2f}MB / {disk_limit}MB). Cannot start.")
        return

    script_path = os.path.join(folder, "app.py")
    if not os.path.exists(script_path):
        console_logs.get(key, []).append(f"[!] Error: app.py not found at {script_path}")
        return

    command = ["python3", "-u", "app.py"]
    console_logs.setdefault(key, []).append(f"[*] Starting server with: {' '.join(command)}")
    console_logs[key].append(f"[*] Working directory: {folder}")

    try:
        process = subprocess.Popen(
            command,
            cwd=folder,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            errors='replace',
            start_new_session=True
        )

        server_processes[key] = {
            'process': process,
            'start_time': time.time(),
            'cpu_usage': 0.0,
            'ram_used': 0,
            'disk_used': disk_usage,
            'throttled': False,
            'net_io': safe_net_io_counters(),
            'disk_io': safe_disk_io_counters()
        }

        console_logs[key].append(f"[✓] Server started successfully (PID: {process.pid})")

        def read_output():
            try:
                while True:
                    line = process.stdout.readline()
                    if not line and process.poll() is not None:
                        break
                    if line:
                        cleaned_line = line.rstrip('\n\r')
                        if cleaned_line:
                            console_logs.get(key, []).append(cleaned_line)
                
                return_code = process.poll()
                if return_code == 0:
                    console_logs.get(key, []).append("[✓] Process finished successfully.")
                else:
                    console_logs.get(key, []).append(f"[!] Process exited with code {return_code}")
            except Exception as e:
                console_logs.get(key, []).append(f"[!] Error reading output: {e}")
            finally:
                if key in server_processes:
                    server_processes.pop(key, None)

        output_thread = threading.Thread(target=read_output, daemon=True)
        output_thread.start()

        monitor_thread = threading.Thread(target=monitor_server, args=(owner, server_name, ram_limit, cpu_limit, disk_limit), daemon=True)
        monitor_thread.start()

    except Exception as e:
        console_logs.get(key, []).append(f"[!] Failed to start server: {e}")
        if key in server_processes:
            server_processes.pop(key, None)

def stop_server(owner, server_name):
    key = f"{owner}_{server_name}"
    process_data = server_processes.get(key)
    
    if process_data and isinstance(process_data.get('process'), subprocess.Popen) and process_data['process'].poll() is None:
        process = process_data['process']
        try:
            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)

            for child in children:
                try:
                    if child.status() == psutil.STATUS_STOPPED:
                        child.resume()
                    child.terminate()
                except psutil.NoSuchProcess:
                    continue

            parent.terminate()
            gone, alive = psutil.wait_procs(children + [parent], timeout=5)

            for p in alive:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass

            console_logs.get(key, []).append("[✓] Server stopped.")
        except psutil.NoSuchProcess:
            console_logs.get(key, []).append("[✓] Server was already stopped.")
        finally:
            server_processes.pop(key, None)
            if key in server_monitoring:
                server_monitoring.pop(key, None)

def restart_server(owner, server_name):
    stop_server(owner, server_name)
    time.sleep(1)
    run_server(owner, server_name)

def get_server_status(owner, server_name):
    key = f"{owner}_{server_name}"
    process_data = server_processes.get(key)
    
    if process_data and isinstance(process_data.get('process'), subprocess.Popen) and process_data['process'].poll() is None:
        return 'running'
    return 'stopped'

def get_server_stats(owner, server_name):
    key = f"{owner}_{server_name}"
    servers = load_servers()
    server = next((s for s in servers if s["owner"] == owner and s["name"] == server_name), None)
    
    if not server:
        return {"error": "Server not found"}
    
    process_data = server_processes.get(key)
    is_running = process_data and isinstance(process_data.get('process'), subprocess.Popen) and process_data['process'].poll() is None
    
    ram_used = 0
    cpu_usage = 0.0
    uptime = "N/A"
    
    if is_running:
        try:
            ps_proc = psutil.Process(process_data['process'].pid)
            ram_used = ps_proc.memory_info().rss // (1024 * 1024)
            cpu_usage = process_data.get('cpu_usage', 0.0)
            uptime = format_uptime(time.time() - process_data.get('start_time', time.time()))
        except:
            pass
    
    disk_used = get_folder_size(server_folder(owner, server_name))
    
    return {
        "status": "Online" if is_running else "Offline",
        "ram_used": format_ram(ram_used),
        "ram_limit": format_ram(int(server.get("ram", 0))),
        "cpu": round(cpu_usage, 1),
        "cpu_limit": int(server.get("cpu", 0)),
        "disk_used": format_ram(disk_used),
        "disk_limit": format_ram(int(server.get("disk", 0))),
        "uptime": uptime,
        "ram_percent": (ram_used / int(server.get("ram", 1))) * 100 if int(server.get("ram", 0)) > 0 else 0,
        "disk_percent": (disk_used / int(server.get("disk", 1))) * 100 if int(server.get("disk", 0)) > 0 else 0
    }

# ==================== BACKUP FUNCTIONS ====================

def create_backup():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"backup_{timestamp}"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    os.makedirs(backup_path, exist_ok=True)
    
    # Backup JSON files
    for file in [USERS_FILE, SERVERS_FILE, SETTINGS_FILE]:
        if os.path.exists(file):
            shutil.copy(file, os.path.join(backup_path, file))
    
    # Backup user storage
    for user_dir in os.listdir(STORAGE_DIR):
        user_path = os.path.join(STORAGE_DIR, user_dir)
        if os.path.isdir(user_path):
            shutil.copytree(user_path, os.path.join(backup_path, "user_storage", user_dir))
    
    # Create zip archive
    zip_path = os.path.join(BACKUP_DIR, f"{backup_name}.zip")
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(backup_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, backup_path)
                zipf.write(file_path, arcname)
    
    shutil.rmtree(backup_path)
    return zip_path

def list_backups():
    backups = []
    for file in os.listdir(BACKUP_DIR):
        if file.endswith('.zip'):
            file_path = os.path.join(BACKUP_DIR, file)
            stat = os.stat(file_path)
            backups.append({
                "name": file,
                "size": format_bytes(stat.st_size),
                "created": datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
            })
    return sorted(backups, key=lambda x: x['created'], reverse=True)

# ==================== AUTH ROUTES ====================

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
    email = request.form.get("email", "")
    
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
            "email": email,
            "created_at": datetime.now().isoformat()
        })
        save_users(users)
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
    storage_limit = 10 * 1024**3
    
    all_servers = load_servers()
    user_servers = []
    for s in all_servers:
        if s["owner"] == username:
            stats = get_server_stats(s["owner"], s["name"])
            s.update(stats)
            user_servers.append(s)
    
    return render_template("user_dashboard.html", 
                         username=username,
                         storage_used=format_bytes(storage_used),
                         storage_limit=format_bytes(storage_limit),
                         storage_percent=(storage_used / storage_limit) * 100,
                         servers=user_servers)

# ==================== ADMIN DASHBOARD ====================

@app.route("/admin")
@admin_required
def admin_dashboard():
    users = load_users()
    servers = load_servers()
    
    # Update server status
    for server in servers:
        stats = get_server_stats(server["owner"], server["name"])
        server.update(stats)
    
    total_ram = sum(int(s.get("ram", 0)) for s in servers)
    total_cpu = sum(int(s.get("cpu", 0)) for s in servers)
    total_disk = sum(int(s.get("disk", 0)) for s in servers)
    
    for user in users:
        user['storage_used'] = format_bytes(get_user_storage(user['username']))
        user['server_count'] = len([s for s in servers if s["owner"] == user["username"]])
    
    running_servers = len([s for s in servers if s.get("status") == "Online"])
    
    stats = {
        'total_ram': format_ram(total_ram),
        'max_ram': format_ram(MAX_RAM_MB),
        'ram_percent': (total_ram / MAX_RAM_MB) * 100 if MAX_RAM_MB > 0 else 0,
        'total_cpu': total_cpu,
        'max_cpu': MAX_CPU_PERCENT,
        'cpu_percent': (total_cpu / MAX_CPU_PERCENT) * 100 if MAX_CPU_PERCENT > 0 else 0,
        'total_disk': format_ram(total_disk),
        'max_disk': format_ram(MAX_DISK_MB),
        'disk_percent': (total_disk / MAX_DISK_MB) * 100 if MAX_DISK_MB > 0 else 0,
        'active_users': len([u for u in users if u.get('role') != 'admin']),
        'total_users': len(users),
        'total_servers': len(servers),
        'running_servers': running_servers
    }
    
    settings = load_settings()
    backups = list_backups()
    
    return render_template("admin_dashboard.html", 
                         users=users,
                         servers=servers,
                         stats=stats,
                         backups=backups,
                         registration_enabled=settings.get("registration_enabled", True),
                         splitter_enabled=settings.get("splitter_enabled", True),
                         maintenance_mode=settings.get("maintenance_mode", False),
                         username=session['username'])

@app.route("/admin/server/<owner>/<server_name>/start", methods=["POST"])
@admin_required
def admin_start_server(owner, server_name):
    run_server(owner, server_name)
    flash(f"Server '{server_name}' is starting...", "success")
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/server/<owner>/<server_name>/stop", methods=["POST"])
@admin_required
def admin_stop_server(owner, server_name):
    stop_server(owner, server_name)
    flash(f"Server '{server_name}' stopped.", "success")
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/server/<owner>/<server_name>/restart", methods=["POST"])
@admin_required
def admin_restart_server(owner, server_name):
    restart_server(owner, server_name)
    flash(f"Server '{server_name}' is restarting...", "success")
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/backup/create", methods=["POST"])
@admin_required
def admin_create_backup():
    try:
        backup_path = create_backup()
        flash(f"Backup created successfully!", "success")
    except Exception as e:
        flash(f"Backup failed: {e}", "danger")
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/backup/download/<filename>")
@admin_required
def admin_download_backup(filename):
    return send_from_directory(BACKUP_DIR, filename, as_attachment=True)

@app.route("/admin/backup/delete/<filename>", methods=["POST"])
@admin_required
def admin_delete_backup(filename):
    file_path = os.path.join(BACKUP_DIR, filename)
    if os.path.exists(file_path):
        os.remove(file_path)
        flash(f"Backup '{filename}' deleted.", "success")
    return redirect(url_for('admin_dashboard'))

# ==================== ADMIN POST HANDLERS ====================

@app.route("/admin", methods=["POST"])
@admin_required
def admin_post():
    users = load_users()
    servers = load_servers()
    
    # Create User
    if "create_user" in request.form:
        new_user = request.form["new_username"].strip()
        new_pass = request.form["new_password"].strip()
        
        if new_user and new_pass and not any(u["username"] == new_user for u in users):
            users.append({"username": new_user, "password": new_pass, "role": "user", "created_at": datetime.now().isoformat()})
            save_users(users)
            os.makedirs(os.path.join(STORAGE_DIR, new_user), exist_ok=True)
            flash(f"User '{new_user}' created successfully!", "success")
        else:
            flash("User already exists or invalid input", "danger")
    
    # Delete User
    elif "delete_user_from_modal" in request.form:
        target = request.form.get("old_username")
        if target == 'Antrax':
            flash("Cannot delete main admin", "danger")
        else:
            users = [u for u in users if u["username"] != target]
            save_users(users)
            servers = [s for s in servers if s["owner"] != target]
            save_servers(servers)
            user_storage = os.path.join(STORAGE_DIR, target)
            if os.path.exists(user_storage):
                shutil.rmtree(user_storage)
            flash(f"User '{target}' deleted", "success")
    
    # Edit User
    elif "edit_user" in request.form:
        old_username = request.form["old_username"]
        if old_username == 'Antrax':
            flash("Cannot modify main admin", "danger")
        else:
            new_username = request.form["new_username"].strip()
            new_password = request.form.get("new_password", "").strip()
            
            user_to_edit = next((u for u in users if u["username"] == old_username), None)
            if user_to_edit:
                if new_username and new_username != old_username:
                    if any(u["username"] == new_username for u in users):
                        flash(f"Username '{new_username}' already taken", "danger")
                        return redirect(url_for('admin_dashboard'))
                    user_to_edit["username"] = new_username
                    for server in servers:
                        if server["owner"] == old_username:
                            server["owner"] = new_username
                    old_dir = os.path.join(STORAGE_DIR, old_username)
                    new_dir = os.path.join(STORAGE_DIR, new_username)
                    if os.path.exists(old_dir):
                        os.rename(old_dir, new_dir)
                
                if new_password:
                    user_to_edit["password"] = new_password
                
                save_users(users)
                save_servers(servers)
                flash("User updated successfully!", "success")
            else:
                flash("User not found", "danger")
    
    # Create Server
    elif "create_server" in request.form:
        name = request.form["server_name"].strip()
        owner = request.form["server_owner"].strip()
        
        try:
            ram = int(request.form["server_ram"])
            cpu = int(request.form["server_cpu"])
            disk = int(request.form["server_disk"])
        except (ValueError, TypeError):
            flash("Invalid resource values", "danger")
            return redirect(url_for('admin_dashboard'))
        
        if not any(u["username"] == owner for u in users):
            flash(f"User '{owner}' does not exist", "danger")
        elif any(s["name"] == name and s["owner"] == owner for s in servers):
            flash(f"Server '{name}' already exists", "danger")
        elif ram < 1 or cpu < 1 or disk < 1:
            flash("Resources must be at least 1", "danger")
        else:
            servers.append({
                "name": name,
                "owner": owner,
                "ram": str(ram),
                "cpu": str(cpu),
                "disk": str(disk),
                "created_at": datetime.now().isoformat()
            })
            save_servers(servers)
            server_dir = os.path.join(SERVERS_BASE_DIR, owner, name)
            os.makedirs(server_dir, exist_ok=True)
            flash(f"Server '{name}' created for '{owner}'", "success")
    
    # Delete Server
    elif "delete_server_from_modal" in request.form:
        old_name = request.form.get("old_server_name")
        old_owner = request.form.get("old_owner")
        
        # Stop server if running
        stop_server(old_owner, old_name)
        
        servers = [s for s in servers if not (s["name"] == old_name and s["owner"] == old_owner)]
        save_servers(servers)
        
        server_dir = os.path.join(SERVERS_BASE_DIR, old_owner, old_name)
        if os.path.exists(server_dir):
            shutil.rmtree(server_dir)
        
        flash(f"Server '{old_name}' deleted", "success")
    
    # Edit Server
    elif "edit_server" in request.form:
        old_name = request.form.get("old_server_name")
        old_owner = request.form.get("old_owner")
        
        new_name = request.form.get("server_name")
        new_owner = request.form.get("server_owner")
        
        try:
            new_ram = int(request.form.get("server_ram"))
            new_cpu = int(request.form.get("server_cpu"))
            new_disk = int(request.form.get("server_disk"))
        except (ValueError, TypeError):
            flash("Invalid resource values", "danger")
            return redirect(url_for('admin_dashboard'))
        
        server_to_edit = next((s for s in servers if s["name"] == old_name and s["owner"] == old_owner), None)
        
        if server_to_edit:
            # Stop server if running before changes
            was_running = get_server_status(old_owner, old_name) == 'running'
            if was_running:
                stop_server(old_owner, old_name)
            
            server_to_edit["name"] = new_name
            server_to_edit["owner"] = new_owner
            server_to_edit["ram"] = str(new_ram)
            server_to_edit["cpu"] = str(new_cpu)
            server_to_edit["disk"] = str(new_disk)
            save_servers(servers)
            
            if new_name != old_name or new_owner != old_owner:
                old_dir = os.path.join(SERVERS_BASE_DIR, old_owner, old_name)
                new_dir = os.path.join(SERVERS_BASE_DIR, new_owner, new_name)
                if os.path.exists(old_dir):
                    os.makedirs(os.path.dirname(new_dir), exist_ok=True)
                    os.rename(old_dir, new_dir)
            
            if was_running:
                run_server(new_owner, new_name)
            
            flash(f"Server '{old_name}' updated", "success")
        else:
            flash("Server not found", "danger")
    
    # Update Settings
    elif "update_settings" in request.form:
        settings = load_settings()
        settings['registration_enabled'] = 'registration_enabled' in request.form
        settings['splitter_enabled'] = 'splitter_enabled' in request.form
        settings['maintenance_mode'] = 'maintenance_mode' in request.form
        save_settings(settings)
        flash("Settings updated!", "success")
    
    # Create Backup
    elif "create_backup" in request.form:
        try:
            create_backup()
            flash("Backup created successfully!", "success")
        except Exception as e:
            flash(f"Backup failed: {e}", "danger")
    
    return redirect(url_for('admin_dashboard'))

# ==================== API ROUTES ====================

@app.route("/api/system/stats")
@admin_required
def system_stats():
    cpu_percent = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    load_avg = safe_getloadavg()
    
    return jsonify({
        "cpu": cpu_percent,
        "cpu_cores": psutil.cpu_count(),
        "cpu_load_1m": load_avg[0],
        "cpu_load_5m": load_avg[1],
        "cpu_load_15m": load_avg[2],
        "ram_used": format_bytes(ram.used),
        "ram_total": format_bytes(ram.total),
        "ram_percent": ram.percent,
        "disk_used": format_bytes(disk.used),
        "disk_total": format_bytes(disk.total),
        "disk_percent": disk.percent,
        "uptime": format_uptime(time.time() - psutil.boot_time())
    })

@app.route("/api/server/stats/<owner>/<server_name>")
@login_required
def api_server_stats(owner, server_name):
    if session['username'] != owner and session['username'] != 'Antrax' and session.get('role') != 'admin':
        return jsonify({"error": "Access denied"}), 403
    
    stats = get_server_stats(owner, server_name)
    return jsonify(stats)

@app.route("/api/server/logs/<owner>/<server_name>")
@login_required
def api_server_logs(owner, server_name):
    if session['username'] != owner and session['username'] != 'Antrax' and session.get('role') != 'admin':
        return jsonify({"error": "Access denied"}), 403
    
    key = f"{owner}_{server_name}"
    logs = console_logs.get(key, [])
    return jsonify({"logs": logs[-100:]})  # Return last 100 lines

@app.route("/api/terminal/execute", methods=["POST"])
@admin_required
def execute_terminal():
    command = request.json.get("command", "").strip()
    
    if not command:
        return jsonify({"error": "No command provided"}), 400
    
    dangerous = ["rm -rf /", "mkfs", "dd if=/dev/zero", ":(){ :|:& };:", "chmod 777 /", "sudo", "su -"]
    for d in dangerous:
        if d in command:
            return jsonify({"error": f"Command blocked for security: {d}"}), 403
    
    try:
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
            output = f"Command executed (exit code: {result.returncode})"
        
        return jsonify({"output": output, "returncode": result.returncode})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Command timed out (30s limit)"}), 408
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/files/list")
@login_required
def list_files():
    path = request.args.get("path", "")
    username = session['username']
    base_dir = os.path.join(STORAGE_DIR, username)
    
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
    
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)
    
    if current_storage + file_size > storage_limit:
        return jsonify({"error": f"Storage limit exceeded! Max: {format_bytes(storage_limit)}"}), 413
    
    filename = secure_filename(file.filename)
    path = request.form.get("path", "")
    target_dir = os.path.normpath(os.path.join(user_dir, path))
    
    if not target_dir.startswith(user_dir):
        return jsonify({"error": "Access denied"}), 403
    
    os.makedirs(target_dir, exist_ok=True)
    file.save(os.path.join(target_dir, filename))
    
    return jsonify({"success": True, "message": f"{filename} uploaded"})

@app.route("/api/files/delete", methods=["POST"])
@login_required
def delete_file():
    username = session['username']
    data = request.json
    path = data.get("path", "")
    item_name = data.get("name", "")
    
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
    data = request.json
    path = data.get("path", "")
    folder_name = data.get("name", "")
    
    user_dir = os.path.join(STORAGE_DIR, username)
    target = os.path.normpath(os.path.join(user_dir, path, folder_name))
    
    if not target.startswith(user_dir):
        return jsonify({"error": "Access denied"}), 403
    
    try:
        os.makedirs(target, exist_ok=True)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/server/console/<owner>/<server_name>", methods=["POST"])
@login_required
def server_console_action(owner, server_name):
    if session['username'] != owner and session['username'] != 'Antrax' and session.get('role') != 'admin':
        return jsonify({"error": "Access denied"}), 403
    
    action = request.json.get("action", "")
    
    if action == "start":
        run_server(owner, server_name)
        return jsonify({"success": True, "message": "Server starting..."})
    elif action == "stop":
        stop_server(owner, server_name)
        return jsonify({"success": True, "message": "Server stopped"})
    elif action == "restart":
        restart_server(owner, server_name)
        return jsonify({"success": True, "message": "Server restarting..."})
    else:
        return jsonify({"error": "Invalid action"}), 400

# ==================== COMPATIBILITY ROUTES ====================

@app.route("/dashboard")
@login_required
def dashboard():
    if session['username'] == 'Antrax' or session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('user_dashboard'))

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
                         storage_percent=(storage_used / storage_limit) * 100 if storage_limit > 0 else 0)

@app.route("/console/<owner>/<server_name>")
@login_required
def console(owner, server_name):
    if session['username'] != owner and session['username'] != 'Antrax' and session.get('role') != 'admin':
        flash("Access denied", "danger")
        return redirect(url_for('dashboard'))
    
    servers = load_servers()
    server = next((s for s in servers if s["owner"] == owner and s["name"] == server_name), None)
    
    if not server:
        flash("Server not found", "danger")
        return redirect(url_for('dashboard'))
    
    settings = load_settings()
    stats = get_server_stats(owner, server_name)
    server.update(stats)
    
    return render_template("console.html", 
                         owner=owner, 
                         server_name=server_name, 
                         server=server,
                         splitter_enabled=settings.get("splitter_enabled", True))

# ==================== MAIN ====================

if __name__ == "__main__":
    ensure_admin()
    app.run(host="0.0.0.0", port=5000, debug=True)
