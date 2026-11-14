"""remote-screen: this computer's screen and microphone in a browser, from another device.

    python server.py             run (install.sh / install.ps1 start it at login)
    python server.py --url       print the viewer address with the access key in it
    python server.py --new-key   make a new access key (old links stop working)
    python server.py --join DIRECTORY_URL KEY --public-url MY_URL
                                 join a group: use its key, and check in with its directory

One page (static/) at http://<host>:<port>/ shows the screen live; with Control on, the mouse and
keyboard drive it; Listen plays the microphone. Everything except the page's own files needs the
access key (config.json, made on first run), except when opened on this computer itself (127.0.0.1, its own page). Listens on 127.0.0.1 unless config.json says otherwise.

Wire format, one WebSocket at /ws:
  client -> {"t":"hello","key"} then {"t":"view","mon"} / {"t":"ack"} (one per frame; the next frame waits
            for it) / {"t":"mic","on"} / {"t":"clipset","text"} (paste: put it on this clipboard) / input: {"t":"move","x","y"} {"t":"btn","b","down","x","y"}
            {"t":"wheel","dx","dy"} {"t":"key","code","key","down"} (x, y = 0..1 of the shown monitor)
  server -> {"t":"info",...} / {"t":"err","msg"} / {"t":"clip","text"} (text just copied here); binary: 0x01 + JPEG frame, 0x02 + 16-bit mono PCM
GET /shot?edge=N&mon=M with header X-Key -> one JPEG, longest side <= N. GET /screens -> {name, screens: [{w, h}]}.
Both answer other pages' fetches (CORS).

Devices: computers that share one key form a group. One of them is the directory: the others (config "directory" =
its address, "url" = their own address as the others reach it) check in every minute (POST /announce), and
GET /devices on the directory lists the ones seen in the last few minutes, itself included. Both need X-Key;
/devices also answers other pages' fetches (CORS), so a page that knows the key can show the list.
"""
import argparse
import asyncio
import hashlib
import io
import json
import logging
import logging.handlers
import os
import platform
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# settings + log: beside the code, except on macOS, where a program started at login may not read ~/Documents
DATA = Path.home() / "Library" / "Application Support" / "remote-screen" if sys.platform == "darwin" else HERE
CFG_PATH = DATA / "config.json"
STATIC = HERE / "static"
DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8760,
    "key": "",
    "id": "",                 # made on first run; tells devices apart in the directory
    "url": "",                # this computer's viewer address as other devices reach it (for the directory)
    "directory": "",          # the group's directory (a device's address); empty = none, or this one is it
    "name": "",               # shown in the viewer; default = this computer's name
    "fps": 8,
    "quality": 70,            # JPEG quality of the live view
    "max_width": 1920,        # live view is scaled down to this
    "control": True,          # allow mouse + keyboard
    "mic": True,              # allow Listen
    "mic_rate": 24000,
    "clipboard": True,        # copy/paste text both ways with the viewer (paste needs control too)
    "frame_ancestors": ["'self'", "http://127.0.0.1:*", "http://localhost:*"],   # pages that may embed the viewer
}
log = logging.getLogger("remote-screen")


# ---- config ----------------------------------------------------------------

