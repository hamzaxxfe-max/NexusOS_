#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${VM_DIR:-/var/lib/aion-vm}/.env"

if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a
fi

VM_DIR="${VM_DIR:-/var/lib/aion-vm}"
VNC_PORT="${VNC_PORT:-1}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
WEBSOCKIFY_PORT="${WEBSOCKIFY_PORT:-6081}"
NOVNC_DIR="${NOVNC_DIR:-/opt/noVNC}"
WEBLOG_DIR="${VM_DIR}/logs/weblog"

log()    { echo -e "${CYAN}[noVNC]${NC} $*"; }
log_ok() { echo -e "${GREEN}[noVNC]${NC} $*"; }
log_warn(){ echo -e "${YELLOW}[noVNC]${NC} $*"; }
log_err(){ echo -e "${RED}[noVNC]${NC} $*"; }

install_websockify() {
    if command -v websockify &>/dev/null; then
        log_ok "websockify already installed"
        return
    fi

    log "Installing websockify..."

    pip3 install websockify 2>/dev/null || pip install websockify 2>/dev/null || true

    if command -v websockify &>/dev/null; then
        log_ok "websockify installed via pip"
        return
    fi

    log "Installing websockify from source..."

    local WEBSockIFY_DIR="/opt/websockify"
    git clone --depth 1 https://github.com/novnc/websockify.git "$WEBSockIFY_DIR" 2>/dev/null
    ln -sf "$WEBSockIFY_DIR/run" /usr/local/bin/websockify
    chmod +x /usr/local/bin/websockify

    log_ok "websockify installed from source"
}

install_novnc() {
    if [ -d "$NOVNC_DIR" ]; then
        log_ok "noVNC already installed at $NOVNC_DIR"
        return
    fi

    log "Installing noVNC..."

    git clone --depth 1 https://github.com/novnc/noVNC.git "$NOVNC_DIR"
    ln -sf "$NOVNC_DIR/vnc.html" "$NOVNC_DIR/index.html"

    log_ok "noVNC installed at $NOVNC_DIR"
}

create_novnc_wrapper() {
    log "Creating noVNC launcher page..."

    cat > "$NOVNC_DIR/aion.html" << 'HTMLEOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Aion Cloud Viewer</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #0a0a0f;
            color: #e0e0e0;
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
            overflow: hidden;
            height: 100vh;
        }
        #header {
            background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
            border-bottom: 1px solid #30363d;
            padding: 8px 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            height: 48px;
        }
        #header h1 {
            font-size: 16px;
            font-weight: 600;
            color: #58a6ff;
        }
        #header .status {
            font-size: 12px;
            color: #7ee787;
        }
        #header .status.error { color: #f85149; }
        #header .status.connecting { color: #d29922; }
        #controls {
            background: #161b22;
            border-bottom: 1px solid #30363d;
            padding: 6px 16px;
            display: flex;
            gap: 8px;
            align-items: center;
        }
        #controls button {
            background: #21262d;
            color: #c9d1d9;
            border: 1px solid #30363d;
            padding: 4px 12px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 12px;
        }
        #controls button:hover { background: #30363d; }
        #controls button.active { background: #238636; border-color: #2ea043; }
        #controls select, #controls input {
            background: #21262d;
            color: #c9d1d9;
            border: 1px solid #30363d;
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 12px;
        }
        #viewer {
            width: 100%;
            height: calc(100vh - 48px - 38px);
            display: flex;
            align-items: center;
            justify-content: center;
        }
        #viewer iframe {
            width: 100%;
            height: 100%;
            border: none;
        }
        #log-panel {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            height: 0;
            background: #0d1117;
            border-top: 1px solid #30363d;
            overflow: hidden;
            transition: height 0.3s ease;
            z-index: 100;
        }
        #log-panel.open { height: 200px; }
        #log-panel pre {
            padding: 8px 16px;
            font-family: 'Cascadia Code', 'Fira Code', monospace;
            font-size: 11px;
            color: #8b949e;
            overflow-y: auto;
            height: 100%;
        }
        #log-toggle {
            position: fixed;
            bottom: 8px;
            right: 16px;
            background: #21262d;
            color: #58a6ff;
            border: 1px solid #30363d;
            padding: 4px 10px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 11px;
            z-index: 101;
        }
    </style>
