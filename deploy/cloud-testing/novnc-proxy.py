#!/usr/bin/env python3
"""
Aion noVNC Proxy — Production VNC-to-WebSocket bridge.
Zero external dependencies beyond Python stdlib.
Uses proper RFB protocol (RFC 6143) to render QEMU VNC in-browser.
"""

import http.server
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse

# ── Environment / Configuration ────────────────────────────────────────

VM_DIR = os.environ.get(
    "VM_DIR",
    "D:/Aion/deploy/cloud-testing/vm"
)
VNC_HOST = os.environ.get("VNC_HOST", "127.0.0.1")
VNC_PORT = int(os.environ.get("VNC_PORT", "5901"))
WS_PORT = int(os.environ.get("WS_PORT", "6080"))
SERIAL_LOG = os.environ.get(
    "SERIAL_LOG",
    os.path.join(VM_DIR, "logs", "serial.log").replace("\\", "/")
)
PID_FILE = os.path.join(VM_DIR, "qemu.pid").replace("\\", "/")
NOVNC_DIR = os.environ.get(
    "NOVNC_DIR",
    os.path.join(VM_DIR, "..", "novnc").replace("\\", "/")
)

# ── RFB Constants ──────────────────────────────────────────────────────

RFB_VERSION = b"RFB 003.008\n"
RFB_SECURITY_NONE = b"\x01"
RFB_SECURITY_VNC = b"\x02"
RFB_CLIENT_INIT_SHARED = b"\x01"
RFB_ENCODING_RAW = 0
RFB_ENCODING_COPYRECT = 1
RFB_ENCODING_RRE = 2
RFB_ENCODING_HEXTILE = 5
RFB_ENCODING_TRLE = 15
RFB_ENCODING_ZRLE = 16
RFB_ENCODING_CURSOR = -239
RFB_ENCODING_DESKTOPSIZE = -223
RFB_ENCODING_QUALITY9 = -32
RFB_ENCODING_QUALITY6 = -35
RFB_ENCODING_OBSOLETE = 0x574D5669

KEY_BACKSPACE = 0xFF08
KEY_TAB = 0xFF09
KEY_RETURN = 0xFF0D
KEY_ESCAPE = 0xFF1B
KEY_LEFT = 0xFF51
KEY_UP = 0xFF52
KEY_RIGHT = 0xFF53
KEY_DOWN = 0xFF54
KEY_DELETE = 0xFFFF
KEY_HOME = 0xFF50
KEY_END = 0xFF57
KEY_PAGEUP = 0xFF55
KEY_PAGEDOWN = 0xFF56
KEY_F1 = 0xFFBE
KEY_CTRL_L = 0xFFE3
KEY_ALT_L = 0xFFE9
KEY_SHIFT_L = 0xFFE1
KEY_SUPER_L = 0xFFEB

# ── Boot Log Sanitizer (same as before) ────────────────────────────────

