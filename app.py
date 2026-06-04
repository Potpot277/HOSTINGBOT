import os
import json
import subprocess
import threading
import time
import shutil
import psutil
import zipfile
import uuid
import tarfile
import platform
# import GPUtil  # Commented out - not compatible with Python 3.13
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from datetime import timedelta
from werkzeug.utils import secure_filename
from functools import wraps

app = Flask(__name__)
app.secret_key = "i_thought_no_one_care_about_secret_key_but_why_are_you_here_nigga?"
app.permanent_session_lifetime = timedelta(days=30)

USERS_FILE = "users.json"
SERVERS_FILE = "servers.json"
SETTINGS_FILE = "settings.json"
BASE_SERVER_DIR = "servers"

MAX_RAM_MB = 64 * 1024
MAX_CPU_PERCENT = 800
MAX_DISK_MB = 100 * 1024

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "username" not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "username" not in session or session["username"] != "Antrax":
            flash("Admin access required.", "danger")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated_function

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

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        default_settings = {"registration_enabled": True, "splitter_enabled": True}
        with open(SETTINGS_FILE, "w") as f:
            json.dump(default_settings, f, indent=4)
        return default_settings
    with open(SETTINGS_FILE, "r") as f:
        try:
            settings = json.load(f)
            if "splitter_enabled" not in settings:
                settings["splitter_enabled"] = True
            return settings
        except json.JSONDecodeError:
            return {"registration_enabled": True, "splitter_enabled": True}

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4)

def load_users():
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, "w") as f:
            json.dump([], f)
    with open(USERS_FILE, "r") as f:
        try:
            return json.load(f)
        except:
            return []

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=4)

def load_servers():
    if not os.path.exists(SERVERS_FILE):
        with open(SERVERS_FILE, "w") as f:
            json.dump([], f)
    with open(SERVERS_FILE, "r") as f:
        try:
            return json.load(f)
        except:
            return []

def save_servers(servers):
    with open(SERVERS_FILE, "w") as f:
        json.dump(servers, f, indent=4)

def ensure_admin():
    users = load_users()
    if not any(u["username"]=="Antrax" for u in users):
        users.append({"username":"Antrax","password":"Antrax27"})
        save_users(users)

def server_folder(owner, server_name):
    path = os.path.join(BASE_SERVER_DIR, owner, server_name)
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

def format_ram(mb):
    if mb is None: return "0 MB"
    mb = float(mb)
    if mb < 1024:
        return f"{mb:.0f} MB"
    else:
        return f"{mb / 1024:.2f} GB"

def format_size(mb):
    if mb is None: return "0 KB"
    mb = float(mb)
    if mb < 1:
        return f"{mb * 1024:.2f} KB"
    elif mb < 1024:
        return f"{mb:.2f} MB"
    else:
        return f"{mb / 1024:.2f} GB"

def format_uptime(seconds):
    if seconds is None:
        return "N/A"
    seconds = int(seconds)
    days = seconds // (24 * 3600)
    hours = (seconds % (24 * 3600)) // 3600
    minutes = (seconds % 3600) // 60
    return f"{days}d {hours}h {minutes}m"

def format_bytes(bytes_val):
    if bytes_val is None:
        return "0 B"
    bytes_val = float(bytes_val)
    if bytes_val < 1024:
        return f"{bytes_val:.2f} B"
    elif bytes_val < 1024**2:
        return f"{bytes_val / 1024:.2f} KB"
    elif bytes_val < 1024**3:
        return f"{bytes_val / 1024**2:.2f} MB"
    else:
        return f"{bytes_val / 1024**3:.2f} GB"

def get_system_stats():
    """Get comprehensive system statistics"""
    cpu_percent = psutil.cpu_percent(interval=0.5)
    cpu_per_core = psutil.cpu_percent(percpu=True)
    cpu_freq = psutil.cpu_freq()
    
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    
    disk = psutil.disk_usage('/')
    
    return {
        'cpu': {
            'percent': cpu_percent,
            'per_core': cpu_per_core,
            'frequency': cpu_freq.current if cpu_freq else 0,
            'cores': psutil.cpu_count(),
            'threads': psutil.cpu_count(logical=True)
        },
        'memory': {
            'total': memory.total / (1024**3),
            'available': memory.available / (1024**3),
            'used': memory.used / (1024**3),
            'percent': memory.percent,
            'swap_used': swap.used / (1024**3),
            'swap_total': swap.total / (1024**3),
            'swap_percent': swap.percent
        },
        'disk': {
            'total': disk.total / (1024**3),
            'used': disk.used / (1024**3),
            'free': disk.free / (1024**3),
            'percent': disk.percent
        },
        'uptime': time.time() - psutil.boot_time(),
        'processes': len(psutil.pids())
    }
    
    return {
        'cpu': {
            'percent': cpu_percent,
            'per_core': cpu_per_core,
            'frequency': cpu_freq.current if cpu_freq else 0,
            'cores': psutil.cpu_count(),
            'threads': psutil.cpu_count(logical=True)
        },
        'memory': {
            'total': memory.total / (1024**3),
            'available': memory.available / (1024**3),
            'used': memory.used / (1024**3),
            'percent': memory.percent,
            'swap_used': swap.used / (1024**3),
            'swap_total': swap.total / (1024**3),
            'swap_percent': swap.percent
        },
        'disk': {
            'total': disk.total / (1024**3),
            'used': disk.used / (1024**3),
            'free': disk.free / (1024**3),
            'percent': disk.percent
        },
        'gpu': gpu_stats,
        'uptime': time.time() - psutil.boot_time(),
        'processes': len(psutil.pids())
    }

