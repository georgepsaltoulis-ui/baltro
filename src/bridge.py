"""
Talks to the ArrowBot app instead of running adb itself: no computer needed. The app listens on
127.0.0.1:47123 for this bot (inside the app, or in Termux on the same phone). It plays through
scrcpy over its own adb connection when the phone has Wireless debugging (as on a computer: video,
taps, screen off), else through Android's accessibility service (taps, swipes) and screen capture.
Each program has to be allowed once on the phone ("Allow" in a notification); the bot's random
token for that is kept in Temp/helper_token.txt (the bot inside the app has its own).

HelperLink offers the same calls as bot.py's ScrcpyLink (frames, tap, drag, pinch, display power),
so the bot plays the same way through either.
"""
import os
import secrets
import socket
import threading
import time

import cv2
import numpy as np

HOST, PORT = "127.0.0.1", 47123
HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "Temp", "helper_token.txt")
APK_URL = "https://github.com/georgepsaltoulis-ui/baltro/raw/HEAD/ArrowBot.apk"

SETUP_HELP = f"""[!] No Wi-Fi needed: the bot can play through the ArrowBot app instead (it has the bot
    inside too: with it you don't need Termux at all - just tap "Start bot" in the app).
    1. Download and install it (tap the link in your phone's browser; allow installing from it):
         {APK_URL}
    2. Open "ArrowBot", tap "Turn on in Accessibility" and turn "ArrowBot" on.
       If Android says the setting is restricted: Settings > Apps > ArrowBot > the menu at the
       top right > "Allow restricted settings", then try again.
    3. Come back here: the bot finds it by itself.
    Waiting for the helper app... (Ctrl+C to stop)"""

APPROVE_HELP = """[!] On the phone: tap "Allow" in the "Allow the Arrow bot to control this phone?" notification
    (or open ArrowBot and tap "Allow the waiting program")."""

CAPTURE_HELP = """[!] On the phone: allow screen capture - choose "A single app" > Arrows (then the screen can be
    black while the bot plays) and tap "Start" / "Start now"."""