def _read_saved():
    try:
        return json.loads(CFG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _save(saved):
    DATA.mkdir(parents=True, exist_ok=True)
    CFG_PATH.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(CFG_PATH, 0o600)
    except OSError:
        pass


def load_cfg(new_key=False):
    saved = _read_saved()
    if new_key or not saved.get("key") or not saved.get("id"):
        if new_key or not saved.get("key"):
            saved["key"] = secrets.token_urlsafe(24)
        saved["id"] = saved.get("id") or secrets.token_hex(6)
        _save(saved)
    cfg = dict(DEFAULTS)
    cfg.update(saved)
    cfg["name"] = cfg["name"] or socket.gethostname().split(".")[0]
    return cfg


# ---- screen ----------------------------------------------------------------

if sys.platform == "win32":
    try:   # physical pixels for both capture and mouse positions
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

_tl = threading.local()


def _sct():
    if not hasattr(_tl, "sct"):
        import mss
        _tl.sct = mss.mss()
    return _tl.sct


def monitors():
    """The screens, 1-based like mss ([0] there = all of them together)."""
    return _sct().monitors[1:]


def _monitor(i):
    mons = monitors()
    return mons[i - 1] if 1 <= i <= len(mons) else mons[0]


def grab(mon, max_edge=None, max_width=None, quality=80, last=None):
    """JPEG of one monitor, or None when its pixels equal `last` (a digest). Returns (jpeg, digest)."""
    from PIL import Image
    shot = _sct().grab(_monitor(mon))
    digest = hashlib.blake2b(shot.bgra, digest_size=16).digest()
    if last is not None and digest == last:
        return None, digest
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    w, h = img.size
    k = 1.0
    if max_width:
        k = min(k, max_width / w)
    if max_edge:
        k = min(k, max_edge / max(w, h))
    if k < 1:
        img = img.resize((max(1, round(w * k)), max(1, round(h * k))), Image.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue(), digest


# ---- mouse + keyboard ------------------------------------------------------

# ---- clipboard: text copied on this computer goes to the viewer ------------------

CLIP_MAX = 1 << 20   # longer text isn't sent


def clip_marker():
    """Something cheap that changes whenever the clipboard does (None = read the text to compare)."""
    if sys.platform == "win32":
        import ctypes
        return ctypes.windll.user32.GetClipboardSequenceNumber()
    return None


def clip_text():
    """The clipboard's text now, or None."""
    if sys.platform == "darwin":
        import subprocess
        r = subprocess.run(["pbpaste"], capture_output=True, timeout=3, env=dict(os.environ, LANG="en_US.UTF-8"))
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        u.GetClipboardData.restype = wintypes.HANDLE
        k.GlobalLock.argtypes = [wintypes.HGLOBAL]; k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        for _ in range(5):   # another program may hold it open for a moment
            if u.OpenClipboard(None):
                break
            time.sleep(0.05)
        else:
            return None
        try:
            h = u.GetClipboardData(13)   # CF_UNICODETEXT
            if not h:
                return None
            p = k.GlobalLock(h)
            try:
                return ctypes.wstring_at(p) if p else None
            finally:
                k.GlobalUnlock(h)
        finally:
            u.CloseClipboard()
    return None


def clip_set(text):
    """Put text on this computer's clipboard."""
    if sys.platform == "darwin":
        import subprocess
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=3, env=dict(os.environ, LANG="en_US.UTF-8"))
    elif sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        k.GlobalAlloc.restype = wintypes.HGLOBAL
        k.GlobalLock.argtypes = [wintypes.HGLOBAL]; k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]; u.SetClipboardData.restype = wintypes.HANDLE
        data = (text + "\0").encode("utf-16-le")
        for _ in range(5):
            if u.OpenClipboard(None):
                break
            time.sleep(0.05)
        else:
            raise OSError("clipboard busy")
        try:
            u.EmptyClipboard()
            h = k.GlobalAlloc(0x0002, len(data))   # GMEM_MOVEABLE; the clipboard owns it after SetClipboardData
            p = k.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            k.GlobalUnlock(h)
            if not u.SetClipboardData(13, h):   # CF_UNICODETEXT
                raise OSError("SetClipboardData failed")
        finally:
            u.CloseClipboard()