server_processes = {}
console_logs = {}
MONITOR_INTERVAL = 2

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
                console_logs.get(key, []).append(f"RAM limit reached ({ram_used}MB / {ram_limit}MB). Stopping server.")
                stop_server(owner, server_name)
                break
            disk_used = get_folder_size(folder)
            if disk_limit > 0 and disk_used >= disk_limit:
                console_logs.get(key, []).append(f"Disk limit reached ({disk_used:.2f}MB / {disk_limit}MB). Stopping server.")
                stop_server(owner, server_name)
                break

            total_cpu_usage = ps_proc.cpu_percent(interval=1)

            for child in ps_proc.children(recursive=True):
                try:
                    total_cpu_usage += child.cpu_percent(interval=None)
                except psutil.NoSuchProcess:
                    continue

            server_processes[key].cpu_usage = total_cpu_usage

            try:
                ps = psutil.Process(process.pid)
            except psutil.NoSuchProcess:
                break

            if cpu_limit > 0:
                if total_cpu_usage > cpu_limit:
                    if not getattr(server_processes.get(key), 'throttled', False):
                        try:
                            server_processes[key].original_nice = ps.nice()
                        except Exception:
                            server_processes[key].original_nice = None

                        try:
                            if os.name == 'nt':
                                ps.nice(psutil.IDLE_PRIORITY_CLASS)
                            else:
                                try:
                                    ps.nice(10 if ps.nice() <= 0 else ps.nice() + 5)
                                except Exception:
                                    ps.nice(10)
                        except Exception as e:
                            console_logs.get(key, []).append(f"Could not throttle process priority: {e}")
                        else:
                            console_logs.get(key, []).append(f"High CPU ({total_cpu_usage:.1f}%) - throttling process priority to reduce load.")
                            server_processes[key].throttled = True

                elif getattr(server_processes.get(key), 'throttled', False) and total_cpu_usage < max(cpu_limit - 10, 0):
                    try:
                        orig = getattr(server_processes.get(key), 'original_nice', None)
                        if orig is not None:
                            ps.nice(orig)
                    except Exception as e:
                        console_logs.get(key, []).append(f"Could not restore process priority: {e}")
                    else:
                        console_logs.get(key, []).append(f"CPU back to normal ({total_cpu_usage:.1f}%). Restored process priority.")
                        server_processes[key].throttled = False

            if cpu_limit > 0 and cpu_limit <= 50:
                console_logs.get(key, []).append(f"CPU Overload! Configured limit ({cpu_limit}%) is too low for running server. Stopping server.")
                stop_server(owner, server_name)
                break
        except psutil.NoSuchProcess:
            break
        except Exception as e:
            console_logs.get(key, []).append(f"Error in monitoring thread: {e}")
            break
        time.sleep(MONITOR_INTERVAL)

def run_server(owner, server_name):
    key = f"{owner}_{server_name}"
    folder = server_folder(owner, server_name)
    servers = load_servers()
    server = next((s for s in servers if s["owner"]==owner and s["name"]==server_name), None)
    if not server: return
    ram_limit = int(server.get("ram", 0))
    cpu_limit = int(server.get("cpu", 0))
    disk_limit = int(server.get("disk", 0))
    disk_usage = get_folder_size(folder)
    if disk_limit > 0 and disk_usage >= disk_limit:
        console_logs.get(key, []).append(f"Disk limit exceeded ({disk_usage:.2f}MB / {disk_limit}MB). Cannot start server.")
        return

    if cpu_limit > 0 and cpu_limit <= 50:
        console_logs.get(key, []).append(f"CPU Overload! exceeded limit 50%. Stopping server.")
        return

    script_path = os.path.join(folder, "app.py")
    if not os.path.exists(script_path):
        console_logs.get(key, []).append(f"Error: app.py not found at {script_path}")
        return

    command = ["python3", "-u", "app.py"]

    console_logs.get(key, []).append(f"Starting server with command: {' '.join(command)}")
    console_logs.get(key, []).append(f"Working directory: {folder}")

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

        server_processes[key] = process

        server_processes[key].net_io = safe_net_io_counters()
        server_processes[key].disk_io = safe_disk_io_counters()
        server_processes[key].start_time = time.time()
        server_processes[key].cpu_usage = 0.0
        server_processes[key].original_nice = None
        server_processes[key].throttled = False

        try:
            psutil.Process(process.pid).cpu_percent()
        except:
            pass

        console_logs.get(key, []).append(f"Server started successfully (PID: {process.pid})")
        if cpu_limit > 0:
            console_logs.get(key, []).append(f"CPU limit active: Throttling to {cpu_limit}%.")

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
                    console_logs.get(key, []).append("Process finished successfully.")
                else:
                    console_logs.get(key, []).append(f"Process exited with code {return_code}")

            except Exception as e:
                console_logs.get(key, []).append(f"Error reading output: {str(e)}")
            finally:
                if key in server_processes:
                    server_processes.pop(key, None)

        output_thread = threading.Thread(target=read_output, daemon=True)
        output_thread.start()

        monitor_thread = threading.Thread(target=monitor_server, args=(owner, server_name, ram_limit, cpu_limit, disk_limit), daemon=True)
        monitor_thread.start()

    except Exception as e:
        console_logs.get(key, []).append(f"Failed to start server: {str(e)}")
        if key in server_processes:
            server_processes.pop(key, None)

def stop_server(owner, server_name):
    key = f"{owner}_{server_name}"
    process = server_processes.get(key)
    if process and isinstance(process, subprocess.Popen) and process.poll() is None:
        try:
            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)

            if parent.status() == psutil.STATUS_STOPPED:
                parent.resume()

            for child in children:
                try:
                    if child.status() == psutil.STATUS_STOPPED:
                        child.resume()
                    child.terminate()
                except psutil.NoSuchProcess:
                    continue

            parent.terminate()

            gone, alive = psutil.wait_procs(children + [parent], timeout=3)

            for p in alive:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass

            console_logs.get(key, []).append("Server stopped.")
        except psutil.NoSuchProcess:
            console_logs.get(key, []).append("Server was already stopped.")
        finally:
            server_processes.pop(key, None)

@app.route("/")
def home():
    return redirect(url_for("login"))

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        username = request.form["username"]
        password = request.form["password"]
        users = load_users()
        for u in users:
            if u["username"]==username and u["password"]==password:
                session["username"] = username
                session.permanent = True
                return redirect(url_for("dashboard") if username != "Antrax" else url_for("admin"))
        flash("Invalid credentials!", "danger")

    settings = load_settings()
    registration_enabled = settings.get("registration_enabled", True)
    return render_template("login.html", registration_enabled=registration_enabled)

