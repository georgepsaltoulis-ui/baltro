"""
Arrows bot - plays the Android game "Arrows" (com.arrow.out) on a USB-connected phone, or on the
phone itself in Android's Terminal app / Termux (no computer: it uses the phone's own Wireless
debugging, see onphone.py). Stop it with the "Arrow bot is playing" notification, Esc / q, or Ctrl+C.

    python bot.py                 play (asks whether to show the phone screen on this PC)
    python bot.py --show          play and show the phone screen in a window (view only)
    python bot.py --no-show       play without the window
    python bot.py --grab          cut new tap-when-seen button templates from a screenshot -> buttons/
    python bot.py --grab-ingame   cut new "this is a level" HUD markers -> ingame/
    python bot.py --test PNG      vision only, on a saved screenshot -> debug_vision.png

See HOW_IT_WORKS.md for how it all fits together.
"""
import atexit
import glob
import json
import math
import os
import random
import re
import socket
import struct
import subprocess
import threading
import time
import sys
import cv2
import numpy as np
import onphone
import bridge

ON_PHONE = onphone.ON_PHONE   # running on the phone itself (Termux) instead of a computer
# No adb at all: everything through the ArrowBot Helper app (bridge.py) - no Wi-Fi, no computer
BRIDGE_MODE = os.environ.get("ARROWBOT_BRIDGE") == "1"
helper = None                 # bridge.Helper in BRIDGE_MODE
HERE = os.path.dirname(os.path.abspath(__file__))   # templates live next to this file
TEMP = os.path.join(HERE, "Temp")   # everything the bot writes (log, debug picture, unknown screens)
os.makedirs(TEMP, exist_ok=True)

# -----------------------------------------------------------------------------
# EXACT SCREEN & PUZZLE REGION CALIBRATION (1080 x 2410)
# -----------------------------------------------------------------------------
SCREEN_W = 1080
SCREEN_H = 2410
ROI_Y1 = 0      # the board also shows above the level header
HEADER_BAND = (115, 290)  # screen rows hidden by the level header (+ HARD LEVEL tag): a blind spot
STATUS_BAR_BAND = (0, 290) # blind spot while Android's status bar is showing (its icons sit up there)
ROI_Y2 = 1975   # just above the power-up buttons (boards reach down to here)
ROI_X1 = 0
ROI_X2 = 1080

LINE_V_THRESH = 140      # arrows/lines are bright
MIN_LINE_BLOB = 60       # line blobs smaller than this (px) are dots/specks, not arrows
BOARD_V_THRESH = 50      # grid dots are dim but brighter than the background
EDGE_MARGIN = 20         # board bbox this far from the ROI edge => real board edge
HEAD_BORDER = 15         # (largest) margin from the view edge where a head may be clipped

def head_border(half):
    """Heads closer to the view edge than this may be cut off: about one arrowhead (~2.5 line
    widths), so arrows sitting near the edge of the board still get detected."""
    return int(min(HEAD_BORDER, max(6, half * 2.5)))
PAN_FRACTION = 0.55      # default pan: 55% of the view (same as the survey; 40% meant more pans)
SOLID_MAX_SAT = 60       # resting arrowheads S ~36; flying ones tint blue (S ~85); red/failed ~200
SOLID_MIN_VAL = 215      # real arrowheads are V ~250; fading ones are dimmer
TAP_COST = 0.15          # roughly how long one `input tap` takes on the phone
TAP_INTERVAL = 0.17      # pace when tapping through `input tap` (backup): just above TAP_COST
MIN_TAP_INTERVAL = 0.05  # 34ms lost ~15% of taps on a busy level (the game missed them): 50ms
TAP_MARGIN = 0.01        # added to the measured time a uiautomator2 tap takes (10ms)
TAP_QUEUE_MAX = 12       # verified arrows waiting for the tapper (re-checked every frame)
AGGRESSIVE_TAPS = True   # planned arrows only need resting arrows out of their path: arrows already
                         # flying out don't block. Turned off for a level if it ever costs a star.
SYNC_TAPS = False        # tests: tap immediately instead of on the background tapper
MAX_WAVES = 200          # plan everything visible that can escape (wave 2 = freed by wave 1, ...)
WAVE_RETAP_AFTER = 0.6   # planned arrow tapped but still in place after this => tap again
PLAN_IDLE_DROP = 1.0     # nothing in the plan became free for this long => re-plan from scratch
WAVE_MAX_TAPS = 3        # ...at most this many times, then the plan is dropped and rebuilt
MAX_TAPS_PER_ARROW = 4   # an arrow tapped this often without leaving is skipped for the level
RECENT_TAP_GUARD = 1.0   # don't let the normal (unplanned) path re-tap the same spot sooner
ZOOM_MAX_TRIES = 5       # pinches to try at level start before giving up
ZOOMED_IN_HALF = 5.75    # lines this thick after a pinch = still at the starting zoom (pinch ignored)
ZOOM_INTRO_WAIT = 0.8    # first pinch no sooner than this after a level appears (the intro ignores it)
ZOOM_CHECK_TIMEOUT = 0.4 # after a pinch, watch this long for the lines to get thinner
RECENTER_MIN_GAP = 150   # board touching one edge with this much empty space opposite => recenter

# Counters for --trial runs
STUCK_TIMEOUT = 15    # seconds without progress: levels => reset view; other screens => BACK once
STUCK_FRAME_WAIT = 0.3    # pause per "nothing clear and nothing to explore" frame (5 -> soft retry, 10 ->
                          # map redone); 0.5 made that 5s, while flying arrows clear in ~1s
MENU_BACK_AFTER = 10      # outside a level with nothing to tap: press BACK once after this long
MENU_RESTART_AFTER = 15   # ...and restart the app after this long (never with a level underneath)
LIMIT_MAX_MOVE = 30       # a pan that moves less than this (px)...
LIMIT_FRACTION = 0.25     # ...or less than this share of the drag means the camera is at its limit
SURVEY_FRACTION = 0.55    # survey pans move 55% of the view (45% overlap to measure the move)
PAN_MAX = 0.55            # no pan longer than this share of the view: on dense boards longer ones
                          # left too little overlap and got measured wrong (map off -> wasted pans)
PAN_MAX_TO_EDGE = 0.7     # ...except a pan that ends at a camera limit already known: one longer pan
                          # instead of a 55% pan + a short one just to get there (68 such pans/session)
LIMIT_REACHED_TOL = 30    # this close (px) to a known camera limit = at it (15 cost extra pans)
RELOC_MIN_INK = 1500      # line pixels needed on screen to measure a pan / relocate on the map
LIMIT_SNAP_MAX = 150      # back at a camera limit seen before: snap position to it if this close
LOW_LINE_INK = 6000       # fewer line pixels than this before/after a pan: measure with the dots
RELOC_MIN_SCORE = 0.6     # how well the view must match the map to trust a relocation
RELOC_NEAR = 500          # the regular re-check looks for the view within this many px of the pose
SETTLE_TIMEOUT = 2.0      # max seconds to wait for the view to stop moving (overscroll spring-back)
SETTLE_GAP = 0.1          # "stopped moving" = picture hasn't shifted over this long
POSE_CONFIRM_WAIT = 1.0   # after a pan measured unsure: wait up to this long for the map match first
SPRING_MIN = 12           # px the view must slide back after a pan to count as hitting the limit
RESTART_LOAD_WAIT = 8    # seconds to let the game load after relaunch
MAX_BLIND_PANS = 2        # while no arrows are visible: at most this many pans per direction
FUTILE_RESWEEP_AFTER = 4  # big board, already swept: this many pans in a row without a tap -> sweep
                          # it again (hunting the last arrows on a loose map went back and forth)
LEVEL_END_WAIT = 4.0     # no arrows + progress bar ~full: wait this long for the win screen first
BOARD_CLEARED_WAIT = 4.0 # board looks empty: wait this long for the win screen, then go looking
                         # (real game: win screen within 2.4s of 'looks finished' in 80 of 80 levels,
                         #  +1s grace before that; 5-6s here only delayed finding a last arrow)
NO_ARROWS_GRACE = 1.0    # in a level with no arrows in view: wait this long, then pan to search
DEBUG_IMAGE_EVERY = 1e9      # seconds between debug_vision.png updates
STATUS_BAR_CHECK_EVERY = 3.0  # seconds between status-bar checks (a heavy dumpsys on the phone)
FOREGROUND_CHECK_INTERVAL = 5  # seconds between "is the game still the open app?" checks

DIRS = {"RIGHT": (1, 0), "LEFT": (-1, 0), "BOTTOM": (0, 1), "TOP": (0, -1)}
OPPOSITE = {"LEFT": "RIGHT", "RIGHT": "LEFT", "TOP": "BOTTOM", "BOTTOM": "TOP"}

# Which game: "arrows" (Arrows, com.arrow.out) or "amaze" (Amaze GO!, com.oakever.arrows): the same
# puzzle - tap the arrows whose path out is clear - drawn differently (dark lines on a light board,
# its own buttons). Chosen with --game amaze or ARROWBOT_GAME=amaze (the ArrowBot app sets it).
# Any other app can be played with --package <app id> (ARROWBOT_GAME_PACKAGE): it gets Amaze GO!'s look
# unless it's Arrows itself.
if "--game" in sys.argv[:-1]:
    os.environ["ARROWBOT_GAME"] = sys.argv[sys.argv.index("--game") + 1]
if "--package" in sys.argv[:-1]:
    os.environ["ARROWBOT_GAME_PACKAGE"] = sys.argv[sys.argv.index("--package") + 1]
_package = os.environ.get("ARROWBOT_GAME_PACKAGE", "").strip()
GAME = os.environ.get("ARROWBOT_GAME", "").strip().lower() or (
    "amaze" if _package and _package != "com.arrow.out" else "arrows")
AMAZE = GAME == "amaze"
GAME_PACKAGE = _package or ("com.oakever.arrows" if AMAZE else "com.arrow.out")
SCREENSHOT_TIMEOUT = 5   # seconds before a hung screenshot is abandoned
CAPTURE_THREADS = 2      # screenshots taken in parallel (more fresh frames per second)
USE_SCRCPY = True        # live video + taps through scrcpy (./Scrcpy); falls back to screenshots
SCRCPY_MAX_DIFF = 10     # scrcpy frame must look like a real screenshot (median abs diff) to be used
SCRCPY_FPS = 60          # live video frame rate (60 = a frame every ~17ms: changes seen sooner;
                         # 30 saves some battery/USB load)
SCRCPY_BITRATE = 8_000_000  # video quality; lower = less battery/USB load
SCREEN_OFF = True        # True: the phone's screen goes off while the bot plays (saves battery; the
                         # game keeps running) and the power button turns it back on. False: the
                         # screen stays on the whole time, so you can watch the game. Either way
                         # its timeout is held off by keep_phone_awake().
SCRCPY_VERSION = "5.0"   # version of ./Scrcpy/scrcpy-server (asked from scrcpy.exe on Windows; the
                         # server only starts with its own version, so update this with the server)

# -----------------------------------------------------------------------------
# SCRCPY LINK: live video + instant touches through scrcpy's server (./Scrcpy/scrcpy-server)
#   Video: the phone streams the screen as H.264 (hardware encoded); PyAV decodes it here, a new
#   frame every ~33ms instead of a ~200ms screenshot. Touch: raw scrcpy control messages over a
#   socket (no `input` process, no RPC), with two fingers for pinches. Everything is optional: if
#   the server can't start or the stream dies, link.alive turns False and the bot falls back to
#   uiautomator2 screenshots/taps.
# -----------------------------------------------------------------------------
SCRCPY_DIR = os.path.join(HERE, "Scrcpy")
SERVER_LOCAL = os.path.join(SCRCPY_DIR, "scrcpy-server")
SERVER_REMOTE = "/data/local/tmp/scrcpy-server.jar"

# scrcpy control protocol (app/src/control_msg.h): INJECT_TOUCH_EVENT = 2
MSG_INJECT_TOUCH = 2
MSG_SET_DISPLAY_POWER = 10   # 2 bytes: type, on (1/0)
ACTION_DOWN, ACTION_UP, ACTION_MOVE = 0, 1, 2
POINTER_GENERIC_FINGER = -2


def _adb(*args, timeout=15):
    return subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout)


def scrcpy_version():
    """The server must be started with the exact version of the scrcpy it came with."""
    exe = os.path.join(SCRCPY_DIR, "scrcpy.exe")
    if os.name != "nt" or not os.path.exists(exe):
        return SCRCPY_VERSION   # scrcpy.exe only runs on Windows (on the phone only the server is used)
    out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout
    first = out.strip().splitlines()[0] if out.strip() else ""
    parts = first.split()
    if len(parts) >= 2 and parts[0] == "scrcpy":
        return parts[1]
    raise RuntimeError(f"can't read scrcpy version from: {first!r}")


class ScrcpyLink:
    def __init__(self, max_fps=30, bit_rate=8_000_000, log=print):
        self.log = log
        self.max_fps = max_fps
        self.bit_rate = bit_rate
        self.alive = False
        self.cond = threading.Condition()
        self.frame = None          # latest decoded av.VideoFrame (converted lazily)
        self.frame_t = 0.0
        self.frames = 0
        self.size = None           # (width, height) of the video = touch coordinate space
        self.video = None
        self.control = None
        self.server = None
        self.send_lock = threading.Lock()
        self.codec_lock = threading.Lock()  # decoder and frame conversion take turns

    # ------------------------------------------------------------------ start / stop
    def start(self, timeout=8.0):
        import av  # noqa: F401  (fail early if PyAV is missing)
        if not os.path.exists(SERVER_LOCAL):
            raise RuntimeError(f"missing {SERVER_LOCAL}")
        version = scrcpy_version()
        r = _adb("push", SERVER_LOCAL, SERVER_REMOTE)
        if r.returncode != 0:
            raise RuntimeError(f"adb push failed: {r.stderr.strip()}")
        scid = random.randint(1, 0x7FFFFFFF)
        name = f"scrcpy_{scid:08x}"
        port = random.randint(27200, 27900)
        r = _adb("forward", f"tcp:{port}", f"localabstract:{name}")
        if r.returncode != 0:
            raise RuntimeError(f"adb forward failed: {r.stderr.strip()}")
        self.port, self.name = port, name
        args = (f"CLASSPATH={SERVER_REMOTE} app_process / com.genymobile.scrcpy.Server {version} "
                f"scid={scid:08x} log_level=warn tunnel_forward=true audio=false control=true "
                f"raw_stream=true video_codec=h264 max_fps={self.max_fps} "
                f"video_bit_rate={self.bit_rate} cleanup=true")
        # no stay_awake / screen_off_timeout here: keep_phone_awake() owns those settings (scrcpy's
        # own restore could run after the bot's, or a video restart could save the bot's value as
        # "original", and leave the phone set to never sleep)
        self.server = subprocess.Popen(["adb", "shell", args], stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True)
        threading.Thread(target=self._server_log, daemon=True).start()

        # The forward tunnel accepts connections before the server listens; a too-early connection
        # just closes. Retry until the video stream actually delivers bytes.
        end = time.time() + timeout
        first = b""
        while time.time() < end:
            time.sleep(0.4)
            if self.server.poll() is not None:
                raise RuntimeError("scrcpy server exited at start (see log above)")
            try:
                v = socket.create_connection(("127.0.0.1", port), timeout=2)
                c = socket.create_connection(("127.0.0.1", port), timeout=2)
                v.settimeout(2.0)
                first = v.recv(1 << 16)
                if first:
                    self.video, self.control = v, c
                    break
                v.close(); c.close()
            except OSError:
                continue
        if self.video is None:
            self.stop()
            raise RuntimeError("no video from scrcpy server")
        self.video.settimeout(None)
        self.alive = True
        threading.Thread(target=self._decode_loop, args=(first,), daemon=True).start()
        threading.Thread(target=self._drain_control, daemon=True).start()
        # wait for the first decoded frame (tells us the touch coordinate size)
        with self.cond:
            self.cond.wait_for(lambda: self.frame is not None or not self.alive, timeout=5.0)
        if self.frame is None:
            self.stop()
            raise RuntimeError("scrcpy stream started but no frame could be decoded")
        return self

    def stop(self):
        self.alive = False
        for s in (self.video, self.control):
            try:
                if s:
                    s.close()
            except OSError:
                pass
        if self.server and self.server.poll() is None:
            self.server.kill()
        try:
            _adb("forward", "--remove", f"tcp:{self.port}", timeout=5)
        except Exception:
            pass

    def _server_log(self):
        for line in self.server.stdout:
            line = line.strip()
            if line:
                self.log(f"    [scrcpy-server] {line}")

    def _drain_control(self):
        # the device may send clipboard/ack messages back; never let that socket fill up
        try:
            while self.alive and self.control.recv(4096):
                pass
        except OSError:
            pass

    # ------------------------------------------------------------------ video
    def _decode_loop(self, first):
        import av
        codec = av.CodecContext.create("h264", "r")
        data = first
        try:
            while self.alive:
                if data:
                    with self.codec_lock:   # never decode while a frame is being converted (crashed once)
                        for packet in codec.parse(data):
                            for frame in codec.decode(packet):
                                with self.cond:
                                    self.frame = frame
                                    self.frame_t = time.time()
                                    self.frames += 1
                                    self.size = (frame.width, frame.height)
                                    self.cond.notify_all()
                data = self.video.recv(1 << 17)
                if not data:
                    break
        except Exception as e:
            self.log(f"    [scrcpy] video stopped: {e}")
        self.alive = False
        with self.cond:
            self.cond.notify_all()

    def next_frame(self, newer_than=0.0, timeout=1.0):
        """Newest frame received after `newer_than` as a BGR numpy array, plus its time."""
        with self.cond:
            ok = self.cond.wait_for(lambda: (self.frame is not None and self.frame_t > newer_than)
                                    or not self.alive, timeout=timeout)
            if not ok or not self.alive:
                return None, 0.0
            frame, t = self.frame, self.frame_t
        # convert only the frames actually used (decoding keeps up at 60fps, converting all wouldn't)
        with self.codec_lock:
            return frame.to_ndarray(format="bgr24"), t

    # ------------------------------------------------------------------ touch
    def _touch(self, action, x, y, pointer=POINTER_GENERIC_FINGER):
        w, h = self.size
        pressure = 0xFFFF if action != ACTION_UP else 0
        msg = struct.pack(">BBqiiHHHII", MSG_INJECT_TOUCH, action, pointer,
                          int(x), int(y), w, h, pressure, 0, 0)
        with self.send_lock:
            self.control.sendall(msg)

    def drag(self, x0, y0, x1, y1, steps=10, step_time=0.012, hold=0.08):
        """One finger: down, move (slowing down toward the end), hold still (no fling), up."""
        self._touch(ACTION_DOWN, x0, y0)
        for i in range(1, steps + 1):
            time.sleep(step_time)
            f = 1 - (1 - i / steps) ** 2      # ease out: release speed ~0, so the game doesn't fling
            self._touch(ACTION_MOVE, x0 + (x1 - x0) * f, y0 + (y1 - y0) * f)
        time.sleep(hold)
        self._touch(ACTION_UP, x1, y1)

    def pinch(self, a0, b0, a1, b1, steps=15, step_time=0.012):
        """Two fingers from a0/b0 to a1/b1 (the server turns the 2nd DOWN into POINTER_DOWN)."""
        pa, pb = 10, 11
        self._touch(ACTION_DOWN, *a0, pointer=pa)
        self._touch(ACTION_DOWN, *b0, pointer=pb)
        for i in range(1, steps + 1):
            time.sleep(step_time)
            f = i / steps
            self._touch(ACTION_MOVE, a0[0] + (a1[0] - a0[0]) * f, a0[1] + (a1[1] - a0[1]) * f, pointer=pa)
            self._touch(ACTION_MOVE, b0[0] + (b1[0] - b0[0]) * f, b0[1] + (b1[1] - b0[1]) * f, pointer=pb)
        time.sleep(0.05)
        self._touch(ACTION_UP, *b1, pointer=pb)
        self._touch(ACTION_UP, *a1, pointer=pa)

    def display_power(self, on):
        """Turn the phone's screen panel on/off. Apps keep running (and streaming) with it off:
        the screen is the biggest battery drain."""
        with self.send_lock:
            self.control.sendall(struct.pack(">BB", MSG_SET_DISPLAY_POWER, 1 if on else 0))

    def tap(self, x, y, hold=0.03):
        """Finger down, short hold (so the game sees it in its own frame), finger up."""
        self._touch(ACTION_DOWN, x, y)
        time.sleep(hold)
        self._touch(ACTION_UP, x, y)

stopping = False         # stop_bot() is shutting everything down (no more complaints about it)
link = None              # ScrcpyLink when the fast path is up
link_video = False       # use its video frames (checked against a real screenshot first)
GESTURE_TIMEOUT = 8      # seconds before a hung zoom/pan is abandoned

class _Tee:
    """Everything printed also goes to bot.log with a timestamp (to see later why something happened)."""
    MAX_BYTES = 20 * 2**20   # rotate at 20MB (keeps one old copy as bot.log.1)

    def __init__(self, stream, path=os.path.join(TEMP, "bot.log")):
        self.stream, self.path, self.at_line_start = stream, path, True
        self.file = open(path, "a", encoding="utf-8")

    def write(self, text):
        self.stream.write(text)
        out = []
        for chunk in text.splitlines(keepends=True):
            if self.at_line_start and chunk.strip():
                now = time.time()
                out.append(time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now % 1 * 1000):03d} " + chunk)
            else:
                out.append(chunk)
            self.at_line_start = chunk.endswith("\n")
        self.file.write("".join(out))
        self.file.flush()
        if self.file.tell() > self.MAX_BYTES:
            self.file.close()
            try:
                os.replace(self.path, self.path + ".1")
            except OSError:
                pass
            self.file = open(self.path, "a", encoding="utf-8")

    def flush(self):
        self.stream.flush()

# Device handles, set up in connect()
d = None
adb_shell = None

# -----------------------------------------------------------------------------
# 1. HYBRID DEVICE CONNECTIONS (every device call has a timeout so nothing hangs forever)
# -----------------------------------------------------------------------------
def disable_quick_edit():
    """A click in a Windows console window pauses the script until a key is pressed. Turn that off."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ENABLE_QUICK_EDIT_MODE, ENABLE_EXTENDED_FLAGS = 0x0040, 0x0080
            kernel32.SetConsoleMode(handle, (mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS)
    except Exception:
        pass

_console_handler = None   # kept referenced: Windows calls it from its own thread

def on_console_close(fn):
    """Run fn when the console window is closed / the user logs off (atexit doesn't run then;
    Windows allows a few seconds before it kills the process)."""
    global _console_handler
    if os.name != "nt":
        return
    try:
        import ctypes
        @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
        def handler(event):
            if event in (2, 5, 6):   # CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT
                fn()
            return 0                 # let the default handling (exit / KeyboardInterrupt) go on
        _console_handler = handler
        ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)
    except Exception:
        pass

def run_with_timeout(fn, timeout):
    """Run fn() in a daemon thread. Returns (finished, value, error)."""
    result = {}
    def worker():
        try:
            result["value"] = fn()
        except Exception as e:
            result["error"] = e
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return False, None, None
    return True, result.get("value"), result.get("error")

def connect_u2():
    global d
    import uiautomator2 as u2
    # on the phone: 127.0.0.1:PORT, its own wireless debugging (connect_usb: older uiautomator2
    # versions would take an IP given to connect() for their WiFi agent)
    serial = os.environ.get("ANDROID_SERIAL")
    finished, value, error = run_with_timeout(lambda: u2.connect_usb(serial) if serial else u2.connect(), 30)
    if not finished or error is not None:
        print(f"[-] Could not connect via uiautomator2: {error or 'timed out'}")
        return False
    d = value
    return True

def device_call(fn, timeout, what):
    """Call a uiautomator2 function with a timeout; reconnect if it hangs. Returns None on failure."""
    finished, value, error = run_with_timeout(fn, timeout)
    if not finished:
        print(f"[!] {what} hung for {timeout}s. Reconnecting to the phone...")
        connect_u2()
        return None
    if error is not None:
        print(f"[-] {what} failed: {error}")
        return None
    return value

def start_adb_shell():
    global adb_shell
    try:
        if adb_shell is not None:
            adb_shell.kill()
    except Exception:
        pass
    adb_shell = subprocess.Popen(
        ["adb", "shell"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        universal_newlines=True,
        bufsize=1
    )

def adb_input(cmd):
    """Send an `input ...` command through the persistent shell, restarting it if it died."""
    if BRIDGE_MODE:
        helper_input(cmd)
        return
    for _ in range(2):
        try:
            if adb_shell is None or adb_shell.poll() is not None:
                print("[!] ADB shell died, restarting it.")
                start_adb_shell()
            adb_shell.stdin.write(cmd + "\n")
            adb_shell.stdin.flush()
            return
        except (OSError, ValueError):
            start_adb_shell()

def helper_input(cmd):
    """BRIDGE_MODE: `input keyevent KEYCODE_BACK` / `input tap X Y` / `input swipe X0 Y0 X1 Y1 MS`."""
    a = cmd.split()
    try:
        if a[1:3] == ["keyevent", "KEYCODE_BACK"]:
            helper.back()
        elif a[1] == "tap":
            helper.tap(float(a[2]), float(a[3]))
        elif a[1] == "swipe":
            helper.drag(*(float(v) for v in a[2:6]), float(a[6]) if len(a) > 6 else 300, 100)
        else:
            print(f"[-] The helper app can't do: {cmd}")
    except Exception as e:
        print(f"[-] {cmd} through the helper app failed: {e}")

def adb_run(args, timeout=10):
    try:
        return subprocess.run(["adb"] + args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception as e:
        print(f"[-] adb {' '.join(args)} failed: {e}")
        return ""

# The phone must not sleep / lock while the bot plays (its screen timeout would run out and pause
# the game). Two Android settings are changed only while the bot runs: "stay awake" (screen on while
# charging) and the screen timeout itself (keeps the screen on on battery too: on the phone itself
# nothing has to be plugged in). The values found at start are saved to files and put back when the
# bot stops (the supervisor does it, so crash restarts don't). If the bot was killed before it could
# restore, the next start finds the files and restores / reuses those original values.
AWAKE_SETTINGS = [   # (namespace, name, value while the bot runs, Android default, file with the original)
    ("global", "stay_on_while_plugged_in", "7", "0",            # 7 = on with any power source
     os.path.join(TEMP, "stay_awake_original.txt")),
    ("system", "screen_off_timeout", "2147483647", "30000",     # ms: ~24 days, i.e. never
     os.path.join(TEMP, "screen_timeout_original.txt")),
]

def _setting_value(namespace, name):
    out = adb_run(["shell", "settings", "get", namespace, name], timeout=5).strip()
    return out if out.isdigit() else None

def keep_phone_awake():
    """Keep the screen on for as long as the bot runs (original values saved once)."""
    if BRIDGE_MODE:
        helper.keep_awake()        # an overlay of the helper app, gone when the bot's connection is
        return
    for namespace, name, value, _, path in AWAKE_SETTINGS:
        if not os.path.exists(path):
            orig = _setting_value(namespace, name)
            if orig is None:
                print(f"[-] Couldn't read the phone's {name} setting; not changing it.")
                continue
            with open(path, "w") as f:
                f.write(orig)
        adb_run(["shell", "settings", "put", namespace, name, value], timeout=5)

def restore_phone_sleep():
    """Put the phone's stay-awake and screen timeout settings back to what they were before."""
    if BRIDGE_MODE:
        return                     # (no settings were changed)
    if ON_PHONE and any(os.path.exists(s[-1]) for s in AWAKE_SETTINGS):
        serial, _ = onphone.try_connect("adb")   # wireless debugging may have a new port by now
        if serial:
            os.environ["ANDROID_SERIAL"] = serial
    for namespace, name, _, default, path in AWAKE_SETTINGS:
        try:
            with open(path) as f:
                orig = f.read().strip()
        except OSError:
            continue
        if not orig.isdigit():
            orig = default
        adb_run(["shell", "settings", "put", namespace, name, orig], timeout=5)
        if _setting_value(namespace, name) == orig:
            os.remove(path)
            print(f"[*] Phone screen timeout back to normal ({name} = {orig}).")
        else:
            print(f"[-] Couldn't restore the phone's {name} setting (wanted {orig}); "
                  f"run: adb shell settings put {namespace} {name} {orig}")

def wait_for_phone():
    """Don't give up if the phone isn't plugged in (or USB debugging isn't allowed yet): wait."""
    if ON_PHONE:
        # no cable: Termux's adb talks to this phone's wireless debugging (its port changes
        # whenever wireless debugging is switched on again, so it's looked up every time)
        onphone.wait_until_connected("adb")
        return
    told = False
    while True:
        out = adb_run(["devices"])
        if any(l.strip().endswith("	device") for l in out.splitlines()[1:]):
            return
        if not told:
            print("[*] Waiting for the phone: plug it in by USB (and allow USB debugging if asked)...")
            told = True
        time.sleep(2)

def phone_locked():
    out = adb_run(["shell", "dumpsys", "window"])
    return re.search(r"(mKeyguardShowing|isKeyguardShowing|mShowingLockscreen|mDreamingLockscreen)=true",
                     out) is not None