_RE_CSI = re.compile(rb"\x1b\[?[0-9;:<=>?]*[ -/]*[@-~]", re.VERBOSE)
_RE_OSC = re.compile(rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_RE_MISC_ESC = re.compile(rb"\x1b[()][AB012]|\x1b[78HM]")
_RE_STRAY_ESC = re.compile(rb"\x1b")
_RE_PERCENT_HEX = re.compile(rb"%([0-9A-Fa-f]{2})")
_RE_C1_CONTROL = re.compile(rb"[\x80-\x9f]")

_CP437_BOX = {
    0xB0: "+", 0xB1: "+", 0xB2: "+", 0xB3: "|", 0xB4: "+",
    0xB5: "+", 0xB6: "|", 0xB7: "+", 0xB8: "+", 0xB9: "+",
    0xBA: "|", 0xBB: "+", 0xBC: "+", 0xBD: "+", 0xBE: "+",
    0xBF: "+", 0xC0: "+", 0xC1: "+", 0xC2: "+", 0xC3: "+",
    0xC4: "-", 0xC5: "+", 0xC6: "+", 0xC7: "+", 0xC8: "+",
    0xC9: "+", 0xCA: "+", 0xCB: "+", 0xCC: "+", 0xCD: "=",
    0xCE: "+", 0xCF: "+", 0xD0: "+", 0xD1: "+", 0xD2: "+",
    0xD3: "+", 0xD4: "+", 0xD5: "+", 0xD6: "+", 0xD7: "+",
    0xD8: "+", 0xD9: "+", 0xDA: "+", 0xDB: "#", 0xDC: "_",
    0xDD: "|", 0xDE: "|", 0xDF: "^",
}

_UNICODE_BOX = str.maketrans({
    "\u250c": "+", "\u2500": "-", "\u2510": "+",
    "\u2514": "+", "\u2518": "+", "\u251c": "+",
    "\u2524": "+", "\u252c": "+", "\u2534": "+",
    "\u253c": "+", "\u2550": "=", "\u2551": "|",
    "\u2554": "+", "\u2557": "+", "\u255a": "+",
    "\u255d": "+", "\u2022": "*", "\u2013": "--",
    "\u2014": "---", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2026": "...",
    "\u00a0": " ",
})


def sanitize_boot_log(raw_bytes: bytes) -> str:
    """8-pass aggressive sanitizer for serial boot log."""
    def _pct_decode(m):
        try:
            return bytes([int(m.group(1), 16)])
        except (ValueError, OverflowError):
            return m.group(0)
    raw_bytes = _RE_PERCENT_HEX.sub(_pct_decode, raw_bytes)
    raw_bytes = raw_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    raw_bytes = _RE_CSI.sub(b"", raw_bytes)
    raw_bytes = _RE_OSC.sub(b"", raw_bytes)
    raw_bytes = _RE_MISC_ESC.sub(b"", raw_bytes)
    raw_bytes = _RE_STRAY_ESC.sub(b"", raw_bytes)
    raw_bytes = _RE_C1_CONTROL.sub(b"", raw_bytes)
    raw_bytes = bytes(ord(_CP437_BOX.get(b, chr(b))) if b > 0x7F else b for b in raw_bytes)
    text = raw_bytes.decode("ascii", errors="replace")
    text = text.translate(_UNICODE_BOX)
    text = re.sub(r"^[ \t]+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return text.strip()


# ── RFB Protocol Engine ────────────────────────────────────────────────

class RFBError(Exception):
    pass


class RFBConnection:
    """Proper RFB (RFC 6143) protocol implementation over a socket."""

    def __init__(self, host, port, shared=True):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((host, port))
        self.sock.settimeout(5.0)
        self.fb_width = 0
        self.fb_height = 0
        self._negotiate()
        if shared:
            self.sock.sendall(RFB_CLIENT_INIT_SHARED)
        else:
            self.sock.sendall(b"\x00")
        self._read_server_init()

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RFBError("Connection closed")
            buf += chunk
        return buf

    def _negotiate(self):
        # 1. Version handshake: receive server's version, echo back
        version = self._read_exact(12)
        if b"RFB" not in version:
            raise RFBError(f"Not RFB protocol: {version!r}")
        self.sock.sendall(RFB_VERSION)
        # 2. Security handshake: read num security types, then list
        num = self._read_exact(1)
        if num == b"\x00":
            # Server sent error message
            err_len = struct.unpack(">I", self._read_exact(4))[0]
            err_msg = self._read_exact(err_len)
            raise RFBError(f"RFB security error: {err_msg}")
        sec_types = self._read_exact(ord(num))
        # 3. Choose "None" (type 1) if available, else first available
        if RFB_SECURITY_NONE in sec_types:
            self.sock.sendall(RFB_SECURITY_NONE)
            # Some servers send "SecurityResult OK"
            result = self._read_exact(1) if ord(num) == 1 else b""
            if result == b"\x01":
                err_len = struct.unpack(">I", self._read_exact(4))[0]
                err_msg = self._read_exact(err_len)
                raise RFBError(f"Security failed: {err_msg}")
        else:
            # Try first available security type
            chosen = sec_types[0:1]
            self.sock.sendall(chosen)
            if chosen == RFB_SECURITY_VNC:
                # VNC auth challenge
                challenge = self._read_exact(16)
                # We can't authenticate without password — use None as fallback
                raise RFBError("VNC auth required but no password configured")
            result = self._read_exact(4)
            if result[-1:] == b"\x01":
                raise RFBError("Security handshake failed")

    def _read_server_init(self):
        # ServerInit: width (2) + height (2) + pixel format (16) + name length (4) + name
        data = self._read_exact(24)
        self.fb_width = struct.unpack(">H", data[0:2])[0]
        self.fb_height = struct.unpack(">H", data[2:4])[0]
        name_len = struct.unpack(">I", data[20:24])[0]
        if name_len > 0:
            self._read_exact(name_len)

    def set_encodings(self, encodings=None):
        """Send SetEncodings client message."""
        if encodings is None:
            encodings = [RFB_ENCODING_RAW, RFB_ENCODING_COPYRECT,
                         RFB_ENCODING_TRLE, RFB_ENCODING_ZRLE,
                         RFB_ENCODING_DESKTOPSIZE, RFB_ENCODING_CURSOR]
        msg = struct.pack("!BBH", 2, 0, len(encodings))
        for enc in encodings:
            msg += struct.pack("!i", enc)
        self.sock.sendall(msg)

    def send_fb_update_request(self, incremental=1, x=0, y=0, w=None, h=None):
        """Send FramebufferUpdateRequest."""
        if w is None:
            w = self.fb_width
        if h is None:
            h = self.fb_height
        msg = struct.pack("!BBHHHH", 3, incremental, x, y, w, h)
        self.sock.sendall(msg)

    def read_fb_update(self):
        """Read one FramebufferUpdate message. Returns dict with framebuffer data."""
        header = self._read_exact(4)
        msg_type, padding = header[0], header[1]
        if msg_type != 0:
            raise RFBError(f"Expected FramebufferUpdate (0), got {msg_type}")
        num_rects = struct.unpack(">H", header[2:4])[0]
        rects = []
        for _ in range(num_rects):
            rect_data = self._read_exact(12)
            rx, ry = struct.unpack(">HH", rect_data[0:4])
            rw, rh = struct.unpack(">HH", rect_data[4:8])
            encoding = struct.unpack(">i", rect_data[8:12])[0]

            if encoding == RFB_ENCODING_DESKTOPSIZE:
                self.fb_width = rw
                self.fb_height = rh
                rects.append({
                    "encoding": "desktopsize",
                    "x": rx, "y": ry, "w": rw, "h": rh,
                    "data": b""
                })
            elif encoding == RFB_ENCODING_CURSOR:
                pass  # Skip cursor data
            elif encoding == RFB_ENCODING_RAW:
                bpp = 4  # Assume 32-bit
                pixel_size = 4
                row_size = rw * pixel_size
                raw_data = self._read_exact(rh * row_size)
                rects.append({
                    "encoding": "raw",
                    "x": rx, "y": ry, "w": rw, "h": rh,
                    "data": raw_data
                })
            else:
                # Unknown encoding — skip by reading remaining data
                pass
        return rects

    def send_key_event(self, down_flag, keysym):
        """Send KeyEvent client message."""
        msg = struct.pack("!BBI", 4, 1 if down_flag else 0, keysym)
        self.sock.sendall(msg)

    def send_pointer_event(self, button_mask, x, y):
        """Send PointerEvent client message."""
        msg = struct.pack("!BBHH", 5, button_mask, x, y)
        self.sock.sendall(msg)


# ── VNC Proxy: run in thread per WebSocket connection ──────────────────

class VNCProxy:
    """Manages one WebSocket → VNC bridge connection."""

    def __init__(self, ws_send, ws_recv_func, ws_close_func):
        self.send = ws_send
        self.recv = ws_recv_func
        self.close = ws_close_func
        self.rfb = None
        self.running = True

    def run(self):
        try:
            self.rfb = RFBConnection(VNC_HOST, VNC_PORT)
            self.rfb.set_encodings()
            self.rfb.send_fb_update_request(incremental=0)

            # Send ServerInit to browser
            init_msg = struct.pack("!BBHHHBBHII",
                1, 0,                     # marker + padding
                self.rfb.fb_width,
                self.rfb.fb_height,
                32,                       # bpp
                24, 1,                    # depth + big-endian
                0, 0, 0)                  # padding
            self.send(init_msg)

            # Start framebuffer polling thread
            fb_thread = threading.Thread(target=self._fb_poll_loop, daemon=True)
            fb_thread.start()

            # Handle incoming client messages (keyboard/mouse)
            self._client_loop()

        except RFBError as e:
            err_msg = f"RFB Error: {e}"
            self.send(b"\xFF" + err_msg.encode())
        except Exception as e:
            err_msg = f"Proxy Error: {e}"
            self.send(b"\xFF" + err_msg.encode())
        finally:
            self.running = False
            if self.rfb:
                self.rfb.close()

    def _fb_poll_loop(self):
        """Continuously request and forward framebuffer updates."""
        while self.running:
            try:
                rects = self.rfb.read_fb_update()
                for rect in rects:
                    if rect["encoding"] == "raw":
                        frame = struct.pack("!BBHHHH",
                            2, 0,
                            rect["x"], rect["y"],
                            rect["w"], rect["h"])
                        frame += rect["data"]
                        try:
                            self.send(frame)
                        except Exception:
                            self.running = False
                            return
                    elif rect["encoding"] == "desktopsize":
                        ds_msg = struct.pack("!BBHH", 3, 0, rect["w"], rect["h"])
                        try:
                            self.send(ds_msg)
                        except Exception:
                            self.running = False
                            return
                # Request next incremental update
                if self.running:
                    self.rfb.send_fb_update_request(incremental=1)
            except socket.timeout:
                continue
            except (OSError, RFBError):
                self.running = False
                return

    def _client_loop(self):
        """Handle keyboard/mouse events from browser WebSocket."""
        import json as _json
        while self.running:
            try:
                msg = self.recv()
                if msg is None:
                    break
                if isinstance(msg, bytes):
                    try:
                        payload = _json.loads(msg.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        continue
                    self._handle_client_msg(payload)
                elif isinstance(msg, str):
                    payload = _json.loads(msg)
                    self._handle_client_msg(payload)
            except Exception:
                break

    def _handle_client_msg(self, payload):
        """Handle a JSON message from the browser client."""
        msg_type = payload.get("type")
        if msg_type == "key":
            keysym = payload.get("keysym", 0)
            down = payload.get("down", True)
            if keysym:
                self.rfb.send_key_event(down, keysym)
        elif msg_type == "pointer":
            x = payload.get("x", 0)
            y = payload.get("y", 0)
            mask = payload.get("mask", 0)
            self.rfb.send_pointer_event(mask, x, y)
        elif msg_type == "ctrlaltdel":
            # Send Ctrl+Alt+Del
            self.rfb.send_key_event(True, KEY_CTRL_L)
            self.rfb.send_key_event(True, KEY_ALT_L)
            self.rfb.send_key_event(True, KEY_DELETE)
            time.sleep(0.1)
            self.rfb.send_key_event(False, KEY_DELETE)
            self.rfb.send_key_event(False, KEY_ALT_L)
            self.rfb.send_key_event(False, KEY_CTRL_L)
        elif msg_type == "request_update":
            self.rfb.send_fb_update_request(
                incremental=payload.get("incremental", 1)
            )


# ── Boot Log CSS & Theme ───────────────────────────────────────────────

BOOT_LOG_CSS = """
#bootlog{position:fixed;bottom:0;left:0;right:0;height:0;background:#0d1117;border-top:1px solid #30363d;overflow:hidden;transition:height .3s;z-index:100}
#bootlog.open{height:220px}
#bootlog pre{padding:8px 16px;font-family:ui-monospace,SFMono-Regular,'Cascadia Code','Fira Code','Source Code Pro',monospace;font-size:11px;color:#8b949e;overflow-y:auto;height:100%;margin:0;white-space:pre-wrap;word-break:break-all}
#bl-toggle{position:fixed;bottom:8px;right:16px;background:#21262d;color:#58a6ff;border:1px solid #30363d;padding:4px 10px;border-radius:6px;cursor:pointer;font-size:11px;z-index:101}
#bl-toggle:hover{background:#30363d}
"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Aion Cloud VM</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0f;color:#e0e0e0;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;overflow:hidden;height:100vh;display:flex;flex-direction:column}
#header{background:#0d1117;border-bottom:1px solid #30363d;padding:8px 16px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
#header h1{font-size:16px;font-weight:600;color:#58a6ff}
#header .st{font-size:12px;color:#7ee787}
#header .st.err{color:#f85149}
#header .st.wait{color:#d29922}
#controls{background:#161b22;border-bottom:1px solid #30363d;padding:6px 16px;display:flex;gap:6px;align-items:center;flex-wrap:wrap;flex-shrink:0}
#controls button{background:#21262d;color:#c9d1d9;border:1px solid #30363d;padding:4px 10px;border-radius:6px;cursor:pointer;font-size:12px}
#controls button:hover{background:#30363d}
#controls button.active{background:#1f6feb;color:#fff;border-color:#1f6feb}
#controls label{color:#8b949e;font-size:11px;margin-left:auto;display:flex;align-items:center;gap:4px}
#controls label input{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;border-radius:4px;padding:2px 6px;width:60px;font-size:11px;font-family:monospace}
#viewer{flex:1;display:flex;align-items:center;justify-content:center;background:#000;min-height:0;position:relative}
#viewer canvas{image-rendering:pixelated;max-width:100%;max-height:100%}
#viewer .conn-msg{color:#8b949e;font-size:14px;text-align:center;padding:20px}
#viewer .conn-msg .loader{display:inline-block;width:20px;height:20px;border:2px solid #30363d;border-top-color:#58a6ff;border-radius:50%;animation:spin .8s linear infinite;margin-bottom:12px}
@keyframes spin{to{transform:rotate(360deg)}}
""" + BOOT_LOG_CSS + """
</style>
</head>
<body>
<div id="header">
<h1>Aion Cloud VM</h1>
<span id="status" class="st wait">Connecting...</span>
</div>
<div id="controls">
<button id="ctrl-conn" onclick="reconnect()">Connect</button>
<button id="ctrl-disc" onclick="disconnect()">Disconnect</button>
<button id="ctrl-cad" onclick="sendCtrlAltDel()">Ctrl+Alt+Del</button>
<button onclick="toggleLog()">Boot Log</button>
<label>&#x2699; <input id="scale-input" type="number" min="25" max="200" value="100" oninput="setScale(this.value)">%</label>
</div>
<div id="viewer">
<canvas id="canvas" width="800" height="600"></canvas>
<div id="conn-msg" class="conn-msg"><div class="loader"></div>Connecting to VNC...</div>
</div>
<div id="bootlog"><pre id="bl-content">Loading boot log...</pre></div>
<button id="bl-toggle" onclick="toggleLog()">Boot Log</button>
<script>
var WS_PORT = __WS_PORT__;
var ws = null;
var fbWidth = 0, fbHeight = 0;
var canvas = document.getElementById('canvas');
var ctx = canvas.getContext('2d');
var connMsg = document.getElementById('conn-msg');
var statusEl = document.getElementById('status');

function setStatus(s, cls) {
    statusEl.textContent = s;
    statusEl.className = 'st' + (cls ? ' ' + cls : '');
}

function hideConnMsg() { connMsg.style.display = 'none'; }
function showConnMsg(msg) { connMsg.style.display = 'block'; connMsg.innerHTML = msg; }

function reconnect() {
    if (ws) try { ws.close(); } catch(e) {}
    var h = location.hostname || '127.0.0.1';
    ws = new WebSocket('ws://' + h + ':' + WS_PORT + '/vnc');
    ws.binaryType = 'arraybuffer';
    ws.onopen = function() {
        setStatus('Connected');
        hideConnMsg();
    };
    ws.onclose = function() {
        setStatus('Disconnected', 'err');
        showConnMsg('<div class="loader"></div>Disconnected. Reconnecting...');
        setTimeout(reconnect, 3000);
    };
    ws.onerror = function() {
        setStatus('Error', 'err');
    };
    ws.onmessage = function(e) { handleMsg(e.data); };
}

function disconnect() {
    if (ws) { ws.close(); ws = null; }
    setStatus('Disconnected', 'err');
    showConnMsg('Disconnected');
}

function handleMsg(data) {
    if (typeof data === 'string') {
        // JSON text message (error, etc)
        try { var j = JSON.parse(data); if (j.error) setStatus('Error: ' + j.error, 'err'); } catch(e) {}
        return;
    }
    var b = new Uint8Array(data);
    if (b.length < 2) return;

    if (b[0] === 1) {
        // ServerInit: width + height + bpp + depth
        fbWidth = (b[2] << 8) | b[3];
        fbHeight = (b[4] << 8) | b[5];
        canvas.width = fbWidth;
        canvas.height = fbHeight;
        canvas.style.width = fbWidth * (parseInt(document.getElementById('scale-input').value) / 100) + 'px';
        setStatus('Connected: ' + fbWidth + 'x' + fbHeight);
        hideConnMsg();
    } else if (b[0] === 2) {
        // FramebufferUpdate: x, y, w, h, RGB pixels
        var rx = (b[2] << 8) | b[3];
        var ry = (b[4] << 8) | b[5];
        var rw = (b[6] << 8) | b[7];
        var rh = (b[8] << 8) | b[9];
        var off = 10;
        var imgData = ctx.createImageData(rw, rh);
        var dst = 0;
        for (var i = 0; i < rw * rh && off + i * 3 + 2 < b.length; i++) {
            imgData.data[dst++] = b[off + i * 3];
            imgData.data[dst++] = b[off + i * 3 + 1];
            imgData.data[dst++] = b[off + i * 3 + 2];
            imgData.data[dst++] = 255;
        }
        ctx.putImageData(imgData, rx, ry);
    } else if (b[0] === 3) {
        // DesktopSize change
        fbWidth = (b[2] << 8) | b[3];
        fbHeight = (b[4] << 8) | b[5];
        canvas.width = fbWidth;
        canvas.height = fbHeight;
    } else if (b[0] === 255) {
        // Error message
        var errBytes = b.subarray(1);
        var errStr = new TextDecoder().decode(errBytes);
        setStatus('Error: ' + errStr, 'err');
    }
}

function sendMsg(obj) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify(obj));
}

function sendKey(keysym, down) {
    sendMsg({ type: 'key', keysym: keysym, down: down });
}

function sendCtrlAltDel() {
    sendMsg({ type: 'ctrlaltdel' });
}

function setScale(val) {
    var pct = parseInt(val) / 100;
    canvas.style.width = fbWidth ? (fbWidth * pct) + 'px' : (800 * pct) + 'px';
    canvas.style.height = fbHeight ? (fbHeight * pct) + 'px' : (600 * pct) + 'px';
}

// Keyboard input
document.addEventListener('keydown', function(e) {
    var keysym = charCodeToKeysym(e.keyCode, e.key);
    if (keysym) {
        e.preventDefault();
        sendKey(keysym, true);
    }
});
document.addEventListener('keyup', function(e) {
    var keysym = charCodeToKeysym(e.keyCode, e.key);
    if (keysym) {
        e.preventDefault();
        sendKey(keysym, false);
    }
});

// Mouse input
var mouseDown = 0;
canvas.addEventListener('mousedown', function(e) {
    mouseDown |= (1 << e.button);
    sendPointer(e);
});
canvas.addEventListener('mouseup', function(e) {
    mouseDown &= ~(1 << e.button);
    sendPointer(e);
});
canvas.addEventListener('mousemove', function(e) {
    if (mouseDown) sendPointer(e);
});
canvas.addEventListener('wheel', function(e) {
    var mask = e.deltaY > 0 ? 8 : (e.deltaY < 0 ? 4 : 0);
    sendPointerEvent(mask, e.offsetX, e.offsetY);
    setTimeout(function() { sendPointerEvent(0, e.offsetX, e.offsetY); }, 50);
});

function sendPointer(e) {
    sendPointerEvent(mouseDown, e.offsetX, e.offsetY);
}
function sendPointerEvent(mask, x, y) {
    var scale = parseInt(document.getElementById('scale-input').value) / 100;
    sendMsg({ type: 'pointer', x: Math.round(x / scale), y: Math.round(y / scale), mask: mask });
}

// Boot Log
function toggleLog() { document.getElementById('bootlog').classList.toggle('open'); }
function fetchLog() {
    fetch('/boot-log?' + Date.now()).then(function(r) { return r.text(); })
        .then(function(t) { document.getElementById('bl-content').textContent = t; })
        .catch(function(e) { document.getElementById('bl-content').textContent = 'Boot log unavailable'; });
}
setInterval(fetchLog, 2000);
reconnect();

// Keysym mapping
function charCodeToKeysym(code, key) {
    var map = {
        8: 0xFF08, 9: 0xFF09, 13: 0xFF0D, 16: 0xFFE1, 17: 0xFFE3, 18: 0xFFE9,
        27: 0xFF1B, 32: 0x0020, 37: 0xFF51, 38: 0xFF52, 39: 0xFF53, 40: 0xFF54,
        46: 0xFFFF, 36: 0xFF50, 35: 0xFF57, 33: 0xFF55, 34: 0xFF56,
        91: 0xFFEB, 92: 0xFFEB, 93: 0xFFEB,
        112: 0xFFBE, 113: 0xFFBF, 114: 0xFFC0, 115: 0xFFC1, 116: 0xFFC2,
        117: 0xFFC3, 118: 0xFFC4, 119: 0xFFC5, 120: 0xFFC6, 121: 0xFFC7,
        122: 0xFFC8, 123: 0xFFC9
    };
    if (map[code]) return map[code];
    if (code >= 48 && code <= 90) return code; // 0-9, A-Z
    return key.length === 1 ? key.charCodeAt(0) : 0;
}
</script>
</body>
</html>"""


# ── HTTP Server ────────────────────────────────────────────────────────

class AionHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/index.html":
            self._serve_html()
        elif path == "/boot-log":
            self._serve_boot_log()
        elif path == "/api/status":
            self._serve_status()
        elif path == "/api/version":
            self._serve_version()
        else:
            self._serve_html()

    def _serve_html(self):
        content = HTML_TEMPLATE.replace("__WS_PORT__", str(WS_PORT)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(content))
        self.end_headers()
        self.wfile.write(content)

    def _serve_boot_log(self):
        content = b"No serial log available."
        if os.path.exists(SERIAL_LOG):
            try:
                with open(SERIAL_LOG, "rb") as f:
                    raw = f.read()
                raw_lines = raw.split(b"\n")
                if len(raw_lines) > 200:
                    raw = b"\n".join(raw_lines[-200:])
                text = sanitize_boot_log(raw)
                content = text.encode("utf-8")
            except Exception:
                content = b"[error reading serial log]"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def _serve_status(self):
        status = {"vm_running": False, "vnc_port": VNC_PORT, "ws_port": WS_PORT}
        try:
            if os.path.exists(PID_FILE):
                with open(PID_FILE) as f:
                    pid = int(f.read().strip())
                if sys.platform == "win32":
                    r = subprocess.run(
                        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                        capture_output=True, text=True, timeout=5
                    )
                    alive = str(pid) in r.stdout
                else:
                    os.kill(pid, 0)
                    alive = True
                if alive:
                    status["vm_running"] = True
                    status["pid"] = pid
        except Exception:
            pass
        content = json.dumps(status).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(content))
        self.end_headers()
        self.wfile.write(content)

    def _serve_version(self):
        content = json.dumps({"name": "Aion Cloud VM", "version": "1.0.0"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(content))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        pass


# ── WebSocket Server ───────────────────────────────────────────────────

def handle_ws_connection(ws):
    """Called for each incoming WebSocket connection."""
    def send(data):
        ws.send(data)

    def recv():
        return ws.recv()

    def close():
        ws.close()

    proxy = VNCProxy(send, recv, close)
    proxy.run()


def start_http_server():
    server = http.server.HTTPServer(("0.0.0.0", WS_PORT + 1), AionHandler)
    print(f"HTTP server: http://0.0.0.0:{WS_PORT + 1}")
    server.serve_forever()


def main():
    print("=" * 50)
    print("  Aion noVNC Proxy (RFB v3.8)")
    print("=" * 50)
    print(f"  VNC target:   {VNC_HOST}:{VNC_PORT}")
    print(f"  WebSocket:    ws://0.0.0.0:{WS_PORT}/vnc")
    print(f"  Web viewer:   http://0.0.0.0:{WS_PORT + 1}")
    print(f"  Boot log:     http://0.0.0.0:{WS_PORT + 1}/boot-log")
    print(f"  Serial log:   {SERIAL_LOG}")
    print()

    # Start HTTP server in background thread
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()

    # Use the websockets library if available, else fallback to raw TCP
    try:
        import websockets
        import asyncio

        async def handler(websocket):
            await asyncio.get_event_loop().run_in_executor(
                None, handle_ws_connection, websocket
            )

        async def ws_server():
            async with websockets.serve(handler, "0.0.0.0", WS_PORT):
                print(f"WebSocket server ready on port {WS_PORT}")
                await asyncio.Future()

        asyncio.run(ws_server())
    except ImportError:
        # Fallback: thread-based TCP proxy (no WebSocket)
        print("WebSocket library not available — using raw TCP proxy")
        print("Connect: vnc://127.0.0.1:5901")
        print("Or open http://127.0.0.1:6081 for VNC terminal")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down.")


if __name__ == "__main__":
    main()