@app.route("/register", methods=["POST"])
def register():
    settings = load_settings()
    if not settings.get("registration_enabled", True):
        flash("Registration is currently disabled by the administrator.", "danger")
        return redirect(url_for("login"))

    username = request.form["username"].strip()
    password = request.form["password"]
    confirm_password = request.form["confirm_password"]
    users = load_users()

    if any(u["username"] == username for u in users):
        flash("Username already exists.", "danger")
    elif password != confirm_password:
        flash("Passwords do not match.", "warning")
    elif len(password) < 6:
        flash("Password must be at least 6 characters long.", "warning")
    else:
        users.append({"username": username, "password": password})
        save_users(users)
        flash("Account created successfully! You can now log in.", "success")

    return redirect(url_for("login"))

@app.route("/logout")
def logout():
    session.pop("username", None)
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    all_servers = load_servers()
    user_servers = [s for s in all_servers if s["owner"] == session["username"]]
    for server in user_servers:
        key = f"{server['owner']}_{server['name']}"
        process = server_processes.get(key)
        server['status'] = 'Online' if process and isinstance(process, subprocess.Popen) and process.poll() is None else 'Offline'
    return render_template("dashboard.html", username=session["username"], servers=user_servers, page="dashboard")

@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    username = session["username"]
    if request.method == "POST":
        current_password = request.form.get("current_password")
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")
        users = load_users()
        user_to_update = next((u for u in users if u["username"] == username), None)
        if not user_to_update or user_to_update["password"] != current_password:
            flash("Your current password was incorrect.", "danger")
        elif not new_password or new_password != confirm_password:
            flash("New passwords do not match.", "warning")
        elif len(new_password) < 6:
            flash("New password must be at least 6 characters long.", "warning")
        else:
            user_to_update["password"] = new_password
            save_users(users)
            flash("Your password has been updated successfully!", "success")
        return redirect(url_for("account"))
    return render_template("account.html", username=username, page="account")

@app.route("/settings")
@login_required
def settings():
    username = session["username"]
    user_servers = [s for s in load_servers() if s["owner"] == username]
    stats = {
        "servers": len(user_servers),
        "ram": sum(int(s.get("ram", 0)) for s in user_servers),
        "cpu": sum(int(s.get("cpu", 0)) for s in user_servers),
        "disk": sum(int(s.get("disk", 0)) for s in user_servers),
    }
    return render_template("settings.html", username=username, stats=stats, page="settings")

@app.route("/api/system_stats")
def api_system_stats():
    """API endpoint for real-time system statistics"""
    stats = get_system_stats()
    return jsonify(stats)

@app.route("/console/<owner>/<server_name>", methods=["GET", "POST"])
@login_required
def console(owner, server_name):
    settings = load_settings()
    splitter_enabled = settings.get("splitter_enabled", True)

    servers = load_servers()
    server = next((s for s in servers if s["owner"]==owner and s["name"]==server_name), None)
    if not server:
        flash("Server not found.", "danger")
        return redirect(url_for("dashboard"))

    key = f"{owner}_{server_name}"
    if key not in console_logs: console_logs[key] = []

    if session["username"] != owner and session["username"] != "Antrax":
        flash("No permission!", "danger")
        return redirect(url_for("dashboard"))

    if request.method=="POST":
        if "start" in request.form:
            if key in server_processes and server_processes.get(key) and isinstance(server_processes.get(key), subprocess.Popen) and server_processes.get(key).poll() is None:
                console_logs[key].append("Server already running!")
            else:
                console_logs[key].append("Starting server...")
                run_server(owner, server_name)
        elif "restart" in request.form:
            stop_server(owner, server_name)
            time.sleep(1)
            console_logs[key].append("Restarting server...")
            run_server(owner, server_name)
        elif "stop" in request.form:
            stop_server(owner, server_name)
            console_logs[key].clear()

        elif "split_server" in request.form:
            if not splitter_enabled:
                flash("Server splitting is currently disabled by the administrator.", "danger")
                return redirect(url_for("console", owner=owner, server_name=server_name))

            new_name = request.form.get("new_server_name", "").strip()

            try:
                new_ram = int(request.form.get("new_server_ram"))
                new_cpu = int(request.form.get("new_server_cpu"))
                new_disk = int(request.form.get("new_server_disk"))
            except (ValueError, TypeError):
                flash("Invalid resource values. Please enter numbers.", "danger")
                return redirect(url_for("console", owner=owner, server_name=server_name))

            original_ram = int(server["ram"])
            original_cpu = int(server["cpu"])
            original_disk = int(server["disk"])

            if not new_name:
                flash("New server name cannot be empty.", "danger")
            elif any(s["owner"] == owner and s["name"] == new_name for s in servers):
                flash(f"A server with the name '{new_name}' already exists.", "danger")
            elif not (0 < new_ram < original_ram):
                flash(f"New RAM must be between 1 and {original_ram - 1} MB.", "danger")
            elif not (0 < new_disk < original_disk):
                flash(f"New Disk must be between 1 and {original_disk - 1} MB.", "danger")
            elif not (0 < new_cpu < original_cpu):
                flash(f"New CPU must be between 1 and {original_cpu - 1}%.", "danger")
            else:
                server["ram"] = str(original_ram - new_ram)
                server["cpu"] = str(original_cpu - new_cpu)
                server["disk"] = str(original_disk - new_disk)

                new_server = {
                    "owner": owner, "name": new_name,
                    "ram": str(new_ram), "cpu": str(new_cpu), "disk": str(new_disk),
                    "parent_server": server_name
                }
                servers.append(new_server)
                save_servers(servers)
                server_folder(owner, new_name)
                flash(f"Server '{server_name}' was split successfully. Created new server '{new_name}'.", "success")
                return redirect(url_for("dashboard"))

        return redirect(url_for("console", owner=owner, server_name=server_name))

    return render_template("console.html", owner=owner, server_name=server_name, server=server, console_lines=console_logs.get(key, []), path=request.args.get('path',''), page="console", splitter_enabled=splitter_enabled)