def wake_and_open_game():
    """At start: screen on, lock screen out of the way, the game open. A PIN / pattern / fingerprint
    lock can't (and shouldn't) be opened by the bot: it waits for you to unlock."""
    if BRIDGE_MODE:                # (the phone is in use: it's where the bot was just started)
        if game_in_foreground():
            print("[+] The game is already open.")
            return
        print(f"[*] Opening {GAME_PACKAGE}...")
        launch_game()
        end = time.time() + 20
        while time.time() < end and not game_in_foreground():
            time.sleep(1)
        time.sleep(3)
        return
    if "mWakefulness=Awake" not in adb_run(["shell", "dumpsys", "power"]):
        print("[*] Waking the phone...")
        adb_run(["shell", "input", "keyevent", "KEYCODE_WAKEUP"])
        time.sleep(0.6)
    if phone_locked():
        adb_run(["shell", "wm", "dismiss-keyguard"])            # swipe-only lock screens
        time.sleep(0.8)
        if phone_locked():
            adb_run(["shell", "input", "swipe", "540", "1900", "540", "700", "250"])
            time.sleep(0.8)
    if phone_locked():
        print("[!] The phone is locked (PIN / pattern / fingerprint): unlock it, the bot carries on by itself.")
        while phone_locked():
            time.sleep(2)
        print("[+] Unlocked.")
    if game_in_foreground():
        print("[+] The game is already open.")
        return
    print(f"[*] Opening {GAME_PACKAGE}...")
    launch_game()
    end = time.time() + 20
    while time.time() < end and not game_in_foreground():
        time.sleep(1)
    time.sleep(3)                                              # let it finish loading

def connect_helper():
    """BRIDGE_MODE: no adb, no uiautomator2, no scrcpy: everything through the ArrowBot Helper app."""
    global helper
    told = False
    while not bridge.available():
        if not told:
            print(bridge.SETUP_HELP)
            told = True
        time.sleep(2)
    print("[*] Connecting to the ArrowBot app (taps + screen)...")
    helper = bridge.Helper()
    helper.connect()                 # the first time, the phone asks "Allow?"
    keep_phone_awake()
    start_scrcpy()                   # (asks to allow screen capture: before the game covers this)
    wake_and_open_game()
    if SCREEN_OFF:
        bridge_screen_off()

def bridge_screen_off():
    """BRIDGE_MODE: the screen off while the bot plays, once the game is open. With adb the app
    turns the panel off like scrcpy does on a computer; without, it covers the screen with black.
    The power button turns it back on (and then it stays on)."""
    reply = helper.screen(False)
    if reply.startswith("OK off"):
        print("[*] Screen off while the bot plays (the game keeps running). Press the power button to turn it on.")
    elif reply.startswith("OK on setting"):
        print("[*] The screen stays on while the bot plays (ArrowBot's setting).")
    elif not reply.startswith("OK"):
        print(f"[-] The screen stays on: {reply[4:] if reply.startswith('ERR ') else reply}")

def connect():
    if BRIDGE_MODE:
        connect_helper()
        return
    wait_for_phone()
    print("[*] Connecting to device via uiautomator2 (for zooming/screenshots)...")
    if not connect_u2():
        sys.exit(1)
    print(f"[+] Connected to {d.serial}")

    print("[*] Establishing persistent ADB shell (for instant taps)...")
    try:
        start_adb_shell()
    except Exception:
        print("[-] Failed to start ADB shell.")
        sys.exit(1)
    keep_phone_awake()
    wake_and_open_game()
    if USE_SCRCPY:
        start_scrcpy()

def start_scrcpy():
    """Live screen + taps through scrcpy. Its picture is checked against a real screenshot first
    (video compression must not change what the bot sees); anything wrong -> old path."""
    global link, link_video
    if BRIDGE_MODE:
        try:
            link = bridge.HelperLink(helper, max_fps=SCRCPY_FPS, log=print).start()
            link_video = True
            mode, _ = helper.mode()
            print(f"[+] Live picture {link.size[0]}x{link.size[1]}: " + (
                "scrcpy over the app's adb (Wireless debugging), taps through scrcpy" if mode == "adb" else
                "screen capture, taps through accessibility (Wireless debugging would let it use scrcpy)"))
        except Exception as e:
            print(f"[-] The helper app can't capture the screen ({e}); trying again in a moment.")
            link, link_video = None, False
        return
    try:
        print("[*] Starting scrcpy live video + touch...")
        link = ScrcpyLink(max_fps=SCRCPY_FPS, bit_rate=SCRCPY_BITRATE, log=print).start()
        shot = device_call(lambda: d.screenshot(format="opencv"), SCREENSHOT_TIMEOUT, "Screenshot")
        live, _ = link.next_frame(timeout=2.0)
        if shot is None or live is None:
            raise RuntimeError("couldn't get frames to compare")
        live = fit_to_screen(live, shot.shape)
        diff = float(np.median(cv2.absdiff(cv2.cvtColor(live, cv2.COLOR_BGR2GRAY),
                                           cv2.cvtColor(shot, cv2.COLOR_BGR2GRAY))))
        link_video = diff <= SCRCPY_MAX_DIFF
        if SCREEN_OFF and link_video:
            link.display_power(False)
            atexit.register(restore_screen)
            print("[*] Phone screen turned off to save battery (the game keeps running). "
                  "Press the phone's power button to turn it on.")
        print(f"[+] scrcpy up: video {link.size[0]}x{link.size[1]}, looks {'the same' if link_video else 'different'} "
              f"as a screenshot (diff {diff:.1f}) -> frames from {'scrcpy' if link_video else 'screenshots'}, taps via scrcpy")
    except Exception as e:
        print(f"[-] scrcpy not available ({e}); using screenshots and uiautomator2 taps.")
        if link is not None:
            link.stop()
        link, link_video = None, False

desktop_view = None   # scrcpy.exe window process (phone screen on this PC), if shown