class Input:
    """Applies the viewer's mouse and keyboard to this computer; releases whatever is still held on close."""

    def __init__(self):
        from pynput import keyboard, mouse
        self.kb, self.ms = keyboard.Controller(), mouse.Controller()
        self.Key, self.KeyCode, self.Button = keyboard.Key, keyboard.KeyCode, mouse.Button
        self.keys, self.buttons = {}, set()

    def _at(self, mon, x, y):
        m = _monitor(mon)
        x = min(max(float(x), 0.0), 1.0)
        y = min(max(float(y), 0.0), 1.0)
        self.ms.position = (m["left"] + round(x * (m["width"] - 1)), m["top"] + round(y * (m["height"] - 1)))

    def apply(self, mon, d):
        t = d.get("t")
        if t == "move":
            self._at(mon, d["x"], d["y"])
        elif t == "btn":
            b = {0: self.Button.left, 1: self.Button.middle, 2: self.Button.right}.get(d.get("b"))
            if b is None:
                return
            if "x" in d:
                self._at(mon, d["x"], d["y"])
            if d.get("down"):
                self.ms.press(b); self.buttons.add(b)
            else:
                self.ms.release(b); self.buttons.discard(b)
        elif t == "wheel":
            dx, dy = int(d.get("dx", 0)), int(d.get("dy", 0))
            if dx or dy:
                self.ms.scroll(max(-10, min(10, dx)), max(-10, min(10, dy)))
        elif t == "key":
            k = self._key(d.get("code", ""), d.get("key", ""))
            if k is None:
                return
            code = d.get("code") or d.get("key")
            if d.get("down"):
                self.kb.press(k); self.keys[code] = k
            elif code in self.keys:
                self.kb.release(self.keys.pop(code))
            else:
                self.kb.release(k)

    _SPECIAL = {
        "Enter": "enter", "NumpadEnter": "enter", "Tab": "tab", "Backspace": "backspace", "Escape": "esc",
        "Delete": "delete", "Insert": "insert", "Home": "home", "End": "end", "PageUp": "page_up",
        "PageDown": "page_down", "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right",
        "ShiftLeft": "shift_l", "ShiftRight": "shift_r", "ControlLeft": "ctrl_l", "ControlRight": "ctrl_r",
        "AltLeft": "alt_l", "AltRight": "alt_r", "MetaLeft": "cmd_l", "MetaRight": "cmd_r",
        "CapsLock": "caps_lock", "Space": "space", "PrintScreen": "print_screen", "ContextMenu": "menu",
        "NumLock": "num_lock", "ScrollLock": "scroll_lock", "Pause": "pause",
    }
    _CHARS = {"Minus": "-", "Equal": "=", "BracketLeft": "[", "BracketRight": "]", "Backslash": "\\",
              "Semicolon": ";", "Quote": "'", "Comma": ",", "Period": ".", "Slash": "/", "Backquote": "`",
              "IntlBackslash": "\\", "NumpadDecimal": ".", "NumpadAdd": "+", "NumpadSubtract": "-",
              "NumpadMultiply": "*", "NumpadDivide": "/"}

    def _key(self, code, key):
        # by physical key, so held modifiers work as on a real keyboard (Ctrl+C = Ctrl + "c")
        if code.startswith("Key") and len(code) == 4:
            return self.KeyCode.from_char(code[3].lower())
        if code.startswith("Digit") and len(code) == 6:
            return self.KeyCode.from_char(code[5])
        if code.startswith("Numpad") and code[6:].isdigit():
            return self.KeyCode.from_char(code[6:])
        if code in self._CHARS:
            return self.KeyCode.from_char(self._CHARS[code])
        if code in self._SPECIAL:
            return getattr(self.Key, self._SPECIAL[code], None)
        if code.startswith("F") and code[1:].isdigit():
            return getattr(self.Key, code.lower(), None)
        if len(key) == 1:
            return self.KeyCode.from_char(key)
        return None

    def release_all(self):
        for k in list(self.keys.values()):
            try:
                self.kb.release(k)
            except Exception:
                pass
        for b in list(self.buttons):
            try:
                self.ms.release(b)
            except Exception:
                pass
        self.keys.clear(); self.buttons.clear()


# ---- microphone ------------------------------------------------------------

class Mic:
    """Default input device -> callback(bytes of 16-bit mono PCM), ~100 ms per call."""

    def __init__(self, rate, on_chunk):
        import sounddevice as sd
        try:
            self.stream = sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", blocksize=rate // 10,
                                            callback=lambda data, n, t, s: on_chunk(bytes(data)))
            self.rate = rate
        except Exception:
            rate = int(sd.query_devices(kind="input")["default_samplerate"])
            self.stream = sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", blocksize=rate // 10,
                                            callback=lambda data, n, t, s: on_chunk(bytes(data)))
            self.rate = rate
        self.stream.start()

    def close(self):
        try:
            self.stream.stop(); self.stream.close()
        except Exception:
            pass


# ---- macOS permissions -----------------------------------------------------