@app.route("/server_stats/<owner>/<server_name>")
def server_stats(owner, server_name):
    servers = load_servers()
    server = next((s for s in servers if s["owner"]==owner and s["name"]==server_name), None)
    if not server: return jsonify({"error": "Server not found"}), 404
    key = f"{owner}_{server_name}"
    process = server_processes.get(key)

    ram_used = 0
    cpu_usage = 0.0
    uptime = "N/A"
    load_avg = 0.0
    network_in = "0 B/s"
    network_out = "0 B/s"
    disk_read = "0 B/s"
    disk_write = "0 B/s"

    if process and isinstance(process, subprocess.Popen) and process.poll() is None:
        try:
            ps_proc = psutil.Process(process.pid)

            ram_used = ps_proc.memory_info().rss // (1024 * 1024)
            cpu_usage = getattr(server_processes.get(key), 'cpu_usage', 0.0)

            uptime = format_uptime(time.time() - getattr(process, 'start_time', time.time()))

            load_avg = safe_getloadavg()[0]

            current_net_io = safe_net_io_counters()
            previous_net_io = getattr(process, 'net_io', current_net_io)
            network_in = format_bytes(current_net_io.bytes_recv - previous_net_io.bytes_recv) + "/s"
            network_out = format_bytes(current_net_io.bytes_sent - previous_net_io.bytes_sent) + "/s"
            process.net_io = current_net_io

            current_disk_io = safe_disk_io_counters()
            previous_disk_io = getattr(process, 'disk_io', current_disk_io)
            disk_read = format_bytes(current_disk_io.read_bytes - previous_disk_io.read_bytes) + "/s"
            disk_write = format_bytes(current_disk_io.write_bytes - previous_disk_io.write_bytes) + "/s"
            process.disk_io = current_disk_io

        except psutil.NoSuchProcess:
            pass
        except PermissionError as e:
            console_logs.get(key, []).append(f"Process stats limited: {e}")

    disk_used_mb = get_folder_size(server_folder(owner, server_name))
    disk_limit_mb = int(server.get("disk", 0))
    ram_limit_mb = int(server.get("ram", 0))

    return jsonify({
        "status": "Online" if process and isinstance(process, subprocess.Popen) and process.poll() is None else "Offline",
        "ram_used": format_ram(ram_used),
        "ram_limit": format_ram(ram_limit_mb),
        "cpu": cpu_usage,
        "cpu_limit": int(server.get("cpu", 0)),
        "disk_used": format_size(disk_used_mb),
        "disk_limit": format_size(disk_limit_mb),
        "uptime": uptime,
        "avg_cpu_load": f"{load_avg:.2f}",
        "network_in": network_in,
        "network_out": network_out,
        "disk_read": disk_read,
        "disk_write": disk_write
    })

@app.route('/api/list_files/<owner>/<server_name>')
def api_list_files(owner, server_name):
    path = request.args.get('path', '')
    server_dir = server_folder(owner, server_name)
    current_folder = os.path.join(server_dir, path)
    if not os.path.abspath(current_folder).startswith(os.path.abspath(server_dir)):
        return jsonify({"error": "Access Denied"}), 403
    try:
        items = os.listdir(current_folder)
        dirs = sorted([d for d in items if os.path.isdir(os.path.join(current_folder, d))])
        files = sorted([f for f in items if os.path.isfile(os.path.join(current_folder, f))])
        return jsonify({"dirs": dirs, "files": files, "current_path": path})
    except FileNotFoundError:
        return jsonify({"error": "Directory not found"}), 404

@app.route('/console/<owner>/<server_name>/download')
def download_file(owner, server_name):
    path = request.args.get('path', '')
    filename = request.args.get('filename', '')
    if ".." in path or ".." in filename: return "Invalid path", 400
    server_dir = server_folder(owner, server_name)
    full_path = os.path.join(server_dir, path)
    return send_from_directory(full_path, filename, as_attachment=True)

@app.route('/console/<owner>/<server_name>/delete', methods=['POST'])
def delete_item(owner, server_name):
    path = request.form.get('path', '')
    item_name = request.form.get('item_name', '')
    if ".." in path or ".." in item_name: return jsonify({"error": "Invalid path"}), 400
    item_path = os.path.join(server_folder(owner, server_name), path, item_name)
    try:
        if os.path.isfile(item_path): os.remove(item_path)
        elif os.path.isdir(item_path): shutil.rmtree(item_path)
        return jsonify({"success": True})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/console/<owner>/<server_name>/rename', methods=['POST'])
def rename_item(owner, server_name):
    path = request.form.get('path', '')
    old_name = request.form.get('old_name', '')
    new_name = request.form.get('new_name', '')
    if ".." in path or ".." in old_name or ".." in new_name or not new_name: return jsonify({"error": "Invalid name"}), 400
    base_dir = server_folder(owner, server_name)
    old_path = os.path.join(base_dir, path, old_name)
    new_path = os.path.join(base_dir, path, new_name)
    try:
        os.rename(old_path, new_path)
        return jsonify({"success": True})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/console/<owner>/<server_name>/move', methods=['POST'])
def move_item(owner, server_name):
    source_path = request.form.get('source_path', '')
    new_path = request.form.get('new_path', '')
    item_name = request.form.get('item_name', '')

    if ".." in source_path or ".." in new_path or ".." in item_name:
        return jsonify({"error": "Invalid path"}), 400

    base_dir = server_folder(owner, server_name)

    old_item_path = os.path.join(base_dir, source_path, item_name)
    new_item_path = os.path.join(base_dir, new_path, item_name)

    if not os.path.abspath(old_item_path).startswith(os.path.abspath(base_dir)) or \
       not os.path.abspath(new_item_path).startswith(os.path.abspath(base_dir)):
        return jsonify({"error": "Access denied"}), 403

    if not os.path.exists(old_item_path):
        return jsonify({"error": f"Source item not found at path: {old_item_path}"}), 404

    try:
        shutil.move(old_item_path, new_item_path)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/console/<owner>/<server_name>/copy', methods=['POST'])