def start_desktop_view():
    """Show the phone screen in a window on this PC with scrcpy.exe (view only: mouse clicks in the
    window can't interfere with the bot's taps). Works with the phone's screen turned off too."""
    global desktop_view
    import shutil
    exe = os.path.join(HERE, "Scrcpy", "scrcpy.exe")
    if not os.path.exists(exe):
        print(f"[-] Can't show the screen: {exe} not found.")
        return
    env = os.environ.copy()
    adb_path = shutil.which("adb")
    if adb_path:
        # scrcpy ships its own adb; a different adb restarts the adb server and would cut the
        # bot's connection, so make the window use the same adb as the bot
        env["ADB"] = adb_path
    try:
        desktop_view = subprocess.Popen(
            [exe, "--no-control", "--no-audio", "--max-fps=30", "--video-bit-rate=4M",
             "--window-title=Arrow bot - phone screen"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        atexit.register(stop_desktop_view)
        print("[+] Showing the phone screen in a window (view only).")
    except Exception as e:
        print(f"[-] Couldn't open the screen window: {e}")

def stop_desktop_view():
    if desktop_view is not None and desktop_view.poll() is None:
        desktop_view.terminate()

def ask_show_screen():
    """Startup choice: --show / --no-show, otherwise ask (default: no)."""
    if ON_PHONE:
        return False   # running on the phone: its own screen shows the game
    if "--show" in sys.argv:
        return True
    if "--no-show" in sys.argv or not sys.stdin.isatty():
        return False
    try:
        return _ask_key("Show the phone screen on this PC? (y/N): ")
    except EOFError:
        return False

def restore_screen():
    """Screen back on (runs on any exit: Ctrl+C, crash, window closed)."""
    if BRIDGE_MODE:
        if helper is not None:     # (the app does it too when the bot's connection ends)
            helper.screen(True)
        return
    try:
        if link is not None and link.alive:
            link.display_power(True)
            return
    except Exception:
        pass
    # stream already gone: wake the screen the plain way
    adb_run(["shell", "input keyevent KEYCODE_WAKEUP"], timeout=5)

def fit_to_screen(img, shape):
    """scrcpy may stream at a smaller size than the screen: scale back to screen pixels."""
    if img.shape[:2] != shape[:2]:
        img = cv2.resize(img, (shape[1], shape[0]), interpolation=cv2.INTER_LINEAR)
    return img

def capture_frame():
    if BRIDGE_MODE:
        if link is None or not link.alive:
            start_scrcpy()         # capture stopped (screen locked, stopped from the status bar, ...)
        f = link.next_frame(timeout=2.0)[0] if link is not None else None
        return fit_to_screen(f, (SCREEN_H, SCREEN_W)) if f is not None else None
    frame = device_call(lambda: d.screenshot(format="opencv"), SCREENSHOT_TIMEOUT, "Screenshot")
    if frame is None:
        # Slower fallback that doesn't depend on uiautomator2
        try:
            out = subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True, timeout=10).stdout
        except Exception:
            out = b""
        if out:
            frame = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
    return frame

class FrameGrabber:
    """
    Keeps taking screenshots in a background thread so a fresh frame is always waiting
    (a screenshot takes ~200ms; the analysis only ~60ms). Paused during swipes/pinches so the
    extra traffic can't make a drag stutter into a fling.
    """
    def __init__(self):
        self.cond = threading.Condition()
        self.frame, self.t = None, 0.0
        self.running = threading.Event()
        self.running.set()
        # Several capture threads overlap the phone's screenshot work, so fresh frames arrive more
        # often (the newest one always wins). If the phone can only do one at a time, no harm done.
        for i in range(CAPTURE_THREADS):
            threading.Thread(target=self._loop, args=(i,), daemon=True).start()

    def _loop(self, index=0):
        while True:
            self.running.wait()
            if link is not None and link.alive and link_video:
                if index:                      # one thread is plenty for the live stream
                    time.sleep(0.2)
                    continue
                f, t = link.next_frame(newer_than=self.t, timeout=1.0)
                if f is not None:
                    f = fit_to_screen(f, (SCREEN_H, SCREEN_W))
                    with self.cond:
                        if t > self.t:
                            self.frame, self.t = f, t
                            self.cond.notify_all()
                    continue
                if not stopping:
                    print("[!] The live picture stopped; back to screenshots." if BRIDGE_MODE else
                          "[!] scrcpy video stopped; back to screenshots.")
            t = time.time()
            f = capture_frame()
            if f is None:
                time.sleep(0.2)
                continue
            with self.cond:
                if t > self.t:          # keep only the newest capture
                    self.frame, self.t = f, t
                    self.cond.notify_all()

    def get(self, newer_than=0.0, timeout=5.0):
        """Latest frame whose capture STARTED after `newer_than`. Returns (frame, start_time)."""
        end = time.time() + timeout
        with self.cond:
            while self.frame is None or self.t <= newer_than:
                left = end - time.time()
                if left <= 0:
                    return None, 0.0
                self.cond.wait(left)
            return self.frame, self.t

    def pause(self):
        self.running.clear()
        time.sleep(0.25)  # let an in-flight screenshot finish

    def resume(self):
        self.running.set()

grabber = None

VIDEO_CHECK_EVERY = 10.0  # at most one "is the video still live?" check this often
VIDEO_STALE_DIFF = 0.5    # board shapes overlap less than half: a different camera spot
_video_check = {"t": 0.0}

def _view_diff(a, b):
    """How different two screens are in where the board is: 1 - overlap of their board shapes
    (grid dots + arrow lines, coarse). A dark, nearly empty board barely changes in raw pixels even
    when the camera is somewhere else entirely; its dots and lines do move."""
    def shape(img):
        roi = img[ROI_Y1:ROI_Y2, ROI_X1:ROI_X2]
        _, board = build_masks(roi)
        small = cv2.resize(board, (board.shape[1] // 8, board.shape[0] // 8), interpolation=cv2.INTER_AREA)
        return cv2.dilate((small > 0).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    sa, sb = shape(a), shape(b)
    union = np.count_nonzero(sa | sb)
    if union < 30:
        return 0.0                       # (almost) nothing on either: can't tell, assume fine
    return 1.0 - np.count_nonzero(sa & sb) / union

def _video_stale_once():
    t0 = time.time()
    shot = capture_frame()
    if shot is None or link is None or not link.alive:
        return False
    live, _ = link.next_frame(newer_than=time.time() + 0.05, timeout=1.0)
    if live is None:
        live, _ = link.next_frame(newer_than=t0, timeout=0.5)
    if live is None:
        return True                      # no video at all
    return _view_diff(fit_to_screen(live, shot.shape[:2]), shot) > VIDEO_STALE_DIFF

def check_video(reason):
    """Only when the bot is idle in a level for no visible reason (no arrows in view / nothing to
    do): make sure the scrcpy picture is still the real screen. Once it went stale and the bot
    stared at an old picture for a minute. Restarting the video flashes the screen, so this runs
    rarely and only in those moments, never on a timer."""
    global link, link_video
    if BRIDGE_MODE or link is None or not link.alive or not link_video:
        return
    if time.time() - _video_check["t"] < VIDEO_CHECK_EVERY:
        return
    _video_check["t"] = time.time()
    try:
        if not _video_stale_once():
            return
        time.sleep(0.5)
        if not _video_stale_once():
            return
        print(f"[!] scrcpy video is showing an old picture ({reason}). Restarting the video...")
        try:
            link.stop()
        except Exception:
            pass
        start_scrcpy()
        if link is None or not link_video:
            print("[!] Using screenshots for the picture from now on.")
    except Exception as e:
        print(f"[-] video check failed: {e}")

def game_in_foreground():
    if BRIDGE_MODE:
        try:
            return GAME_PACKAGE in helper.foreground()
        except Exception:
            return True            # couldn't tell; don't relaunch on a guess
    out = adb_run(["shell", "dumpsys", "window"])
    focus_lines = [l for l in out.splitlines() if "mCurrentFocus" in l or "mFocusedApp" in l]
    if not focus_lines:
        return True  # couldn't tell; don't relaunch on a guess
    return any(GAME_PACKAGE in l for l in focus_lines)

def launch_game():
    if BRIDGE_MODE:
        try:
            helper.launch(GAME_PACKAGE)
        except Exception as e:
            print(f"[-] Couldn't open {GAME_PACKAGE}: {e}")
        return
    adb_run(["shell", "monkey", "-p", GAME_PACKAGE, "-c", "android.intent.category.LAUNCHER", "1"])

TERMINAL_CHECK_EVERY = 2.0   # on the phone, outside levels: seconds between "is Termux open?" checks

def terminal_in_front():
    """On the phone: the terminal the bot runs in (the Terminal app or Termux) is the open app, so
    you're looking at the bot or about to stop it. Taps, BACK and relaunching the game would land
    in it: the bot pauses."""
    if BRIDGE_MODE:
        try:
            out = helper.foreground()
        except Exception:
            return False
    else:
        out = adb_run(["shell", "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'"], timeout=5)
    return any(app in out for app in onphone.TERMINAL_APPS) and GAME_PACKAGE not in out

class Tapper:
    """
    Taps arrows one at a time at an even pace (TAP_INTERVAL) on its own thread, so the vision loop
    never waits for taps and taps never come in bursts with pauses between. The vision loop keeps a
    short queue topped up with arrows it verified on the live screen, re-checks queued ones every
    frame (prune) and empties the queue before any pan or zoom.
    """
    def __init__(self, sync=False):
        self.lock = threading.Lock()
        self.queue = []          # {"x", "y", "record", "arrow", "pose"}
        self.executed = []       # in-flight records of taps actually sent (drained by the loop)
        self.busy = False
        self.last_tap = 0.0
        self.sync = sync
        # Fast path: inject finger down/up through the uiautomator2 connection (~40ms per call),
        # no `input` process start-up (~150ms). Backup: `input tap` if that ever fails.
        self.use_u2 = not sync and not BRIDGE_MODE
        self.cost = 0.08         # measured seconds a u2 tap takes (running average)
        if not sync:
            threading.Thread(target=self._loop, daemon=True).start()

    def interval(self):
        """Fastest even pace: one tap must be fully done (finger up) before the next starts, and
        never two taps in the same game frame."""
        if self.use_u2 or (link is not None and link.alive):
            return max(MIN_TAP_INTERVAL, self.cost + TAP_MARGIN)
        return TAP_INTERVAL

    def _send(self, item):
        x, y = int(item["x"]), int(item["y"])
        started = time.time()   # the pace is measured start-to-start
        sent = False
        if not self.sync and link is not None and link.alive:
            try:
                vw, vh = link.size      # touch coordinates are in the video's pixel space
                t0 = time.time()
                link.tap(x * vw / SCREEN_W, y * vh / SCREEN_H)
                self.cost = 0.8 * self.cost + 0.2 * (time.time() - t0)
                sent = True
            except Exception as e:
                print(f"    [tap] scrcpy tap failed ({e}); using uiautomator2 taps")
                link.alive = False
        if not sent and self.use_u2:
            def tap_u2():
                d.touch.down(x, y)
                d.touch.up(x, y)
                return True
            t0 = time.time()
            if device_call(tap_u2, 2.0, "Tap"):
                self.cost = 0.8 * self.cost + 0.2 * (time.time() - t0)
                sent = True
            else:
                print("    [tap] fast taps failed; switching to `input tap` (slower, steady pace)")
                self.use_u2 = False
        if not sent:
            adb_input(f"input tap {x} {y}")
        self.last_tap = started
        item["record"]["t"] = self.last_tap
        with self.lock:
            self.executed.append(item["record"])

    def stop(self):
        """Bot shutting down: no more taps (they used to go on for a moment after Ctrl+C, through
        the slower fallback once the scrcpy link was closed)."""
        with self.lock:
            self.queue = []
            self.stopped = True

    def _loop(self):
        while not getattr(self, "stopped", False):
            with self.lock:
                ready = bool(self.queue)
            if not ready:
                time.sleep(0.01)
                continue
            wait = self.last_tap + self.interval() - time.time()
            if wait > 0:
                time.sleep(wait)     # keep the pace even (the queue may be pruned meanwhile)
            with self.lock:
                item = self.queue.pop(0) if self.queue and not getattr(self, "stopped", False) else None
                self.busy = item is not None
            if item is not None:
                self._send(item)
                with self.lock:
                    self.busy = False

    def add(self, items):
        if self.sync:
            for item in items:
                time.sleep(TAP_INTERVAL)
                self._send(item)
            return
        with self.lock:
            self.queue.extend(items)

    def room(self):
        with self.lock:
            return max(0, TAP_QUEUE_MAX - len(self.queue))

    def pending(self):
        with self.lock:
            return list(self.queue)

    def prune(self, keep):
        """Drop queued taps that are no longer safe (keep(item) -> bool). Returns the dropped ones."""
        with self.lock:
            dropped = [it for it in self.queue if not keep(it)]
            self.queue = [it for it in self.queue if keep(it)]
            return dropped

    def drain(self):
        with self.lock:
            out, self.executed = self.executed, []
            return out

    def clear(self, wait=True):
        """Empty the queue (and wait for a tap in progress to finish) before pans/zooms/menus."""
        with self.lock:
            self.queue = []
        end = time.time() + 1.0
        while wait and not self.sync and time.time() < end:
            with self.lock:
                if not self.busy:
                    break
            time.sleep(0.01)
        if wait and not self.sync and not self.use_u2:
            # `input tap` runs asynchronously: let the last one finish before anything else
            # touches the screen (u2 taps are already finished when they return)
            time.sleep(max(0.0, self.last_tap + TAP_COST - time.time()))

tapper = None

def pan_camera(direction, amount=None):
    """Reveal more of the board in `direction` (by `amount` px, default PAN_FRACTION of the view).
    Returns the expected content shift (dx, dy)."""
    roi_w, roi_h = ROI_X2 - ROI_X1, ROI_Y2 - ROI_Y1
    b0, b1 = band_rows()
    want, reach = {}, {}
    for d in direction.split("+"):        # "BOTTOM+RIGHT": one diagonal drag
        vx, vy = DIRS[d]
        size = roi_w if vx else (roi_h - (b1 + 1 if b1 >= b0 else 0))   # visible height
        amt = amount.get(d) if isinstance(amount, dict) else amount
        # exact distances can go up to 75% of the view in one pan (still enough overlap to measure)
        # (callers keep pans within PAN_MAX; only pans that finish at a known limit go further)
        want[d] = int(size * PAN_FRACTION) if amt is None else max(40, min(int(size * PAN_MAX_TO_EDGE), int(amt)))
        reach[d] = int(0.9 * ((ROI_X2 - ROI_X1) - 2 * 60)) if vx else int(0.9 * (ROI_Y2 - (ROI_Y1 + b1 + 1)))
    # The board moves only part of the way the finger goes (learned from measured pans): drag
    # that much further so one pan lands where it should (short pans meant a 2nd pan every time).
    # The lost part is mostly a fixed chunk at the start (touch slop, ~100px on the real game:
    # 698px -> 576, 1119px -> 986), so short pans need the whole chunk added, not a percentage:
    # a 50px pan used to be a ~60px drag that barely moved the board, read as "camera limit" -
    # and the position then snapped to the wrong place, forever (board just wider than the screen).
    total_x = total_y = 0
    for d, amt in want.items():
        vx, vy = DIRS[d]
        amt = min(reach[d], max(int(amt / pan_gain), int(amt + TOUCH_SLOP)))
        # Content moves opposite to the side we want to reveal
        total_x, total_y = total_x - vx * amt, total_y - vy * amt
    return pan_by(total_x, total_y)

pan_fast = True   # quick drags; switched off for the rest of a level if a quick pan misreads
pan_gain = 0.85   # board movement per px of finger movement (learned from measured long pans)
pan_gain_samples = 0   # long pans it was learned from (this session)
AMAZE_CUT_SHORT = 0.5  # Amaze: the camera stops dead at its limit (no overscroll and spring-back),
                       # so a pan that moved less than this share of what pan_gain predicts ran into
                       # it. Only once pan_gain was measured on a few full pans (a limit becomes a
                       # board edge, and a wrong edge would mean a wrong tap).
AMAZE_GAIN_SAMPLES = 2
TOUCH_SLOP = 110  # px at the start of a drag the game ignores (real game: ~100, "often 100+")
PAN_GAIN_MIN_DRAG = 500   # learn pan_gain only from drags this long (short ones are mostly slop)
PAN_MAX_OVERSHOOT = 1.2   # a pan can't move the board further than this times the finger
PAN_PRIOR_PER_PX = 0.0003 # match score traded per half-size px away from the expected move...
PAN_PRIOR_MAX = 0.03      # ...at most this much (only breaks near-ties between dot-grid repeats)
MAX_BLIND_SURVEY_PANS = 8 # survey pans one way with no arrows in view before calling it that side's
                          # limit (a misread pan must not sweep "right" forever)

def expected_move(finger):
    """Board movement a drag of `finger` px (signed) should cause: the slop is lost, the rest
    follows at about pan_gain (whichever predicts less)."""
    m = min(abs(finger) * pan_gain, max(0.0, abs(finger) - TOUCH_SLOP))
    return m if finger >= 0 else -m

def pan_by(content_dx, content_dy):
    """Drag so the board content moves by (content_dx, content_dy). Returns that expected shift."""
    if tapper:
        tapper.clear()
    # drag in the middle of the board area: below the header, away from the screen edges
    b0, b1 = band_rows()
    cx, cy = (ROI_X1 + ROI_X2) // 2, (ROI_Y1 + b1 + 1 + ROI_Y2) // 2
    sx, sy = int(cx - content_dx / 2), int(cy - content_dy / 2)
    ex, ey = int(cx + content_dx / 2), int(cy + content_dy / 2)

    # Slow drag + hold before release so the camera doesn't fling (keeps frames overlapping)
    def drag():
        d.touch.down(sx, sy)
        steps, step_sleep, hold = (6, 0.0, 0.15) if pan_fast else (10, 0.01, 0.2)
        for i in range(1, steps + 1):
            f = 1 - (1 - i / steps) ** 2      # ease out: the finger slows down before stopping
            d.touch.move(int(sx + (ex - sx) * f), int(sy + (ey - sy) * f))
            if step_sleep:
                time.sleep(step_sleep)
        time.sleep(hold)  # hold still before letting go so the camera doesn't fling
        d.touch.up(ex, ey)
        return True

    done = False
    if link is not None and link.alive:
        # instant touch messages instead of ~40ms RPCs per step: quicker, evenly timed drags
        try:
            vw, vh = link.size
            fx, fy = vw / SCREEN_W, vh / SCREEN_H
            link.drag(sx * fx, sy * fy, ex * fx, ey * fy,
                      steps=20 if pan_fast else 28, step_time=0.015, hold=0.1 if pan_fast else 0.2)
            # ~0.3s drags: the game moves the board ~90% of a steady drag but only ~55% (and
            # unevenly) of a 0.12s flick - so a slower drag needs fewer pans overall
            done = True
        except Exception as e:
            print(f"    [pan] scrcpy drag failed ({e}); using uiautomator2")
    if not done:
        if grabber: grabber.pause()
        if not device_call(drag, GESTURE_TIMEOUT, "Pan"):
            adb_input(f"input swipe {sx} {sy} {ex} {ey} 600")
            time.sleep(0.65)             # `input swipe` runs in the background: let it finish
        if grabber: grabber.resume()
    return (ex - sx, ey - sy)        # (no fixed wait: wait_until_settled watches the board stop)

def zoom_out():
    # Custom two-finger pinch kept well away from the screen edges: pinch_in() on the root
    # element starts the fingers at x=0 / x=1080, which Android treats as edge-swipe gestures
    # (shows the system bars / triggers back).
    cx, cy = (ROI_X1 + ROI_X2) // 2, (ROI_Y1 + ROI_Y2) // 2
    spread_x, spread_y = 340, 500
    end_x, end_y = (170, 250) if AMAZE else (40, 40)   # Amaze: ~2x per pinch (it zooms out a lot)
    if tapper: tapper.clear()
    # uiautomator2 pinch first: the scrcpy pinch often stopped short of the zoom limit
    if grabber: grabber.pause()
    try:
        ok = None if BRIDGE_MODE else device_call(lambda: d().gesture((cx - spread_x, cy - spread_y), (cx + spread_x, cy + spread_y),
                                             (cx - end_x, cy - end_y), (cx + end_x, cy + end_y), steps=30) or True,
                         GESTURE_TIMEOUT, "Zoom gesture")   # True = done (gesture() returns None)
    except Exception as e:
        print(f"    [zoom] uiautomator2 pinch failed ({e})")
        ok = None
    finally:
        if grabber: grabber.resume()
    if ok is not None or link is None or not link.alive:
        return
    try:   # backup only
        vw, vh = link.size
        fx, fy = vw / SCREEN_W, vh / SCREEN_H
        link.pinch(((cx - spread_x) * fx, (cy - spread_y) * fy), ((cx + spread_x) * fx, (cy + spread_y) * fy),
                   ((cx - end_x) * fx, (cy - end_y) * fy), ((cx + end_x) * fx, (cy + end_y) * fy))
        print("    [zoom] used the scrcpy pinch instead")
    except Exception as e:
        print(f"    [zoom] scrcpy pinch failed too ({e})")

# -----------------------------------------------------------------------------
# SCREEN RECOGNITION: "are we in a level?" + tappable buttons
# -----------------------------------------------------------------------------
# ingame/  : pieces of the level HUD (power-up bar, LVL header, gear) + positions.json saying
#            where each sits. In a level when enough of them match in color, in place, and at
#            normal brightness (a popup dims the HUD, which plain correlation would ignore).
# buttons/ : anything to tap when seen outside a level (play, next, close X, ...). Add more with
#            grab_button.py; every .png in the folder is loaded at startup.
INGAME_DIR = os.path.join(HERE, "ingame")
BUTTONS_DIR = os.path.join(HERE, "buttons")
INGAME_THRESHOLD = 0.85
INGAME_MIN_MARKERS = 3
INGAME_SEARCH_MARGIN = 60
BUTTON_THRESHOLD = 0.85
BUTTON_SCALE = 0.5        # buttons are searched on a half-size frame for speed
BUTTON_SIZES = (0.88, 1.0, 1.12)   # PLAY/NEXT pulse in size, so match a few sizes
BUTTON_BRIGHTNESS = (0.7, 1.5)     # ...and a shine sweeps across them
BRIGHTNESS_RANGE = (0.8, 1.25)
PREFERRED_BUTTONS = ()             # tapped first when they show with others

# Amaze GO!: its own HUD and buttons, a smaller header (title, lives, hint), nothing at the bottom
if AMAZE:
    INGAME_DIR = os.path.join(HERE, "ingame_amaze")
    BUTTONS_DIR = os.path.join(HERE, "buttons_amaze")
    HEADER_BAND = STATUS_BAR_BAND = (0, 445)
    ROI_Y2 = 2400
    PREFERRED_BUTTONS = ("restart",)   # out of lives: Restart, not "Continue" (an ad for lives)
AMAZE_ZOOM_MIN_HALF = 2.0   # Amaze zooms out much further than needed: stop before lines get thinner
                            # than this (arrowheads must stay recognisable)
AMAZE_ZOOM_MAX_PINCHES = 8

def load_templates(folder):
    templates = []
    for path in sorted(glob.glob(os.path.join(folder, "*.png"))):
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is not None:
            templates.append({"name": os.path.basename(path), "img": img})
    return templates

def match_in(region, tmpl_img):
    """Best color match of tmpl_img inside region -> (score, top_left, brightness_ratio)."""
    th, tw = tmpl_img.shape[:2]
    if region.shape[0] < th or region.shape[1] < tw:
        return 0.0, (0, 0), 0.0
    res = cv2.matchTemplate(region, tmpl_img, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    patch = region[loc[1]:loc[1] + th, loc[0]:loc[0] + tw]
    ratio = patch.mean() / max(tmpl_img.mean(), 1.0)
    return score, loc, ratio

def load_ingame_markers():
    markers = load_templates(INGAME_DIR)
    try:
        with open(os.path.join(INGAME_DIR, "positions.json")) as f:
            positions = json.load(f)
    except Exception:
        positions = {}
    for m in markers:
        m["pos"] = positions.get(m["name"])
    return [m for m in markers if m["pos"] is not None]

_hud_cache = {"crops": None, "found": 0, "t": 0.0}

def count_ingame_markers(frame, markers, under_popup=False):
    """
    HUD pieces found in place. under_popup=True also accepts them dimmed: a popup sitting on top
    of a level darkens the HUD but its shapes still match, so "a level is underneath".
    The plain check reuses the last answer while every HUD spot looks the same as last time
    (a few cheap pixel diffs instead of template matching every frame); re-checked once a second.
    """
    if not under_popup:
        crops = [frame[m["pos"][1]:m["pos"][1] + m["img"].shape[0],
                       m["pos"][0]:m["pos"][0] + m["img"].shape[1]] for m in markers]
        old, now = _hud_cache["crops"], time.time()
        if (old is not None and len(old) == len(crops) and now - _hud_cache["t"] < 1.0
                and all(a.shape == b.shape and cv2.norm(a, b, cv2.NORM_L1) < 6 * a.size
                        for a, b in zip(old, crops))):
            return _hud_cache["found"]
        found = _count_markers(frame, markers, False)
        _hud_cache.update(crops=[c.copy() for c in crops], found=found, t=now)
        return found
    return _count_markers(frame, markers, True)

def _count_markers(frame, markers, under_popup):
    lo, hi = (0.25, 1.25) if under_popup else BRIGHTNESS_RANGE
    threshold = 0.8 if under_popup else INGAME_THRESHOLD
    found = 0
    for m in markers:
        x, y = m["pos"]
        th, tw = m["img"].shape[:2]
        M = INGAME_SEARCH_MARGIN
        region = frame[max(0, y - M):y + th + M, max(0, x - M):x + tw + M]
        score, _, ratio = match_in(region, m["img"])
        if score >= threshold and lo <= ratio <= hi:
            found += 1
    return found

def find_button(frame, buttons):
    """
    Returns (name, (x, y)) of the best visible button, or None.
    Coarse-to-fine: a quick grayscale search on a quarter-size frame finds where each button
    might be, then the strict color check runs at half size only in a small window there.
    """
    if PREFERRED_BUTTONS:
        first = [b for b in buttons if b["name"].startswith(PREFERRED_BUTTONS)]
        hit = find_button(frame, first) if first and len(first) < len(buttons) else None
        if hit is not None:
            return hit
    small = cv2.resize(frame, None, fx=BUTTON_SCALE, fy=BUTTON_SCALE, interpolation=cv2.INTER_AREA)
    tiny = cv2.cvtColor(cv2.resize(small, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA),
                        cv2.COLOR_BGR2GRAY)
    best = None
    for b in buttons:
        if "sized" not in b:
            b["sized"] = [cv2.resize(b["img"], None, fx=BUTTON_SCALE * s, fy=BUTTON_SCALE * s,
                                     interpolation=cv2.INTER_AREA) for s in BUTTON_SIZES]
            b["coarse"] = [cv2.cvtColor(cv2.resize(t, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA),
                                        cv2.COLOR_BGR2GRAY) for t in b["sized"]]
        for tmpl, ctmpl in zip(b["sized"], b["coarse"]):
            if ctmpl.shape[0] > tiny.shape[0] or ctmpl.shape[1] > tiny.shape[1]:
                continue
            res = cv2.matchTemplate(tiny, ctmpl, cv2.TM_CCOEFF_NORMED)
            _, cscore, _, cloc = cv2.minMaxLoc(res)
            if cscore < BUTTON_THRESHOLD - 0.25:
                continue
            # Refine in color around the coarse hit
            th, tw = tmpl.shape[:2]
            pad = 6
            x0, y0 = max(0, cloc[0] * 2 - pad), max(0, cloc[1] * 2 - pad)
            window = small[y0:y0 + th + 2 * pad, x0:x0 + tw + 2 * pad]
            score, loc, ratio = match_in(window, tmpl)
            if score < BUTTON_THRESHOLD or not (BUTTON_BRIGHTNESS[0] <= ratio <= BUTTON_BRIGHTNESS[1]):
                continue
            if best is None or score > best[0]:
                center = ((x0 + loc[0] + tw / 2) / BUTTON_SCALE, (y0 + loc[1] + th / 2) / BUTTON_SCALE)
                best = (score, b["name"], center)
    return None if best is None else (best[1], best[2])

# -----------------------------------------------------------------------------
# 2. IMAGE PROCESSING: LINE MASK, BOARD EXTENT, ARROWHEADS
# -----------------------------------------------------------------------------
status_bar_visible = False   # kept up to date by StatusBarWatcher

class StatusBarWatcher:
    """Asks Android about once a second whether the status bar is showing (its clock and icons
    would otherwise look like board lines in the strip above the level header)."""
    def __init__(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        global status_bar_visible
        while True:
            if BRIDGE_MODE:
                try:
                    status_bar_visible = helper.status_bar()
                except Exception:
                    pass
                time.sleep(STATUS_BAR_CHECK_EVERY)
                continue
            out = adb_run(["shell", "dumpsys window | grep -m1 'type=statusBars frame'"], timeout=5)
            if "visible=" in out:
                status_bar_visible = "visible=true" in out
            time.sleep(STATUS_BAR_CHECK_EVERY)

def band_rows():
    """Blind-spot rows in ROI coordinates: the header, or the whole top while the status bar shows."""
    top, bottom = STATUS_BAR_BAND if status_bar_visible else HEADER_BAND
    return max(0, top - ROI_Y1), max(-1, bottom - ROI_Y1)

def build_masks(roi_bgr, hsv=None, keep=None):
    """Line mask + board mask. keep (dict): also stores the line blobs' labels/stats for reuse."""
    if hsv is None:
        hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    if AMAZE:
        # dark brown lines (red after a bounce) on a pale beige board; the grid dots are a pale tan
        line_mask = cv2.bitwise_or(cv2.inRange(hsv, (0, 90, 0), (30, 255, 234)),
                                   cv2.inRange(hsv, (160, 90, 0), (180, 255, 234)))
        board = cv2.bitwise_or(cv2.inRange(hsv, (0, 45, 60), (30, 255, 255)), line_mask)
    else:
        v = cv2.extractChannel(hsv, 2)
        _, line_mask = cv2.threshold(v, LINE_V_THRESH, 255, cv2.THRESH_BINARY)
        _, board = cv2.threshold(v, BOARD_V_THRESH, 255, cv2.THRESH_BINARY)
    board = cv2.morphologyEx(board, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    b0, b1 = band_rows()
    line_mask[b0:b1 + 1] = 0   # the header is drawn here; what's behind it is unknown, not empty
    board[b0:b1 + 1] = 0

    # Drop tiny isolated specks (bright grid dots / flight trails on some levels): they aren't
    # arrows but would block lanes. Specks touching the view edge are kept, since they could be
    # the visible tip of an arrow that continues off-screen.
    h, w = line_mask.shape
    lx, ly, lw, lh = cv2.boundingRect(line_mask)    # label only the box around the lines
    if lw == 0:
        n, labels, stats = 1, np.zeros((h, w), np.int32), np.zeros((1, 5), np.int32)
    else:
        n, sub_labels, stats, _ = cv2.connectedComponentsWithStats(
            line_mask[ly:ly + lh, lx:lx + lw], connectivity=8)
        labels = np.zeros((h, w), np.int32)
        labels[ly:ly + lh, lx:lx + lw] = sub_labels
        stats[:, 0] += lx
        stats[:, 1] += ly
    if n > 1:
        x, y, bw, bh, area = (stats[:, i] for i in range(5))
        at_edge = (x <= 1) | (y <= 1) | (x + bw >= w - 1) | (y + bh >= h - 1)
        speck = (area < MIN_LINE_BLOB) & ~at_edge
        speck[0] = False
        for i in np.flatnonzero(speck):     # each speck only touches its own small box
            sx, sy, sw, sh = int(x[i]), int(y[i]), int(bw[i]), int(bh[i])
            sub = labels[sy:sy + sh, sx:sx + sw]
            sel = sub == i
            line_mask[sy:sy + sh, sx:sx + sw][sel] = 0
            if keep is not None:
                sub[sel] = 0
    if keep is not None:
        keep["labels"], keep["stats"] = labels, stats
    return line_mask, board

def still_mask(hsv):
    """Pixels that aren't an arrow flying out (in Arrows they tint blue: saturated, not red; in
    Amaze GO! they keep their colour, and recent taps are tracked by their lanes instead)."""
    if AMAZE:
        return np.full(hsv.shape[:2], 255, np.uint8)
    return cv2.bitwise_not(cv2.inRange(hsv, (11, SOLID_MAX_SAT + 1, LINE_V_THRESH), (169, 255, 255)))

def resting_only(mask, hsv):
    """Line mask without arrows that are flying out.
    Flying arrows are gone a moment later, so they must not go on the map, into pan measurements
    or into "has the view stopped moving" checks."""
    return cv2.bitwise_and(mask, still_mask(hsv))

def resting_line_mask(frame):
    roi = frame[ROI_Y1:ROI_Y2, ROI_X1:ROI_X2]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask, _ = build_masks(roi, hsv)
    return resting_only(mask, hsv)

def measure_grid_step(board, mask):
    """Spacing (px) of the board's dot grid, or None. Every empty cell shows a dot, so if the
    board went on past its outermost dot, the next dot would sit exactly one step further."""
    dots = cv2.bitwise_and(board, cv2.bitwise_not(cv2.dilate(mask, np.ones((9, 9), np.uint8))))
    n, _, st, cen = cv2.connectedComponentsWithStats(dots, connectivity=8)
    pts = cen[1:][(st[1:, 4] >= 2) & (st[1:, 4] < 80)]
    if len(pts) < 12:
        return None
    sample = pts if len(pts) <= 300 else pts[np.linspace(0, len(pts) - 1, 300).astype(int)]
    d = np.sqrt(((sample[:, None, :] - pts[None, :, :]) ** 2).sum(-1))   # each sample vs ALL dots
    d[d < 0.5] = 1e9                                                     # (itself)
    nn = d.min(1)
    step = float(np.median(nn))
    # a real grid: most dots have a neighbour at the same distance
    if step < 8 or np.mean(np.abs(nn - step) < 2.0) < 0.6:
        return None
    return step

def edge_margin(world):
    """How far (px) the board's outermost pixel must stay from the view edge to count as ending
    there. With a measured dot grid: one grid step (+1px) - the next dot would be fully visible.
    Without one: ~1.2 grid steps estimated from the line thickness."""
    step = getattr(world, "grid_step", None)
    if step:
        return max(EDGE_MARGIN, int(step + 1))
    return max(EDGE_MARGIN, int(11 * getattr(world, "half", 4.0)))

def board_bbox(board_mask):
    if cv2.countNonZero(board_mask) < 20:
        return None
    x, y, w, h = cv2.boundingRect(board_mask)
    return x, y, x + w - 1, y + h - 1

def line_half_thickness(mask, dist):
    ridge = (dist >= cv2.dilate(dist, np.ones((3, 3), np.uint8))) & (mask > 0)
    if not ridge.any():
        return 4.0
    return max(2.0, float(np.median(dist[ridge])))

def run_length(mask, x, y, dx, dy, limit):
    """Ink pixels in a row from (x, y) along an axis direction (numpy slice, not a Python loop)."""
    h, w = mask.shape
    if type(x) is not int or type(y) is not int:
        x, y = int(round(x)), int(round(y))
    if not (0 <= x < w and 0 <= y < h):
        return 0
    if dx == 1:
        seg = mask[y, x:x + limit]
    elif dx == -1:
        seg = mask[y, max(0, x - limit + 1):x + 1][::-1]
    elif dy == 1:
        seg = mask[y:y + limit, x]
    else:
        seg = mask[max(0, y - limit + 1):y + 1, x][::-1]
    gaps = np.flatnonzero(seg == 0)
    return int(gaps[0]) if len(gaps) else len(seg)

def is_solid_head(hsv, cx, cy):
    """
    Resting arrows are pale lavender, or red if they bounced off something earlier (still a real
    arrow that can be tapped again). Flying arrows tint blue and fading ones go dim: ghosts.
    """
    if hsv is None:
        return True
    patch = np.sort(hsv[max(0, cy - 2):cy + 3, max(0, cx - 2):cx + 3].reshape(-1, 3), axis=0)
    k = len(patch)
    if k == 0:
        return False
    mid = patch[k // 2].astype(float) if k % 2 else (patch[k // 2 - 1].astype(float) + patch[k // 2]) / 2
    h, s, v = (float(c) for c in mid)    # per-channel medians (one sort instead of three)
    if AMAZE:   # brown at rest, red after a bounce; a fading arrow goes pale
        return s >= 80 and (h <= 30 or h >= 160)
    if (h <= 10 or h >= 170) and s >= 120 and v >= 150:
        return True                                   # red: bounced earlier, resting
    return s <= SOLID_MAX_SAT and v >= SOLID_MIN_VAL  # lavender: resting

def detect_arrows(mask, hsv=None, keep=None, half=None):
    """
    Arrowheads are filled triangles, much thicker than the lines. The distance transform
    isolates them, then the direction is the side with the shortest run of ink (the tip),
    opposite the longest run (the stem the head is attached to).
    """
    h, w = mask.shape
    b0, b1 = band_rows()
    # Only the box around the lines (+2px of background) needs the distance transform: the result
    # is the same, since every line pixel's nearest background pixel lies inside that box.
    bx, by, bw, bh = cv2.boundingRect(mask)
    if bw == 0:
        if keep is not None:
            keep["core_centers"] = np.zeros((0, 2))
        return [], (4.0 if half is None else half)
    cx0, cy0 = max(0, bx - 2), max(0, by - 2)
    cx1, cy1 = min(w, bx + bw + 2), min(h, by + bh + 2)
    sub = mask[cy0:cy1, cx0:cx1]
    dist = cv2.distanceTransform(sub, cv2.DIST_L2, 5)
    if half is None:
        half = line_half_thickness(sub, dist)
    core = cv2.compare(dist, float(half * 1.35), cv2.CMP_GT)
    n, _, stats, centroids = cv2.connectedComponentsWithStats(core)
    centroids = centroids + (cx0, cy0)          # back to full-view coordinates
    if keep is not None:
        keep["core_centers"] = centroids[1:]

    arrows = []
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        if area < 3 or area > half * half * 12:
            continue
        cx, cy = centroids[i]
        cx, cy = float(cx), float(cy)
        hb = head_border(half)
        if not (hb <= cx < w - hb and hb <= cy < h - hb):
            continue

        limit = int(half * 12)
        icx, icy = int(round(cx)), int(round(cy))
        runs = {k: run_length(mask, icx, icy, dx, dy, limit) for k, (dx, dy) in DIRS.items()}
        back = max(runs, key=runs.get)
        others = [runs[k] for k in runs if k != back]
        # The stem may run into the header or off the screen: then it goes on out of sight and
        # only has to be the longest run (the shape checks below still apply)
        bdx, bdy = DIRS[back]
        ex, ey = int(round(cx + bdx * (runs[back] + 2))), int(round(cy + bdy * (runs[back] + 2)))
        hidden = not (0 <= ex < w and 0 <= ey < h) or (b1 >= b0 and b0 - 1 <= ey <= b1 + 1)
        if runs[back] < (1.0 if hidden else 1.6) * max(others) + (1 if hidden else 0):
            continue  # no clear stem => not an arrowhead

        direction = OPPOSITE[back]
        dx, dy = DIRS[direction]
        tip_run = runs[direction]

        # Shape checks so menu text/icons don't pass as arrowheads:
        # symmetric sides, tip noticeably longer than the sides, and a thin stem behind.
        side_a, side_b = [runs[k] for k in runs if k not in (back, direction)]
        side = (side_a + side_b) / 2
        if abs(side_a - side_b) > max(2, 0.2 * side):
            continue
        if not (1.15 <= tip_run / max(side, 1) <= 1.9):
            continue
        sx, sy = cx - dx * tip_run, cy - dy * tip_run  # a point on the stem, behind the head
        px, py = dy, dx                                # perpendicular to the arrow
        stem_width = run_length(mask, sx, sy, px, py, limit) + run_length(mask, sx, sy, -px, -py, limit)
        if stem_width > half * 2.6 + 1:
            continue
        arrows.append({
            "x": int(round(cx)), "y": int(round(cy)),
            "dir": direction,
            "solid": is_solid_head(hsv, int(round(cx)), int(round(cy))),
            "tip_x": int(round(cx + dx * (tip_run + 2))),
            "tip_y": int(round(cy + dy * (tip_run + 2))),
        })
    return arrows, half

# -----------------------------------------------------------------------------
# 3. WORLD MAP (stitched across pans) + AXIS-ALIGNED RAYCAST
# -----------------------------------------------------------------------------
UNKNOWN, EMPTY, LINE = 0, 1, 2
TILE = 120   # map freshness is tracked per TILE x TILE px block

class World:
    SIZE = 8000

    def __init__(self):
        self.grid = np.zeros((self.SIZE, self.SIZE), np.uint8)
        self.bgrid = np.zeros((self.SIZE // 4, self.SIZE // 4), np.uint8)   # board dots, 1/4 size
        self.seen = None
        self.reset()

    def reset(self, keep_level=False):
        """Forget the map. keep_level=True (a mid-level glitch) keeps facts about the level itself,
        like "this board is bigger than the screen"."""
        too_big = keep_level and getattr(self, "too_big", False)
        # Clear only the part of the map that was ever written (reallocating 64MB each level is slow)
        if self.seen is not None:
            x0, y0, x1, y1 = self.seen
            self.grid[y0:y1, x0:x1] = UNKNOWN
            self.bgrid[y0 // 4:y1 // 4 + 1, x0 // 4:x1 // 4 + 1] = 0
        self.seen = None                     # (x0, y0, x1, y1) of everything written so far
        self.arrows = {}                     # arrows seen anywhere: key -> {"wx","wy","dir","tip"}
        self.tile_time = np.zeros((self.SIZE // TILE + 1, self.SIZE // TILE + 1), np.float64)
        self.at_limit = set()                # directions the camera can't pan any further
        self.explore_dir = None              # direction we keep panning until the camera stops
        self.edge_from_limit = set()         # edges proven by the camera stopping (always trusted)
        self.too_big = too_big               # board seen running off the screen this level
        self.still_frames = 2 if keep_level else 0   # frames used to decide too_big (first 2 only)
        self.chased = {}                     # off-screen arrow -> times we panned to it in vain
        self.surveyed = False                # big board: whole board swept once
        self.cut_seen = set()                # sides where the board ever ran off the screen
        self.survey_dir = None               # current row direction of the sweep
        self.survey_plan = None              # which axes to sweep and from which side
        self.survey_started = False          # corner reached, rows under way
        self.survey_extended = set()         # sides added to the sweep after the first view
        self.missing = {}                    # arrow key -> frames it's been missing from view
        self.blind = {}                      # direction -> pans in a row with nothing to measure
        self.board_box = None                # extent of the board (dots + lines) seen in any view
        self.half_cached = None              # line half-thickness (re-measured every 30 frames)
        self.grid_step = None                # dot grid spacing in px (measured with the thickness)
        self.flights = []                    # recent taps (wx, wy, dir, t): arrows flying out
        self.pose_uncertain = False          # last pan measured shakily: don't trust the off-screen map
        self.anchor = {"x": False, "y": False}   # position exact on that axis (stopped at a known limit)
        self.idle_pans = 0                   # re-check/refresh pans in a row without a tap (max 2)
        self.unseen_pans = 0                 # pans to never-seen board (capped per map)
        self.pose_check_t = 0.0
        self.frame_data = {}                 # this frame's line labels / arrowhead cores (reused)
        self.ox, self.oy = 3500, 3200        # world position of the ROI's top-left
        self.edges = {k: None for k in DIRS}  # confirmed board edges (world coords)
        self.prev_small = None
        self.last_bbox = None
        self.fully_visible = False

    def ray_edge(self, d):
        """Where an arrow's path stops mattering. Big board after the sweep: the board's real
        extent (everything seen), not the camera limit, so paths into the empty space past the
        board (e.g. up behind the header) count as clear."""
        if self.too_big and self.surveyed and self.board_box is not None:
            x0, y0, x1, y1 = self.board_box
            return {"LEFT": x0, "TOP": y0, "RIGHT": x1, "BOTTOM": y1}[d]
        return self.edges[d]

    def remember_arrows(self, arrows, shape):
        """Keep a memory of every arrow seen, in board coordinates. Arrows inside the current view
        that aren't detected any more are forgotten (they left)."""
        h, w = shape
        b0, b1 = band_rows()
        m = head_border(getattr(self, "half", 4.0)) + 5
        def in_view(x, y):
            return m <= x < w - m and m <= y < h - m and not (b0 - m <= y <= b1 + m)
        seen_now = {}
        for a in arrows:
            if a["solid"]:
                key = (round((self.ox + a["x"]) / 6), round((self.oy + a["y"]) / 6), a["dir"])
                seen_now[key] = {"wx": self.ox + a["x"], "wy": self.oy + a["y"], "dir": a["dir"],
                                 "tip": (self.ox + a["tip_x"], self.oy + a["tip_y"])}
        for key, a in list(self.arrows.items()):
            if key in seen_now or not in_view(a["wx"] - self.ox, a["wy"] - self.oy):
                self.missing.pop(key, None)
                continue
            # In view but not detected: gone after two frames in a row (one could be a flying arrow
            # passing over it). Then erase its whole line from the map, including any part that
            # runs off-screen, so arrows behind it count as free without another visit.
            self.missing[key] = self.missing.get(key, 0) + 1
            if self.missing[key] >= 2:
                del self.arrows[key]
                self.missing.pop(key, None)
                self.erase_arrow_line(a)
        self.arrows.update(seen_now)

    def fill_edges_from_sweep(self):
        """After a full sweep: a side where the board NEVER ran off the screen in any view (e.g. a
        board wider than the screen but not taller) ends where the board was seen to end. Without
        this, arrows pointing that way had rays into an "unknown" edge and were never tapped."""
        if self.board_box is None:
            return
        x0, y0, x1, y1 = self.board_box
        for d, v in (("LEFT", x0), ("TOP", y0), ("RIGHT", x1), ("BOTTOM", y1)):
            if self.edges[d] is None and d not in self.cut_seen:
                self.edges[d] = v

    def update_flights(self, results, half, now):
        """Tapped arrows: confirm which really left (its spot seen empty) or stayed (tap missed),
        then erase the ones that left from the map - their old line and the lane they flew down -
        even if the camera has moved on. Otherwise their pixels stay on the map as blockers."""
        h, w = ROI_Y2 - ROI_Y1, ROI_X2 - ROI_X1
        b0, b1 = band_rows()
        for f in self.flights:
            if f["erased"] or f["stayed"]:
                continue
            if not f["left"] and now - f["t"] > 0.3:
                x, y = f["wx"] - self.ox, f["wy"] - self.oy
                if 20 <= x < w - 20 and 20 <= y < h - 20 and not (b0 - 20 <= y <= b1 + 20):
                    here = any(a["solid"] and a["dir"] == f["dir"] and abs(a["x"] - x) <= 6
                               and abs(a["y"] - y) <= 6 for a, _, _ in results)
                    if not here:
                        f["left"] = True
                    elif now - f["t"] > 1.0:
                        f["stayed"] = True     # still there a second later: the tap didn't take
            if f["left"] and now - f["t"] > 1.2:
                self._erase_flight(f, half)
                f["erased"] = True

    def _erase_flight(self, f, half):
        px, py = f["pose"]
        x0, y0, x1, y1 = f["bbox"]
        if f.get("single"):
            # its own line (only if no other arrowhead shared that line blob)
            gx0, gy0 = px + x0, py + y0
            sub = self.grid[gy0:gy0 + f["blob"].shape[0], gx0:gx0 + f["blob"].shape[1]]
            if sub.shape == f["blob"].shape:
                sub[(f["blob"] > 0) & (sub == LINE)] = EMPTY
        # the lane it flew down (it was clear when tapped: any line there now is left-over)
        tx, ty = px + f["tip"][0], py + f["tip"][1]
        d = f["dir"]
        hw = max(2, int(round(half * 0.75))) + 2
        edge = self.ray_edge(d)
        far = {"RIGHT": tx + 4000, "LEFT": tx - 4000, "BOTTOM": ty + 4000, "TOP": ty - 4000}[d]
        if edge is not None:
            far = edge
        if d in ("LEFT", "RIGHT"):
            a, b = sorted((tx, far))
            region = self.grid[max(0, ty - hw):ty + hw + 1, max(0, a):min(self.SIZE, b + 1)]
        else:
            a, b = sorted((ty, far))
            region = self.grid[max(0, a):min(self.SIZE, b + 1), max(0, tx - hw):tx + hw + 1]
        region[region == LINE] = EMPTY

    def drop_tapped_missing(self, tap_log, now):
        """Arrows we tapped in the last few seconds that were already missing from their spot in
        the last frame have flown out. The usual rule waits for a 2nd frame (a flying arrow can pass
        over a resting one), but before a pan there is no 2nd frame: without this their record
        stays behind off-screen and the bot comes back for an arrow that's gone."""
        for key in [k for k, n in self.missing.items() if n >= 1 and k in self.arrows]:
            a = self.arrows[key]
            if any(td == a["dir"] and abs(tx - a["wx"]) <= 6 and abs(ty - a["wy"]) <= 6 and now - t < 4.0
                   for tx, ty, td, t in tap_log):
                del self.arrows[key]
                self.missing.pop(key, None)
                self.erase_arrow_line(a)

    def erase_arrow_line(self, a):
        """Clear a departed arrow's line blob on the map, if no other remembered arrow shares it."""
        if self.seen is None:
            return
        sx0, sy0, sx1, sy1 = self.seen
        # a window around the arrow (one arrow's line, not the whole map: labelling every line on
        # the map took ~5ms per departed arrow); the whole map only if its blob reaches the border
        R = 650                                   # (arrows are at most ~25 cells ~ 650px long)
        hx, hy = int(a["wx"]), int(a["wy"])
        win = (max(sx0, hx - R), max(sy0, hy - R), min(sx1, hx + R), min(sy1, hy + R))
        if win[2] <= win[0] or win[3] <= win[1]:
            return
        if not self._erase_in(a, win) and win != (sx0, sy0, sx1, sy1):
            self._erase_in(a, (sx0, sy0, sx1, sy1))

    def _erase_in(self, a, win):
        """erase_arrow_line within win; False when the blob touches the window's edge (unsure)."""
        sx0, sy0, sx1, sy1 = win
        lines = cv2.inRange(self.grid[sy0:sy1, sx0:sx1], LINE, LINE)
        # its head pixels are already gone on screen; start from the stem behind the head
        dx, dy = DIRS[a["dir"]]
        seeds = [(int(a["wx"] - dx * k) - sx0, int(a["wy"] - dy * k) - sy0) for k in range(0, 40, 2)]
        seeds = [(x, y) for x, y in seeds if 0 <= x < lines.shape[1] and 0 <= y < lines.shape[0] and lines[y, x]]
        if not seeds:
            return True
        n, lab = cv2.connectedComponents(lines, connectivity=8)
        blob = lab == lab[seeds[0][1], seeds[0][0]]
        ys, xs = np.nonzero(blob)
        if (ys.min() == 0 and sy0 > self.seen[1]) or (xs.min() == 0 and sx0 > self.seen[0]) or                 (ys.max() == blob.shape[0] - 1 and sy1 < self.seen[3]) or                 (xs.max() == blob.shape[1] - 1 and sx1 < self.seen[2]):
            return False                        # runs past the window: look at the whole map
        others = sum(1 for o in self.arrows.values()
                     if 0 <= o["wy"] - sy0 < blob.shape[0] and 0 <= o["wx"] - sx0 < blob.shape[1]
                     and blob[int(o["wy"] - sy0), int(o["wx"] - sx0)])
        if others:
            return True
        # The departed arrow's own head is already gone from the map, so ANY arrowhead left in this
        # blob belongs to another arrow (maybe one never recognised, e.g. always behind the header):
        # then keep it all.
        crop = blob[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.uint8) * 255
        dist = cv2.distanceTransform(crop, cv2.DIST_L2, 5)
        if (dist > getattr(self, "half", 4.0) * 1.35).any():
            return True
        self.grid[sy0:sy1, sx0:sx1][blob] = EMPTY
        return True

    def relocalize(self, mask, near=None):
        """Find where the current view sits on the remembered map. Returns (ox, oy) or None.
        near=r: only look within r px of the current position (the usual re-check: the position is
        roughly right, off by a few grid steps) - a fraction of the work of searching everywhere."""
        if self.seen is None:
            return None
        sx0, sy0, sx1, sy1 = self.seen
        h, w = mask.shape
        if near is not None:
            x0, y0 = max(0, self.ox - near), max(0, self.oy - near)
            x1, y1 = min(self.SIZE, self.ox + w + near), min(self.SIZE, self.oy + h + near)
        else:
            pad = max(w, h)
            x0, y0 = max(0, sx0 - pad), max(0, sy0 - pad)
            x1, y1 = min(self.SIZE, sx1 + pad), min(self.SIZE, sy1 + pad)
        # (inRange gives the 0/255 mask directly: "(grid == LINE) * 255" went through an int64
        #  array of up to ~450MB first - most of a 200ms stall every 0.4s while the pose was unsure)
        known = cv2.inRange(self.grid[y0:y1, x0:x1], LINE, LINE)
        f = 4  # search at quarter size
        big = cv2.resize(known, None, fx=1 / f, fy=1 / f, interpolation=cv2.INTER_AREA)
        small = cv2.resize(mask, None, fx=1 / f, fy=1 / f, interpolation=cv2.INTER_AREA)
        b0, b1 = band_rows()
        small[max(0, b0 // f):(b1 // f) + 1] = 0
        if small.shape[0] > big.shape[0] or small.shape[1] > big.shape[1]:
            return None
        if float(small.std()) < 1e-3:
            return None
        res = cv2.matchTemplate(big, small, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if score < RELOC_MIN_SCORE:
            return None
        return x0 + loc[0] * f, y0 + loc[1] * f

    def board_pose_agrees(self, board, tol=8):
        """Does this view's board (dots) sit where the current position says, on the coarse board
        map? Only confirms: the dot pattern repeats, so a match elsewhere could be one step off."""
        h, w = board.shape
        qx, qy = self.ox // 4, self.oy // 4
        view = cv2.resize(board, (w // 4, h // 4), interpolation=cv2.INTER_AREA).astype(np.float32)
        r = 6                                          # search +-24 px
        y0, x0 = max(0, qy - r), max(0, qx - r)
        area = self.bgrid[y0:qy + view.shape[0] + r, x0:qx + view.shape[1] + r]
        if area.shape[0] < view.shape[0] or area.shape[1] < view.shape[1] or not (area == 2).any():
            return False
        known = (area == 2).astype(np.float32) * 255
        view = cv2.GaussianBlur(view, (0, 0), 1.0)
        known = cv2.GaussianBlur(known, (0, 0), 1.0)
        res = cv2.matchTemplate(known, view, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        dx, dy = (x0 + loc[0] - qx) * 4, (y0 + loc[1] - qy) * 4
        return score >= RELOC_MIN_SCORE and abs(dx) <= tol and abs(dy) <= tol

    @property
    def prev_small(self):
        if self._prev_small is None and getattr(self, "_prev_mask", None) is not None:
            self._prev_small = self._small(self._prev_mask)
        return self._prev_small

    @prev_small.setter
    def prev_small(self, value):
        self._prev_small = value
        self._prev_mask = None

    def _small_board(self, board):
        """Board picture for measuring: blurred enough that the outline of the dotted area counts
        more than the regular dot spacing (which could match one step off)."""
        h, w = board.shape
        small = cv2.resize(board, (w // 2, h // 2), interpolation=cv2.INTER_AREA).astype(np.float32)
        return cv2.GaussianBlur(small, (0, 0), 4)

    def _small(self, mask):
        h, w = mask.shape
        small = cv2.resize(mask, (w // 2, h // 2), interpolation=cv2.INTER_AREA).astype(np.float32)
        return cv2.GaussianBlur(small, (0, 0), 2)

    def predict_move(self, direction, ex, ey, shape):
        """Content shift a drag should have caused when no picture can measure it: what the game
        lets through (drag minus touch slop), capped at board edges already known per axis."""
        h, w = shape
        out = {"x": 0.0, "y": 0.0}
        for d in direction.split("+"):
            horiz = d in ("LEFT", "RIGHT")
            e = ex if horiz else ey
            pred = abs(expected_move(e))
            edge = self.edges.get(d) if d in self.edge_from_limit else None
            if edge is not None:
                room = {"LEFT": self.ox - edge, "RIGHT": edge - (self.ox + w - 1),
                        "TOP": self.oy - edge, "BOTTOM": edge - (self.oy + h - 1)}[d]
                pred = min(pred, max(0.0, room))
            out["x" if horiz else "y"] = pred if e > 0 else -pred
        return out["x"], out["y"]

    @staticmethod
    def _axis_xcorr(A, B, lo, hi, prior=None):
        """Shift s (columns of A/B; content moved by +s) in [lo, hi] where B matches A best, and the
        match (normalised correlation). One sliding-template pass each way: a strip of A that
        stays in view for every possible shift, slid over B; and a strip of B slid over A.
        prior=(s0, per_px): among near-equal matches prefer the one nearest s0 (the move the drag
        should have made). The grid dots repeat every step, so with only dots in view many shifts
        match equally well; the first of those used to win, often hundreds of px off."""
        W = A.shape[1]
        lo, hi = int(np.floor(max(lo, -0.85 * W))), int(np.ceil(min(hi, 0.85 * W)))
        best_v, best_s, best_adj = -1.0, 0.0, -1e9

        def peak(res, offset, sign, lo_i, hi_i):
            lo_i, hi_i = max(0, lo_i), min(len(res) - 1, hi_i)
            if hi_i < lo_i:
                return -1.0, 0.0, -1e9
            window = res[lo_i:hi_i + 1]
            if prior is not None:
                # a tie-breaker only (capped): a clearly better match elsewhere still wins - e.g.
                # "didn't move" at a camera limit, far from the move the drag would have made
                s_of = sign * (np.arange(lo_i, hi_i + 1) - offset)
                adj = window - np.minimum(PAN_PRIOR_MAX, prior[1] * np.abs(s_of - prior[0]))
            else:
                adj = window
            i = lo_i + int(np.argmax(adj))
            v = float(res[i])
            a = float(adj[i - lo_i])
            frac = 0.0
            if 0 < i < len(res) - 1:            # sub-step peak position (parabola through 3 points)
                d = res[i - 1] - 2 * res[i] + res[i + 1]
                if d < 0:
                    frac = float(0.5 * (res[i - 1] - res[i + 1]) / d)
            return v, sign * (i + frac - offset), a

        x0, x1 = max(0, -lo), min(W, W - hi)            # A's columns that stay inside B
        if x1 - x0 >= 0.1 * W:
            t = A[:, x0:x1]
            if float(t.std()) > 1e-3:
                res = cv2.matchTemplate(B, t, cv2.TM_CCOEFF_NORMED)[0]
                v, s, a = peak(res, x0, 1, x0 + lo, x0 + hi)       # window start i <=> s = i - x0
                if a > best_adj:
                    best_v, best_s, best_adj = v, s, a
        y0, y1 = max(0, hi), min(W, W + lo)              # B's columns that came from inside A
        if y1 - y0 >= 0.1 * W:
            t = B[:, y0:y1]
            if float(t.std()) > 1e-3:
                res = cv2.matchTemplate(A, t, cv2.TM_CCOEFF_NORMED)[0]
                v, s, a = peak(res, y0, -1, y0 - hi, y0 - lo)      # window start j <=> s = y0 - j
                if a > best_adj:
                    best_v, best_s, best_adj = v, s, a
        if not np.isfinite(best_v):
            return 0.0, -1.0
        return best_s, best_v

    def _check_axis_move(self, small, direction, ex, ey, mdx, mdy, response, mask=None):
        """
        Double-check the pan reading. A straight drag moves the camera only along the drag, the
        way the finger went (a little further if it glides). On dense, regular boards the first
        reading can lock onto the wrong match, so the reading is compared with a search along the
        drag axis that scores only the part both views share, and the better match wins. If
        nothing matches: find the view on the map; guess from the drag only on a near-empty view.
        """
        horiz = direction in ("LEFT", "RIGHT")
        along, perp, e = (mdx, mdy, ex) if horiz else (mdy, mdx, ey)
        # The board never moved further than the finger on the real game (it follows ~1:1 after
        # the touch slop). Allowing up to 1.5x let the dot grid's repeats pass as moves (+980 for
        # a 628px drag), and the map went off by hundreds of px.
        far = e * PAN_MAX_OVERSHOOT + (40 if e > 0 else -40)
        lo, hi = (min(-24, far), 24) if e < 0 else (-24, max(24, far))
        pred = expected_move(e)
        if horiz:
            A, B = self.prev_small, small
        else:
            # leave out everything down to the header's bottom: it stays put on screen while the
            # board moves, and would pull an up/down measurement toward "didn't move"
            b0, b1 = band_rows()
            cut = (b1 + 1) // 2 if b1 >= b0 else 0
            A, B = self.prev_small[cut:].T, small[cut:].T
        W = A.shape[1]
        A4 = cv2.resize(A, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        B4 = cv2.resize(B, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)

        def score(k, a=A, b=B):
            """How alike the two views are where they overlap if the content moved by k px of a/b."""
            n = a.shape[1]
            if n - abs(k) < int(0.2 * n):
                return -1.0
            pa = a[:, max(0, -k):n - max(0, k)]
            pb = b[:, max(0, k):n - max(0, -k)]
            if not pa.any() or not pb.any():
                return -1.0
            return float(cv2.matchTemplate(pb, pa, cv2.TM_CCOEFF_NORMED)[0, 0])

        plausible = abs(perp) <= 12 and lo <= along <= hi
        # Main check: slide one picture over the other along the drag, every 2px over the whole
        # physically possible range, in one go. The first reading is kept only if it agrees.
        xs, xv = self._axis_xcorr(A, B, lo / 2, hi / 2, prior=(pred / 2, PAN_PRIOR_PER_PX))
        if xv >= 0.6:
            s = xs * 2
            if plausible and abs(along - s) <= 4:
                return mdx, mdy, max(response, xv)
            print(f"    [pan] reading {mdx:+.0f},{mdy:+.0f} (resp={response:.2f}) replaced: "
                  f"the pictures line up at {s:+.0f} (match {xv:.2f})")
            if xv < 0.85:
                self.pose_uncertain = True
            return (s, 0.0, xv) if horiz else (0.0, s, xv)
        first = score(int(round(along / 2))) if plausible else -1.0
        if plausible and response >= 0.3 and first >= 0.8:
            return mdx, mdy, response            # clear, sensible, and the pictures agree
        # search the whole possible range at quarter size (4 px steps), then refine at half size
        # (near-equal matches: the one nearest the expected move, as above)
        scored = [(score(k, A4, B4), k) for k in range(int(np.floor(lo / 4)), int(np.ceil(hi / 4)) + 1)]
        best_v, best_k = max(scored, key=lambda vk: vk[0] - min(PAN_PRIOR_MAX,
                                                                  PAN_PRIOR_PER_PX / 2 * abs(vk[1] * 4 - pred))) \
            if scored else (-1.0, 0)
        best_s = best_k * 4.0
        if best_v >= 0.45:
            v2, k2 = max((score(k), k) for k in range(best_k * 2 - 3, best_k * 2 + 4))
            if v2 > first:
                if not plausible or abs(k2 * 2 - along) > 6:
                    print(f"    [pan] reading {mdx:+.0f},{mdy:+.0f} (resp={response:.2f}, match {first:.2f}) "
                          f"replaced: the pictures line up at {k2 * 2:+.0f} (match {v2:.2f})")
                if v2 < 0.85:
                    self.pose_uncertain = True
                s = k2 * 2.0
                return (s, 0.0, max(0.21, v2)) if horiz else (0.0, s, max(0.21, v2))
        if plausible and first >= 0.45:
            if first < 0.85:
                self.pose_uncertain = True
            return mdx, mdy, max(response, 0.21)
        # the map knows more than the last frame: find this view on it
        if mask is not None:
            pose = self.relocalize(mask)
            if pose is not None:
                rx, ry = self.ox - pose[0], self.oy - pose[1]
                r_along, r_perp = (rx, ry) if horiz else (ry, rx)
                if abs(r_perp) <= 12 and lo <= r_along <= hi:
                    print(f"    [pan] found the view on the map: moved {rx:+.0f},{ry:+.0f}")
                    return float(rx), float(ry), 0.21
        # Plenty on screen but no way to line it up: a guessed position would put arrows in the
        # wrong place on the map (that caused mistakes). Report failure: the map gets redone.
        if mask is not None and cv2.countNonZero(mask) >= RELOC_MIN_INK:
            print(f"    [pan] couldn't measure the {direction} pan; not guessing")
            return mdx, mdy, 0.0
        # Nothing in common to measure with (a nearly empty board): go by the drag itself - what
        # the game lets through - capped by the board edges already known.
        px, py = self.predict_move(direction, ex, ey, (small.shape[0] * 2, small.shape[1] * 2))
        self.pose_uncertain = True
        print(f"    [pan] nothing in common to measure the {direction} pan; going by the drag: "
              f"{px:+.0f},{py:+.0f}")
        return px, py, 0.21

    def _edge_shift(self, prev_board, board, direction):
        """How far the board's outline moved along the drag axis, from an end of the board (dots +
        lines) that's inside the view in both frames - not touching the screen edge, not under the
        header. None when no end is visible in both, or the two ends disagree."""
        if prev_board is None or board is None or prev_board.shape != board.shape:
            return None
        a, b = board_bbox(prev_board), board_bbox(board)
        if a is None or b is None:
            return None
        h, w = board.shape
        em = edge_margin(self)
        b0, b1 = band_rows()
        top_min = max(em, (b1 + 1 + em) if b1 >= b0 else em)
        if direction in ("LEFT", "RIGHT"):
            ends = [(a[0], b[0], lambda v: v > em), (a[2], b[2], lambda v: v < w - 1 - em)]
        else:
            ends = [(a[1], b[1], lambda v: v > top_min), (a[3], b[3], lambda v: v < h - 1 - em)]
        shifts = [after - before for before, after, inside in ends if inside(before) and inside(after)]
        if not shifts or (len(shifts) == 2 and abs(shifts[0] - shifts[1]) > 8):
            return None
        return float(sum(shifts) / len(shifts))

    def register_pan(self, mask, direction, expected, sprang=False, board=None):
        """Measure the actual camera move by phase correlation against the pre-pan frame."""
        global pan_gain, pan_gain_samples
        was_uncertain = self.pose_uncertain
        for d in direction.split("+"):              # an axis that moves is anchored again only by
            self.anchor["x" if d in ("LEFT", "RIGHT") else "y"] = False   # stopping at a known limit
        # Few arrow lines on either side of the pan (end of a big level): measure with the grid
        # dots + board outline instead, otherwise there's nothing to line up and the map is lost
        prev_board = getattr(self, "_prev_board", None)
        few_lines = (board is not None and prev_board is not None and prev_board.shape == board.shape
                     and min(cv2.countNonZero(mask), getattr(self, "_prev_line_ink", 0)) < LOW_LINE_INK)
        if few_lines:
            # Dots + whatever arrow lines are left, the lines weighted ~3x: the dots repeat every
            # grid step (a reading can be steps off), a line is unique - one arrow in view is
            # enough to pin the move down.
            prev_lines = getattr(self, "_prev_mask", None)
            def weighted(b, lines):
                if lines is None or lines.shape != b.shape:
                    return b
                return cv2.addWeighted(b, 0.3, lines, 0.7, 0)
            small = self._small_board(weighted(board, mask))
            self.prev_small = self._small_board(weighted(prev_board, prev_lines))
            print("    [pan] few arrow lines in view: measuring with the board's dots")
            self.pose_uncertain = True     # dots repeat every grid step: could be a step or more off
        else:
            small = self._small(mask)
        if self.prev_small is None or self.prev_small.shape != small.shape:
            self.prev_small = small
            return "ok"
        ex, ey = expected
        # (a) whole frames: best when the view barely moved (camera at its limit)
        win = cv2.createHanningWindow(small.shape[::-1], cv2.CV_32F)
        (sdx, sdy), response = cv2.phaseCorrelate(self.prev_small, small, win)
        mdx, mdy = sdx * 2, sdy * 2
        # (b) only the strip both frames should share if the drag moved as asked: works with much
        #     less overlap (bigger pans), measuring just the leftover difference
        H, W = small.shape
        sx, sy = int(round(ex / 2)), int(round(ey / 2))
        x0, x1 = max(0, -sx), min(W, W - sx)
        y0, y1 = max(0, -sy), min(H, H - sy)
        if (x1 - x0) > 0.25 * W and (y1 - y0) > 0.25 * H:
            a = self.prev_small[y0:y1, x0:x1]
            b = small[y0 + sy:y1 + sy, x0 + sx:x1 + sx]
            wwin = cv2.createHanningWindow(a.shape[::-1], cv2.CV_32F)
            (rx, ry), r2 = cv2.phaseCorrelate(a, b, wwin)
            if r2 > response and abs(rx) < a.shape[1] / 4 and abs(ry) < a.shape[0] / 4:
                mdx, mdy, response = (sx + rx) * 2, (sy + ry) * 2, r2
        if "+" not in direction:
            mdx, mdy, response = self._check_axis_move(small, direction, ex, ey, mdx, mdy, response,
                                                       None if few_lines else mask)
            # The board's own edge, seen before and after: an exact reading where the picture
            # can't give one. With only grid dots left, "didn't move" (camera at its limit) and
            # "moved N grid steps" line up equally well - the sweep then went "right" forever.
            es = self._edge_shift(prev_board, board, direction)
            if es is not None:
                cur = mdx if direction in ("LEFT", "RIGHT") else mdy
                if abs(es - cur) > 6:
                    print(f"    [pan] the board's edge moved {es:+.0f} px (picture said {cur:+.0f}): going by the edge")
                    mdx, mdy = (es, 0.0) if direction in ("LEFT", "RIGHT") else (0.0, es)
                    response = max(response, 0.5)
        comps = direction.split("+")            # "BOTTOM+RIGHT" = one diagonal drag
        horiz = {d: d in ("LEFT", "RIGHT") for d in comps}
        exp_of = {d: (ex if horiz[d] else ey) for d in comps}
        meas_of = {d: (mdx if horiz[d] else mdy) for d in comps}
        sprang = sprang if isinstance(sprang, (set, frozenset)) else (set(comps) if sprang else set())

        # A move of a few px either way is "didn't move" (measurement noise at a limit can come out
        # as -0.3px); only a clear move the wrong way means the measurement can't be trusted.
        if response > 0.05:
            self.blind.pop(direction, None)   # could measure this time: not panning into emptiness
        wrong_way = any(meas_of[d] * exp_of[d] < 0 and abs(meas_of[d]) > 20 for d in comps)
        # The game ignores the first part of every drag (touch slop), so a normal pan moves a good
        # deal less than the finger did. Only "barely moved" means the camera is at its limit;
        # treating short-but-real moves as limits gave fake edges (mistakes) and banned directions.
        stopped = {d for d in comps
                   if abs(meas_of[d]) < max(LIMIT_MAX_MOVE,
                                            LIMIT_FRACTION * max(0, abs(exp_of[d]) - TOUCH_SLOP))}
        if response > 0.2 and not wrong_way:
            for d in sorted(sprang & set(comps) - stopped):
                print(f"    [pan] view sprang back after the drag: reached the {d} limit")
                stopped.add(d)    # moved, but the elastic border pulled it back: that's the limit
        if AMAZE and response > 0.2 and not wrong_way and pan_gain_samples >= AMAZE_GAIN_SAMPLES:
            for d in sorted(set(comps) - stopped):
                predicted = pan_gain * max(0, abs(exp_of[d]) - TOUCH_SLOP)
                if abs(exp_of[d]) >= PAN_GAIN_MIN_DRAG and abs(meas_of[d]) < AMAZE_CUT_SHORT * predicted:
                    print(f"    [pan] moved {abs(meas_of[d]):.0f} of ~{predicted:.0f} px and stopped: "
                          f"reached the {d} limit")
                    stopped.add(d)
        if (response > 0.2 and not wrong_way and len(comps) == 1 and not stopped
                and abs(exp_of[direction]) >= PAN_GAIN_MIN_DRAG and not self.pose_uncertain):
            ratio = min(1.1, max(0.35, abs(meas_of[direction]) / abs(exp_of[direction])))
            pan_gain = 0.6 * pan_gain + 0.4 * ratio
            pan_gain_samples += 1
        if response > 0.2 and not wrong_way and stopped:
            # Camera didn't move (or sprang back): it's at the pan limit, nothing more that way.
            for d in sorted(stopped):
                self.at_limit.add(d)
                print(f"    [pan] hit camera limit {d} (moved {mdx:+.0f},{mdy:+.0f})")
        elif response < 0.05 or wrong_way:
            ink = cv2.countNonZero(mask)
            if ink < RELOC_MIN_INK:
                # Almost nothing on screen to measure against (panned into empty board): assume
                # the drag moved as asked rather than throwing the whole map away.
                self.blind[direction] = self.blind.get(direction, 0) + 1
                if self.blind[direction] >= 2:
                    # Panning into emptiness again: don't keep going that way (it used to loop)
                    print(f"    [pan] nothing to see {direction} twice in a row; not going that way again")
                    self.at_limit.update(comps)
                    return "empty"
                px, py = self.predict_move(direction, ex, ey, mask.shape)
                self.pose_uncertain = True
                print(f"    [pan] too little on screen to measure; going by the drag: {px:+.0f},{py:+.0f}")
                self.ox -= int(round(px))
                self.oy -= int(round(py))
                return "empty"
            pose = self.relocalize(mask)
            if pose is None:
                print(f"    [pan] registration failed (resp={response:.2f}); resetting world map")
                self.reset(keep_level=True)
                return "failed"
            self.ox, self.oy = pose
            print(f"    [pan] measurement failed (resp={response:.2f}); found the view on the map instead")
            return "failed"
        else:
            print(f"    [pan] moved {mdx:+.0f},{mdy:+.0f} (expected {ex:+d},{ey:+d}, resp={response:.2f})")
        for d in comps:
            if abs(meas_of[d]) > 8:
                self.at_limit.discard(OPPOSITE[d])   # moved away from the opposite limit (even a little)
        # Content shifted by (mdx, mdy) => camera moved the opposite way
        self.ox -= int(round(mdx))
        self.oy -= int(round(mdy))
        h, w = mask.shape
        for d in comps:
            if d not in self.at_limit:
                continue
            here = {"LEFT": self.ox, "RIGHT": self.ox + w - 1,  # last pixel seen
                    "TOP": self.oy, "BOTTOM": self.oy + h - 1}[d]
            known = self.edges.get(d) if d in self.edge_from_limit else None
            if known is not None and abs(here - known) <= LIMIT_SNAP_MAX:
                # The camera stops at the same spot every time: this limit was reached before, so
                # the position is exactly known again. Snap to it (cancels drift from earlier pans)
                fix = known - here
                if fix:
                    print(f"    [pan] back at the known {d} limit: position corrected by {fix:+d} px")
                if d in ("LEFT", "RIGHT"):
                    self.ox += fix
                else:
                    self.oy += fix
                self.anchor["x" if d in ("LEFT", "RIGHT") else "y"] = True
            else:
                self.edges[d] = here
                self.edge_from_limit.add(d)
        # Stopped at known limits: the position is exact there, whatever the picture said. Late in
        # a level pans are measured on the dots alone and always marked unsure, so the off-screen
        # map was never trusted: a free arrow on screen whose path left the screen read "unknown",
        # and the bot went back and forth between the arrow and its path instead of tapping it.
        if self.anchor["x"] and self.anchor["y"]:
            if self.pose_uncertain:
                print("    [pan] in a known corner: position exact")
            self.pose_uncertain = False
        elif len(comps) == 1 and self.anchor["x" if comps[0] in ("LEFT", "RIGHT") else "y"]:
            self.pose_uncertain = was_uncertain        # this pan added no doubt
        return "ok"

    def integrate(self, mask, board):
        h, w = mask.shape
        if not (0 <= self.ox and self.ox + w <= self.SIZE and 0 <= self.oy and self.oy + h <= self.SIZE):
            print("[!] World map overflow, resetting.")
            self.reset(keep_level=True)
        view = cv2.min(mask, 1) + np.uint8(EMPTY)  # EMPTY=1, LINE=2 (uint8 all the way)
        b0, b1 = band_rows()
        if b1 >= b0:
            # Behind the header: keep whatever an earlier view saw there (UNKNOWN if never seen)
            behind = self.grid[self.oy + b0:self.oy + b1 + 1, self.ox:self.ox + w].copy()
            if "TOP" in self.edge_from_limit and self.edges["TOP"] is not None and                     self.oy <= self.edges["TOP"] + 5:
                # Camera at its top limit: nobody can ever see behind the header here, so the game
                # can't have arrows there. Count never-seen cells as empty.
                behind[behind == UNKNOWN] = EMPTY
            view[b0:b1 + 1] = behind
        self.grid[self.oy:self.oy + h, self.ox:self.ox + w] = view
        q = cv2.resize(board, (w // 4, h // 4), interpolation=cv2.INTER_AREA)
        q = np.where(q > 40, 2, 1).astype(np.uint8)          # 2 = board, 1 = seen, no board
        bq0, bq1 = band_rows()
        if bq1 >= bq0:
            q[bq0 // 4:bq1 // 4 + 1] = self.bgrid[self.oy // 4 + bq0 // 4:self.oy // 4 + bq1 // 4 + 1,
                                                   self.ox // 4:self.ox // 4 + q.shape[1]]
        self.bgrid[self.oy // 4:self.oy // 4 + q.shape[0], self.ox // 4:self.ox // 4 + q.shape[1]] = q
        # freshness of the map, per tile (only tiles fully inside the view, outside the blind band)
        now = time.time()
        tx0, tx1 = -(-self.ox // TILE), (self.ox + w) // TILE
        b0, b1 = band_rows()
        for (r0, r1) in ((0, b0), (b1 + 1, h)) if b1 >= b0 else ((0, h),):
            ty0, ty1 = -(-(self.oy + r0) // TILE), (self.oy + r1) // TILE
            if ty1 > ty0 and tx1 > tx0:
                self.tile_time[ty0:ty1, tx0:tx1] = now
        box = (self.ox, self.oy, self.ox + w, self.oy + h)
        self.seen = box if self.seen is None else (min(self.seen[0], box[0]), min(self.seen[1], box[1]),
                                                   max(self.seen[2], box[2]), max(self.seen[3], box[3]))
        self._prev_mask = mask            # its small version is made only if a pan needs it
        self._prev_small = None
        self._prev_board = board          # dots + lines: for measuring pans when few lines are left
        self._prev_line_ink = cv2.countNonZero(mask)

        bb = board_bbox(board)
        self.last_bbox = bb
        if bb is None:
            return
        x0, y0, x1, y1 = bb
        wb = (self.ox + x0, self.oy + y0, self.ox + x1, self.oy + y1)
        # Board seen past an edge we believed in => that edge was wrong: forget it
        for d, beyond in (("LEFT", wb[0] < (self.edges["LEFT"] or -1e9) - 12),
                          ("TOP", wb[1] < (self.edges["TOP"] or -1e9) - 12),
                          ("RIGHT", wb[2] > (self.edges["RIGHT"] if self.edges["RIGHT"] is not None else 1e9) + 12),
                          ("BOTTOM", wb[3] > (self.edges["BOTTOM"] if self.edges["BOTTOM"] is not None else 1e9) + 12)):
            if self.edges[d] is not None and beyond and self.too_big:
                # (only big boards: a board that fits takes its edges from every full view anyway,
                # and an arrow just starting to fly off past its edge must not wipe them)
                print(f"    [map] board seen past the {d} edge; that edge was wrong, forgetting it")
                self.edges[d] = None
                self.edge_from_limit.discard(d)
                self.at_limit.discard(d)
        self.board_box = wb if self.board_box is None else (
            min(self.board_box[0], wb[0]), min(self.board_box[1], wb[1]),
            max(self.board_box[2], wb[2]), max(self.board_box[3], wb[3]))
        # An edge only counts if this frame shows the board's whole extent along it: on irregular
        # boards, seeing just the right half might show a lower top than the left half has.
        # Edges only ever widen (the grid dots stay when arrows leave, so the board never shrinks).
        b0, b1 = band_rows()
        # the board must stay about a grid cell (+) away from the screen edges to count as ending
        # there: grid spacing is ~9 line half-widths
        em = edge_margin(self)
        under_header = b0 - em <= y0 <= b1 + em
        left_ok, top_ok = x0 > em, y0 > em and not under_header
        right_ok, bottom_ok = x1 < w - 1 - em, y1 < h - 1 - em
        full_width, full_height = left_ok and right_ok, top_ok and bottom_ok
        # sides where the board has ever run off the screen this level (it may go on there)
        for side, ok in (("LEFT", left_ok), ("TOP", top_ok), ("RIGHT", right_ok), ("BOTTOM", bottom_ok)):
            if not ok:
                self.cut_seen.add(side)
        self.still_frames += 1
        if not (full_width and full_height) and not self.too_big and self.still_frames <= 2:
            # The board runs off the screen: it's bigger than the view. From now on a view that
            # happens to show one whole island of it proves nothing about where the board ends,
            # so only the camera stopping counts as an edge (and outline-based edges are dropped).
            # Decided only on the first frames of a level, before anything is tapped: arrows
            # flying off past the screen edge later must not make a board that fits look too big.
            self.too_big = True
            for d in DIRS:
                if d not in self.edge_from_limit:
                    self.edges[d] = None
        if self.too_big:
            self.fully_visible = False
            if self.surveyed:
                self.fill_edges_from_sweep()
            return
        if full_width and full_height:
            # Board fits and is fully on screen: take its edges from THIS frame. The game can nudge
            # the camera by itself as arrows clear, so remembered edges can go stale.
            self.edges = {"LEFT": self.ox + x0, "TOP": self.oy + y0,
                          "RIGHT": self.ox + x1, "BOTTOM": self.oy + y1}
            self.fully_visible = True
            return
        def widen(side, value, outward_is_smaller):
            old = self.edges[side]
            if old is None:
                self.edges[side] = value
            else:
                self.edges[side] = min(old, value) if outward_is_smaller else max(old, value)
        if left_ok and full_height: widen("LEFT", self.ox + x0, True)
        if top_ok and full_width: widen("TOP", self.oy + y0, True)
        if right_ok and full_height: widen("RIGHT", self.ox + x1, False)
        if bottom_ok and full_width: widen("BOTTOM", self.oy + y1, False)
        self.fully_visible = full_width and full_height

def strip(arr, x, y, direction, hw, length):
    """Rows of `arr` along a ray from (x, y), nearest first. Shape: (steps, 2*hw+1)."""
    H, W = arr.shape
    length = max(0, int(length))
    y0, y1 = max(0, y - hw), min(H, y + hw + 1)
    x0, x1 = max(0, x - hw), min(W, x + hw + 1)
    if direction == "RIGHT":
        return arr[y0:y1, max(0, x):min(W, x + length)].T
    if direction == "LEFT":
        return arr[y0:y1, max(0, x - length + 1):min(W, x + 1)].T[::-1]
    if direction == "BOTTOM":
        return arr[max(0, y):min(H, y + length), x0:x1]
    return arr[max(0, y - length + 1):min(H, y + 1), x0:x1][::-1]

def first_true(rows):
    idx = np.flatnonzero(rows)
    return idx[0] if len(idx) else None

def _why_unknown(world, reason):
    w = getattr(world, "unknown_why", None)
    if w is not None:
        w[reason] = w.get(reason, 0) + 1

def cast_ray(arrow, mask, world, half):
    """
    Returns ("CLEAR" | "BLOCKED" | "UNKNOWN", hit_distance_or_None).
    Clear = nothing in the arrow's lane until it leaves the board.
    """
    h, w = mask.shape
    direction = arrow["dir"]
    tx, ty = arrow["tip_x"], arrow["tip_y"]
    hw = max(2, int(round(half * 0.75)))

    # Distances from the tip to the end of the visible ROI and to the board edge
    to_roi_end = {"RIGHT": w - tx, "LEFT": tx + 1, "BOTTOM": h - ty, "TOP": ty + 1}[direction]
    edge = world.ray_edge(direction)
    to_edge = None
    if edge is not None:
        to_edge = {"RIGHT": edge - (world.ox + tx), "LEFT": (world.ox + tx) - edge,
                   "BOTTOM": edge - (world.oy + ty), "TOP": (world.oy + ty) - edge}[direction]
        to_edge = max(0, to_edge)

    # 1) Visible part: exact, from the current frame
    vis_len = to_roi_end if to_edge is None else min(to_roi_end, to_edge + 1)
    s = strip(mask, tx, ty, direction, hw, vis_len)
    rows_hit = s.any(axis=1) if s.size else np.zeros(0, bool)
    unk_rows = None
    b0, b1 = band_rows()
    if direction in ("TOP", "BOTTOM") and b1 >= b0 and len(rows_hit):
        # Rows behind the header: use the world map (what an earlier view saw there)
        step = -1 if direction == "TOP" else 1
        ys = ty + step * np.arange(len(rows_hit))
        in_band = (ys >= b0) & (ys <= b1)
        if in_band.any():
            gx0 = world.ox + max(0, tx - hw)
            g = world.grid[world.oy + ys[in_band], gx0:world.ox + tx + hw + 1]
            rows_hit = rows_hit.copy()
            rows_hit[in_band] = (g == LINE).any(axis=1)
            unk_rows = np.zeros(len(rows_hit), bool)
            unk_rows[in_band] = (g == UNKNOWN).any(axis=1)
            top = world.edges.get("TOP") if "TOP" in world.edge_from_limit else None
            if top is not None:
                # With the camera at its top limit the header still covers these rows: nobody can
                # ever see them, so the game can't put arrows there (anywhere along the board)
                unk_rows[in_band] &= (world.oy + ys[in_band]) > top + b1
    hit = first_true(rows_hit) if len(rows_hit) else None
    unk = first_true(unk_rows) if unk_rows is not None else None
    if unk is not None and (hit is None or unk < hit):
        _why_unknown(world, "behind header")
        return "UNKNOWN", None   # path runs behind the header into something never seen
    if hit is not None:
        return "BLOCKED", hit
    if to_edge is not None and to_edge <= to_roi_end:
        return "CLEAR", None

    # 2) Off-screen part: from the stitched world map (wider lane to absorb registration error)
    if getattr(world, "pose_uncertain", False):
        _why_unknown(world, "position not confirmed yet")
        return "UNKNOWN", None   # the map may be shifted against this view: only trust what's visible
    dx, dy = DIRS[direction]
    wx, wy = world.ox + tx + dx * to_roi_end, world.oy + ty + dy * to_roi_end
    off_len = (to_edge - to_roi_end + 1) if to_edge is not None else World.SIZE
    s = strip(world.grid, wx, wy, direction, hw + 3, off_len)
    if s.size == 0:
        if to_edge is None:
            _why_unknown(world, f"no {direction} edge")
        return ("CLEAR", None) if to_edge is not None else ("UNKNOWN", None)
    line_hit = first_true((s == LINE).any(axis=1))
    unk_rows = (s == UNKNOWN).any(axis=1)
    if direction == "TOP" and "TOP" in world.edge_from_limit and world.edges.get("TOP") is not None:
        never_seen = world.edges["TOP"] + max(b1, 0)              # hidden by the header even at the limit
        unk_rows &= (wy - np.arange(len(unk_rows))) > never_seen
    unk_hit = first_true(unk_rows)
    if line_hit is not None and (unk_hit is None or line_hit < unk_hit):
        return "BLOCKED", to_roi_end + line_hit
    if unk_hit is not None:
        _why_unknown(world, f"unseen map {direction}")
        return "UNKNOWN", None
    if to_edge is None:
        _why_unknown(world, f"no {direction} edge")
        return "UNKNOWN", None
    return "CLEAR", None

def cast_world(world, tip, direction, half):
    """Same rule as cast_ray, using only the stitched map (for arrows that aren't on screen)."""
    hw = max(2, int(round(half * 0.75))) + 3
    tx, ty = tip
    edge = world.ray_edge(direction)
    to_edge = None
    if edge is not None:
        to_edge = max(0, {"RIGHT": edge - tx, "LEFT": tx - edge, "BOTTOM": edge - ty, "TOP": ty - edge}[direction])
    s = strip(world.grid, int(tx), int(ty), direction, hw, (to_edge + 1) if to_edge is not None else World.SIZE)
    if s.size == 0:
        return "CLEAR" if to_edge is not None else "UNKNOWN"
    line_hit = first_true((s == LINE).any(axis=1))
    unk_hit = first_true((s == UNKNOWN).any(axis=1))
    if line_hit is not None and (unk_hit is None or line_hit < unk_hit):
        return "BLOCKED"
    if unk_hit is not None or to_edge is None:
        return "UNKNOWN"
    return "CLEAR"

_last_debug = [0.0]

def save_debug_image(frame, results, world=None):
    # drawing + PNG encoding every frame costs CPU for nothing: twice a second is plenty
    if time.time() - _last_debug[0] < DEBUG_IMAGE_EVERY:
        return
    _last_debug[0] = time.time()
    _debug_writer.submit(draw_debug(frame, results, world))

def draw_debug(frame, results, world=None):
    """The frame with what the bot made of it: green = clear arrow (with its path), red = blocked
    (path up to what blocks it), yellow = unknown, purple ring = flying/ghost, orange = board edges."""
    debug_img = frame.copy()
    colors = {"CLEAR": (0, 255, 0), "BLOCKED": (0, 0, 255), "UNKNOWN": (0, 255, 255), "GHOST": (255, 0, 255)}
    for arrow, status, hit in results:
        col = colors[status]
        if status == "GHOST":
            cv2.circle(debug_img, (arrow["x"] + ROI_X1, arrow["y"] + ROI_Y1), 18, col, 3)
            continue
        x, y = arrow["x"] + ROI_X1, arrow["y"] + ROI_Y1
        tx, ty = arrow["tip_x"] + ROI_X1, arrow["tip_y"] + ROI_Y1
        dx, dy = DIRS[arrow["dir"]]
        cv2.circle(debug_img, (x, y), 14, col, 2)
        length = hit if hit is not None else 3000
        cv2.line(debug_img, (tx, ty), (int(tx + dx * length), int(ty + dy * length)), col, 2)
        if status == "CLEAR":
            cv2.circle(debug_img, (x, y), 10, col, -1)
    cv2.rectangle(debug_img, (ROI_X1, ROI_Y1), (ROI_X2 - 1, ROI_Y2 - 1), (255, 0, 255), 2)
    if world is not None:
        for name, val in world.edges.items():
            if val is None: continue
            if name in ("LEFT", "RIGHT"):
                x = val - world.ox + ROI_X1
                if 0 <= x < debug_img.shape[1]:
                    cv2.line(debug_img, (x, ROI_Y1), (x, ROI_Y2), (255, 128, 0), 1)
            else:
                y = val - world.oy + ROI_Y1
                if 0 <= y < debug_img.shape[0]:
                    cv2.line(debug_img, (ROI_X1, y), (ROI_X2, y), (255, 128, 0), 1)
    return debug_img


# -----------------------------------------------------------------------------
# LEVEL STATS + DIAGNOSTICS: how long levels take (normal / hard / super hard), and everything
# needed to look into a level that was slow, got stuck or cost a star (Temp/diagnostics/).
# Nothing here changes how the bot plays.
# -----------------------------------------------------------------------------
STATS_FILE = os.devnull
DIAG_DIR = os.path.join(TEMP, "diagnostics")
DIAG_KEEP = 30            # newest bundles kept
SLOW_FACTOR = 1.6         # a level this many times its kind's average (and 15s more) counts as slow
# A level that needs no panning should take less than this (else: diagnostics). Its time is set by
# the longest chain of arrows freeing each other (waves), not the arrow count: each link waits ~0.3s
# for the arrow in front to visibly fly off (measured: 18 waves ~10s, 49 waves ~21s).
LEVEL_BASE_SECONDS = 7.0
LEVEL_SECONDS_PER_WAVE = 0.3
MANY_PANS = 30

def level_target_seconds(waves):
    return LEVEL_BASE_SECONDS + LEVEL_SECONDS_PER_WAVE * max(waves, 8)

def level_kind(frame):
    """The tag on the level header: none = normal, magenta "HARD LEVEL", red "SUPER HARD"."""
    if AMAZE:   # a purple "Hard" under the title
        hsv = cv2.cvtColor(frame[228:272, 470:620], cv2.COLOR_BGR2HSV)
        purple = (hsv[:, :, 0] >= 120) & (hsv[:, :, 0] <= 160) & (hsv[:, :, 1] > 80)
        return "hard" if np.count_nonzero(purple) > 150 else "normal"
    hsv = cv2.cvtColor(frame[105:165, 50:290], cv2.COLOR_BGR2HSV)
    lit = (hsv[:, :, 1] > 120) & (hsv[:, :, 2] > 120)
    if np.count_nonzero(lit) < 300:
        return "normal"
    hue = float(np.median(hsv[:, :, 0][lit]))
    if hue <= 15 or hue >= 165:
        return "super hard"
    if 125 <= hue < 165:
        return "hard"
    return "normal"

def _log_size():
    try:
        return os.path.getsize(os.path.join(TEMP, "bot.log"))
    except OSError:
        return 0

class LevelRecorder:
    KINDS = ("normal", "hard", "super hard")

    def __init__(self):
        self.cur = None
        try:
            with open(STATS_FILE, encoding="utf-8") as f:
                self.levels = json.load(f).get("levels", [])
        except (OSError, ValueError):
            self.levels = []

    # ---- during a level
    def start(self, frame):
        if self.cur is not None:
            self.end("unfinished", None)
        self.cur = {"kind": level_kind(frame), "t0": time.time(), "log_pos": _log_size(),
                    "pans": 0, "taps": 0, "mistakes": 0, "stuck": 0, "waves": 0, "events": [], "progress": 0.0,
                    "shots": [], "last_check": 0.0, "stars": 3}
        print(f"[stats] new {self.cur['kind']} level")

    def note(self, key, n=1):
        if self.cur is not None:
            self.cur[key] += n

    def waves(self, n):
        """Deepest plan seen: on a board that fits, the first plan covers the whole chain."""
        if self.cur is not None:
            self.cur["waves"] = max(self.cur["waves"], n)

    def seen(self, frame, stars):
        """Called on level frames: remembers how far the progress bar got (to tell a finished
        level from a popup in the middle of one)."""
        c = self.cur
        if c is None or time.time() - c["last_check"] < 0.3:
            return
        c["last_check"] = time.time()
        c["progress"] = max(c["progress"], progress_fill(frame))
        if stars is not None:
            c["stars"] = stars

    def event(self, what, frame, results=(), world=None):
        """Something worth looking at later: keep the screen as the bot saw it (max 8 per level)."""
        c = self.cur
        if c is None:
            return
        t = time.time() - c["t0"]
        c["events"].append(f"{t:6.1f}s {what}")
        if frame is not None and len(c["shots"]) < 8:
            try:
                img = draw_debug(frame, list(results), world)
                ok, png = cv2.imencode(".jpg", cv2.resize(img, None, fx=0.5, fy=0.5), [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ok:
                    c["shots"].append((f"{len(c['shots'])}_{t:05.1f}s_{re.sub(r'[^a-z]+', '_', what.lower())[:30]}.jpg", png))
            except Exception:
                pass

    def finished(self):
        c = self.cur
        return c is not None and (c["progress"] >= 0.9 or c["stars"] == 0)

    # ---- level over
    def end(self, result, world):
        c, self.cur = self.cur, None
        if c is None:
            return
        dur = time.time() - c["t0"]
        rec = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": c["kind"], "result": result,
               "seconds": round(dur, 1), "pans": c["pans"], "taps": c["taps"],
               "mistakes": c["mistakes"], "stuck": c["stuck"], "waves": c["waves"]}
        avg_before = self.average(c["kind"])
        reasons = []
        if c["stuck"]:
            reasons.append("stuck")
        if c["mistakes"]:
            reasons.append("mistake")
        if result == "lost":
            reasons.append("lost")
        if result == "won" and ((c["pans"] == 0 and dur > level_target_seconds(c["waves"])) or
                                (avg_before and dur > max(SLOW_FACTOR * avg_before[0], avg_before[0] + 15))):
            reasons.append("slow")
        if c["pans"] >= MANY_PANS:
            reasons.append("many pans")
        rec["flagged"] = reasons
        self.levels.append(rec)
        self.levels = self.levels[-1000:]
        try:
            with open(STATS_FILE, "w", encoding="utf-8") as f:
                json.dump({"averages": self.summary_dict(), "levels": self.levels}, f, indent=1)
        except OSError:
            pass
        print(f"[stats] {c['kind']} level {result} in {dur:.1f}s ({c['pans']} pans, {c['taps']} taps"
              + (f", {c['mistakes']} star(s) lost" if c["mistakes"] else "") + ")"
              )
        print("[stats] averages: " + self.summary_text())
        if False:
            self._bundle(c, rec, reasons, world)

    def average(self, kind=None):
        times = [r["seconds"] for r in self.levels if r["result"] == "won" and (kind is None or r["kind"] == kind)]
        if len(times) < 3:
            return None
        return sum(times) / len(times), len(times)

    def summary_dict(self):
        out = {}
        for k in self.KINDS + (None,):
            times = [r["seconds"] for r in self.levels if r["result"] == "won" and (k is None or r["kind"] == k)]
            out[k or "all"] = {"levels": len(times), "average_seconds": round(sum(times) / len(times), 1) if times else None}
        return out

    def summary_text(self):
        d = self.summary_dict()
        return " | ".join(f"{k} {v['average_seconds']}s ({v['levels']})" if v["levels"] else f"{k} -"
                          for k, v in d.items())

    def _bundle(self, c, rec, reasons, world):
        """Everything needed to look into this level later, in one folder."""
        try:
            name = time.strftime("%Y%m%d_%H%M%S") + "_" + c["kind"].replace(" ", "") + "_" + "_".join(r.replace(" ", "") for r in reasons)
            d = os.path.join(DIAG_DIR, name)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "summary.json"), "w", encoding="utf-8") as f:
                json.dump(dict(rec, events=c["events"], average_for_kind=self.average(c["kind"])), f, indent=1)
            try:                                            # the level's part of the log
                with open(os.path.join(TEMP, "bot.log"), encoding="utf-8", errors="ignore") as f:
                    f.seek(c["log_pos"])
                    part = f.read()
                with open(os.path.join(d, "log.txt"), "w", encoding="utf-8") as f:
                    f.write(part)
                with open(os.path.join(d, "timing.txt"), "w", encoding="utf-8") as f:
                    f.write(level_timing(part))
            except OSError:
                pass
            for fname, png in c["shots"]:
                with open(os.path.join(d, fname), "wb") as f:
                    f.write(png.tobytes())
            if world is not None:
                with open(os.path.join(d, "state.json"), "w", encoding="utf-8") as f:
                    json.dump({"camera": [world.ox, world.oy], "edges": world.edges,
                               "edges_from_camera_limits": sorted(world.edge_from_limit),
                               "at_limit": sorted(world.at_limit), "board_bigger_than_screen": world.too_big,
                               "surveyed": world.surveyed, "board_ran_off": sorted(getattr(world, "cut_seen", [])),
                               "board_box": world.board_box, "remembered_arrows": len(world.arrows),
                               "position_uncertain": world.pose_uncertain, "grid_step": world.grid_step,
                               "line_half": getattr(world, "half", None), "pan_gain": pan_gain},
                              f, indent=1, default=str)
                if world.seen is not None:                  # the map: white = arrow lines, grey = empty
                    x0, y0, x1, y1 = world.seen
                    g = world.grid[y0:y1, x0:x1]
                    img = np.zeros(g.shape, np.uint8)
                    img[g == EMPTY] = 70
                    img[g == LINE] = 255
                    img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
                    cv2.rectangle(img, (world.ox - x0, world.oy - y0),
                                  (world.ox - x0 + ROI_X2 - ROI_X1, world.oy - y0 + ROI_Y2 - ROI_Y1), (0, 200, 255), 4)
                    cv2.imwrite(os.path.join(d, "map.png"), cv2.resize(img, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA))
            old = sorted(glob.glob(os.path.join(DIAG_DIR, "*")))
            for o in old[:-DIAG_KEEP]:
                import shutil
                shutil.rmtree(o, ignore_errors=True)
            print(f"[stats] saved what's needed to look into this level: Temp/diagnostics/{name}")
        except Exception as e:
            print(f"[-] couldn't save the diagnostics: {e}")

recorder = None

def level_timing(log_text):
    """Where a level's time went, from its log: time to the first tap, panning, zooming, the
    longest stretches without a tap (with what was going on), and the wait after the last tap."""
    rows = []
    for line in log_text.splitlines():
        m = re.match(r"(\d\d):(\d\d):(\d\d\.\d{3}) (.*)", line)
        if m:
            rows.append((int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)), m.group(4)))
    if len(rows) < 2:
        return "not enough log lines\n"
    t0, t_end = rows[0][0], rows[-1][0]
    taps = [t for t, x in rows if x.startswith("[+] Tapping") and "arrows" in x]
    pan_s = sum(float(v) for v in re.findall(r"\[pan\] took ([\d.]+)s", log_text))
    pans = len(re.findall(r"\[>\] .*Panning", log_text))
    zoom_s = 0.0
    for (t1, x1), (t2, _) in zip(rows, rows[1:]):
        if "Zooming out" in x1 or "[zoom]" in x1:
            zoom_s += t2 - t1
    gaps = sorted(((t2 - t1, x1[:120], x2[:120]) for (t1, x1), (t2, x2) in zip(rows, rows[1:])),
                  reverse=True)[:5]
    out = [f"level time        {t_end - t0:6.1f}s",
           f"first arrow tap   {(taps[0] - t0) if taps else float('nan'):6.1f}s after the level appeared",
           f"after last tap    {(t_end - taps[-1]) if taps else float('nan'):6.1f}s until the level ended",
           f"panning           {pan_s:6.1f}s in {pans} pans",
           f"zooming           {zoom_s:6.1f}s",
           f"tap batches       {len(taps)}",
           "longest pauses between log lines (what came before -> after):"]
    out += [f"  {g:5.2f}s  {x1}\n         -> {x2}" for g, x1, x2 in gaps]
    return "\n".join(out) + "\n"

def save_crash_bundle(rc):
    """The bot process died: keep the end of the log and its last view (Temp/diagnostics/)."""
    return None
    try:
        d = os.path.join(DIAG_DIR, time.strftime("%Y%m%d_%H%M%S") + "_crash")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "crash.txt"), "w", encoding="utf-8") as f:
            f.write(f"exit code {rc:#x}\n")
        try:
            with open(os.path.join(TEMP, "bot.log"), encoding="utf-8", errors="ignore") as f:
                tail = f.readlines()[-600:]
            with open(os.path.join(d, "log_tail.txt"), "w", encoding="utf-8") as f:
                f.writelines(tail)
        except OSError:
            pass
        src_img = os.path.join(TEMP, "debug_vision.png")
        if os.path.exists(src_img):
            import shutil
            shutil.copy(src_img, os.path.join(d, "last_view.png"))
        return d
    except Exception:
        return None

class _DebugWriter:
    """Writes debug_vision.png off the main loop (PNG encoding costs ~20ms per frame)."""
    def __init__(self):
        self.pending = None
        self.cond = threading.Condition()
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, img):
        with self.cond:
            self.pending = img
            self.cond.notify()

    def flush(self):
        while self.pending is not None:
            time.sleep(0.01)

    def _loop(self):
        while True:
            with self.cond:
                while self.pending is None:
                    self.cond.wait()
                img = self.pending
            cv2.imwrite(os.path.join(TEMP, "debug_vision.png"), img)
            with self.cond:
                if self.pending is img:
                    self.pending = None

_debug_writer = _DebugWriter()

def analyze(frame, world):
    roi = frame[ROI_Y1:ROI_Y2, ROI_X1:ROI_X2]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    keep = {}
    mask, board = build_masks(roi, hsv, keep)
    if getattr(world, "half_cached", None) is None or getattr(world, "half_age", 0) >= 29:
        step = measure_grid_step(board, mask)
        if step is not None:
            world.grid_step = step
    still = still_mask(hsv)
    resting = cv2.bitwise_and(mask, still)
    board = cv2.bitwise_and(board, still)   # an arrow flying off past the edge isn't more board
    # line thickness only changes with zoom: measure it now and then, not every frame
    world.half_age = getattr(world, "half_age", 0) + 1
    cached = getattr(world, "half_cached", None) if world.half_age < 30 else None
    arrows, half = detect_arrows(mask, hsv, keep, half=cached)
    world.half_cached = half
    if cached is None:
        world.half_age = 0
    # Arrows we tapped are flying out along their own lane. On some levels they keep the resting
    # colour (blue only briefly), so they'd look like new arrows: tapped again, remembered and
    # chased later, and blocking other lanes. A head moving down the lane of a recent tap, same
    # direction, is that flying arrow: a ghost, and its line comes off the resting picture.
    flying_heads = mark_flying(arrows, world, half)
    if flying_heads:
        labels = keep.get("labels")
        cores = keep.get("core_centers", np.zeros((0, 2)))
        for a in flying_heads:
            if labels is None:
                break
            L = labels[a["y"], a["x"]]
            if not L:
                continue
            # only if this line blob holds just this head (never erase a resting arrow with it)
            n_heads = sum(1 for cx, cy in cores if labels[int(round(cy)), int(round(cx))] == L)
            if n_heads > 1:
                continue
            bx, by, bw, bh = (int(v) for v in keep["stats"][L][:4])
            sel = labels[by:by + bh, bx:bx + bw] == L
            resting[by:by + bh, bx:bx + bw][sel] = 0
            board[by:by + bh, bx:bx + bw][sel] = 0     # flying past the edge isn't "more board" either
    if world.pose_uncertain and time.time() - world.pose_check_t > 0.4:
        world.pose_check_t = time.time()
        pose = None
        if cv2.countNonZero(resting) >= RELOC_MIN_INK:
            pose = world.relocalize(resting, near=RELOC_NEAR)
            if pose is None and time.time() - getattr(world, "reloc_full_t", 0.0) > 2.0:
                world.reloc_full_t = time.time()       # the whole map: rarely (it's the slow one)
                pose = world.relocalize(resting)
        if pose is not None:
            if abs(pose[0] - world.ox) > 8 or abs(pose[1] - world.oy) > 8:
                print(f"    [map] view found on the map {pose[0] - world.ox:+d},{pose[1] - world.oy:+d} px "
                      f"from where the pan put it; corrected")
                world.ox, world.oy = pose
            else:
                print("    [map] position confirmed against the map")
            world.pose_uncertain = False
        elif world.board_pose_agrees(board):
            print("    [map] position confirmed against the board's dots")
            world.pose_uncertain = False
    world.integrate(resting, board)   # flying arrows never go on the map (they'd linger as blockers)
    world.frame_data = keep
    # Lines of arrows at rest (lavender, or red after a bounce). Arrows flying out tint blue.
    world.resting_mask = resting
    world.remember_arrows(arrows, mask.shape)
    world.half = half
    results = []
    world.unknown_why = {}
    for arrow in arrows:
        if not arrow["solid"]:
            results.append((arrow, "GHOST", None))  # never tapped, but its pixels still block rays
            continue
        status, hit = cast_ray(arrow, mask, world, half)
        results.append((arrow, status, hit))
    return mask, results, half

FLIGHT_MEMORY = 3.0   # seconds a tapped arrow counts as flying out along its lane
FLIGHT_KEEP = 8.0     # seconds a tap is remembered (to erase the arrow from the map once it's gone)

def mark_flying(arrows, world, half):
    """Mark heads that are a recently tapped arrow flying out (same direction, on its lane,
    already moved forward) as not solid. Returns them."""
    flights = getattr(world, "flights", [])
    if not flights:
        return []
    now = time.time()
    out = []
    for a in arrows:
        if not a["solid"]:
            continue
        wx, wy = world.ox + a["x"], world.oy + a["y"]
        for f in flights:
            fx, fy, d, t = f["wx"], f["wy"], f["dir"], f["t"]
            if d != a["dir"] or now - t > FLIGHT_MEMORY:
                continue
            dx, dy = DIRS[d]
            along = (wx - fx) * dx + (wy - fy) * dy
            side = abs((wx - fx) * dy - (wy - fy) * dx)
            if along > 3 * half and side <= 2 * half + 4:
                a["solid"] = False
                a["flying"] = True
                f["left"] = True               # seen flying down its lane: it really went
                out.append(a)
                break
    return out

def lane_rect(arrow, half, shape):
    """Rectangle (x0, y0, x1, y1) an arrow sweeps when it flies out: tip to the edge of the view."""
    h, w = shape
    hw = max(2, int(round(half * 0.75))) + 3
    tx, ty = arrow["tip_x"], arrow["tip_y"]
    return {"RIGHT": (tx, ty - hw, w - 1, ty + hw), "LEFT": (0, ty - hw, tx, ty + hw),
            "BOTTOM": (tx - hw, ty, tx + hw, h - 1), "TOP": (tx - hw, 0, tx + hw, ty)}[arrow["dir"]]

def rects_overlap(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])

def plan_waves(results, mask, world, half, prev, is_recent):
    """
    Same rules as a single pass, applied ahead of time:
      wave 1 = arrows that are still (same spot in the previous frame) and whose lane is clear in
               BOTH frames;
      then those arrows are erased from the picture and the remaining still arrows are re-checked,
      giving wave 2, and so on. Saves a screenshot round-trip (the pause) per wave.
    An arrow is only erased if its line blob holds exactly one head (so two touching arrows are
    never erased together); otherwise its pixels stay and keep blocking.
    """
    if prev is None:
        return []
    def still(a):
        return any(b["solid"] and b["dir"] == a["dir"] and abs(b["x"] - a["x"]) <= 3
                   and abs(b["y"] - a["y"]) <= 3 for b in prev["arrows"])

    pool = [a for a, _, _ in results if a["solid"] and not is_recent(a)]
    moving = [a for a in pool if not still(a)]
    for a in moving:
        print(f"    [ghost] skipped moving arrow at ({a['x'] + ROI_X1},{a['y'] + ROI_Y1})")
    pool = [a for a in pool if still(a)]
    if not pool:
        return []

    work = cv2.bitwise_or(mask, prev["mask"])
    fd = getattr(world, "frame_data", {})
    if "labels" in fd and fd["labels"].shape == mask.shape:
        labels, lstats = fd["labels"], fd["stats"]          # computed once this frame already
    else:
        _, labels, lstats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    n_labels = len(lstats)
    # Count arrowhead-like blobs per line blob, not just detected arrows: if a blob holds a head we
    # failed to recognise (clipped, odd shape), erasing it would wrongly free lanes behind it.
    if "core_centers" in fd:
        core_centers = fd["core_centers"]
    else:
        dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
        _, _, _, cc = cv2.connectedComponentsWithStats(((dist > half * 1.35) * 255).astype(np.uint8))
        core_centers = cc[1:]
    heads = {}
    for cx, cy in core_centers:
        lab = labels[int(round(cy)), int(round(cx))]
        heads[lab] = heads.get(lab, 0) + 1
    for a, _, _ in results:
        lab = labels[a["y"], a["x"]]
        heads[lab] = max(heads.get(lab, 0), 1)
    kernel = np.ones((3, 3), np.uint8)
    # Line blobs touching the edge of the view may continue off-screen into other arrows whose
    # heads we can't see, so those are never erased (they keep blocking).
    h, w = mask.shape
    edge_labels = set(np.unique(np.concatenate([labels[:2].ravel(), labels[-2:].ravel(),
                                                labels[:, :2].ravel(), labels[:, -2:].ravel()])))

    # Which line blobs could be erased once their arrow is planned (one head, not running off-view)
    erasable = np.zeros(n_labels, bool)
    for lab, cnt in heads.items():
        if lab and cnt == 1 and lab not in edge_labels:
            erasable[lab] = True
    # Label every pixel of `work`: current-frame blobs keep their label; pixels only in the previous
    # frame take the label of a blob within 2px (an erase grows the blob by 2px), else -1 = fixed.
    # (labels are already 0 wherever this frame's mask is empty: specks were cleared from both)
    labwork = labels.astype(np.int32, copy=True)
    prev_only = cv2.bitwise_and(prev["mask"], cv2.bitwise_not(mask))
    if cv2.countNonZero(prev_only):
        # grow labels only around the previous-frame pixels (a 5x5 dilation needs 2px of margin)
        px, py, pw, ph = cv2.boundingRect(prev_only)
        gx0, gy0 = max(0, px - 2), max(0, py - 2)
        gx1, gy1 = min(w, px + pw + 2), min(h, py + ph + 2)
        grown = cv2.dilate(labels[gy0:gy1, gx0:gx1].astype(np.float32),
                           np.ones((5, 5), np.uint8)).astype(np.int32)
        ys, xs = np.nonzero(prev_only[gy0:gy1, gx0:gx1])
        g = grown[ys, xs]
        labwork[ys + gy0, xs + gx0] = np.where(g > 0, g, -1)
    # Everything in `work` stays, except the pixels of blobs that can be erased (cleared inside
    # each blob's own box, grown by the 2px the previous frame may add)
    static = work.copy()
    for lab in np.flatnonzero(erasable):
        bx, by, bw_, bh_ = (int(v) for v in lstats[lab][:4])
        x0, y0 = max(0, bx - 2), max(0, by - 2)
        x1, y1 = min(w, bx + bw_ + 2), min(h, by + bh_ + 2)
        static[y0:y1, x0:x1][labwork[y0:y1, x0:x1] == lab] = 0

    # For each arrow: (1) its path must be clear of everything that can never be erased (incl. the
    # off-screen map and what's behind the header) -> one ray; (2) the erasable blobs in its path.
    # Then waves come from those dependencies alone: no ray is cast again per wave.
    hw = max(2, int(round(half * 0.75)))
    needs = {}
    for a in pool:
        if cast_ray(a, static, world, half)[0] != "CLEAR":
            continue
        d = a["dir"]
        tx, ty = a["tip_x"], a["tip_y"]
        to_roi_end = {"RIGHT": w - tx, "LEFT": tx + 1, "BOTTOM": h - ty, "TOP": ty + 1}[d]
        edge = world.ray_edge(d)
        vis_len = to_roi_end
        if edge is not None:
            to_edge = max(0, {"RIGHT": edge - (world.ox + tx), "LEFT": (world.ox + tx) - edge,
                              "BOTTOM": edge - (world.oy + ty), "TOP": (world.oy + ty) - edge}[d])
            vis_len = min(to_roi_end, to_edge + 1)
        s = strip(labwork, tx, ty, d, hw, vis_len)
        needs[id(a)] = set(np.unique(s).tolist()) - {0}

    waves = []
    erased = set()
    remaining = [a for a in pool if id(a) in needs]
    for _ in range(MAX_WAVES):
        wave = [a for a in remaining if needs[id(a)] <= erased]
        if not wave:
            break
        waves.append(wave)
        for a in wave:
            lab = labels[a["y"], a["x"]]
            if lab and erasable[lab]:
                erased.add(int(lab))
        remaining = [a for a in remaining if not any(a is b for b in wave)]
    return waves

# -----------------------------------------------------------------------------
# 4. PAN PLANNING
# -----------------------------------------------------------------------------
def cut_off_sides(world, w, h):
    """
    Sides of the current view where the board runs off (it continues past the screen there),
    with how much board is drawn near that side, e.g. {"RIGHT": 5400, "BOTTOM": 1200}.
    """
    bb = world.last_bbox
    if bb is None:
        return {}
    x0, y0, x1, y1 = bb
    b0, b1 = band_rows()
    em = edge_margin(world)
    view = world.grid[world.oy:world.oy + h, world.ox:world.ox + w] == LINE
    near = 150
    cut = {}
    if x0 <= em:
        cut["LEFT"] = int(view[:, :near].sum())
    if x1 >= w - 1 - em:
        cut["RIGHT"] = int(view[:, -near:].sum())
    if y0 <= em or b0 - em <= y0 <= b1 + em:
        top = b1 + 1 if b1 >= b0 else 0
        cut["TOP"] = int(view[:top + near].sum())
    if y1 >= h - 1 - em:
        cut["BOTTOM"] = int(view[-near:].sum())
    return cut

def plan_survey(world, w, h):
    """Sweep only along the axes where the board doesn't fit, starting from the side where it's
    cut off (or, if cut off on both sides, the side with more board near it). Always going to the
    top-left first wasted pans up and left and found the right/bottom arrows late."""
    cut = cut_off_sides(world, w, h)
    def pick(a, b):
        if a in cut and b in cut:
            return a if cut[a] >= cut[b] else b
        return a if a in cut else b if b in cut else None
    v, hz = pick("TOP", "BOTTOM"), pick("LEFT", "RIGHT")
    return {"v": v, "h": hz, "why": f"board runs off {sorted(cut) or 'nowhere'}: start {v or '-'}/{hz or '-'}"}

UNSEEN_NEAR = 12          # map cells (4 px) from seen board within which never-seen map counts
UNSEEN_MIN_CELLS = 60     # ...and this many such cells together (~1000 px²) before going to look
UNSEEN_MAX_PANS = 6       # pans to unseen board per map (a stuck level redoes the map: 6 more)

def unseen_board(world, w, h, cx, cy, limit=12):
    """
    Spots next to the board that were never on screen (world coords), nearest to (cx, cy) first.
    Uses the coarse board map (bgrid: 0 never seen, 1 seen without board, 2 board), so it's cheap.
    Left out: what the camera can't ever show - past an edge the camera stopped at, and the strip
    the header hides even at the top limit.
    """
    if world.seen is None:
        return []
    pad = 4 * (UNSEEN_NEAR + 2)
    x0, y0 = max(0, world.seen[0] - pad), max(0, world.seen[1] - pad)
    x1, y1 = min(World.SIZE, world.seen[2] + pad), min(World.SIZE, world.seen[3] + pad)
    q = world.bgrid[y0 // 4:y1 // 4, x0 // 4:x1 // 4]
    if q.size == 0 or not (q == 2).any():
        return []
    k = 2 * UNSEEN_NEAR + 1
    near_board = cv2.dilate((q == 2).astype(np.uint8), np.ones((k, k), np.uint8))
    cand = ((q == 0) & (near_board > 0)).astype(np.uint8)
    b0, b1 = band_rows()
    for d in ("LEFT", "TOP", "RIGHT", "BOTTOM"):
        e = world.edges[d] if d in world.edge_from_limit else None
        if e is None:
            continue
        if d == "LEFT":
            cand[:, :max(0, (e - x0) // 4 + 1)] = 0
        elif d == "RIGHT":
            cand[:, max(0, (e - x0) // 4):] = 0
        elif d == "TOP":
            cand[:max(0, (e + max(b1, 0) - y0) // 4 + 1)] = 0
        else:
            cand[max(0, (e - y0) // 4):] = 0
    n, _, stats, cents = cv2.connectedComponentsWithStats(cand, connectivity=8)
    spots = []
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= UNSEEN_MIN_CELLS:
            fx, fy = x0 + 4 * cents[i][0], y0 + 4 * cents[i][1]
            spots.append((math.hypot(fx - cx, fy - cy), fx, fy))
    return [(fx, fy) for _, fx, fy in sorted(spots)[:limit]]

def choose_pan(results, world, mask_shape, skip=lambda wx, wy, d: False):
    """Returns (direction, reason, amount_or_None). skip(wx, wy, dir): arrows never to chase."""
    h, w = mask_shape
    cx, cy = world.ox + w / 2, world.oy + h / 2

    def reached(d):
        """Camera already at the board's edge that way (known from an earlier stop)."""
        if d in world.at_limit:
            return True
        if d not in world.edge_from_limit or world.edges[d] is None:
            return False
        view = {"LEFT": world.ox, "RIGHT": world.ox + w, "TOP": world.oy, "BOTTOM": world.oy + h}[d]
        return abs(view - world.edges[d]) <= LIMIT_REACHED_TOL

    b0, b1 = band_rows()
    m = head_border(getattr(world, "half", 4.0)) + 6
    few_lines = getattr(world, "_prev_line_ink", LOW_LINE_INK) < LOW_LINE_INK

    def visible(a):
        """Where an arrowhead can actually be detected (not at the very screen edge, not behind
        the header). Arrows anywhere else count as off-screen, so the camera goes to get them.
        At a camera limit an arrow near that screen edge is as visible as it will ever get."""
        x, y = a["wx"] - world.ox, a["wy"] - world.oy
        ml = 0 if reached("LEFT") else m
        mr = 0 if reached("RIGHT") else m
        mt = 0 if reached("TOP") else m
        mb = 0 if reached("BOTTOM") else m
        return ml <= x < w - mr and mt <= y < h - mb and not (b0 - m <= y <= b1 + m)

    def room(d):
        """How far the camera can still go that way (None = unknown)."""
        if d not in world.edge_from_limit or world.edges[d] is None:
            return None
        e = world.edges[d]
        if d == "LEFT":
            return max(0, world.ox - e)
        if d == "RIGHT":
            return max(0, e - (world.ox + w - 1))
        if d == "TOP":
            return max(0, world.oy - e)
        return max(0, e - (world.oy + h - 1))

    def span(d):
        """Visible size of the view along d's axis (below the header for up/down)."""
        return w if d in ("LEFT", "RIGHT") else h - (b1 + 1 if b1 >= b0 else 0)

    def sweep(d):
        """Survey pan length toward d: the usual 55% of the view, or - when that side's camera limit
        is known and close enough - exactly to it, so a row ends in one pan (a 55% pan used to stop
        just short and a 2nd pan of ~30px followed, only to get there)."""
        r = room(d)
        if r is not None and r <= span(d) * PAN_MAX_TO_EDGE:
            return max(40, r + 12)
        return span(d) * SURVEY_FRACTION

    def go_to(a, reason, key=None):
        """One pan that brings the arrow to the middle of the visible area (or as far as the camera
        goes). Moves on both axes when needed: an arrow hidden behind the header stays hidden after
        a sideways pan, so it also has to come down (one diagonal drag)."""
        if key is not None:
            if world.chased.get(key, 0) >= 2:
                return None          # tried twice already without tapping anything: give up on it
        x, y = a["wx"] - world.ox, a["wy"] - world.oy
        vis_top = b1 + 1 if b1 >= b0 else 0
        tx, ty = w / 2, (vis_top + h) / 2
        moves = {}
        if not (m <= x < w - m):
            moves["RIGHT" if x > tx else "LEFT"] = abs(x - tx)
        if not (m <= y < h - m) or (b1 >= b0 and b0 - m <= y <= b1 + m):
            moves["BOTTOM" if y > ty else "TOP"] = abs(y - ty)
        if not moves:
            d, dist = toward(a["wx"], a["wy"])
            moves[d] = dist
        for d in list(moves):
            r = room(d)
            if d in world.at_limit or (r is not None and r < 8):
                del moves[d]         # camera can't go any further that way
                continue
            if r is not None and moves[d] >= r and r <= span(d) * PAN_MAX_TO_EDGE:
                moves[d] = r + 12    # all the way to the known limit in one pan
            else:
                moves[d] = min(moves[d], r if r is not None else 1e9, span(d) * PAN_MAX)
        if not moves:
            return None
        if key is not None:
            world.chased[key] = world.chased.get(key, 0) + 1
        if few_lines and len(moves) > 1:                   # (no diagonals on dots alone, as above)
            moves = dict([max(moves.items(), key=lambda kv: kv[1] / span(kv[0]))])
        if len(moves) == 1:
            d, dist = next(iter(moves.items()))
            return d, reason, dist
        v = next(d for d in moves if d in ("TOP", "BOTTOM"))
        hz = next(d for d in moves if d in ("LEFT", "RIGHT"))
        return f"{v}+{hz}", reason, moves

    def toward(wx, wy):
        dx, dy = wx - cx, wy - cy
        if abs(dx) / w >= abs(dy) / h:
            return ("RIGHT" if dx > 0 else "LEFT"), abs(dx)
        return ("BOTTOM" if dy > 0 else "TOP"), abs(dy)

    # 0) Board bigger than the screen and not swept yet: one systematic sweep maps everything,
    #    starting from the side it runs off toward, sweeping only the axes where it doesn't fit.
    if world.too_big and not world.surveyed:
        if world.survey_plan is None:
            world.survey_plan = plan_survey(world, w, h)
            print(f"    [survey] {world.survey_plan['why']}")
        sp = world.survey_plan
        if not world.survey_started:
            # first to the corner the board runs off toward (one diagonal drag if both ways - but
            # not with only dots on screen: a diagonal pan can't be measured on the dots, the map
            # was redone, the same diagonal planned again... forever)
            if sp["v"] and sp["h"] and not reached(sp["v"]) and not reached(sp["h"]) and not few_lines:
                return (f"{sp['v']}+{sp['h']}", f"survey: to the {sp['v'].lower()}-{sp['h'].lower()} corner",
                        {sp["v"]: sweep(sp["v"]), sp["h"]: sweep(sp["h"])})
            if sp["v"] and not reached(sp["v"]):
                return sp["v"], f"survey: to the {sp['v'].lower()} edge", sweep(sp["v"])
            if sp["h"] and not reached(sp["h"]):
                return sp["h"], f"survey: to the {sp['h'].lower()} edge", sweep(sp["h"])
            world.survey_started = True
            world.survey_dir = OPPOSITE[sp["h"]] if sp["h"] else None
        # then rows: across (only if it doesn't fit sideways), a step, back across, ...
        if world.survey_dir and not reached(world.survey_dir):
            return world.survey_dir, "survey: across", sweep(world.survey_dir)
        step = OPPOSITE[sp["v"]] if sp["v"] else None
        if step is None or reached(step):
            # The plan came from the first view only. An irregular board can turn out to run off
            # a side the plan didn't sweep (LVL 245 / 265: "runs off LEFT/RIGHT", but one end
            # reaches up past the screen). Sweep that way too before calling the board mapped.
            # (a side whose camera limit was hit is done, wherever the camera is now)
            extra = [d for d in ("TOP", "BOTTOM", "LEFT", "RIGHT")
                     if d in world.cut_seen and d not in world.edge_from_limit
                     and not reached(d) and d not in world.survey_extended]
            if extra:
                d = extra[0]
                world.survey_extended.add(d)
                print(f"    [survey] the board also runs off {d}: sweeping that way too")
                if d in ("TOP", "BOTTOM"):
                    sp["v"] = OPPOSITE[d]            # rows now step toward d
                    if world.survey_dir:
                        world.survey_dir = OPPOSITE[world.survey_dir]
                    return d, "survey: next row", sweep(d)
                sp["h"] = OPPOSITE[d]
                world.survey_dir = d                 # rows now go across toward d
                return d, "survey: across", sweep(d)
            world.surveyed = True
            world.fill_edges_from_sweep()
            print("    [survey] whole board mapped")
        else:
            if world.survey_dir:
                world.survey_dir = OPPOSITE[world.survey_dir]
            return step, "survey: next row", sweep(step)

    # A board that fits the screen has nothing off-screen: no chasing or refreshing (recentering
    # takes care of a board that got pushed partly out of view)
    if not world.too_big:
        return None, "board fits on screen", None

    # 1) An arrow remembered off-screen that the map says can escape: go straight to it. Chases
    #    are counted per spot, not per arrow key: the key comes from the arrow's exact position,
    #    which shifts a few px between visits, and every shift gave it two more chases.
    half = getattr(world, "half", 4.0)
    def chase_key(a):
        return ("arrow", int(a["wx"] // 60), int(a["wy"] // 60), a["dir"])

    def can_be_seen(a):
        """Somewhere the camera can show: inside the known limits, and not in the strip the header
        hides even at the top limit. A remembered arrow anywhere else is a misplaced memory (the
        map drifted): it can never be confirmed gone, so it got chased again and again."""
        # (well inside the hidden areas only: an arrow just below the header at the top limit is
        #  real and findable - forgetting those made the bot search blind for the last arrow)
        e = world.edges
        lim = world.edge_from_limit
        far = 3 * m
        if "TOP" in lim and e["TOP"] is not None and b1 >= b0 and \
                e["TOP"] + b0 + far <= a["wy"] <= e["TOP"] + b1 - far:
            return False
        return not (("LEFT" in lim and e["LEFT"] is not None and a["wx"] < e["LEFT"] - far) or
                    ("RIGHT" in lim and e["RIGHT"] is not None and a["wx"] > e["RIGHT"] + far) or
                    ("TOP" in lim and e["TOP"] is not None and a["wy"] < e["TOP"] - far) or
                    ("BOTTOM" in lim and e["BOTTOM"] is not None and a["wy"] > e["BOTTOM"] + far))

    for k in [k for k, a in world.arrows.items()
              if not can_be_seen(a) and world.chased.get(chase_key(a), 0) >= 1]:   # chased in vain
        print(f"    [map] forgetting an arrow remembered where the camera can't show it "
              f"({world.arrows[k]['wx']},{world.arrows[k]['wy']})")
        del world.arrows[k]
    free = [a for a in world.arrows.values()
            if not visible(a)
            and world.chased.get(chase_key(a), 0) < 2 and not skip(a["wx"], a["wy"], a["dir"])
            and cast_world(world, a["tip"], a["dir"], half) == "CLEAR"]
    for a in sorted(free, key=lambda a: math.hypot(a["wx"] - cx, a["wy"] - cy)):
        move = go_to(a, f"free arrow off-screen ({len(free)} known)", chase_key(a))
        if move:
            return move

    # (board fits, or edges still unknown for another reason): explore one way until it stops
    cut = cut_off_sides(world, w, h)
    unknown_edges = sorted((d for d in ("TOP", "LEFT", "BOTTOM", "RIGHT")
                            if world.edges[d] is None and d not in world.at_limit),
                           key=lambda d: -cut.get(d, -1))   # where the board runs off, most first
    if unknown_edges and not world.surveyed:   # after a sweep the board's real extent is known
        if world.explore_dir not in unknown_edges:
            world.explore_dir = unknown_edges[0]
        return world.explore_dir, "exploring to the board's edge", None
    world.explore_dir = None

    # 3) Reveal where unresolved rays are heading
    votes = {}
    for arrow, status, _ in results:
        if status == "UNKNOWN":
            votes[arrow["dir"]] = votes.get(arrow["dir"], 0) + 1
    # Never pan toward a camera limit: that's what used to drag down (or up) forever. And once the
    # whole board has been swept, what's still unknown (e.g. behind the header) stays unknown.
    for d in world.at_limit:
        votes.pop(d, None)
    if world.surveyed:
        # after the sweep: toward a side the camera isn't already at (e.g. up, to see what the
        # header hides at this height). At the top limit that strip can never be seen, so it
        # counts as empty and can't pull the camera again - no loop.
        votes = {d: v for d, v in votes.items() if not reached(d)}
    if votes:
        return max(votes, key=votes.get), "unknown rays", None

    # 3a) Board never looked at: the survey only sweeps the axes where the first view ran off, so
    #     an irregular board (the 80s level: one corner reaching further up, under the header) can
    #     have parts nobody has seen - with the last free arrows in them. Go look, nearest first
    #     (before re-checking / refreshing parts already seen: unseen board is the likelier lead).
    #     Only with arrows in view that are all stuck, and a few times per map: with nothing in view
    #     (end of a level) pans are measured on the dots alone, the map smears, and its holes look
    #     like unseen board.
    if world.surveyed and world.unseen_pans < UNSEEN_MAX_PANS and \
            any(a.get("solid", True) for a, _, _ in results):
        for fx, fy in unseen_board(world, w, h, cx, cy):
            move = go_to({"wx": fx, "wy": fy}, "unseen part of the board",
                         ("unseen", int(fx // 240), int(fy // 240)))
            if move:
                world.unseen_pans += 1
                return move

    # 3b) Arrows in view blocked only by something OFF-screen on the map: that part of the map may
    #     be stale (an arrow flew out of there while out of view and its line stayed behind). Go
    #     look at the spot that blocks, nearest first - one look fixes the map.
    off_blocks = []
    for arrow, status, hit in results:
        if status != "BLOCKED" or hit is None or not arrow.get("solid", True):
            continue
        tx, ty = arrow["tip_x"], arrow["tip_y"]
        to_roi_end = {"RIGHT": w - tx, "LEFT": tx + 1, "BOTTOM": h - ty, "TOP": ty + 1}[arrow["dir"]]
        dx, dy = DIRS[arrow["dir"]]
        behind_header = b1 >= b0 and dy != 0 and b0 <= ty + dy * hit <= b1
        if hit <= to_roi_end and not behind_header:
            continue                                   # blocked by something on screen
        # (a block behind the header comes from the map too - what an earlier view saw there -
        #  and goes stale the same way: it kept arrows "blocked" until the whole board was redone)
        bx, by = world.ox + tx + dx * hit, world.oy + ty + dy * hit
        off_blocks.append((hit - to_roi_end, bx, by))
    for _, bx, by in (sorted(off_blocks) if world.idle_pans < 2 else []):
        move = go_to({"wx": bx, "wy": by}, "re-checking what blocks the arrows in view",
                     ("blocker", int(bx // 120), int(by // 120)))
        if move:
            world.idle_pans += 1
            return move

    # 4) Refresh the part of the board seen longest ago that still has arrows in it (arrows there
    #    may have left already; the stale map is what's blocking everything)
    if world.seen is not None:
        stale = sorted(((world.tile_time[int(a["wy"] // TILE), int(a["wx"] // TILE)], k, a)
                        for k, a in world.arrows.items() if not visible(a)), key=lambda t: t[0])
        for _, k, a in (stale if world.idle_pans < 2 else []):
            move = go_to(a, "refreshing the oldest part of the map", ("refresh",) + tuple(k))
            if move:
                world.idle_pans += 1
                return move

    return None, "nothing off-screen", None

# -----------------------------------------------------------------------------
# 5. MAIN CONTROLLER LOOP
# -----------------------------------------------------------------------------
def restart_game():
    print(f"[!] No progress for {STUCK_TIMEOUT}s. Restarting {GAME_PACKAGE}...")
    if BRIDGE_MODE:                # (an app can't close another one: home screen, then open it again)
        try:
            helper.home()
        except Exception:
            pass
        time.sleep(1.0)
        launch_game()
        time.sleep(RESTART_LOAD_WAIT)
        return
    adb_run(["shell", "am", "force-stop", GAME_PACKAGE])
    time.sleep(1.0)
    launch_game()
    time.sleep(RESTART_LOAD_WAIT)

def progress_fill(frame):
    """How full the green level-progress bar in the header is (0..1); it fills as arrows clear."""
    if AMAZE:
        return 0.0      # no progress bar (an empty board is what tells the level is done)
    hsv = cv2.cvtColor(frame[240:265, 400:680], cv2.COLOR_BGR2HSV)
    green = (hsv[:, :, 0] > 35) & (hsv[:, :, 0] < 85) & (hsv[:, :, 1] > 100) & (hsv[:, :, 2] > 120)
    return min(1.0, int(green.any(axis=0).sum()) / 222.0)

def count_stars(frame):
    """Lit stars in the level header (each mistake costs one)."""
    if AMAZE:   # lives: blue drops (a lost one turns grey)
        hsv = cv2.cvtColor(frame[350:440, 40:330], cv2.COLOR_BGR2HSV)
        blue = cv2.inRange(hsv, (95, 120, 150), (115, 255, 255))
        n, _, st, _ = cv2.connectedComponentsWithStats(blue)
        return int(np.count_nonzero(st[1:, cv2.CC_STAT_AREA] > 800))
    hsv = cv2.cvtColor(frame[150:240, 410:680], cv2.COLOR_BGR2HSV)
    yellow = (hsv[:, :, 0] >= 18) & (hsv[:, :, 0] <= 35) & (hsv[:, :, 1] > 120) & (hsv[:, :, 2] > 180)
    return min(3, int(round(yellow.sum() / 2250)))

def recenter_board(frame):
    """If the board was flung against an edge with empty space on the other side, pan it back."""
    roi = frame[ROI_Y1:ROI_Y2, ROI_X1:ROI_X2]
    _, board = build_masks(roi)
    bb = board_bbox(board)
    if bb is None:
        return False
    h, w = board.shape
    x0, y0, x1, y1 = bb
    dx = dy = 0
    b0, b1 = band_rows()
    # the empty space above the board only counts from the header down (can't see behind it)
    top_gap = y0 - (b1 + 1) if b1 >= b0 and y0 > b1 else y0
    if (x0 <= EDGE_MARGIN and w - 1 - x1 > RECENTER_MIN_GAP) or (w - 1 - x1 <= EDGE_MARGIN and x0 > RECENTER_MIN_GAP):
        dx = int(w / 2 - (x0 + x1) / 2)
    if (y0 <= EDGE_MARGIN and h - 1 - y1 > RECENTER_MIN_GAP) or (h - 1 - y1 <= EDGE_MARGIN and top_gap > RECENTER_MIN_GAP):
        dy = int(h / 2 - (y0 + y1) / 2)
    # Part of the board behind (or above) the level header, but the whole board would fit below
    # it: move it down there in one go, instead of panning later to see what the header hides.
    below_top, below_bottom = b1 + 1 + EDGE_MARGIN, h - 1 - EDGE_MARGIN
    if b1 >= b0 and y0 <= b1 + EDGE_MARGIN and (y1 - y0) <= below_bottom - below_top:
        target_top = below_top + ((below_bottom - below_top) - (y1 - y0)) // 2
        dy = int(target_top - y0)
    if not dx and not dy:
        return False
    dx = max(-int(w * PAN_FRACTION), min(int(w * PAN_FRACTION), dx))
    dy = max(-int(h * PAN_FRACTION), min(int(h * PAN_FRACTION), dy))
    print(f"[>] Board is off-center, recentering by ({dx:+d},{dy:+d})")
    pan_by(dx, dy)
    return True

def camera_disturbed(prev_bb, bb):
    """
    Did the view move although we didn't pan? Uses the board outline (grid dots + lines): it stays
    put when arrows are removed, shifts when the camera is flung, and changes size on a zoom.
    """
    # Removing an arrow can trim ONE side of the outline (heads poke out past the grid dots on
    # irregular boards), so only count it when opposite sides agree: a pan moves both by the same
    # amount, a zoom grows/shrinks both. Sides clipped by the screen edge can't be judged.
    if prev_bb is None or bb is None:
        return False
    pw, ph = prev_bb[2] - prev_bb[0], prev_bb[3] - prev_bb[1]
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    if min(pw, ph, w, h) < 50:
        return False
    roi_w, roi_h = ROI_X2 - ROI_X1, ROI_Y2 - ROI_Y1

    b0, b1 = band_rows()

    def clipped(lo, hi, size):
        return lo <= EDGE_MARGIN or hi >= size - 1 - EDGE_MARGIN or \
            (size == roi_h and b0 - EDGE_MARGIN <= lo <= b1 + EDGE_MARGIN)

    for axis, (lo, hi, size) in enumerate(((0, 2, roi_w), (1, 3, roi_h))):
        if clipped(prev_bb[lo], prev_bb[hi], size) or clipped(bb[lo], bb[hi], size):
            continue
        d_lo, d_hi = bb[lo] - prev_bb[lo], bb[hi] - prev_bb[hi]
        if abs(d_lo - d_hi) <= 4 and abs(d_lo) > 10:
            return True   # both sides shifted together: the camera moved
    if all(not clipped(bb[lo], bb[hi], s) and not clipped(prev_bb[lo], prev_bb[hi], s)
           for lo, hi, s in ((0, 2, roi_w), (1, 3, roi_h))):
        gw, gh = (w - pw) / pw, (h - ph) / ph
        if abs(gw) > 0.08 and abs(gh) > 0.08 and gw * gh > 0:
            return True   # width and height both changed the same way: zoom
    return False

def current_line_half():
    """Line half-thickness on a fresh frame: a zoom gauge (≈4 at the zoom-out limit, ≈6 zoomed in)."""
    frame = grabber.get(newer_than=time.time())[0] if grabber else capture_frame()
    if frame is None:
        return None
    mask, _ = build_masks(frame[ROI_Y1:ROI_Y2, ROI_X1:ROI_X2])
    if cv2.countNonZero(mask) < 500:
        return None
    return line_half_thickness(mask, cv2.distanceTransform(mask, cv2.DIST_L2, 5))

settle_first_mask = None   # first view wait_until_settled saw (to spot a spring-back)

def _settle_small(mask):
    small = cv2.resize(mask, (mask.shape[1] // 4, mask.shape[0] // 4), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(small.astype(np.float32), (0, 0), 1.5)

def wait_until_settled(timeout=SETTLE_TIMEOUT):
    """
    Wait until two screenshots in a row show the board in the same place. Needed after every
    pan: dragging past the board's border overscrolls and the camera then springs back smoothly,
    so measuring too early reads a move the camera then partly undoes. Also used before zooming
    (level intros animate the board in, and the game ignores pinches until they finish).
    Returns (frame, capture_time) of the settled frame (or the last one seen on timeout).
    """
    global settle_first_mask
    end = time.time() + timeout
    last, last_t, frame, t = None, 0.0, None, 0.0
    settle_first_mask = None
    while time.time() < end:
        if grabber:
            frame, t = grabber.get(newer_than=time.time())
        else:
            frame, t = capture_frame(), time.time()
        if frame is None:
            break
        if last is not None and t - last_t < SETTLE_GAP:
            continue    # live video frames are ~33ms apart: a slow spring-back barely changes in that
        mask = resting_line_mask(frame)   # arrows still flying out aren't "the view moving"
        if settle_first_mask is None:
            settle_first_mask = mask      # the view right after the gesture (before any spring-back)
        small = _settle_small(mask)
        if last is not None:
            # Settled = the picture as a whole stopped shifting. Arrows flying out (on some levels
            # they keep the resting colour) change pixels but don't shift the whole picture; the
            # old "same pixels" test waited out every flight (up to 2s per pan).
            (sx, sy), resp = cv2.phaseCorrelate(last_small, small)
            if resp > 0.2 and abs(sx) < 0.5 and abs(sy) < 0.5:    # < 2px in 0.1s (quarter size)
                return frame, t
            ink = max(cv2.countNonZero(mask), 1)
            if cv2.countNonZero(cv2.bitwise_xor(mask, last)) < max(50, 0.01 * ink):
                return frame, t
        last, last_t, last_small = mask, t, small
    return frame, t

def board_fits_view():
    """The whole board is on screen with room around it (zooming out gains nothing)."""
    frame = grabber.get(newer_than=time.time())[0] if grabber else capture_frame()
    if frame is None:
        return False
    roi = frame[ROI_Y1:ROI_Y2, ROI_X1:ROI_X2]
    mask, board = build_masks(roi)
    bb = board_bbox(board)
    step = measure_grid_step(board, mask)
    if bb is None or (step is None and not AMAZE):
        return False
    if step is None:    # Amaze shows grid dots only where lines have gone: ~1.2 cells from the lines
        step = 11 * line_half_thickness(mask, cv2.distanceTransform(mask, cv2.DIST_L2, 5))
    h, w = mask.shape
    em = max(EDGE_MARGIN, int(step + 1))
    b0, b1 = band_rows()
    x0, y0, x1, y1 = bb
    return x0 > em and x1 < w - 1 - em and y0 > max(em, b1 + step / 2) and y1 < h - 1 - em

def _wait_thinner(before, timeout=ZOOM_CHECK_TIMEOUT):
    """Line thickness right after a pinch: returns as soon as the lines got thinner (the zoom took),
    otherwise what it reads at the timeout."""
    end = time.time() + timeout
    after = current_line_half()
    while (after is None or before is None or after >= before - 0.25) and time.time() < end:
        after = current_line_half()
    return after

def full_zoom_out(level_seen=None):
    """
    One pinch normally reaches the zoom limit, but the game ignores it while a level's intro is
    still playing (the board doesn't visibly move then, so wait_until_settled can't tell). A pinch
    sent within ~0.2s of the level appearing was always ignored, one ~0.85s after never was, and an
    ignored pinch costs ~1.8s (gesture + retry): so the first pinch waits until the level has shown
    for ZOOM_INTRO_WAIT. Line thickness is still checked after each pinch, pinching again if needed.
    """
    wait_until_settled()
    if board_fits_view():
        print("[*] The whole board is already on screen: no zoom needed.")
        return
    if AMAZE:
        amaze_zoom_out(level_seen)
        return
    print("[*] Zooming out to view full puzzle...")
    before = current_line_half()
    if level_seen is not None:
        time.sleep(max(0.0, ZOOM_INTRO_WAIT - (time.time() - level_seen)))
    unchanged = 0
    for attempt in range(ZOOM_MAX_TRIES):
        zoom_out()
        after = _wait_thinner(before)
        if before is None or after is None:
            return
        if after < before - 0.25:
            print(f"    [zoom] lines {before:.1f} -> {after:.1f}")
            return                          # one pinch reaches the game's zoom limit
        unchanged += 1
        if after < ZOOMED_IN_HALF or unchanged >= 2 or board_fits_view():
            return   # already at this level's limit (some levels stop at 5, not 4) / nothing to gain
        print(f"    [zoom] pinch ignored (lines still {after:.1f}), retrying...")
        time.sleep(0.25)                    # probably the level intro; wait it out

amaze_limit_half = None   # Amaze GO!: line thickness at this level's zoom limit (once found)

def amaze_zoom_out(level_seen=None):
    """Amaze GO!: pinch (gently) until the whole board is on screen or the lines would get too thin
    to read. At its zoom limit the game lets the board shrink under the fingers and then snaps it
    back: so a pinch is judged only once the view has settled again - lines no thinner = the limit
    (stop there; measuring mid-pinch read every snap-back as a zoom that worked, and re-zoomed)."""
    print("[*] Zooming out to view full puzzle...")
    if level_seen is not None:
        time.sleep(max(0.0, ZOOM_INTRO_WAIT - (time.time() - level_seen)))
    global amaze_limit_half
    before = current_line_half()
    if before is not None and amaze_limit_half is not None and before <= amaze_limit_half + 0.4:
        print(f"    [zoom] lines {before:.1f}: already at this level's zoom limit, no pinch")
        return
    for attempt in range(AMAZE_ZOOM_MAX_PINCHES):
        if before is None:
            return
        if before <= AMAZE_ZOOM_MIN_HALF:
            print(f"    [zoom] lines {before:.1f}: as far out as they stay readable")
            amaze_limit_half = before
            return
        zoom_out()
        wait_until_settled()
        time.sleep(0.3)                     # a snap-back can start a moment after the fingers lift
        wait_until_settled()
        after = current_line_half()
        if after is None:
            return
        if after >= before - 0.25:
            if attempt == 0 and level_seen is not None and time.time() - level_seen < 3.0:
                print(f"    [zoom] no change (lines {after:.1f}); the level may still be appearing, once more")
                time.sleep(0.5)
                continue
            print(f"    [zoom] lines still {after:.1f} after the pinch: the game's zoom limit")
            amaze_limit_half = after
            return
        print(f"    [zoom] lines {before:.1f} -> {after:.1f}")
        if board_fits_view():
            return
        before = after

def sprang_back(world, first_mask, settled_mask, expected):
    """True if the view slid BACK after the finger lifted: the game's elastic border pulling an
    overscrolled camera back to its limit. That proves the pan hit the limit in one go, even when
    the camera got a long way before stopping (no second pan needed to see it not move).
    Returns the set of sides whose limit was hit."""
    if first_mask is None or first_mask is settled_mask:
        return set()
    a, b = world._small(first_mask), world._small(settled_mask)
    if a.shape != b.shape:
        return set()
    win = cv2.createHanningWindow(a.shape[::-1], cv2.CV_32F)
    (sdx, sdy), resp = cv2.phaseCorrelate(a, b, win)
    if resp <= 0.2:
        return set()
    ex, ey = expected
    # a one-axis drag can't make the other axis spring back: that would be a misread
    if (not ex and abs(sdx * 2) > SPRING_MIN) or (not ey and abs(sdy * 2) > SPRING_MIN):
        return set()
    out = set()
    if ex and -(sdx * 2) * np.sign(ex) > SPRING_MIN:
        out.add("LEFT" if ex > 0 else "RIGHT")      # content was pushed right => revealing LEFT
    if ey and -(sdy * 2) * np.sign(ey) > SPRING_MIN:
        out.add("TOP" if ey > 0 else "BOTTOM")
    return out

def pan_and_register(world, direction, amount=None):
    """Pan, then measure how far the view really moved (and whether it hit the camera limit).
    Returns the capture time of the measuring frame (later frames are the ones to analyse)."""
    global pan_fast
    if recorder is not None:
        recorder.note("pans")
    t_start = time.time()
    expected = pan_camera(direction, amount)
    t_dragged = time.time()
    frame, t = wait_until_settled()   # let any overscroll spring back before measuring
    t_settled = time.time()
    if frame is not None:
        roi = frame[ROI_Y1:ROI_Y2, ROI_X1:ROI_X2]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        lines, board = build_masks(roi, hsv)
        still = still_mask(hsv)
        pmask = cv2.bitwise_and(lines, still)
        pboard = cv2.bitwise_and(board, still)
        sprang = sprang_back(world, settle_first_mask, pmask, expected)
        result = world.register_pan(pmask, direction, expected, sprang, pboard)
        now = time.time()
        print(f"    [pan] took {now - t_start:.2f}s (drag {t_dragged - t_start:.2f}, settle "
              f"{t_settled - t_dragged:.2f}, measure {now - t_settled:.2f}) | board follows the finger {pan_gain:.0%}")
        if result == "failed" and pan_fast:  # ("empty" isn't the pan's fault)
            # Backup: quick pans didn't measure well here, use the careful drag for this level
            print("    [pan] quick pan misread; switching to careful panning for this level")
            pan_fast = False
    return t or time.time()

def run_solver(deadline=None):
    global grabber, tapper, recorder
    if recorder is None:
        recorder = LevelRecorder()
    if grabber is None:
        grabber = FrameGrabber()
        StatusBarWatcher()
    if tapper is None:
        tapper = Tapper(sync=SYNC_TAPS)
    min_frame_time = 0.0  # only use frames captured after this (after our own taps/gestures)
    ingame_markers = load_ingame_markers()
    buttons = load_templates(BUTTONS_DIR)
    print(f"[*] In-game markers: {[m['name'] for m in ingame_markers]}")
    print(f"[*] Buttons: {[b['name'] for b in buttons]}")
    if len(ingame_markers) < INGAME_MIN_MARKERS:
        print(f"[-] Need at least {INGAME_MIN_MARKERS} markers in {INGAME_DIR}/ to recognise levels.")
        return

    print(f"[*] Game package: {GAME_PACKAGE} (restarted only if stuck {MENU_RESTART_AFTER}s outside a level)")
    if not game_in_foreground():
        print("[*] Game isn't open. Launching it...")
        launch_game()
        time.sleep(RESTART_LOAD_WAIT)

    print("[*] Vision Engine Active. Scanning...")

    world = World()
    recent_taps = []
    stuck_frames = 0
    last_progress = time.time()
    pressed_button = None      # name of the button we tapped last frame
    button_attempts = 0
    needs_zoom = True
    was_in_game = None
    last_fg_check = 0
    last_terminal_check = 0
    prev = None                # previous in-level frame (for the stillness check)
    plan = None                # cached escapes: {"arrows", "pose", "mask", "last_tap"}
    waves_ok = True            # turned off for the level if a planned wave ever costs a star
    level_half = None          # line thickness right after zooming out (zoom gauge)
    in_flight = []             # exit lanes of arrows we tapped that may still be flying: [{"rect", "t"}]
    last_arrows_seen = time.time()
    search_step = 0            # position in the outward search spiral when no arrows are in view
    blind_pans = {}            # direction -> pans made while no arrows are visible
    blind_resurveyed = False   # all directions searched blind -> one more full sweep (per sighting)
    blind_survey = {}          # direction -> survey pans made while no arrows are visible
    hunt = {"futile": 0, "resweep_ok": True}   # pans since the last tap; one re-sweep per tap
    prev_line_pixels = None
    menu_frames = 0            # consecutive frames that looked like a menu/popup
    stuck_back_pressed = False # escalation on unknown screens: BACK once, then (later) restart
    stuck_saved = False
    tap_log = []               # (world x, world y, dir, time) of every arrow tap this level
    calm_ref = None            # board outline on the last calm frame (to spot real flings/zooms)
    rezoom_same_level = False  # the next zoom is a re-zoom inside the same level (keep what's known)
    menu_wait_saved = 0        # frames saved while a menu showed nothing to tap (diagnostics)
    aggressive = AGGRESSIVE_TAPS
    bad_spots = []             # arrows that cost a star: (world x, world y, dir), never tapped again
    stuck_soft_done = False    # stuck once already (limits/chases forgotten); next time: start over
    world_soft_reset = False   # 15s without progress once already (map kept); next time: start over
    last_status_line = None
    level_seen_at = None       # when the level screen appeared (first pinch waits out the intro)
    last_flying_seen = 0.0     # last time an arrow flying out was in view (win-screen waits)
    last_pan_done = 0.0        # when the last pan finished (an unsure position gets time to confirm)

    stars = None

    while deadline is None or time.time() < deadline:
        frame, frame_time = grabber.get(newer_than=min_frame_time)
        if frame is None:
            continue
        min_frame_time = frame_time  # never analyse the same frame twice

        current_time = time.time()
        recent_taps = [(tx, ty, t) for tx, ty, t in recent_taps if current_time - t < RECENT_TAP_GUARD]

        markers = count_ingame_markers(frame, ingame_markers)
        in_game = markers >= INGAME_MIN_MARKERS
        if in_game != was_in_game:
            print(f"[*] Screen: {'LEVEL' if in_game else 'MENU / POPUP'} ({markers}/{len(ingame_markers)} HUD markers)")
            was_in_game = in_game
            if in_game:
                level_seen_at = frame_time   # the level's intro ignores pinches for a moment
            last_progress = current_time   # a screen change is progress (e.g. level -> win screen)

        if ON_PHONE and not in_game and current_time - last_terminal_check > TERMINAL_CHECK_EVERY:
            last_terminal_check = current_time
            if terminal_in_front():
                tapper.clear(wait=False)
                print("[*] Paused while ArrowBot is open. \"Stop bot\" stops it; go back to the game to carry on."
                      if os.environ.get("ARROWBOT_APP_PACKAGE") else
                      "[*] Paused while the terminal is open. Tap ESC (or press q) here to stop the bot;"
                      " go back to the game to carry on.")
                while terminal_in_front():
                    time.sleep(1.0)
                print("[*] Back in the game: carrying on.")
                last_progress = last_fg_check = time.time()   # (a minute in the terminal isn't "stuck")
                min_frame_time = time.time()
                continue

        stuck_limit = STUCK_TIMEOUT if in_game else MENU_BACK_AFTER
        if in_game and world.too_big and not world.surveyed:
            last_progress = max(last_progress, current_time - stuck_limit)   # sweeping = progress
        if current_time - last_progress > stuck_limit and (in_game or find_button(frame, buttons) is None):
            if in_game:
                # Closing the app mid-level costs a life, so never restart here. Keep the map (big
                # boards take a while to explore); only re-zoom if the zoom really changed, and
                # mark the map out of date so the stalest parts get re-checked first.
                now_half = current_line_half()
                if now_half is not None and level_half is not None and now_half > level_half + 0.25:
                    print(f"[!] No progress for {STUCK_TIMEOUT}s and the zoom changed. Re-zooming.")
                    needs_zoom = True
                    rezoom_same_level = True
                elif not world_soft_reset:
                    # first time: keep the map (re-surveying a big board costs ~30s); just retry
                    # chases, limits and stale areas
                    print(f"[!] No progress for {STUCK_TIMEOUT}s in a level. Re-trying chases and limits.")
                    recorder.note("stuck")
                    recorder.event("no progress for 15s", frame, [], world)
                    world.chased.clear()
                    world.idle_pans = 0
                    world.at_limit.clear()
                    world.tile_time[:] = 0
                    world_soft_reset = True
                    last_progress = current_time
                else:
                    print(f"[!] No progress for {STUCK_TIMEOUT}s in a level. Starting the board picture over.")
                    recorder.note("stuck")
                    recorder.event("no progress for 15s again - map redone", frame, [], world)
                    world.reset(keep_level=True)
                    world_soft_reset = False
                plan, in_flight, recent_taps = None, [], []
                stuck_frames = 0
                # the search for arrows starts over too (an exhausted blind-pan budget used to
                # outlive every reset: the bot then waited forever)
                blind_pans, search_step = {}, 0
            else:
                stuck_for = current_time - last_progress
                level_under = count_ingame_markers(frame, ingame_markers, under_popup=True) >= INGAME_MIN_MARKERS
                if not stuck_saved:
                    print("[!] Stuck on a screen with nothing I recognise to tap.")
                    stuck_saved = True
                if level_under:
                    # A popup on top of a level: restarting would quit the level and cost a life.
                    print("[!] A level is underneath this popup, so NOT restarting. Waiting.")
                    last_progress = current_time
                    continue
                if not stuck_back_pressed:
                    print(f"[!] Nothing to tap for {int(stuck_for)}s. Pressing BACK once.")
                    adb_input("input keyevent KEYCODE_BACK")
                    stuck_back_pressed = True
                    time.sleep(1.0)
                    min_frame_time = time.time()
                    continue
                if stuck_for < MENU_RESTART_AFTER:
                    continue
                restart_game()
                world.reset()
                recent_taps = []
                stuck_frames = 0
                pressed_button, button_attempts = None, 0
                needs_zoom = True
                stuck_back_pressed, stuck_saved = False, False
            last_progress = time.time()
            continue

        # ------------------------------------------------------------- menus, popups, ads
        if not in_game:
            if recorder.finished():
                recorder.end("won" if recorder.cur["progress"] >= 0.9 else "lost", world)
            tapper.clear(wait=False)
            needs_zoom = True  # whatever comes next, re-zoom once we're back in a level
            rezoom_same_level = False
            prev, prev_line_pixels, plan = None, None, None
            in_flight = []

            if current_time - last_fg_check > FOREGROUND_CHECK_INTERVAL:
                last_fg_check = current_time
                if not game_in_foreground():
                    print(f"[!] {GAME_PACKAGE} isn't the open app. Relaunching...")
                    launch_game()
                    time.sleep(RESTART_LOAD_WAIT)
                    world.reset()
                    last_progress = time.time()
                    continue

            menu_frames += 1
            if menu_frames < 2:
                continue  # one odd frame mid-level must never lead to a tap or BACK (BACK quits levels)
            hit = find_button(frame, buttons)
            if hit is None:
                if pressed_button is not None:
                    print(f"[+] '{pressed_button}' worked.")
                    last_progress = current_time
                    stuck_back_pressed, stuck_saved = False, False
                pressed_button, button_attempts = None, 0
                waited = current_time - last_progress
                print(f"[*] Menu with nothing to tap. Waiting... ({int(waited)}s)")
                if False:
                    # what the screen looks like while no button is recognised (Temp/menu_wait/):
                    # to tell a slow game animation from a button the bot doesn't recognise
                    os.makedirs(os.path.join(TEMP, "menu_wait"), exist_ok=True)
                    cv2.imwrite(os.path.join(TEMP, "menu_wait", f"{menu_wait_saved}.jpg"),
                                cv2.resize(frame, None, fx=0.5, fy=0.5))
                    menu_wait_saved += 1
                time.sleep(0.15)
                continue

            name, (bx, by) = hit
            button_attempts = button_attempts + 1 if name == pressed_button else 1
            pressed_button = name
            if button_attempts > 2:
                # Same button still there after two taps (covered by the status bar, not
                # clickable yet, ...): try Android BACK instead
                print(f"[+] '{name}' isn't responding, pressing BACK.")
                adb_input("input keyevent KEYCODE_BACK")
                button_attempts = 0
            else:
                print(f"[+] Tapping '{name}' at ({int(bx)},{int(by)}) (attempt {button_attempts}).")
                adb_input(f"input tap {int(bx)} {int(by)}")
            world.reset()
            time.sleep(1.0)
            min_frame_time = time.time()
            continue

        # ------------------------------------------------------------- in a level
        menu_frames = 0
        stuck_back_pressed, stuck_saved = False, False
        if pressed_button is not None:
            print(f"[+] '{pressed_button}' worked.")
            last_progress = current_time
            pressed_button, button_attempts = None, 0

        if needs_zoom:
            if not rezoom_same_level:
                recorder.start(frame)
                globals()["amaze_limit_half"] = None   # a new level: its zoom limit isn't known yet
            globals()["pan_fast"] = True
            full_zoom_out(None if rezoom_same_level else level_seen_at)
            level_half = current_line_half()
            frame, _ = grabber.get(newer_than=time.time())
            if frame is not None and recenter_board(frame):
                wait_until_settled()
            min_frame_time = time.time()
            if rezoom_same_level:
                # same level: keep "board too big", the stars, and arrows that cost a star
                world.reset(keep_level=True)
                prev, prev_line_pixels, plan, calm_ref = None, None, None, None
            else:
                world.reset()
                prev, prev_line_pixels, stars, plan, waves_ok = None, None, None, None, True
                tap_log, bad_spots, calm_ref = [], [], None
                aggressive = AGGRESSIVE_TAPS
                hunt["futile"], hunt["resweep_ok"] = 0, True
            needs_zoom = False
            rezoom_same_level = False
            in_flight = []
            continue

        now_stars = count_stars(frame)
        recorder.seen(frame, now_stars)
        if stars is not None and now_stars < stars:
            print(f"[!] Lost a star ({stars} -> {now_stars}): a tap hit something")
            recorder.note("mistakes", stars - now_stars)
            recorder.event("lost a star", frame, [], world)
            if plan is not None and any(a["_taps"] for a in plan["arrows"]):
                print("[!] It happened during a planned wave: planning ahead is off for this level.")
                waves_ok = False
            if aggressive:
                print("[!] Back to careful tapping for this level (wait for paths to be fully empty).")
                aggressive = False
            plan = None
            # Whatever was tapped in the last few seconds and is still sitting where it was bounced
            # back: never tap it again this level (otherwise it can cost every star).
            _, now_results, _ = analyze(frame, world)
            for wx, wy, d, t in tap_log:
                if current_time - t < 3.0 and any(
                        a["dir"] == d and abs(world.ox + a["x"] - wx) <= 6 and abs(world.oy + a["y"] - wy) <= 6
                        for a, _, _ in now_results):
                    bad_spots.append((wx, wy, d))
                    print(f"[!] Won't tap the arrow at board ({wx},{wy}) again this level.")
        stars = now_stars

        mask, results, half = analyze(frame, world)
        save_debug_image(frame, results, world)

        def is_bad(wx, wy, d):
            """Arrow (board coords) that cost a star this level."""
            return any(d == bd and abs(wx - bx) <= 6 and abs(wy - by) <= 6 for bx, by, bd in bad_spots)

        def resweep_due():
            """Big board, swept already, and several pans in a row tapped nothing: sweep it again.
            Late in a level only the grid dots are left to measure pans by, remembered spots drift,
            and the hunt for the last arrows went back and forth (~15 pans). A sweep anchored on
            the camera limits is sure to pass over them. Once per tap, so it can't loop."""
            if not (world.too_big and world.surveyed and hunt["resweep_ok"]
                    and hunt["futile"] >= FUTILE_RESWEEP_AFTER):
                return False
            print(f"[*] {hunt['futile']} pans without a tap: sweeping the board again for the last arrows.")
            world.reset(keep_level=True)
            hunt["futile"], hunt["resweep_ok"] = 0, False
            return True

        if any(a["solid"] for a, _, _ in results):
            last_arrows_seen, search_step = current_time, 0
            blind_pans = {}            # arrows found: every direction may be searched again
            blind_resurveyed = False
            blind_survey = {}
        else:
            # Level screen but no arrows in view. Give arrows that are flying off / a finishing
            # level a moment, then go looking: toward known off-screen arrows or unexplored board
            # edges first, otherwise an outward spiral.
            if results:
                last_flying_seen = current_time   # arrows still flying out: the level may be ending
            if current_time - last_arrows_seen < NO_ARROWS_GRACE:
                time.sleep(0.2)
                continue
            # the win-screen waits count from the last arrow seen at all, flying ones included (a
            # long flight off a big board used to eat the wait, and the bot searched a won level)
            quiet_for = current_time - max(last_arrows_seen, last_flying_seen)
            if current_time - last_arrows_seen > 2.0:
                check_video("no arrows in view")
            world.drop_tapped_missing(tap_log, current_time)
            remembered = [a for a in world.arrows.values() if not is_bad(a["wx"], a["wy"], a["dir"])]
            if (not remembered and progress_fill(frame) >= 0.9
                    and quiet_for < LEVEL_END_WAIT):
                # Progress bar almost full and nothing in view or remembered: most likely the level
                # is done and the win screen is coming. Searching now would just be pans for nothing.
                # (On big levels a "full-looking" bar can still mean a few arrows: if the map knows
                # where some are, go get them instead of waiting.)
                print("[*] Level looks finished (progress bar full). Waiting for the win screen...")
                last_progress = current_time
                time.sleep(0.3)
                continue
            if (world.fully_visible or (not world.arrows and tap_log and (not world.too_big or world.surveyed))) \
                    and quiet_for < BOARD_CLEARED_WAIT:
                # The whole board is on screen and nothing is left on it: the level is done and the
                # win screen is on its way. Panning now would only get in the way.
                print("[*] Board cleared. Waiting for the win screen...")
                last_progress = current_time   # finishing a level is progress (no reset/pinch here)
                time.sleep(0.3)
                continue
            if world.missing:
                continue    # remembered arrows in view not seen: a 2nd frame forgets them (see below)
            if resweep_due():
                blind_pans, search_step, blind_resurveyed = {}, 0, True
                continue
            direction, reason, amount = choose_pan(results, world, mask.shape, is_bad)
            if direction is None:
                spiral =["RIGHT", "BOTTOM", "LEFT", "TOP"]
                # 1 right, 1 down, 2 left, 2 up, 3 right, 3 down, ...
                legs, i, run = [], 0, 1
                while len(legs) <= search_step:
                    legs += [spiral[i % 4]] * run
                    if i % 2 == 1:
                        run += 1
                    i += 1
                direction, reason = legs[search_step], "searching for arrows"
                search_step += 1
            # Never more than 2 pans in any direction while no arrows are found (no loops). Survey
            # pans don't count: each leg of the sweep ends at a camera limit by itself, and capping
            # them stopped the re-survey of a redone map dead (LVL 265: 10 minutes of waiting).
            survey = reason.startswith("survey")
            if survey:
                blind_survey[direction] = blind_survey.get(direction, 0) + 1
                if blind_survey[direction] > MAX_BLIND_SURVEY_PANS:
                    # a board is at most a few screens: this many sweep pans one way without
                    # finding its edge means the pans are being misread - call it the limit
                    print(f"[!] {MAX_BLIND_SURVEY_PANS} survey pans {direction} with nothing in view; "
                          f"treating that as the board's edge")
                    world.at_limit.update(direction.split("+"))
                    blind_survey[direction] = 0
                    continue
            if not survey and blind_pans.get(direction, 0) >= MAX_BLIND_PANS:
                left = [d for d in ("TOP", "RIGHT", "BOTTOM", "LEFT")
                        if blind_pans.get(d, 0) < MAX_BLIND_PANS and d not in world.at_limit]
                if not left:
                    if world.too_big and not blind_resurveyed:
                        # every direction searched from here and still nothing: the map is probably
                        # off. Sweep the whole board once more (the survey pans aren't capped).
                        print("[*] No arrows found in any direction. Sweeping the whole board again.")
                        world.reset(keep_level=True)
                        blind_pans, search_step, blind_resurveyed = {}, 0, True
                        continue
                    print("[*] No arrows found in any direction. Waiting instead of panning.")
                    time.sleep(0.5)
                    continue
                direction, reason, amount = left[0], "searching for arrows", None
            if not survey:
                blind_pans[direction] = blind_pans.get(direction, 0) + 1
                hunt["futile"] += 1
            print(f"[>] No arrows visible. Panning {direction} ({reason}"
                  + ("" if survey else f", {blind_pans[direction]}/{MAX_BLIND_PANS}") + ")")
            prev, prev_line_pixels, plan, calm_ref = None, None, None, None
            in_flight = []
            min_frame_time = pan_and_register(world, direction, amount)
            last_pan_done = time.time()
            continue

        # Progress = arrows actually disappeared (same camera position, not just "we tapped something")
        line_pixels = cv2.countNonZero(mask)
        if prev_line_pixels is not None and line_pixels < prev_line_pixels - 200:
            last_progress = current_time
            stuck_soft_done = False
            world_soft_reset = False
        prev_line_pixels = line_pixels

        # View shifted or zoom changed without us doing it (fling): rebuild and recenter
        # Only judge on calm frames (nothing queued or flying, no tap for a second): arrows flying
        # off in all directions make the board's outline grow on every side, which looks like a zoom.
        solid_now = sum(1 for a, _, _ in results if a["solid"])
        calm = (not in_flight and not tapper.pending() and current_time - tapper.last_tap > 1.0
                and not any(not a["solid"] for a, _, _ in results))
        if calm and solid_now >= 3:
            if (calm_ref is not None and calm_ref["pose"] == (world.ox, world.oy)
                    and camera_disturbed(calm_ref["bbox"], world.last_bbox)):
                print("[!] Camera moved on its own (fling/zoom). Re-zooming and recentering.")
                needs_zoom = True
                rezoom_same_level = True
                calm_ref = None
                continue
            calm_ref = {"pose": (world.ox, world.oy), "bbox": world.last_bbox}

        # Previous frame only counts as a "first look" if the camera hasn't moved and it's recent
        if prev is not None and (prev["pose"] != (world.ox, world.oy) or current_time - prev["time"] > 2.0):
            prev = None
        last_look = prev
        prev = {"mask": mask, "arrows": [a for a, _, _ in results], "pose": (world.ox, world.oy),
                "time": current_time, "bbox": world.last_bbox,
                "solid": sum(1 for a, _, _ in results if a["solid"])}

        def is_recent(arrow):
            """Tapped a moment ago, or blacklisted after it cost a star."""
            ax, ay = arrow["x"] + ROI_X1, arrow["y"] + ROI_Y1
            if any(math.hypot(ax - tx, ay - ty) < 30 for tx, ty, _ in recent_taps):
                return True
            wx, wy = world.ox + arrow["x"], world.oy + arrow["y"]
            return any(d == arrow["dir"] and abs(wx - bx) <= 6 and abs(wy - by) <= 6 for bx, by, d in bad_spots)
        candidates = [a for a, status, _ in results if status == "CLEAR" and not is_recent(a)]

        counts = {s: sum(1 for _, st, _ in results if st == s) for s in ("CLEAR", "BLOCKED", "UNKNOWN", "GHOST")}
        status_line = (f"[*] {len(results)} arrows | clear {counts['CLEAR']} blocked {counts['BLOCKED']} "
                       f"unknown {counts['UNKNOWN']} ghost {counts['GHOST']} | edges known: "
                       f"{[k for k, v in world.edges.items() if v is not None]}"
                       + (f" | unknown because: {getattr(world, 'unknown_why', {})}" if counts['UNKNOWN'] else ""))
        if status_line != last_status_line:   # printing every frame slows the Windows console down
            print(status_line)
            last_status_line = status_line

        def present(arrow):
            """Arrow still sitting where it was planned (same head spot and direction)."""
            return any(b["solid"] and b["dir"] == arrow["dir"] and abs(b["x"] - arrow["x"]) <= 3
                       and abs(b["y"] - arrow["y"]) <= 3 for b, _, _ in results)

        # Every arrow we tap is tracked while it flies out. The game checks an arrow's path at the
        # moment it's tapped, including arrows still on their way out, so a path that crosses a
        # flying arrow's exit lane is only usable once that arrow has fully passed the crossing.
        def ink(rect):
            x0, y0, x1, y1 = (int(v) for v in rect)
            return mask[max(0, y0):max(0, y1) + 1, max(0, x0):max(0, x1) + 1].any()

        def still_at_start(f):
            """Any of the flying arrow's original pixels still inked = its tail hasn't left yet."""
            x0, y0, x1, y1 = f["bbox"]
            return cv2.countNonZero(cv2.bitwise_and(mask[y0:y1 + 1, x0:x1 + 1], f["blob"])) > 0

        sent_now = tapper.drain()          # taps the tapper actually sent since the last frame
        in_flight.extend(sent_now)
        world.flights = [f for f in getattr(world, "flights", []) if current_time - f["t"] < FLIGHT_KEEP] + \
            [dict(rec, left=False, stayed=False, erased=False) for rec in sent_now]
        world.update_flights(results, half, current_time)
        for rec in sent_now:
            wx, wy = rec["wx"], rec["wy"]
            tap_log.append((wx, wy, rec["dir"], rec["t"]))
            times = sum(1 for tx, ty, td, _ in tap_log
                        if td == rec["dir"] and abs(tx - wx) <= 6 and abs(ty - wy) <= 6)
            if times >= MAX_TAPS_PER_ARROW and not is_bad(wx, wy, rec["dir"]):
                bad_spots.append((wx, wy, rec["dir"]))
                print(f"[!] Tapped the arrow at board ({wx},{wy}) {times}x and it won't leave; skipping it.")
        in_flight[:] = [f for f in in_flight
                        if current_time - f["t"] < 4.0 and (ink(f["rect"]) or still_at_start(f))]

        def crosses_flight(arrow):
            r = lane_rect(arrow, half, mask.shape)
            if any(it["arrow"] is not arrow and rects_overlap(r, it["record"]["rect"])
                   for it in tapper.pending()):
                return True  # crosses an arrow that's about to be tapped: wait for it to pass
            for f in in_flight:
                if not rects_overlap(r, f["rect"]):
                    continue
                ix0, iy0 = max(r[0], f["rect"][0]), max(r[1], f["rect"][1])
                ix1, iy1 = min(r[2], f["rect"][2]), min(r[3], f["rect"][3])
                fx0, fy0, fx1, fy1 = f["rect"]
                tx, ty = f["tip"]
                # part of the flying arrow's lane from its start up to (and over) the crossing
                upstream = {"RIGHT": (tx, fy0, ix1, fy1), "LEFT": (ix0, fy0, tx, fy1),
                            "BOTTOM": (fx0, ty, fx1, iy1), "TOP": (fx0, iy0, fx1, ty)}[f["dir"]]
                if still_at_start(f) or ink(upstream):
                    return True  # it hasn't fully passed the crossing yet
            return False

        def tap(arrows, label):
            """Queue arrows for the even-paced tapper. Paths may not cross each other's (later ones
            wait a frame) and the queue stays short so every queued tap is fresh."""
            chosen = []
            room = tapper.room()
            for a in arrows:
                if len(chosen) >= room:
                    break
                r = lane_rect(a, half, mask.shape)
                if any(rects_overlap(r, lane_rect(c, half, mask.shape)) for c in chosen):
                    continue
                chosen.append(a)
            if not chosen:
                return []
            taps = [(a["x"] + ROI_X1, a["y"] + ROI_Y1) for a in chosen]
            for x, y in taps:
                recent_taps.append((x, y, current_time))
            print(f"[+] Tapping {len(taps)} arrows ({label})")
            recorder.note("taps", len(taps))
            hunt["futile"], hunt["resweep_ok"] = 0, True
            world.chased.clear()
            world.idle_pans = 0
            fd = world.frame_data
            if "labels" in fd and fd["labels"].shape == mask.shape:
                lab, lstats = fd["labels"], fd["stats"]       # labelled once this frame already
            else:
                _, lab, lstats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
            items = []
            for a, (x, y) in zip(chosen, taps):
                L = lab[a["y"], a["x"]]
                if L:
                    bx, by, bw, bh = (int(v) for v in lstats[L][:4])
                    blob = (lab[by:by + bh, bx:bx + bw] == L).astype(np.uint8) * 255
                    cores = fd.get("core_centers", np.zeros((0, 2)))
                    single = sum(1 for cx, cy in cores if lab[int(round(cy)), int(round(cx))] == L) <= 1
                else:
                    bx, by, bw, bh = a["x"], a["y"], 1, 1
                    blob = np.zeros((1, 1), np.uint8)
                    single = False
                record = {"rect": lane_rect(a, half, mask.shape), "dir": a["dir"],
                          "wx": world.ox + a["x"], "wy": world.oy + a["y"],
                          "tip": (a["tip_x"], a["tip_y"]), "t": time.time(),
                          "bbox": (bx, by, bx + bw - 1, by + bh - 1),
                          "blob": blob, "pose": (world.ox, world.oy), "single": single}
                items.append({"x": x, "y": y, "record": record, "arrow": a, "pose": (world.ox, world.oy)})
            tapper.add(items)
            return chosen

        def covered(arrow):
            """Something (usually an arrow flying out) is passing over this head right now."""
            r = int(half * 4)
            y0, x0 = max(0, arrow["y"] - r), max(0, arrow["x"] - r)
            was = plan["mask"][y0:arrow["y"] + r + 1, x0:arrow["x"] + r + 1]
            now = mask[y0:arrow["y"] + r + 1, x0:arrow["x"] + r + 1]
            changed = cv2.countNonZero(cv2.bitwise_xor(was, now))
            return changed > 0.25 * max(cv2.countNonZero(was), 1)

        # Queued taps must still be safe on THIS frame, or they're dropped
        dropped = tapper.prune(lambda it: it["pose"] == (world.ox, world.oy) and present(it["arrow"])
                               and cast_ray(it["arrow"], world.resting_mask if aggressive else mask,
                                            world, half)[0] == "CLEAR")
        if dropped:
            print(f"    [tap] dropped {len(dropped)} queued tap(s): no longer safe on this frame")
            # never tapped: may be queued again as soon as it's safe (no retap wait, no "recent" block)
            for it in dropped:
                a = it["arrow"]
                if a.get("_taps"):
                    a["_taps"] -= 1
                recent_taps[:] = [(tx, ty, t) for tx, ty, t in recent_taps
                                  if math.hypot(tx - it["x"], ty - it["y"]) >= 30]

        # 1) Cached plan: every arrow the planner found can escape from what's on screen. Each is
        #    tapped the moment ITS OWN path is empty on the live frame and doesn't cross anything
        #    still flying (no waiting for whole waves). Taps that didn't take are repeated.
        if plan is not None and plan["pose"] != (world.ox, world.oy):
            plan = None
        if plan is not None:
            pending = [a for a in plan["arrows"] if present(a)]
            if not pending:
                plan = None
            else:
                due = [a for a in pending
                       if not any(d == a["dir"] and abs(world.ox + a["x"] - bx) <= 6 and
                                  abs(world.oy + a["y"] - by) <= 6 for bx, by, d in bad_spots)
                       and (a["_taps"] == 0 or current_time - a["_t"] > WAVE_RETAP_AFTER)
                       and not covered(a)
                       and cast_ray(a, world.resting_mask if aggressive else mask, world, half)[0] == "CLEAR"
                       and (aggressive or not crosses_flight(a))]
                if any(a["_taps"] >= WAVE_MAX_TAPS for a in due):
                    print("    [plan] an arrow won't leave; dropping the plan and re-checking")
                    plan = None
                elif due:
                    retaps = sum(1 for a in due if a["_taps"])
                    done = tap(due, f"planned, {len(pending) - len(due)} still waiting"
                                    + (f", {retaps} re-tap(s)" if retaps else ""))
                    for a in done:
                        a["_taps"] += 1
                        a["_t"] = time.time()
                    if done:
                        plan["last_tap"] = time.time()
                        stuck_frames = 0
                    continue
                elif any(not any(b["dir"] == a["dir"] and abs(b["x"] - a["x"]) <= 3 and abs(b["y"] - a["y"]) <= 3
                                 for b in pending) for a in candidates):
                    # Arrows outside the plan are free now (e.g. two touching arrows the planner
                    # left alone): don't make them wait for this plan - plan again with them in it
                    plan = None
                elif current_time - plan["last_tap"] > PLAN_IDLE_DROP:
                    plan = None   # nothing has become free for a while: look at the board afresh
                else:
                    continue      # paths still emptying

        # 2) Fresh plan from this frame + the previous one (first taps = still in both frames, path
        #    clear in both, and not crossing anything in flight)
        if candidates:
            waves = plan_waves(results, mask, world, half, last_look, is_recent)
            if not waves:
                continue  # first look / things moving: the next frame confirms them
            if not waves_ok:
                waves = waves[:1]   # a planned arrow cost a star on this level: no planning ahead
            first = [a for a in waves[0] if aggressive or not crosses_flight(a)]
            done = tap(first, f"planned {sum(map(len, waves))} escapable in {len(waves)} wave(s)")
            recorder.waves(len(waves))
            now_t = time.time()
            flat = [a for w in waves for a in w]
            for a in flat:
                a["_taps"], a["_t"] = (1, now_t) if any(a is d for d in done) else (0, 0.0)
            plan = {"arrows": flat, "pose": (world.ox, world.oy), "mask": mask, "last_tap": now_t}
            if done:
                stuck_frames = 0
            continue

        # A board that fits shouldn't need exploring. If it has drifted (the game moves the camera
        # by itself sometimes) or sits partly behind the header, put it back in view in one move.
        if not world.too_big and calm and recenter_board(frame):
            wait_until_settled()
            world.reset(keep_level=True)
            prev, prev_line_pixels, plan, calm_ref = None, None, None, None
            in_flight = []
            min_frame_time = time.time()
            continue

        if world.missing:
            # A remembered arrow in view wasn't detected: it's forgotten after a 2nd frame without
            # it, but panning away now would reset that count. A gone arrow then stayed on the map
            # as "free", and the bot kept coming back for it (the 80s level: ~10 wasted pans).
            continue
        if world.pose_uncertain and time.time() - last_pan_done < POSE_CONFIRM_WAIT:
            # Just panned and the move couldn't be measured for sure: rays into the off-screen map
            # read UNKNOWN until the view is matched against the map (re-tried every 0.4s). Panning
            # on those "unknown rays" right away went back and forth forever (pan, unsure, pan back).
            continue

        if resweep_due():
            prev, prev_line_pixels, plan, calm_ref = None, None, None, None
            in_flight = []
            continue
        direction, reason, amount = choose_pan(results, world, mask.shape, is_bad)
        if direction is None:
            stuck_frames += 1
            print(f"[?] Nothing clear and nothing to explore ({stuck_frames}).")
            if stuck_frames == 5:
                recorder.note("stuck")
                recorder.event("nothing clear and nothing to explore", frame, results, world)
            if stuck_frames == 3:
                check_video("nothing to do")
            if stuck_frames >= 5:
                now_half = current_line_half()
                if now_half is not None and level_half is not None and now_half > level_half + 0.25:
                    print(f"[*] Zoom drifted (lines {level_half:.1f} -> {now_half:.1f}). Re-zooming.")
                    full_zoom_out()
                    level_half = current_line_half() or level_half
                    world.reset(keep_level=True)
                elif not stuck_soft_done:
                    # First time: forget camera limits and chase attempts (cheap; keeps the map)
                    print("[*] Stuck with arrows left: re-trying directions and chases.")
                    world.at_limit.clear()
                    world.chased.clear()
                    world.idle_pans = 0
                    world.tile_time[:] = 0
                    stuck_soft_done = True
                else:
                    # Still stuck: don't sit there trusting the old map. Start the level's picture
                    # over: decide again whether the board fits, find the edges, sweep it again.
                    print("[*] Stuck with arrows left: starting the board picture over.")
                    world.reset(keep_level=True)
                    stuck_soft_done = False
                min_frame_time = time.time()
                prev, prev_line_pixels, plan, calm_ref = None, None, None, None
                in_flight = []
                stuck_frames = 0
            time.sleep(STUCK_FRAME_WAIT)
            continue

        print(f"[>] Panning {direction} ({reason})")
        if not reason.startswith("survey"):
            hunt["futile"] += 1
        prev, prev_line_pixels, plan, calm_ref = None, None, None, None
        in_flight = []
        world.drop_tapped_missing(tap_log, current_time)
        min_frame_time = pan_and_register(world, direction, amount)
        last_pan_done = time.time()

def offline_test(path):
    """python bot.py --test screenshot.png  -> writes Temp/debug_vision.png without a device."""
    frame = cv2.imread(path)
    world = World()
    _, results, _ = analyze(frame, world)
    for arrow, status, hit in results:
        print(f"  ({arrow['x'] + ROI_X1:4d},{arrow['y'] + ROI_Y1:4d}) {arrow['dir']:6s} {status}")
    print(f"[*] {len(results)} arrows, edges: {world.edges}")
    save_debug_image(frame, results, world)
    _debug_writer.flush()

# -----------------------------------------------------------------------------
# TEMPLATE GRABBER (python bot.py --grab / --grab-ingame)
# -----------------------------------------------------------------------------
def grab_templates(ingame=False):
    """Screenshot the phone, box one or more things (drag, ENTER after each, ESC when done) and name
    them: saved as new tap-when-seen buttons (buttons/) or "this is a level" HUD markers (ingame/,
    position recorded in positions.json). Existing files are never overwritten."""
    folder = INGAME_DIR if ingame else BUTTONS_DIR
    os.makedirs(folder, exist_ok=True)
    raw = subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True).stdout
    frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR) if raw else None
    if frame is None:
        print("[-] Could not get a screenshot. Is the phone connected (adb devices)?")
        return
    scale = 900 / frame.shape[0]          # the phone screen is too tall for most monitors
    preview = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    print("[*] Drag a box, ENTER to keep it, repeat; ESC when done.")
    boxes = cv2.selectROIs("Box each thing, ENTER after each, ESC when done", preview, showCrosshair=False)
    cv2.destroyAllWindows()
    if len(boxes) == 0:
        print("[-] Nothing selected.")
        return
    positions_file = os.path.join(folder, "positions.json")
    positions = {}
    if ingame and os.path.exists(positions_file):
        with open(positions_file) as f:
            positions = json.load(f)
    for i, (x, y, w, h) in enumerate(boxes, 1):
        x0, y0 = int(x / scale), int(y / scale)          # back to full resolution (what the bot sees)
        x1, y1 = int((x + w) / scale), int((y + h) / scale)
        crop = frame[y0:y1, x0:x1]
        name = input(f"Name for box {i} at ({x0},{y0}) {crop.shape[1]}x{crop.shape[0]} (e.g. next, close_x): ")
        name = re.sub(r"[^A-Za-z0-9_\-]+", "_", name.strip()) or "button"
        path, n = os.path.join(folder, name + ".png"), 2
        while os.path.exists(path):
            path, n = os.path.join(folder, f"{name}_{n}.png"), n + 1
        cv2.imwrite(path, crop)
        if ingame:
            positions[os.path.basename(path)] = [x0, y0]
        print(f"[+] Saved {path}")
    if ingame:
        with open(positions_file, "w") as f:
            json.dump(positions, f, indent=2)
        print(f"[+] Updated {positions_file}")

class _UserOut:
    """Console filter for the packaged bot: drops the per-frame detail (pans, map, status lines,
    planning) and keeps what a user cares about: connecting, levels, lost stars, real problems."""
    DROP = re.compile(
        r"^\s+\[|^\[>\]|^\[\?\]|^\[\*\] \d+ arrows \||^\[\+\] Tapping|^\[\+\] '.*' worked"
        r"|^\[\*\] (Menu with nothing to tap|Level looks finished|Board cleared|Zooming out|Screen:"
        r"|Stuck with arrows|Zoom drifted|No arrows found|Nothing in view|\d+ pans without a tap"
        r"|In-game markers|Buttons:|Game package|Vision Engine|The whole board is already)"
        r"|^\[!\] (Won't tap|Tapped the arrow|It happened during|Back to careful|No progress for"
        r"|Camera moved on its own|World map overflow|\d+ survey pans)"
        r"|^\[stats\] averages")

    def __init__(self, stream):
        self.stream, self.buf = stream, ""

    def write(self, text):
        self.buf += text
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if not self.DROP.search(line):
                self.stream.write(line + "\n")
        return len(text)

    def flush(self):
        self.stream.flush()

    def isatty(self):
        return self.stream.isatty()


def _keys_ok():
    """Single key presses can be read (a real console: Windows, or a Linux / Termux terminal)."""
    if sys.stdin is None or not sys.stdin.isatty():
        return False
    if os.name == "nt":
        return True
    try:
        import termios  # noqa: F401
        return True
    except ImportError:
        return False


def _ask_key(prompt):
    """y/N question answered with one key; Esc quits."""
    if os.name != "nt" or not _keys_ok():
        try:
            return input(prompt).strip().lower().startswith("y")
        except EOFError:
            return False
    import msvcrt
    print(prompt, end="", flush=True)
    while True:
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            msvcrt.getwch()                    # arrow / function key: ignore
        elif ch == "\x1b":
            print("\n[*] Stopped.")
            sys.exit(0)
        elif ch in "yY":
            print("y")
            return True
        elif ch in "nN\r\n":
            print("n")
            return False


_tty_saved = None   # (fd, settings) of this terminal before keys were switched to one-at-a-time

def _remember_tty():
    global _tty_saved
    if os.name != "nt" and _keys_ok() and _tty_saved is None:
        import termios
        fd = sys.stdin.fileno()
        _tty_saved = (fd, termios.tcgetattr(fd))
        atexit.register(_restore_tty)

def _restore_tty():
    """Terminal back to normal typing (echo, whole lines)."""
    if _tty_saved is not None:
        import termios
        try:
            termios.tcsetattr(_tty_saved[0], termios.TCSADRAIN, _tty_saved[1])
        except Exception:
            pass


# -----------------------------------------------------------------------------
# STOPPING: the stop button (a notification on the phone), Esc / q in this window, or Ctrl+C
# -----------------------------------------------------------------------------
def show_stopped():
    """The stop button turns into "Arrow bot stopped" (Android's shell can't remove a notification)."""
    if BRIDGE_MODE:
        try:
            (helper or bridge.Helper()).state("stopped")
        except Exception:
            pass
        return
    _post_notification("Arrow bot stopped", "Swipe this away.")

def stop_bot(why):
    """Stop the same way Ctrl+C does: no more taps, phone screen back on, scrcpy closed, the
    phone's sleep setting restored - and exit code 0, so the supervisor doesn't restart it."""
    global stopping
    stopping = True
    print(f"\n[*] {why}: stopping the bot...")
    alone = os.environ.get("ARROWBOT_SUPERVISED") != "1"   # (else the supervisor does the last two)
    for step in (lambda: tapper is not None and tapper.stop(),
                 lambda: link is not None and restore_screen(),
                 lambda: link is not None and link.stop(),
                 stop_desktop_view,
                 _restore_tty,
                 lambda: alone and restore_phone_sleep(),
                 lambda: alone and show_stopped()):
        try:
            step()
        except Exception:
            pass
    try:
        sys.stdout.flush()
    except Exception:
        pass
    os._exit(0)


def _start_stop_keys():
    """Esc (or q) in this window stops the bot. In Android's Terminal app, ESC is a button in the
    row of keys above the keyboard."""
    if not _keys_ok():
        return
    if os.name == "nt":
        import msvcrt

        def pressed():
            while msvcrt.kbhit():
                if msvcrt.getwch() in ("\x1b", "q", "Q"):
                    return True
            return False
    else:
        import select
        import tty
        _remember_tty()
        fd = _tty_saved[0]
        tty.setcbreak(fd)        # keys arrive one at a time, not echoed (Ctrl+C still works)

        def pressed():
            if not select.select([fd], [], [], 0)[0]:
                return False
            # a lone Esc; arrow keys etc. also start with Esc but come with more bytes
            return os.read(fd, 64) in (b"\x1b", b"q", b"Q")

    def watch():
        while True:
            try:
                if pressed():
                    break
            except Exception:
                return
            time.sleep(0.05)
        stop_bot("Stop key pressed")

    threading.Thread(target=watch, daemon=True).start()


STOP_TAG = "arrowbot"
STOP_KEY = f"|com.android.shell|2020|{STOP_TAG}|"   # in the notification's key: user|package|id|tag|uid
STOP_CHECK_EVERY = 1.0    # seconds between looks at the phone's event log
STOP_REASONS = ("1", "2") # notification_canceled reasons that mean "stop": clicked, swiped away

def _post_notification(title, text, intent=""):
    """A notification from Android's shell, always the same one (tag STOP_TAG): posting replaces it."""
    adb_run(["shell", f"cmd notification post -t '{title}' {intent} {STOP_TAG} '{text}'"], timeout=10)

class StopButton:
    """A notification on the phone: "Arrow bot is playing - tap here to stop it". Android's shell
    can post a notification but hears nothing back, so the bot reads the phone's event log (every
    tap or swipe on a notification is written there) once a second. Tapping it (or swiping it
    away) stops the bot; on the phone the tap also opens the terminal the bot runs in."""

    def __init__(self):
        self.intent = None if BRIDGE_MODE else self._tap_opens()
        self.since = None

    @staticmethod
    def _tap_opens():
        """What a tap opens: the terminal the bot runs in. Named exactly (package/activity):
        Android adds a data URI to the intent, which a plain "open this app" wouldn't match.
        Elsewhere (or if it can't be found) a broadcast nobody listens to: just the tap."""
        if ON_PHONE:
            want = onphone.TERMINAL_APPS[1] if onphone.IN_TERMUX else onphone.TERMINAL_APPS[0]
            out = adb_run(["shell", "pm", "list", "packages", want], timeout=10)
            pkgs = [l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("package:")]
            for pkg in sorted(pkgs, key=lambda p: p != want):
                out = adb_run(["shell", "cmd", "package", "resolve-activity", "--brief", pkg], timeout=10)
                found = [l.strip() for l in out.splitlines() if re.fullmatch(r"[\w.]+/[\w.$]+", l.strip())]
                if found:
                    return f"-c activity -f 0x10000000 {found[-1]}"
        return f"-c broadcast -a {STOP_TAG}.STOP {STOP_TAG}:stop"

    def show(self):
        """(Re)post it. Only taps / swipes from now on count."""
        now = adb_run(["shell", "date", "+%s"], timeout=5).strip()
        self.since = float(now) if now.isdigit() else time.time()
        _post_notification("Arrow bot is playing", "Tap here to stop it.", self.intent)

    def check(self):
        """"stop" (tapped / swiped away), "gone" (cleared some other way, e.g. "Clear all") or None."""
        out = adb_run(["shell", f"logcat -b events -t 2000 -v epoch | grep -F '{STOP_KEY[1:]}'"], timeout=5)
        result = None
        for line in out.splitlines():
            t = re.match(r"\s*(\d+(?:\.\d+)?)\s", line)
            if not t or float(t.group(1)) < self.since:
                continue
            if "notification_clicked" in line:
                return "stop"
            if "notification_canceled" in line:
                reason = re.search(re.escape(STOP_KEY) + r"\d+,(\d+)", line)
                if reason and reason.group(1) in STOP_REASONS:
                    return "stop"
                result = "gone"
        return result

    def start(self):
        if BRIDGE_MODE:            # the helper's own notification has a "Stop bot" button
            helper.state("playing")

            def poll():
                while True:
                    time.sleep(STOP_CHECK_EVERY)
                    try:
                        if helper.stop_requested():
                            stop_bot("Stop button tapped")
                    except Exception:
                        pass

            threading.Thread(target=poll, daemon=True).start()
            return
        self.show()

        def watch():
            while True:
                time.sleep(STOP_CHECK_EVERY)
                try:
                    what = self.check()
                except Exception:
                    continue
                if what == "stop":
                    stop_bot("Stop button tapped")
                elif what == "gone":
                    self.show()            # taken away by "Clear all": put it back

        threading.Thread(target=watch, daemon=True).start()


def run_embedded():
    """The bot inside the ArrowBot app (Chaquopy, see android/app/src/main/python/app_main.py): what
    the --child process does, without the supervisor - the app runs it in a process of its own, and
    stopping it ends that process. Talks to the app's helper part (BRIDGE_MODE)."""
    sys.stdout = _UserOut(sys.stdout)
    atexit.register(show_stopped)
    print(f"===== bot started {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
    connect()
    StopButton().start()
    print('[*] To stop the bot: "Stop bot" in the ArrowBot app or its notification.')
    while True:
        try:
            run_solver()
        except Exception:
            # Never quit on an unexpected error: log it in full and carry on
            import traceback
            print("[!!] Unexpected error, continuing:")
            traceback.print_exc()
            time.sleep(2)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--test":
        offline_test(sys.argv[2])
        sys.exit(0)
    if len(sys.argv) >= 2 and sys.argv[1] in ("--grab", "--grab-ingame"):
        grab_templates(ingame=sys.argv[1] == "--grab-ingame")
        sys.exit(0)
    if "--child" not in sys.argv:
        # Supervisor: the bot runs as a child process and is started again if it ever dies (a
        # crash inside the video decoder can't be caught from Python). The game keeps running
        # meanwhile, so a restart costs a few seconds, not the level. Ctrl+C stops both.
        show = ask_show_screen()
        # the phone is kept awake only while the bot runs: put its setting back on any exit, and
        # the stop button says the bot stopped (registered first: runs after the restore)
        atexit.register(show_stopped)
        atexit.register(restore_phone_sleep)
        on_console_close(restore_phone_sleep)
        _remember_tty()                    # (a crashed bot could leave it typing one key at a time)
        if ON_PHONE:
            # the game is the open app while the bot plays: keep Termux running at full speed
            onphone.wake_lock(True)
            atexit.register(onphone.wake_lock, False)
        first = True
        while True:
            child = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--child",
                                      "--show" if show and first else "--no-show"],
                                     env=dict(os.environ, ARROWBOT_SUPERVISED="1"))
            try:
                rc = child.wait()
            except KeyboardInterrupt:
                try:
                    child.wait(timeout=15)       # let it turn the screen back on etc.
                except Exception:
                    child.kill()
                break
            if rc == 0:
                break
            where = save_crash_bundle(rc)
            msg = (f"[!!] The bot crashed (exit code {rc:#x}); starting it again in 2s. The game keeps running."
                   + (f" Saved: {os.path.relpath(where, HERE)}" if where else ""))
            print(msg)
            try:
                pass
            except OSError:
                pass
            first = False
            time.sleep(2)
        sys.exit(0)
    sys.stdout = _UserOut(sys.stdout)
    if os.name == "nt":
        _start_stop_keys()         # (elsewhere after connecting: pairing may still need typed lines)
    if os.environ.get("ARROWBOT_SUPERVISED") != "1":
        # started on its own (python bot.py --child): nobody else will put the phone's sleep back
        atexit.register(show_stopped)
        atexit.register(restore_phone_sleep)
        on_console_close(restore_phone_sleep)
    show_screen = ask_show_screen()
    print(f"===== bot started {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
    disable_quick_edit()
    connect()
    if os.name != "nt":
        _start_stop_keys()
    StopButton().start()
    print("[*] To stop the bot: " + ("\"Stop bot\" in the Arrow bot notification" if BRIDGE_MODE else
          "tap the \"Arrow bot is playing\" notification on the phone")
          + (", or Esc / q in this window." if _keys_ok() else "."))
    if show_screen:
        start_desktop_view()
    while True:
        try:
            run_solver()
        except KeyboardInterrupt:
            if tapper is not None:
                tapper.stop()
            print("\n[*] Bot terminated safely by user.")
            if adb_shell is not None:
                adb_shell.terminate()
            if link is not None:
                if SCREEN_OFF:
                    restore_screen()
                link.stop()
            break
        except Exception:
            # Never quit on an unexpected error: log it in full and carry on
            import traceback
            print("[!!] Unexpected error, continuing:")
            traceback.print_exc()
            time.sleep(2)