# Frame layouts the app sends: bytes per pixel count, and the conversion to BGR
FORMATS = {
    "RGBA": (lambda w, h: w * h * 4, lambda b, w, h: cv2.cvtColor(b.reshape(h, w, 4), cv2.COLOR_RGBA2BGR)),
    "I420": (lambda w, h: w * h * 3 // 2, lambda b, w, h: cv2.cvtColor(b.reshape(h * 3 // 2, w), cv2.COLOR_YUV2BGR_I420)),
    "NV12": (lambda w, h: w * h * 3 // 2, lambda b, w, h: cv2.cvtColor(b.reshape(h * 3 // 2, w), cv2.COLOR_YUV2BGR_NV12)),
    "NV21": (lambda w, h: w * h * 3 // 2, lambda b, w, h: cv2.cvtColor(b.reshape(h * 3 // 2, w), cv2.COLOR_YUV2BGR_NV21)),
}


def _frame_head(head):
    """FRAME seq w h age [format] -> (seq, w, h, age_ms, format, bytes)."""
    seq, w, h, age = (int(v) for v in head[1:5])
    fmt = head[5] if len(head) > 5 else "RGBA"
    if fmt not in FORMATS:
        raise HelperError(f"unknown frame format {fmt}")
    return seq, w, h, age, fmt, FORMATS[fmt][0](w, h)


def to_bgr(buf, w, h, fmt):
    size = FORMATS[fmt][0](w, h)
    return FORMATS[fmt][1](np.frombuffer(buf, np.uint8, count=size), w, h)


class HelperError(Exception):
    pass


def available(timeout=1.0):
    """The helper app is installed, its accessibility service is on, and it answers."""
    try:
        with socket.create_connection((HOST, PORT), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b"PING\n")
            return s.recv(64).startswith(b"OK ArrowBotHelper")
    except OSError:
        return False


def _token():
    if os.environ.get("ARROWBOT_HELPER_TOKEN"):      # the bot inside the ArrowBot app: allowed already
        return os.environ["ARROWBOT_HELPER_TOKEN"]
    try:
        with open(TOKEN_FILE) as f:
            t = f.read().strip()
        if t:
            return t
    except OSError:
        pass
    t = secrets.token_hex(16)
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        f.write(t)
    return t


class _Conn:
    """One connection: one request at a time."""

    def __init__(self, timeout=15.0):
        self.sock = socket.create_connection((HOST, PORT), timeout=5)
        self.sock.settimeout(timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buf = b""

    def line(self):
        while b"\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise HelperError("the helper app closed the connection")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return line.decode("utf-8", "replace").strip()

    def exactly(self, n, into=None):
        out = bytearray(n) if into is None else into
        view = memoryview(out)
        have = min(n, len(self.buf))
        view[:have] = self.buf[:have]
        self.buf = self.buf[have:]
        while have < n:
            got = self.sock.recv_into(view[have:], n - have)
            if not got:
                raise HelperError("the helper app closed the connection")
            have += got
        return out

    def ask(self, *words):
        self.sock.sendall((" ".join(str(w) for w in words) + "\n").encode())
        return self.line()

    def hello(self, token, on_wait=None):
        reply = self.ask("HELLO", token)
        if reply == "WAIT":                       # first time: the phone asks the user
            if on_wait:
                on_wait()
            self.sock.settimeout(330)
            reply = self.line()
            self.sock.settimeout(15)
        if not reply.startswith("OK"):
            raise HelperError("not allowed on the phone" if reply == "DENIED" else reply)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Helper:
    """The helper app's commands. Thread-safe: each thread gets its own connection."""

    def __init__(self):
        self.token = _token()
        self.local = threading.local()
        self.awake = None

    def _conn(self):
        c = getattr(self.local, "conn", None)
        if c is None:
            c = _Conn()
            c.hello(self.token, on_wait=lambda: print(APPROVE_HELP))
            self.local.conn = c
        return c

    def call(self, *words):
        """Send one command; reconnects once if the connection broke. Returns the reply after OK."""
        for attempt in (0, 1):
            try:
                reply = self._conn().ask(*words)
                break
            except (OSError, HelperError):
                self._drop()
                if attempt:
                    raise
        if not reply.startswith("OK"):
            raise HelperError(f"{words[0]}: {reply}")
        return reply[2:].strip()

    def _drop(self):
        c = getattr(self.local, "conn", None)
        if c is not None:
            c.close()
            self.local.conn = None

    def connect(self):
        """Say hello (on the phone, the first time: "Allow?")."""
        self._conn()

    def info(self):
        w, h, cap = self.call("INFO").split()[:3]
        return int(w), int(h), cap == "1"

    def mode(self):
        """("adb", ...) = scrcpy over the app's own adb; ("capture", ...) = screen capture +
        accessibility; ("none", ...) = no frames yet. Second: the app's adb status in words."""
        try:
            words = self.call("MODE").split(None, 1)
        except HelperError:
            return "capture", ""           # (an older app)
        return words[0], words[1] if len(words) > 1 else ""

    def frame(self, after=0):
        """(seq, BGR image, age in s) of the newest frame newer than `after`, or None."""
        for attempt in (0, 1):
            try:
                c = self._conn()
                head = c.ask("FRAME", after, "YUV").split()
                if head[0] != "FRAME":
                    return None
                seq, w, h, age, fmt, n = _frame_head(head)
                return seq, to_bgr(c.exactly(n), w, h, fmt), age / 1000.0
            except (OSError, HelperError):
                self._drop()
                if attempt:
                    raise
        return None

    def tap(self, x, y, hold_ms=30):
        self.call("TAP", round(x), round(y), round(hold_ms))

    def drag(self, x0, y0, x1, y1, move_ms, hold_ms):
        self.call("DRAG", round(x0), round(y0), round(x1), round(y1), round(move_ms), round(hold_ms))

    def pinch(self, a0, b0, a1, b1, ms):
        self.call("PINCH", *(round(v) for p in (a0, b0, a1, b1) for v in p), round(ms))

    def back(self):
        self.call("BACK")

    def home(self):
        self.call("HOME")

    def launch(self, package):
        self.call("LAUNCH", package)

    def foreground(self):
        return self.call("FG")

    def status_bar(self):
        return self.call("STATUSBAR") == "1"

    def _awake_conn(self):
        if self.awake is None:
            self.awake = _Conn()
            self.awake.hello(self.token)
        return self.awake

    def keep_awake(self):
        """Screen stays on as long as this connection (thread) lasts: nothing to undo afterwards."""
        self._awake_conn().ask("AWAKE", 1)

    def screen(self, on):
        """Screen off / back on, on the same connection: when the bot ends (however it ends), the
        app turns it back on. Off: with adb the panel itself (like scrcpy), else a black overlay;
        the power button turns it on. Returns the app's answer ("OK off adb", "ERR why", ...)."""
        try:
            return self._awake_conn().ask("SCREEN", 1 if on else 0)
        except (OSError, HelperError) as e:
            return f"ERR {e}"

    def stop_requested(self):
        return self.call("POLL") == "stop"

    def state(self, text):
        self.call("STATE", text)

    def ensure_capture(self, wait=180):
        """Frames on: the app tries scrcpy over its adb first (a few seconds), else asks to allow
        screen capture on the phone. True when frames come."""
        if self.info()[2]:
            return True
        self.call("CAPTURE")
        start = time.time()
        told = False
        while time.time() < start + wait:
            time.sleep(1)
            if self.info()[2]:
                return True
            if not told and time.time() > start + 6:      # (no adb: Android is asking by now)
                print(CAPTURE_HELP)
                told = True
        return False


class HelperLink:
    """bot.py's ScrcpyLink calls, through the helper app: frames, tap, drag, pinch.

    Frames come the way scrcpy's video did: the app streams every new frame (up to max_fps; the
    latest again when the screen doesn't change), a thread here keeps receiving them, and only the
    frames the bot actually uses get converted to BGR when it asks for one (from YUV with adb, the
    decoder's own output; from RGBA with screen capture)."""
    POOL = 4                 # frame buffers: the newest, one or two being converted, one being filled

    def __init__(self, helper, max_fps=60, log=print):
        self.h = helper
        self.max_fps = max_fps
        self.log = log
        self.alive = False
        self.size = None
        self.cond = threading.Condition()
        self.latest = None    # (buffer, width, height, seq, format) of the newest frame
        self.frame_t = 0.0    # (about) when it was captured
        self.frames = 0
        self.in_use = {}      # id(buffer) -> conversions running on it (the reader leaves it alone)
        self.pool = []
        self.conn = None

    def start(self, timeout=5.0):
        if not self.h.ensure_capture():
            raise HelperError("screen capture wasn't allowed")
        w, h, _ = self.h.info()
        self.size = (w, h)
        self.conn = _Conn(timeout=10)
        self.conn.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
        self.conn.hello(self.h.token)
        self.conn.sock.sendall(f"STREAM {self.max_fps} YUV\n".encode())
        self.alive = True
        threading.Thread(target=self._receive, daemon=True).start()
        with self.cond:
            self.cond.wait_for(lambda: self.latest is not None or not self.alive, timeout=timeout)
        if self.latest is None:
            self.stop()
            raise HelperError("no frames from the helper app")
        return self

    def stop(self):
        self.alive = False
        if self.conn is not None:
            self.conn.close()
        with self.cond:
            self.cond.notify_all()

    def _free_buffer(self, n):
        with self.cond:
            busy = {id(self.latest[0])} if self.latest else set()
            busy |= {k for k, v in self.in_use.items() if v}
            for b in self.pool:
                if id(b) not in busy and len(b) >= n:
                    return b
            b = bytearray(n)
            self.pool = [p for p in self.pool if id(p) in busy or len(p) >= n][:self.POOL - 1] + [b]
            return b

    def _receive(self):
        try:
            while self.alive:
                head = self.conn.line().split()
                if not head or head[0] != "FRAME":
                    raise HelperError(" ".join(head) or "stream ended")
                seq, w, h, age, fmt, n = _frame_head(head)
                buf = self._free_buffer(n)
                self.conn.exactly(n, into=buf)
                t = time.time() - age / 1000.0
                with self.cond:
                    self.latest = (buf, w, h, seq, fmt)
                    self.size = (w, h)         # = tap coordinates (adb's video can differ a little)
                    self.frame_t = t
                    self.frames += 1
                    self.cond.notify_all()
        except Exception as e:
            if self.alive:
                self.log(f"    [helper] frames stopped: {e}")
        self.alive = False
        with self.cond:
            self.cond.notify_all()

    def next_frame(self, newer_than=0.0, timeout=1.0):
        """Newest frame captured after `newer_than` as BGR, plus (about) when it was captured."""
        with self.cond:
            ok = self.cond.wait_for(lambda: (self.latest is not None and self.frame_t > newer_than)
                                    or not self.alive, timeout=timeout)
            if not ok or not self.alive:
                return None, 0.0
            buf, w, h, _, fmt = self.latest
            t = self.frame_t
            self.in_use[id(buf)] = self.in_use.get(id(buf), 0) + 1
        try:
            return to_bgr(buf, w, h, fmt), t
        finally:
            with self.cond:
                self.in_use[id(buf)] -= 1

    def tap(self, x, y, hold=0.03):
        self.h.tap(x, y, hold * 1000)      # queued by the app: returns at once, like a scrcpy tap

    def drag(self, x0, y0, x1, y1, steps=10, step_time=0.012, hold=0.08):
        # moved at an even speed, then held still (the helper lifts the finger with no speed left)
        self.h.drag(x0, y0, x1, y1, max(100, steps * step_time * 1000), max(50, hold * 1000))

    def pinch(self, a0, b0, a1, b1, steps=15, step_time=0.012):
        self.h.pinch(a0, b0, a1, b1, max(400, steps * step_time * 1000))

    def display_power(self, on):
        return self.h.screen(on)