def copy_item(owner, server_name):
    source_path = request.form.get('source_path', '')
    new_path = request.form.get('new_path', '')
    item_name = request.form.get('item_name', '')

    if ".." in source_path or ".." in new_path or ".." in item_name:
        return jsonify({"error": "Invalid path"}), 400

    base_dir = server_folder(owner, server_name)

    old_item_path = os.path.join(base_dir, source_path, item_name)
    new_item_path = os.path.join(base_dir, new_path, item_name)

    if not os.path.abspath(old_item_path).startswith(os.path.abspath(base_dir)) or \
       not os.path.abspath(new_item_path).startswith(os.path.abspath(base_dir)):
        return jsonify({"error": "Access denied"}), 403

    if not os.path.exists(old_item_path):
        return jsonify({"error": "Source item not found at path: {old_item_path}"}), 404

    if os.path.exists(new_item_path):
        return jsonify({"error": f"Destination already exists: {new_item_path}"}), 409

    try:
        if os.path.isfile(old_item_path):
            shutil.copy2(old_item_path, new_item_path)
        elif os.path.isdir(old_item_path):
            shutil.copytree(old_item_path, new_item_path)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/get_file_stats/<owner>/<server_name>')
def get_file_stats(owner, server_name):
    path = request.args.get('path', '')
    item_name = request.args.get('item_name', '')

    if ".." in path or ".." in item_name:
        return jsonify({"error": "Invalid path"}), 400

    base_dir = server_folder(owner, server_name)
    item_path = os.path.join(base_dir, path, item_name)

    if not os.path.exists(item_path):
        return jsonify({"error": "Item not found"}), 404

    if not os.path.abspath(item_path).startswith(os.path.abspath(base_dir)):
        return jsonify({"error": "Access denied"}), 403

    try:
        stats = os.stat(item_path)

        if os.path.isdir(item_path):
            size = get_folder_size(item_path)
            size_formatted = format_size(size)
        else:
            size_bytes = stats.st_size
            size_formatted = format_bytes(size_bytes)

        return jsonify({
            "success": True,
            "path": os.path.join(path, item_name),
            "size": size_formatted,
            "modified": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stats.st_mtime)),
            "created": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stats.st_ctime))
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/console/<owner>/<server_name>/archive', methods=['POST'])
def archive_files(owner, server_name):
    path = request.form.get('path', '')
    files_to_archive = request.form.getlist('selected_files')
    if ".." in path or any(".." in f for f in files_to_archive):
        return jsonify({"error": "Invalid path"}), 400
    base_dir = server_folder(owner, server_name)
    zip_filename = f"archive-{uuid.uuid4().hex[:8]}.zip"
    zip_path = os.path.join(base_dir, path, zip_filename)
    try:
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for item_name in files_to_archive:
                item_path = os.path.join(base_dir, path, item_name)
                if os.path.exists(item_path):
                    zipf.write(item_path, arcname=item_name)
        return jsonify({"success": True, "filename": zip_filename, "path": path})
    except Exception as e:
        return jsonify({"error": f"Failed to create archive: {str(e)}"}), 500

@app.route('/console/<owner>/<server_name>/unarchive', methods=['POST'])
def unarchive_item(owner, server_name):
    path = request.form.get('path', '')
    filename = request.form.get('filename', '')
    if ".." in path or ".." in filename: return jsonify({"error": "Invalid path"}), 400
    base_dir = server_folder(owner, server_name)
    file_path = os.path.join(base_dir, path, filename)
    extract_path = os.path.join(base_dir, path)
    try:
        if filename.endswith('.zip'):
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(extract_path)
        elif filename.endswith(('.tar', '.tar.gz', '.tgz')):
            with tarfile.open(file_path, 'r:*') as tar_ref:
                tar_ref.extractall(path=extract_path)
        else:
            return jsonify({"error": "Unsupported archive format. Only .zip and .tar are supported."}), 400
    except Exception as e: return jsonify({"error": str(e)}), 500
    return jsonify({"success": True, "message": f"Successfully unarchived {filename}"})

@app.route('/console/<owner>/<server_name>/install', methods=['POST'])
def install_requirements(owner, server_name):
    if "username" not in session: return jsonify({"error": "Unauthorized"}), 401
    if session["username"] != owner and session["username"] != "Antrax":
        return jsonify({"error": "Forbidden"}), 403
    key = f"{owner}_{server_name}"
    server_root = server_folder(owner, server_name)
    requirements_path = os.path.join(server_root, 'requirements.txt')
    if not os.path.exists(requirements_path):
        return jsonify({"error": "requirements.txt not found"}), 404
    def run_installation():
        console_logs.get(key, []).append("Starting dependency installation from requirements.txt")
        command = ["python3", "-m", "pip", "install", "-r", "requirements.txt"]
        process = subprocess.Popen(command, cwd=server_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='ignore')
        for line in process.stdout:
            console_logs.get(key, []).append(line.strip())
        process.stdout.close()
        return_code = process.wait()
        if return_code == 0:
            console_logs.get(key, []).append("Installation finished successfully.")
        else:
            console_logs.get(key, []).append(f"Installation failed with exit code {return_code}.")
    install_thread = threading.Thread(target=run_installation, daemon=True)
    install_thread.start()
    return jsonify({"success": True, "message": "Installation started. Check console for logs."})

@app.route('/console/<owner>/<server_name>/delete_multiple', methods=['POST'])
def delete_multiple(owner, server_name):
    path = request.form.get('path', '')
    files_to_delete = request.form.getlist('selected_files')
    if ".." in path or any(".." in f for f in files_to_delete): return jsonify({"error": "Invalid path"}), 400
    base_dir = server_folder(owner, server_name)
    for item_name in files_to_delete:
        item_path = os.path.join(base_dir, path, item_name)
        try:
            if os.path.isfile(item_path): os.remove(item_path)
            elif os.path.isdir(item_path): shutil.rmtree(item_path)
        except Exception: pass
    return jsonify({"success": True})