</head>
<body>
    <div id="header">
        <h1>Aion Cloud Viewer</h1>
        <span id="status" class="status connecting">Connecting...</span>
    </div>
    <div id="controls">
        <button onclick="connect()" class="active">Connect</button>
        <button onclick="disconnect()">Disconnect</button>
        <button onclick="resize()">Fullscreen</button>
        <select id="scaling" onchange="setScale(this.value)">
            <option value="off">No Scaling</option>
            <option value="local" selected>Local Scaling</option>
            <option value="remote">Remote Scaling</option>
        </select>
        <button onclick="sendCtrlAltDel()">Ctrl+Alt+Del</button>
        <button onclick="sendCtrlAltF1()">Ctrl+Alt+F1</button>
    </div>
    <div id="viewer">
        <iframe id="vnc-frame" src="vnc.html?autoconnect=true&resize=local&reconnect=true&reconnect_delay=2000&path=websockify/?token=aion"></iframe>
    </div>
    <div id="log-panel"><pre id="log-content">Boot log monitor ready...</pre></div>
    <button id="log-toggle" onclick="toggleLog()">Boot Log</button>
    <script>
        function setStatus(text, cls) {
            const el = document.getElementById('status');
            el.textContent = text;
            el.className = 'status ' + cls;
        }
        function connect() {
            const frame = document.getElementById('vnc-frame');
            frame.src = 'vnc.html?autoconnect=true&resize=local&reconnect=true&path=websockify/?token=aion';
            setStatus('Connected', '');
        }
        function disconnect() {
            document.getElementById('vnc-frame').src = 'about:blank';
            setStatus('Disconnected', 'error');
        }
        function resize() {
            const frame = document.getElementById('vnc-frame');
            if (document.fullscreenElement) {
                document.exitFullscreen();
            } else {
                frame.requestFullscreen();
            }
        }
        function setScale(val) {
            document.getElementById('vnc-frame').contentWindow.postMessage(
                JSON.stringify({ type: 'setscale', value: val }), '*'
            );
        }
        function sendCtrlAltDel() {
            const frame = document.getElementById('vnc-frame');
            frame.contentWindow.postMessage(JSON.stringify({ type: 'key', keys: 'Delete' }), '*');
        }
        function sendCtrlAltF1() {
            const frame = document.getElementById('vnc-frame');
            frame.contentWindow.postMessage(JSON.stringify({ type: 'key', keys: 'F1' }), '*');
        }
        function toggleLog() {
            document.getElementById('log-panel').classList.toggle('open');
        }
        function fetchBootLog() {
            fetch('/boot-log')
                .then(r => r.text())
                .then(text => {
                    document.getElementById('log-content').textContent = text;
                    const panel = document.getElementById('log-content');
                    panel.scrollTop = panel.scrollHeight;
                })
                .catch(() => {});
        }
        setInterval(fetchBootLog, 3000);
    </script>
</body>
</html>
HTMLEOF

    log_ok "Custom viewer page created at $NOVNC_DIR/aion.html"
}

create_websocket_proxy() {
    log "Creating WebSocket proxy script..."

    cat > "$VM_DIR/websockify-proxy.sh" << 'PROXYEOF'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

VNC_PORT="${VNC_PORT:-1}"
WEBSOCKIFY_PORT="${WEBSOCKIFY_PORT:-6081}"
NOVNC_DIR="${NOVNC_DIR:-/opt/noVNC}"

VNC_TARGET="localhost:$((5900 + VNC_PORT))"

exec websockify \
    --web "$NOVNC_DIR" \
    "$WEBSOCKIFY_PORT" \
    "$VNC_TARGET"
PROXYEOF

    chmod +x "$VM_DIR/websockify-proxy.sh"
    log_ok "WebSocket proxy script created"
}