SETTINGS_PANE = {"screen": "Privacy_ScreenCapture", "control": "Privacy_Accessibility", "mic": "Privacy_Microphone"}


def _fw(name):
    import ctypes
    return ctypes.cdll.LoadLibrary("/System/Library/Frameworks/%s.framework/%s" % (name, name))


def mac_perms(ask=(), cfg=None):
    """{screen, control, mic}: True = allowed, False = not, None = never asked (mic only). macOS only, else {}.
    ask = the ones to request now: macOS shows its prompt the first time; after a no, it stays silent."""
    if sys.platform != "darwin":
        return {}
    import ctypes
    out = {}
    try:
        cg = _fw("CoreGraphics")
        cg.CGPreflightScreenCaptureAccess.restype = cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
        out["screen"] = bool(cg.CGPreflightScreenCaptureAccess())
        if "screen" in ask and not out["screen"]:
            cg.CGRequestScreenCaptureAccess()
    except Exception as e:
        log.warning("screen permission check failed: %s", e)
    try:
        ax, cf = _fw("ApplicationServices"), _fw("CoreFoundation")
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        out["control"] = bool(ax.AXIsProcessTrusted())
        if "control" in ask and not out["control"]:
            cf.CFDictionaryCreate.restype = ctypes.c_void_p
            cf.CFDictionaryCreate.argtypes = [ctypes.c_void_p] * 3 + [ctypes.c_long] + [ctypes.c_void_p] * 2
            keys = (ctypes.c_void_p * 1)(ctypes.c_void_p.in_dll(ax, "kAXTrustedCheckOptionPrompt").value)
            vals = (ctypes.c_void_p * 1)(ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue").value)
            opts = cf.CFDictionaryCreate(None, keys, vals, 1,
                                         ctypes.addressof(ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")),
                                         ctypes.addressof(ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryValueCallBacks")))
            ax.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
            ax.AXIsProcessTrustedWithOptions(opts)
            cf.CFRelease.argtypes = [ctypes.c_void_p]; cf.CFRelease(opts)
    except Exception as e:
        log.warning("accessibility check failed: %s", e)
    try:
        objc, av = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib"), _fw("AVFoundation")
        objc.objc_getClass.restype = objc.sel_registerName.restype = ctypes.c_void_p
        send = objc.objc_msgSend
        send.restype, send.argtypes = ctypes.c_long, [ctypes.c_void_p] * 3
        st = send(objc.objc_getClass(b"AVCaptureDevice"), objc.sel_registerName(b"authorizationStatusForMediaType:"),
                  ctypes.c_void_p.in_dll(av, "AVMediaTypeAudio").value)
        out["mic"] = None if st == 0 else st == 3
        if "mic" in ask and st == 0:   # opening the microphone is what makes macOS ask
            try:
                m = Mic(16000, lambda b: None); time.sleep(0.5); m.close()
            except Exception as e:
                log.warning("microphone open failed: %s", e)
    except Exception as e:
        log.warning("microphone check failed: %s", e)
    if cfg is not None:   # only what this computer allows at all
        out = {k: v for k, v in out.items() if k == "screen" or cfg.get(k)}
    return out


def mac_open_settings(which):
    import subprocess
    subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?" + SETTINGS_PANE[which]])


# ---- web -------------------------------------------------------------------

def build_app(cfg):
    from aiohttp import WSMsgType, web

    key = cfg["key"].encode()
    ok_key = lambda k: isinstance(k, str) and secrets.compare_digest(k.encode(), key)
    own = {"http://%s:%s" % (h, cfg["port"]) for h in ("127.0.0.1", "localhost", "[::1]")}

    def local(request):
        """Opened on this computer itself, by its own page: no key needed. Not through a proxy (that would be
        another device), and not from some other site's page in this computer's browser."""
        h = request.headers
        if request.remote not in ("127.0.0.1", "::1") or any(x in h for x in ("X-Forwarded-For", "Forwarded", "Tailscale-User-Login")):
            return False
        return h.get("Origin") in own or ("Origin" not in h and h.get("Sec-Fetch-Site", "same-origin") == "same-origin")
    csp = "frame-ancestors " + " ".join(cfg["frame_ancestors"])

    @web.middleware
    async def headers(request, handler):
        resp = await handler(request)
        resp.headers["Content-Security-Policy"] = csp
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    async def page(request):
        return web.FileResponse(STATIC / "index.html")

    async def static(request):
        f = STATIC / request.match_info["name"]
        if f.suffix not in (".js", ".css", ".svg") or not f.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(f)

    async def shot(request):   # also answers other pages' fetches (CORS): a page that knows the key can take shots
        if request.method == "OPTIONS":
            return cors(request, web.Response(status=204))
        if not (local(request) or ok_key(request.headers.get("X-Key"))):
            await asyncio.sleep(1)
            return cors(request, web.Response(status=403))
        edge = max(64, min(int(request.query.get("edge", 1568) or 1568), 8000))
        mon = int(request.query.get("mon", 1) or 1)
        jpg, _ = await asyncio.to_thread(grab, mon, edge, None, 88)
        return cors(request, web.Response(body=jpg, content_type="image/jpeg"))

    async def screens(request):   # {name, screens: [{w, h}]}, same rules as /shot
        if request.method == "OPTIONS":
            return cors(request, web.Response(status=204))
        if not (local(request) or ok_key(request.headers.get("X-Key"))):
            await asyncio.sleep(1)
            return cors(request, web.Response(status=403))
        mons = await asyncio.to_thread(monitors)
        return cors(request, web.json_response({"name": cfg["name"], "screens": [{"w": m["width"], "h": m["height"]} for m in mons]}))

    async def ws_handler(request):
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=1 << 16)
        await ws.prepare(request)
        try:
            first = await ws.receive_json(timeout=10)
        except Exception:
            await ws.close(); return ws
        if first.get("t") != "hello" or not (local(request) or ok_key(first.get("key"))):
            await asyncio.sleep(1)
            await ws.send_json({"t": "err", "msg": "bad key"})
            await ws.close(); return ws
        peer = request.headers.get("X-Forwarded-For") or request.remote
        log.info("viewer connected from %s", peer)
        await Session(ws, cfg).run()
        log.info("viewer left (%s)", peer)
        return ws

    seen = {}   # directory: id -> {id, name, url, os, seen}
    ONLINE = 150

    def me():
        return {"id": cfg["id"], "name": cfg["name"], "url": cfg["url"], "os": platform.system()}

    def cors(request, resp):
        resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", "*")
        resp.headers["Access-Control-Allow-Headers"] = "X-Key"
        resp.headers["Access-Control-Allow-Methods"] = "GET"
        resp.headers["Vary"] = "Origin"
        return resp

    async def announce(request):
        if not ok_key(request.headers.get("X-Key")):
            await asyncio.sleep(1)
            raise web.HTTPForbidden()
        d = await request.json()
        if not (isinstance(d.get("id"), str) and isinstance(d.get("url"), str) and d["url"].startswith(("http://", "https://"))):
            raise web.HTTPBadRequest()
        seen[d["id"][:40]] = {"id": d["id"][:40], "name": str(d.get("name") or "?")[:60], "url": d["url"][:300],
                              "os": str(d.get("os") or "")[:20], "seen": time.time()}
        return web.json_response({"ok": True})

    async def devices(request):
        if request.method == "OPTIONS":
            return cors(request, web.Response())
        if not ok_key(request.headers.get("X-Key")):
            await asyncio.sleep(1)
            return cors(request, web.Response(status=403))
        now = time.time()
        out = [dict(me(), seen=now)] if cfg["url"] else []
        out += [v for k, v in seen.items() if now - v["seen"] < ONLINE and k != cfg["id"]]
        out.sort(key=lambda v: v["name"].lower())
        return cors(request, web.json_response({"devices": out}))

    async def check_in(app):
        import aiohttp
        target = cfg["directory"].rstrip("/") + "/announce"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as http:
            while True:
                try:
                    async with http.post(target, json=me(), headers={"X-Key": cfg["key"]}) as r:
                        if r.status != 200:
                            log.warning("directory said %s", r.status)
                except Exception as e:
                    log.warning("directory unreachable: %s", e)
                await asyncio.sleep(60)

    async def start_check_in(app):
        if cfg["directory"] and cfg["url"] and cfg["directory"].rstrip("/") != cfg["url"].rstrip("/"):
            app["check_in"] = asyncio.ensure_future(check_in(app))

    app = web.Application(middlewares=[headers])
    app.on_startup.append(start_check_in)
    app.add_routes([web.post("/announce", announce), web.route("*", "/devices", devices),
                    web.get("/", page), web.get("/ws", ws_handler), web.route("*", "/shot", shot), web.route("*", "/screens", screens),
                    web.get("/{name:[a-z0-9_-]+\\.(?:js|css|svg)}", static)])
    return app


class Session:
    """One connected viewer."""

    def __init__(self, ws, cfg):
        self.ws, self.cfg = ws, cfg
        self.mon, self.viewing, self.last = 1, False, None
        self.acked = asyncio.Event(); self.acked.set()
        self.wake = asyncio.Event()
        self.input, self.mic = None, None
        self.loop = asyncio.get_running_loop()
        self.audio = asyncio.Queue(maxsize=20)
        self.pasted = None   # text the viewer just put on this clipboard: not sent back

    async def run(self):
        from aiohttp import WSMsgType
        mons = await asyncio.to_thread(monitors)
        await self.ws.send_json({"t": "info", "name": self.cfg["name"], "os": platform.system(),
                                 "monitors": [{"w": m["width"], "h": m["height"]} for m in mons],
                                 "control": bool(self.cfg["control"]), "mic": bool(self.cfg["mic"]),
                                 "clipboard": bool(self.cfg["clipboard"]),
                                 "perms": await asyncio.to_thread(mac_perms, (), self.cfg)})
        tasks = [asyncio.ensure_future(self.video()), asyncio.ensure_future(self.audio_out())]
        if self.cfg["clipboard"]:
            tasks.append(asyncio.ensure_future(self.clipboard()))
        try:
            async for msg in self.ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    d = json.loads(msg.data)
                    await self.on_message(d)
                except Exception as e:
                    log.warning("message failed: %s", e)
        finally:
            for t in tasks:
                t.cancel()
            if self.mic:
                self.mic.close()
            if self.input:
                await asyncio.to_thread(self.input.release_all)

    async def on_message(self, d):
        t = d.get("t")
        if t == "view":
            self.mon = int(d.get("mon", 1)); self.viewing = bool(d.get("on", True))
            self.last = None; self.acked.set(); self.wake.set()
        elif t == "ack":
            self.acked.set()
        elif t == "mic":
            await self.set_mic(bool(d.get("on")))
        elif t == "perms":   # {"t":"perms","ask":name}: ask again (macOS only prompts once, so also open Settings there)
            which = d.get("ask")
            if which in SETTINGS_PANE:
                before = await asyncio.to_thread(mac_perms, (), self.cfg)
                if before.get(which) is not None:   # asked before: the prompt won't come back
                    mac_open_settings(which)
                await asyncio.to_thread(mac_perms, (which,), self.cfg)
            await self.ws.send_json({"t": "perms", "perms": await asyncio.to_thread(mac_perms, (), self.cfg)})
        elif t == "clipset":   # the viewer pastes: its text goes on this clipboard before the V key arrives
            if self.cfg["clipboard"] and self.cfg["control"] and isinstance(d.get("text"), str) and len(d["text"]) <= CLIP_MAX:
                self.pasted = d["text"]
                await asyncio.to_thread(clip_set, d["text"])
        elif t == "restart":   # the login item starts it again; macOS applies new permissions on a fresh start
            log.info("restart asked by a viewer")
            await self.ws.close()
            asyncio.get_running_loop().call_later(0.3, os._exit, 0)
        elif t in ("move", "btn", "wheel", "key"):
            if not self.cfg["control"]:
                return
            if self.input is None:
                try:
                    self.input = await asyncio.to_thread(Input)
                except Exception as e:
                    self.cfg = dict(self.cfg, control=False)
                    await self.ws.send_json({"t": "err", "msg": "Control isn't available here: %s" % e})
                    return
            await asyncio.to_thread(self.input.apply, self.mon, d)

    async def set_mic(self, on):
        if on and not self.mic and self.cfg["mic"]:
            def chunk(b):
                def put():
                    if self.audio.full():
                        self.audio.get_nowait()
                    self.audio.put_nowait(b)
                self.loop.call_soon_threadsafe(put)
            try:
                self.mic = await asyncio.to_thread(Mic, int(self.cfg["mic_rate"]), chunk)
                await self.ws.send_json({"t": "mic", "on": True, "rate": self.mic.rate})
            except Exception as e:
                await self.ws.send_json({"t": "mic", "on": False, "msg": "No microphone: %s" % e})
        elif not on and self.mic:
            self.mic.close(); self.mic = None
            await self.ws.send_json({"t": "mic", "on": False})

    async def clipboard(self):
        """Every half second: new text on this computer's clipboard -> {"t":"clip","text"}. What was there when
        the viewer connected isn't sent, only what's copied after."""
        def read():
            return clip_marker(), clip_text()
        try:
            mark, text = await asyncio.to_thread(read)
        except Exception as e:
            log.warning("clipboard unavailable: %s", e); return
        while not self.ws.closed:
            await asyncio.sleep(0.5)
            try:
                m = await asyncio.to_thread(clip_marker)
                if m is not None and m == mark:
                    continue
                t = await asyncio.to_thread(clip_text)
            except Exception:
                continue
            mark = m
            if t and t != text and t != self.pasted and len(t) <= CLIP_MAX:
                await self.ws.send_json({"t": "clip", "text": t})
            text = t

    async def audio_out(self):
        while not self.ws.closed:
            b = await self.audio.get()
            await self.ws.send_bytes(b"\x02" + b)

    async def video(self):
        gap = 1.0 / max(1, float(self.cfg["fps"]))
        while not self.ws.closed:
            if not self.viewing:
                self.wake.clear(); await self.wake.wait(); continue
            t0 = time.monotonic()
            try:
                await asyncio.wait_for(self.acked.wait(), 10)
            except asyncio.TimeoutError:
                self.acked.set()   # a lost ack shouldn't freeze the view
            mon = self.mon
            try:
                jpg, digest = await asyncio.to_thread(grab, mon, None, self.cfg["max_width"], self.cfg["quality"], self.last)
            except Exception as e:
                log.warning("capture failed: %s", e)
                await asyncio.sleep(1); continue
            if jpg is not None and mon == self.mon:
                self.acked.clear()
                await self.ws.send_bytes(b"\x01" + jpg)
            self.last = digest
            await asyncio.sleep(max(0.0, gap - (time.monotonic() - t0)))


# ---- main ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="This computer's screen and microphone in a browser.")
    ap.add_argument("--url", action="store_true", help="print the viewer address with the key, then exit")
    ap.add_argument("--new-key", action="store_true", help="make a new access key, then exit")
    ap.add_argument("--join", nargs=2, metavar=("DIRECTORY_URL", "KEY"), help="join a group, then exit")
    ap.add_argument("--public-url", metavar="URL", help="this computer's viewer address as other devices reach it")
    a = ap.parse_args()
    if a.join or a.public_url:
        saved = _read_saved()
        if a.join:
            saved["directory"], saved["key"] = a.join
        if a.public_url:
            saved["url"] = a.public_url
        _save(saved)
        print("Saved. Restart it (run the installer again) to use it."); return
    cfg = load_cfg(new_key=a.new_key)
    url = "http://%s:%s/#key=%s" % ("127.0.0.1" if cfg["host"] in ("0.0.0.0", "::") else cfg["host"], cfg["port"], cfg["key"])
    if a.url or a.new_key:
        print(url); return

    h = logging.handlers.RotatingFileHandler(DATA / "remote-screen.log", maxBytes=1 << 20, backupCount=2, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[h] + ([logging.StreamHandler()] if sys.stderr else []))
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

    if sys.platform == "darwin":   # first run: macOS asks for each permission once
        mac_perms(ask=("screen", "control", "mic"), cfg=cfg)
    from aiohttp import web
    log.info("listening on %s:%s", cfg["host"], cfg["port"])
    web.run_app(build_app(cfg), host=cfg["host"], port=int(cfg["port"]), print=None, access_log=None)


if __name__ == "__main__":
    main()