@app.route('/console/<owner>/<server_name>/create_file', methods=['POST'])
def create_file(owner, server_name):
    path = request.form.get('path', '')
    filename = request.form.get('file_name', '').strip()
    if not filename or ".." in path or ".." in filename or "/" in filename:
        return jsonify({"error": "Invalid file name"}), 400
    base_dir = server_folder(owner, server_name)
    file_path = os.path.join(base_dir, path, filename)
    if os.path.exists(file_path):
        return jsonify({"error": "File already exists"}), 400
    try:
        open(file_path, 'a').close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/console/<owner>/<server_name>/create_dir', methods=['POST'])
def create_dir(owner, server_name):
    path = request.form.get('path', '')
    dirname = request.form.get('dir_name', '').strip()
    if not dirname or ".." in path or ".." in dirname or "/" in dirname:
        return jsonify({"error": "Invalid directory name"}), 400
    base_dir = server_folder(owner, server_name)
    dir_path = os.path.join(base_dir, path, dirname)
    if os.path.exists(dir_path):
        return jsonify({"error": "Directory already exists"}), 400
    try:
        os.makedirs(dir_path)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/console/<owner>/<server_name>/upload', methods=['POST'])
def upload_file_route(owner, server_name):
    if "username" not in session or (session["username"] != owner and session["username"] != "Antrax"):
        return jsonify({"error": "Unauthorized"}), 401

    path = request.form.get('path', '')
    if ".." in path:
        return jsonify({"error": "Invalid path"}), 400

    if 'files' not in request.files:
        return jsonify({"error": "No file part"}), 400

    files = request.files.getlist('files')
    if not files or files[0].filename == '':
        return jsonify({"error": "No selected file"}), 400

    base_dir = server_folder(owner, server_name)
    upload_path = os.path.join(base_dir, path)

    if not os.path.abspath(upload_path).startswith(os.path.abspath(base_dir)):
        return jsonify({"error": "Access Denied"}), 403
    os.makedirs(upload_path, exist_ok=True)

    try:
        uploaded_filenames = []
        for file in files:
            filename = secure_filename(file.filename)
            file.save(os.path.join(upload_path, filename))
            uploaded_filenames.append(filename)

        message = f"Successfully uploaded {len(uploaded_filenames)} file(s)."
        if len(uploaded_filenames) == 1:
            message = f"File '{uploaded_filenames[0]}' uploaded successfully."

        return jsonify({"success": True, "message": message})
    except Exception as e:
        return jsonify({"error": f"File upload failed: {str(e)}"}), 500

@app.route("/api/get_file_content/<owner>/<server_name>")
def get_file_content(owner, server_name):
    path = request.args.get('path', '')
    filename = request.args.get('filename', '')
    if ".." in path or ".." in filename: return jsonify({"error": "Invalid path"}), 400
    base_dir = server_folder(owner, server_name)
    file_path = os.path.join(base_dir, path, filename)
    if not os.path.isfile(file_path) or not os.path.abspath(file_path).startswith(os.path.abspath(base_dir)):
        return jsonify({"error": "File not found or access denied"}), 404
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        return jsonify({"success": True, "content": content})
    except Exception as e: return jsonify({"error": f"Error reading file: {str(e)}"}), 500

@app.route("/api/save_file_content/<owner>/<server_name>", methods=['POST'])
def save_file_content(owner, server_name):
    path = request.form.get('path', '')
    filename = request.form.get('filename', '')
    content = request.form.get('content', '')
    if ".." in path or ".." in filename: return jsonify({"error": "Invalid path"}), 400
    base_dir = server_folder(owner, server_name)
    file_path = os.path.join(base_dir, path, filename)
    if not os.path.abspath(file_path).startswith(os.path.abspath(base_dir)):
        return jsonify({"error": "Access denied"}), 403
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({"success": True, "message": "File saved successfully"})
    except Exception as e: return jsonify({"error": f"Error saving file: {str(e)}"}), 500

@app.route('/api/list_split_servers/<owner>/<server_name>')
def list_split_servers(owner, server_name):
    if "username" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    if session["username"] != owner and session["username"] != "Antrax":
        return jsonify({"error": "Forbidden"}), 403

    all_servers = load_servers()
    split_servers = [s for s in all_servers if s.get('parent_server') == server_name]

    return jsonify({"split_servers": split_servers})

@app.route('/api/update_split_server/<owner>/<server_name>', methods=['POST'])
def update_split_server(owner, server_name):
    if "username" not in session or session["username"] != owner:
        return jsonify({"error": "Unauthorized or Forbidden"}), 403

    servers = load_servers()
    target_server = next((s for s in servers if s["owner"] == owner and s["name"] == server_name), None)

    if not target_server or "parent_server" not in target_server:
        return jsonify({"error": "Server not found or is a main server."}), 404

    parent_server_name = target_server["parent_server"]
    parent_server = next((s for s in servers if s["owner"] == owner and s["name"] == parent_server_name), None)

    if not parent_server:
         return jsonify({"error": f"Parent server '{parent_server_name}' not found. Cannot return resources."}), 404

    try:
        new_ram = int(request.form.get('new_ram'))
        new_cpu = int(request.form.get('new_cpu'))
        new_disk = int(request.form.get('new_disk'))

        old_ram = int(target_server.get("ram", 0))
        old_cpu = int(target_server.get("cpu", 0))
        old_disk = int(target_server.get("disk", 0))

        ram_diff = new_ram - old_ram
        cpu_diff = new_cpu - old_cpu
        disk_diff = new_disk - old_disk

        if new_ram < 1 or new_cpu < 1 or new_disk < 1:
            return jsonify({"error": "Minimum resource allocation is 1 for RAM, CPU, and Disk."}), 400

        if int(parent_server["ram"]) - ram_diff < 0 or int(parent_server["cpu"]) - cpu_diff < 0 or int(parent_server["disk"]) - disk_diff < 0:
             return jsonify({"error": "Insufficient resources in parent server."}), 400

        parent_server["ram"] = str(int(parent_server["ram"]) + old_ram - new_ram)
        parent_server["cpu"] = str(int(parent_server["cpu"]) + old_cpu - new_cpu)
        parent_server["disk"] = str(int(parent_server["disk"]) + old_disk - new_disk)

        target_server["ram"] = str(new_ram)
        target_server["cpu"] = str(new_cpu)
        target_server["disk"] = str(new_disk)

        save_servers(servers)
        return jsonify({"success": True, "message": f"Server '{server_name}' updated successfully."})
    except (ValueError, TypeError) as e:
        return jsonify({"error": f"Invalid resource values: {str(e)}"}), 400