create_boot_log_server() {
    log "Creating boot log HTTP server..."

    cat > "$VM_DIR/boot-log-server.py" << 'PYEOF'
#!/usr/bin/env python3
import http.server
import json
import os
import signal
import sys
import threading
from pathlib import Path

SERIAL_LOG = os.environ.get("SERIAL_LOG", "")
WEBLOG_DIR = os.environ.get("WEBLOG_DIR", "/var/lib/aion-vm/logs/weblog")
PORT = int(os.environ.get("LOG_SERVER_PORT", "6082"))

class BootLogHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/boot-log":
            log_content = self._read_serial_log()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(log_content.encode())
        elif self.path == "/api/status":
            status = self._get_vm_status()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(status).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def _read_serial_log(self):
        if SERIAL_LOG and os.path.exists(SERIAL_LOG):
            try:
                with open(SERIAL_LOG, "r") as f:
                    lines = f.readlines()
                    return "".join(lines[-200:])
            except Exception:
                return "Unable to read serial log"
        return "Serial log not available"

    def _get_vm_status(self):
        pid_file = "/var/lib/aion-vm/qemu.pid"
        running = False
        pid = None
        if os.path.exists(pid_file):
            try:
                pid = int(Path(pid_file).read_text().strip())
                os.kill(pid, 0)
                running = True
            except (ValueError, ProcessLookupError, PermissionError):
                pass
        return {"vm_running": running, "pid": pid, "serial_log": SERIAL_LOG}

    def log_message(self, format, *args):
        pass

def run_server():
    server = http.server.HTTPServer(("0.0.0.0", PORT), BootLogHandler)
    print(f"Boot log server running on http://0.0.0.0:{PORT}")
    server.serve_forever()

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    run_server()
PYEOF

    chmod +x "$VM_DIR/boot-log-server.py"
    log_ok "Boot log server created"
}

launch_services() {
    log "Launching noVNC + websockify services..."

    pkill -f "websockify.*$WEBSOCKIFY_PORT" 2>/dev/null || true
    pkill -f "boot-log-server" 2>/dev/null || true
    sleep 1

    nohup python3 "$VM_DIR/boot-log-server.py" >> "$VM_DIR/logs/weblog-server.log" 2>&1 &
    echo $! > "$VM_DIR/logs/weblog-server.pid"
    log_ok "Boot log server started (PID: $(cat "$VM_DIR/logs/weblog-server.pid"))"

    nohup websockify \
        --web "$NOVNC_DIR" \
        --record \
        "$WEBSOCKIFY_PORT" \
        "localhost:$((5900 + VNC_PORT))" \
        >> "$VM_DIR/logs/websockify.log" 2>&1 &
    echo $! > "$VM_DIR/logs/websockify.pid"
    log_ok "websockify started (PID: $(cat "$VM_DIR/logs/websockify.pid"))"

    sleep 2
}

check_services() {
    log "Checking services..."

    local all_ok=true

    if kill -0 "$(cat "$VM_DIR/logs/websockify.pid" 2>/dev/null)" 2>/dev/null; then
        log_ok "websockify running on port $WEBSOCKIFY_PORT"
    else
        log_err "websockify not running"
        all_ok=false
    fi

    if kill -0 "$(cat "$VM_DIR/logs/weblog-server.pid" 2>/dev/null)" 2>/dev/null; then
        log_ok "boot log server running on port 6082"
    else
        log_err "boot log server not running"
        all_ok=false
    fi

    $all_ok
}

print_summary() {
    local ip
    ip=$(hostname -I | awk '{print $1}')
    [ -z "$ip" ] && ip="<server-ip>"

    echo ""
    echo "=============================================="
    echo "  Aion Cloud Viewer Ready"
    echo "=============================================="
    echo ""
    echo "  Browser URLs:"
    echo "    VNC Viewer:    http://${ip}:${WEBSOCKIFY_PORT}/aion.html"
    echo "    noVNC Default: http://${ip}:${WEBSOCKIFY_PORT}/vnc.html?autoconnect=true&path=websockify/?token=aion"
    echo "    Boot Log API:  http://${ip}:6082/api/status"
    echo "    Boot Log Raw:  http://${ip}:6082/boot-log"
    echo ""
    echo "  Open any URL above in your browser to view the Aion VM."
    echo "  The VNC Viewer page shows Gaming Mode, Calamares installer,"
    echo "  and boot sequence in real-time."
    echo ""
    echo "  Stop all services:"
    echo "    kill \$(cat $VM_DIR/logs/websockify.pid)"
    echo "    kill \$(cat $VM_DIR/logs/weblog-server.pid)"
    echo ""
    echo "=============================================="
}

main() {
    log "=== Aion noVNC Setup ==="
    install_websockify
    install_novnc
    create_novnc_wrapper
    create_websocket_proxy
    create_boot_log_server
    launch_services
    check_services
    print_summary
}

main "$@"
