"""
Running the bot on the phone itself (in Termux), no computer needed.

Termux's own adb connects to the phone's "Wireless debugging" (Developer options, Android 11+) at
127.0.0.1. That gives the bot the same access a computer on USB has (taps, screenshots, scrcpy,
uiautomator2), so the rest of the bot works unchanged. The first time, Termux has to be paired
with the phone once (like allowing a computer); after that it connects by itself.

Used by ArrowBot.py when it starts and by bot.py whenever it (re)connects.
"""
import os
import re
import socket
import struct
import subprocess
import sys
import time

ON_PHONE = hasattr(sys, "getandroidapilevel") or "ANDROID_ROOT" in os.environ
TERMINAL_PACKAGE = os.environ.get("TERMUX_APP__PACKAGE_NAME") or "com.termux"
HOST = "127.0.0.1"

ADB_HELP = """[-] adb wasn't found. In Termux it comes with android-tools:
      pkg install android-tools
    then run this again."""

WIRELESS_HELP = """[!] On the phone the bot works through Android's "Wireless debugging" (Android 11 or newer):
    1. Settings > About phone > tap "Build number" 7 times (this turns on Developer options).
    2. Be on Wi-Fi (Wireless debugging only runs on Wi-Fi; it doesn't need internet).
    3. Settings > System > Developer options > turn on "Wireless debugging".
       (menu names vary by phone; step by step, "Connect to a device over Wi-Fi":
        https://developer.android.com/tools/adb)
    Waiting for it... (Ctrl+C to stop)"""

PAIR_HELP = """[!] Wireless debugging is on. Termux has to be allowed once, like a computer (pairing):
    1. Put Settings and Termux side by side (split screen or pop-up view) so you can see both.
    2. Settings > Developer options > Wireless debugging > "Pair device with pairing code".
    3. Type here the port shown after the ':' under "IP address & Port" in that box, then the code."""

PAIR_BY_HAND = """[!] Wireless debugging is on, but Termux isn't paired with the phone yet. In Termux, run once:
      adb pair 127.0.0.1:PORT CODE
    with PORT and CODE from Settings > Developer options > Wireless debugging > "Pair device with
    pairing code" (split screen to see both). Waiting... (Ctrl+C to stop)"""

# adb's wire protocol: a CONNECT is answered with STLS (wireless debugging), AUTH or CNXN ("adb tcpip")
A_CNXN, A_AUTH, A_STLS = 0x4E584E43, 0x48545541, 0x534C5453


def _adb(adb, *args, timeout=15):
    try:
        r = subprocess.run([adb, *args], capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return str(e)


def connected(adb):
    """Serial of this phone ("127.0.0.1:PORT") if Termux's adb is already connected to it."""
    for line in _adb(adb, "devices").splitlines()[1:]:
        serial, _, state = line.partition("\t")
        if state.strip() == "device" and serial.startswith((HOST + ":", "localhost:")):
            return serial.strip()
    return None


def _port_range():
    try:
        with open("/proc/sys/net/ipv4/ip_local_port_range") as f:
            lo, hi = (int(v) for v in f.read().split())
        return lo, hi
    except (OSError, ValueError):
        return 32768, 60999


def _open_ports():
    """Ports something listens on at 127.0.0.1. Wireless debugging picks a new random port each
    time it's switched on; a closed port answers at once, so trying them all takes a second or two.
    5555 first: a port opened earlier with `adb tcpip 5555` from a computer works too."""
    lo, hi = _port_range()
    found = []
    for port in [5555] + list(range(lo, hi + 1)):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.2)
        try:
            if s.connect_ex((HOST, port)) == 0:
                found.append(port)
        except OSError:
            pass
        finally:
            s.close()
    return found


def _is_adbd(port):
    """The port answers an adb CONNECT the way the phone's adbd does (other apps listen too)."""
    payload = b"host::\0"
    msg = struct.pack("<6I", A_CNXN, 0x01000001, 256 * 1024, len(payload), sum(payload),
                      A_CNXN ^ 0xFFFFFFFF) + payload
    try:
        with socket.create_connection((HOST, port), timeout=0.5) as s:
            s.settimeout(1.0)
            s.sendall(msg)
            head = s.recv(24)
    except OSError:
        return False
    return len(head) >= 4 and struct.unpack("<I", head[:4])[0] in (A_STLS, A_AUTH, A_CNXN)


def _ready(adb, serial, wait=8.0):
    end = time.time() + wait
    while time.time() < end:
        if _adb(adb, "-s", serial, "get-state", timeout=5) == "device":
            return True
        time.sleep(0.5)
    return False


def try_connect(adb):
    """One attempt. (serial, None) when connected; otherwise (None, why): "off" = no wireless
    debugging found, "pair" = it's on but Termux isn't paired with it yet."""
    serial = connected(adb)
    if serial:
        return serial, None
    unpaired = False
    for port in _open_ports():
        if not _is_adbd(port):
            continue
        serial = f"{HOST}:{port}"
        out = _adb(adb, "connect", serial, timeout=20)
        if "connected to" in out and _ready(adb, serial):    # also "already connected to"
            return serial, None
        unpaired = True                 # it's adbd, but it won't let this Termux in
        _adb(adb, "disconnect", serial, timeout=5)
    return None, ("pair" if unpaired else "off")


def _ask(prompt):
    out = sys.__stdout__ or sys.stdout      # not the bot's line-buffered console filter
    out.write(prompt)
    out.flush()
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line.strip()


def pair(adb, explain=True):
    """Ask for the port and code the phone shows and pair Termux's adb with it. True if it worked."""
    if explain:
        print(PAIR_HELP)
    port = re.search(r"(\d{2,5})\s*$", _ask("    Port (e.g. 37123): "))
    code = re.sub(r"\D", "", _ask("    Pairing code (6 digits): "))
    if not port or not code:
        print("[-] That needs the port and the 6-digit code. Once more:")
        return False
    out = _adb(adb, "pair", f"{HOST}:{port.group(1)}", code, timeout=30)
    if "Successfully paired" in out:
        print("[+] Paired: from now on the bot connects to this phone by itself.")
        return True
    print(f"[-] Pairing didn't work ({out or 'no answer'}). Tap the pairing option again for a new code.")
    return False


def wait_until_connected(adb="adb"):
    """Connect Termux's adb to this phone (pairing it the first time), waiting as long as it takes.
    Every later adb / uiautomator2 call goes to it (ANDROID_SERIAL). Returns the serial."""
    interactive = sys.stdin is not None and sys.stdin.isatty()
    shown = None
    while True:
        serial, why = try_connect(adb)
        if serial:
            os.environ["ANDROID_SERIAL"] = serial
            if shown:
                print("[+] Connected to the phone's wireless debugging.")
            return serial
        if why == "pair" and interactive:
            try:
                pair(adb, explain=shown != "pair")
                shown = "pair"
                continue
            except EOFError:            # no keyboard after all: explain how to pair by hand
                interactive = False
        if why != shown:
            print(WIRELESS_HELP if why == "off" else PAIR_BY_HAND)
            shown = why
        time.sleep(3)


def wake_lock(on):
    """Termux's wake lock: Android keeps Termux (and so the bot) running at full speed while the
    game is the open app."""
    try:
        subprocess.run(["termux-wake-lock" if on else "termux-wake-unlock"], capture_output=True, timeout=10)
    except Exception:
        pass