def get_all_descendants(parent_name, all_servers):
    descendants = []
    children = [s for s in all_servers if s.get('parent_server') == parent_name]
    for child in children:
        descendants.append(child)
        descendants.extend(get_all_descendants(child['name'], all_servers))
    return descendants

@app.route('/api/delete_split_server/<owner>/<server_name>', methods=['POST'])
def delete_split_server(owner, server_name):
    if "username" not in session or session["username"] != owner:
        return jsonify({"error": "Unauthorized or Forbidden"}), 403

    servers = load_servers()
    target_server = next((s for s in servers if s["owner"] == owner and s["name"] == server_name), None)

    if not target_server:
        return jsonify({"error": "Server not found."}), 404

    if "parent_server" not in target_server:
        return jsonify({"error": "This is a main server and cannot be deleted from this interface."}), 400

    parent_server_name = target_server["parent_server"]
    parent_server = next((s for s in servers if s["owner"] == owner and s["name"] == parent_server_name), None)

    if not parent_server:
        return jsonify({"error": f"Parent server '{parent_server_name}' not found. Cannot return resources."}), 404

    descendants = get_all_descendants(server_name, servers)

    total_ram_to_return = int(target_server.get("ram", 0))
    total_cpu_to_return = int(target_server.get("cpu", 0))
    total_disk_to_return = int(target_server.get("disk", 0))

    for descendant in descendants:
        total_ram_to_return += int(descendant.get("ram", 0))
        total_cpu_to_return += int(descendant.get("cpu", 0))
        total_disk_to_return += int(descendant.get("disk", 0))

    parent_server["ram"] = str(int(parent_server["ram"]) + total_ram_to_return)
    parent_server["cpu"] = str(int(parent_server["cpu"]) + total_cpu_to_return)
    parent_server["disk"] = str(int(parent_server["disk"]) + total_disk_to_return)

    servers_to_keep = [s for s in servers if s["name"] != server_name and s not in descendants]
    save_servers(servers_to_keep)

    servers_to_delete = [target_server] + descendants
    for s_to_del in servers_to_delete:
        server_path = os.path.join(BASE_SERVER_DIR, s_to_del["owner"], s_to_del["name"])
        if os.path.exists(server_path):
            shutil.rmtree(server_path)

    return jsonify({"success": True, "message": f"Server '{server_name}' and all its children have been deleted. Resources returned to '{parent_server_name}'."})

@app.route("/admin", methods=["GET","POST"])
@admin_required
def admin():
    servers = load_servers()
    users = load_users()
    total_ram = sum(int(s.get("ram", 0)) for s in servers)
    total_cpu = sum(int(s.get("cpu", 0)) for s in servers)
    total_disk = sum(int(s.get("disk", 0)) for s in servers)

    if request.method=="POST":
        if "create_user" in request.form:
            new_user = request.form["new_username"].strip()
            new_pass = request.form["new_password"].strip()
            if new_user and new_pass and not any(u["username"]==new_user for u in users):
                users.append({"username":new_user,"password":new_pass})
                save_users(users)
                flash(f"User '{new_user}' created successfully.", "success")

        elif "delete_user_from_modal" in request.form:
            target = request.form.get("old_username")
            if target == 'Antrax':
                flash("The primary admin account cannot be deleted.", "danger")
            else:
                users = [u for u in users if u["username"]!=target]
                save_users(users)
                servers_to_keep = [s for s in servers if s["owner"]!=target]
                save_servers(servers_to_keep)
                user_server_dir = os.path.join(BASE_SERVER_DIR, target)
                if os.path.exists(user_server_dir):
                    shutil.rmtree(user_server_dir)
                flash(f"User '{target}' and their servers deleted.", "success")

        elif "create_server" in request.form:
            owner = request.form["server_owner"].strip()

            try:
                new_ram = int(request.form["server_ram"].strip())
                new_cpu = int(request.form["server_cpu"].strip())
                new_disk = int(request.form["server_disk"].strip())
            except (ValueError, TypeError):
                flash("Invalid resource values. Please enter numbers.", "danger")
                return redirect(url_for('admin'))

            if new_ram < 1 or new_cpu < 1 or new_disk < 1:
                flash("Minimum resource allocation is 1 for RAM, CPU, and Disk.", "danger")
            elif not any(u["username"] == owner for u in users):
                flash(f"Cannot create server: Owner username '{owner}' does not exist.", "danger")
            elif total_ram + new_ram > MAX_RAM_MB:
                flash(f"Cannot create server: Exceeds total RAM limit of {format_ram(MAX_RAM_MB)}.", "danger")
            elif total_cpu + new_cpu > MAX_CPU_PERCENT:
                flash(f"Cannot create server: Exceeds total CPU limit of {MAX_CPU_PERCENT}%.", "danger")
            elif total_disk + new_disk > MAX_DISK_MB:
                flash(f"Cannot create server: Exceeds total Disk limit of {format_size(MAX_DISK_MB)}.", "danger")
            else:
                name = request.form["server_name"].strip()
                servers.append({"owner":owner, "name":name, "ram":str(new_ram), "cpu":str(new_cpu), "disk":str(new_disk)})
                save_servers(servers)
                flash(f"Server '{name}' for '{owner}' created.", "success")

        elif "delete_server_from_modal" in request.form:
            old_name = request.form.get("old_server_name")
            old_owner = request.form.get("old_owner")
            server_to_delete = next((s for s in servers if s["name"] == old_name and s["owner"] == old_owner), None)
            if server_to_delete:
                server_path = os.path.join(BASE_SERVER_DIR, old_owner, old_name)
                if os.path.exists(server_path):
                    shutil.rmtree(server_path)
                servers = [s for s in servers if not (s["name"] == old_name and s["owner"] == old_owner)]
                save_servers(servers)
                flash(f"Server '{old_name}' has been deleted.", "success")
            else:
                flash(f"Could not find server '{old_name}' to delete.", "danger")

        elif "edit_user" in request.form:
            old_username = request.form["old_username"]
            if old_username == 'Antrax':
                flash("The primary admin account cannot be modified.", "danger")
            else:
                new_username = request.form["new_username"].strip()
                new_password = request.form["new_password"].strip()
                user_to_edit = next((u for u in users if u["username"] == old_username), None)
                if not user_to_edit:
                    flash(f"User '{old_username}' not found.", "danger")
                else:
                    username_changed = new_username and new_username != old_username
                    if username_changed and any(u["username"] == new_username for u in users):
                        flash(f"Username '{new_username}' is already taken.", "warning")
                    else:
                        if username_changed:
                            user_to_edit["username"] = new_username
                        if new_password:
                            user_to_edit["password"] = new_password
                        save_users(users)
                        if username_changed:
                            for server in servers:
                                if server["owner"] == old_username:
                                    server["owner"] = new_username
                            save_servers(servers)
                            old_user_dir = os.path.join(BASE_SERVER_DIR, old_username)
                            new_user_dir = os.path.join(BASE_SERVER_DIR, new_username)
                            if os.path.exists(old_user_dir):
                                shutil.move(old_user_dir, new_user_dir)
                        flash("User updated successfully!", "success")

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
                flash("Invalid resource values. Please enter numbers.", "danger")
                return redirect(url_for('admin'))

            server_to_edit = next((s for s in servers if s["name"] == old_name and s["owner"] == old_owner), None)

            if server_to_edit:
                if new_ram < 1 or new_cpu < 1 or new_disk < 1:
                    flash("Minimum resource allocation is 1 for RAM, CPU, and Disk.", "danger")
                else:
                    ram_diff = new_ram - int(server_to_edit.get("ram", 0))
                    cpu_diff = new_cpu - int(server_to_edit.get("cpu", 0))
                    disk_diff = new_disk - int(server_to_edit.get("disk", 0))

                    if total_ram + ram_diff > MAX_RAM_MB:
                        flash(f"Cannot update server: Exceeds total RAM limit of {format_ram(MAX_RAM_MB)}.", "danger")
                    elif total_cpu + cpu_diff > MAX_CPU_PERCENT:
                        flash(f"Cannot update server: Exceeds total CPU limit of {MAX_CPU_PERCENT}%.", "danger")
                    elif total_disk + disk_diff > MAX_DISK_MB:
                        flash(f"Cannot update server: Exceeds total Disk limit of {format_size(MAX_DISK_MB)}.", "danger")
                    else:
                        path_changed = new_name != old_name or new_owner != old_owner

                        server_to_edit["name"] = new_name
                        server_to_edit["owner"] = new_owner
                        server_to_edit["ram"] = str(new_ram)
                        server_to_edit["cpu"] = str(new_cpu)
                        server_to_edit["disk"] = str(new_disk)

                        save_servers(servers)

                        if path_changed:
                            old_path = os.path.join(BASE_SERVER_DIR, old_owner, old_name)
                            new_path_dir = os.path.join(BASE_SERVER_DIR, new_owner)
                            os.makedirs(new_path_dir, exist_ok=True)
                            new_path = os.path.join(new_path_dir, new_name)
                            if os.path.exists(old_path):
                                shutil.move(old_path, new_path)
                        flash(f"Server '{old_name}' updated successfully.", "success")
            else:
                flash(f"Could not find server '{old_name}' to update.", "danger")

        elif "update_settings" in request.form:
            settings = load_settings()
            settings['registration_enabled'] = 'registration_enabled' in request.form
            settings['splitter_enabled'] = 'splitter_enabled' in request.form
            save_settings(settings)
            flash("Settings updated successfully.", "success")

        return redirect(url_for('admin'))

    users = load_users()
    user_dict = {u["username"]: 0 for u in users}
    for s in servers:
        if s["owner"] in user_dict:
            user_dict[s["owner"]] += 1
    for u in users:
        u["servers"] = user_dict.get(u["username"], 0)

    settings = load_settings()
    admin_stats = {
        "total_ram": format_ram(total_ram),
        "max_ram": format_ram(MAX_RAM_MB),
        "ram_percent": (total_ram / MAX_RAM_MB * 100) if MAX_RAM_MB > 0 else 0,
        "total_cpu": total_cpu,
        "max_cpu": MAX_CPU_PERCENT,
        "cpu_percent": (total_cpu / MAX_CPU_PERCENT * 100) if MAX_CPU_PERCENT > 0 else 0,
        "total_disk": format_size(total_disk),
        "max_disk": format_size(MAX_DISK_MB),
        "disk_percent": (total_disk / MAX_DISK_MB * 100) if MAX_DISK_MB > 0 else 0,
    }

    return render_template("admin.html", users=users, servers=servers, stats=admin_stats, registration_enabled=settings.get("registration_enabled", True), splitter_enabled=settings.get("splitter_enabled", True))

@app.route("/api/console_logs/<owner>/<server_name>")
def get_console_logs(owner, server_name):
    if "username" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    if session["username"] != owner and session["username"] != "Antrax":
        return jsonify({"error": "Forbidden"}), 403

    key = f"{owner}_{server_name}"
    process = server_processes.get(key)
    is_running = process and isinstance(process, subprocess.Popen) and process.poll() is None

    return jsonify({
        "logs": console_logs.get(key, []),
        "is_running": is_running
    })

if __name__=="__main__":
    ensure_admin()
    os.makedirs(BASE_SERVER_DIR, exist_ok=True)
    app.run(host="0.0.0.0", port=25620, debug=False)
