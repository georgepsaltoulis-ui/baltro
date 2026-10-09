"""
Running the bot on the phone itself, no computer needed: in Android's own Terminal app (the Linux /
Debian one from Developer options > "Linux development environment"), or in Termux.

adb in there connects to the phone's own "Wireless debugging" (Developer options, Android 11+).
That gives the bot the same access a computer on USB has (taps, screenshots, scrcpy,
uiautomator2), so the rest of the bot works unchanged. The Terminal app's Linux runs in a virtual
machine, so it reaches the phone at its Wi-Fi address (typed in once, then remembered); Termux runs
on Android itself and uses 127.0.0.1. The first time, adb has to be paired with the phone once (like
allowing a computer); after that the bot connects by itself.

Used by ArrowBot.py when it starts and by bot.py whenever it (re)connects.
"""
import errno
import os
import platform
import re
import selectors
import socket
import struct
import subprocess
import sys
import time

IN_TERMUX = hasattr(sys, "getandroidapilevel") or "ANDROID_ROOT" in os.environ
# Android's Terminal app: Debian in a VM (user "droid"), the phone's Download folder at /mnt/shared
IN_TERMINAL_APP = (not IN_TERMUX and sys.platform.startswith("linux")
                   and platform.machine() in ("aarch64", "arm64")
                   and (os.path.isdir("/mnt/shared") or os.path.isdir("/home/droid")))
_forced = os.environ.get("ARROWBOT_ON_PHONE")   # "1" / "0": ArrowBot.py --on-phone, or by hand
ON_PHONE = (IN_TERMUX or IN_TERMINAL_APP) if _forced not in ("0", "1") else _forced == "1"
VM = ON_PHONE and not IN_TERMUX   # the phone is another machine on the network, not 127.0.0.1
TERMINAL_APPS = ("virtualization.terminal", os.environ.get("TERMUX_APP__PACKAGE_NAME") or "com.termux")

HERE = os.path.dirname(os.path.abspath(__file__))
ADDRESS_FILE = os.path.join(HERE, "Temp", "phone_address.txt")   # the phone's Wi-Fi IP (VM only)

if IN_TERMUX:
    ADB_HELP = """[-] adb wasn't found. In Termux it comes with android-tools:
      pkg install android-tools
    then run this again."""
else:
    ADB_HELP = """[-] adb wasn't found. Install it in the Terminal app:
      sudo apt update && sudo apt install -y --no-install-recommends adb
    then run this again."""

ADB_TOO_OLD = """[-] This adb ({version}) is too old to pair with Wireless debugging (that needs adb 30 or newer).
    On Debian 12 (bookworm) get the newer one from bookworm-backports:
      echo 'deb http://deb.debian.org/debian bookworm-backports main' | sudo tee /etc/apt/sources.list.d/backports.list
      sudo apt update && sudo apt install -t bookworm-backports adb
    then run this again."""

WIRELESS_STEPS = """    1. Settings > About phone > tap "Build number" 7 times (this turns on Developer options).
    2. Be on Wi-Fi (Wireless debugging only runs on Wi-Fi; it doesn't need internet).
    3. Settings > System > Developer options > turn on "Wireless debugging".
       (menu names vary by phone; step by step, "Connect to a device over Wi-Fi":
        https://developer.android.com/tools/adb)"""

WIRELESS_HELP = ("""[!] On the phone the bot works through Android's "Wireless debugging" (Android 11 or newer):
""" + WIRELESS_STEPS + """
    No Wi-Fi? Any Wi-Fi network will do, without internet: e.g. another phone's hotspot. Some phones
    also allow it with their own hotspot on (Settings > Hotspot), which uses no data by itself.
    Or, once per phone restart, with a computer instead: plug the phone in by USB and run there
      python ArrowBot.py --no-wifi-setup
    then unplug it and start the bot here again. That needs no Wi-Fi at all.
    Waiting for it... (Ctrl+C to stop)""")

ADDRESS_HELP = ("""[!] On the phone the bot works through Android's "Wireless debugging" (Android 11 or newer):
""" + WIRELESS_STEPS + """
    4. Tap the words "Wireless debugging" (not the switch) to open it. At the top, under "IP address
       & Port", it shows something like 192.168.1.23:41234 - type that here (split screen helps).
       Not there? Wireless debugging only switches on while the phone is on Wi-Fi (not mobile data).
       (Not 127.0.0.1: in here that's this Linux itself, not the phone.)""")

PAIR_HELP = """[!] Wireless debugging is on. adb here has to be allowed once, like a computer (pairing):
    1. Put Settings and this terminal side by side (split screen or pop-up view) so you can see both.
    2. Settings > Developer options > Wireless debugging > "Pair device with pairing code".
    3. Type here the "IP address & Port" shown in that box (e.g. 192.168.1.23:37123), then the code."""

PAIR_BY_HAND = """[!] Wireless debugging is on, but adb here isn't paired with the phone yet. Run once:
      adb pair IP:PORT CODE
    with the IP address & Port and the code from Settings > Developer options > Wireless debugging >
    "Pair device with pairing code" (split screen to see both). Waiting... (Ctrl+C to stop)"""

# adb's wire protocol: a CONNECT is answered with STLS (wireless debugging), AUTH or CNXN ("adb tcpip")
A_CNXN, A_AUTH, A_STLS = 0x4E584E43, 0x48545541, 0x534C5453
_port_hint = {}   # host -> port typed in by the user (tried before searching)


def _adb(adb, *args, timeout=15):
    try:
        r = subprocess.run([adb, *args], capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return str(e)


def adb_too_old(adb):
    """The adb version text if it predates wireless pairing (adb 30), else None."""
    m = re.search(r"Version (\d+)[.\d]*\S*", _adb(adb, "version"))
    return m.group(0) if m and int(m.group(1)) < 30 else None


_found_host = []   # VM: where Android answered this run without a typed address (the VM's gateway;
                   # not saved: the VM's addresses can change when the Terminal app restarts)

def phone_host():
    """Where the phone's adbd is: 127.0.0.1 in Termux; from the Terminal app's VM its Wi-Fi IP
    (typed in once, saved) or the VM's gateway if Android answers there."""
    if not VM:
        return "127.0.0.1"
    try:
        with open(ADDRESS_FILE) as f:
            saved = f.read().strip()
        if saved:
            return saved
    except OSError:
        pass
    return _found_host[-1] if _found_host else None


def _save_host(ip):
    os.makedirs(os.path.dirname(ADDRESS_FILE), exist_ok=True)
    with open(ADDRESS_FILE, "w") as f:
        f.write(ip)


def _parse_address(text):
    """'192.168.1.23:41234' -> ('192.168.1.23', 41234); '41234' -> (None, 41234); else (None, None)."""
    m = re.search(r"(?:(\d{1,3}(?:\.\d{1,3}){3})\s*:\s*)?(\d{2,5})\s*$", text.strip())
    return (m.group(1), int(m.group(2))) if m else (None, None)


def connected(adb, host):
    """Serial of this phone ("HOST:PORT") if adb is already connected to it."""
    for line in _adb(adb, "devices").splitlines()[1:]:
        serial, _, state = line.partition("\t")
        serial, state = serial.strip(), state.strip()
        if state != "device" or ":" not in serial:
            continue
        ip = serial.rsplit(":", 1)[0]
        if ip in (host, "localhost") or (host is None and re.fullmatch(r"\d+(\.\d+){3}", ip)):
            return serial
    return None


def _port_range():
    try:
        with open("/proc/sys/net/ipv4/ip_local_port_range") as f:
            lo, hi = (int(v) for v in f.read().split())
        return lo, hi
    except (OSError, ValueError):
        return 32768, 60999


def _open_ports(host, batch=500, wait=0.5):
    """Ports something listens on at `host`. Wireless debugging picks a new random port each time
    it's switched on; closed ports answer at once, so trying them all takes a few seconds (many
    at a time, so even a phone that doesn't answer for closed ports is done in ~30s).
    5555 first: a port opened earlier with `adb tcpip 5555` from a computer works too."""
    lo, hi = _port_range()
    ports = [5555] + [p for p in range(lo, hi + 1) if p != 5555]
    found = []
    for i in range(0, len(ports), batch):
        sel = selectors.DefaultSelector()
        socks = []
        for port in ports[i:i + batch]:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setblocking(False)
            socks.append(s)
            if s.connect_ex((host, port)) in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, 10035):  # 10035: Windows
                sel.register(s, selectors.EVENT_WRITE, port)
        end = time.time() + wait
        while sel.get_map() and time.time() < end:
            for key, _ in sel.select(timeout=max(0.0, end - time.time())):
                if key.fileobj.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0:
                    found.append(key.data)
                sel.unregister(key.fileobj)
        sel.close()
        for s in socks:
            s.close()
    return sorted(found, key=lambda p: (p != 5555, p))


def _adbd_kind(host, port):
    """How the port answers an adb CONNECT: "tls" = Wireless debugging (needs pairing), "tcp" = a
    port opened with `adb tcpip` from a computer (asks "Allow?" on the phone), None = not adbd."""
    payload = b"host::\0"
    msg = struct.pack("<6I", A_CNXN, 0x01000001, 256 * 1024, len(payload), sum(payload),
                      A_CNXN ^ 0xFFFFFFFF) + payload
    try:
        with socket.create_connection((host, port), timeout=1.0) as s:
            s.settimeout(1.5)
            s.sendall(msg)
            head = s.recv(24)
    except OSError:
        return None
    cmd = struct.unpack("<I", head[:4])[0] if len(head) >= 4 else None
    return "tls" if cmd == A_STLS else "tcp" if cmd in (A_AUTH, A_CNXN) else None


def _is_adbd(host, port):
    """The port answers an adb CONNECT the way the phone's adbd does (other apps listen too)."""
    return _adbd_kind(host, port) is not None


def _ready(adb, serial, wait=8.0):
    """Connected and allowed. A port opened with `adb tcpip` (from a computer) asks on the phone's
    screen first, like a new computer on USB: then wait up to a minute for "Allow"."""
    end, asked = time.time() + wait, False
    while time.time() < end:
        state = _adb(adb, "-s", serial, "get-state", timeout=5)
        if state == "device":
            return True
        if "unauthorized" in state and not asked:
            print('[!] The phone asks "Allow USB debugging?": tick "Always allow" and tap Allow.')
            end, asked = time.time() + 60, True
        time.sleep(0.5)
    return False


def _gateway():
    """The Linux VM's default gateway, i.e. Android itself as seen from the Terminal app."""
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) > 2 and parts[1] == "00000000" and parts[2] != "00000000":
                    return socket.inet_ntoa(struct.pack("<L", int(parts[2], 16)))
    except (OSError, ValueError):
        pass
    return None


_gateway_tried = []

def _phone_at_gateway():
    """VM, phone's Wi-Fi IP not known yet: Android may answer right at the VM's gateway, then
    nobody has to type an address. Looked at once per run (a full port search)."""
    gw = _gateway() if VM and not _gateway_tried else None
    _gateway_tried.append(gw)
    if gw:
        for port in _open_ports(gw):
            if _is_adbd(gw, port):
                _found_host.append(gw)
                _port_hint[gw] = port
                return gw
    return None


def _remember(ip):
    """Save where the phone is - unless that's the VM's gateway (found again every run)."""
    if ip == _gateway():
        _found_host.append(ip)
    else:
        _save_host(ip)


def try_connect(adb):
    """One attempt. (serial, None) when connected; otherwise (None, why): "address" = the phone's
    Wi-Fi IP isn't known yet (VM), "off" = no wireless debugging found, "pair" = it's on but adb
    here isn't paired with it yet."""
    host = phone_host()
    serial = connected(adb, host)
    if serial:
        if host is None:
            _remember(serial.rsplit(":", 1)[0])     # connected by hand: remember where the phone is
        return serial, None
    if host is None:
        host = _phone_at_gateway()
        if host is None:
            return None, "address"
    hint = _port_hint.pop(host, None)
    candidates = [hint] if hint and _is_adbd(host, hint) else _open_ports(host)
    ports = [(p, k) for p, k in ((p, _adbd_kind(host, p)) for p in candidates) if k]
    ports.sort(key=lambda pk: pk[1] != "tls")   # Wireless debugging first: it never asks "Allow?"
    unpaired = False
    for port, kind in ports:
        serial = f"{host}:{port}"
        out = _adb(adb, "connect", serial, timeout=20)
        # (also "already connected to"; a "tcp" port may first say it failed: it waits for "Allow")
        if ("connected to" in out or kind == "tcp") and _ready(adb, serial):
            return serial, None
        unpaired = unpaired or kind == "tls"   # Wireless debugging, but it won't let this adb in
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


def ask_address(explain=True):
    """VM: ask for the "IP address & Port" Wireless debugging shows (Enter alone: just look again)."""
    if explain:
        print(ADDRESS_HELP)
    ip, port = _parse_address(_ask("    IP address & Port (e.g. 192.168.1.23:41234; Enter = look again): "))
    if ip and ip.startswith("127."):
        print("[-] 127.x.x.x is this Linux itself, not the phone: it needs the phone's Wi-Fi address,"
              " like 192.168.1.23.")
        return
    if ip is None and port is not None and phone_host():
        ip = phone_host()                   # only the port typed: same phone as before
    if ip is None:
        return
    _save_host(ip)
    if port:
        _port_hint[ip] = port


def pair(adb, explain=True):
    """Ask for the address and code the phone shows and pair adb with it. True if it worked."""
    if explain:
        print(PAIR_HELP)
    ip, port = _parse_address(_ask("    IP address & Port (e.g. 192.168.1.23:37123): "))
    code = re.sub(r"\D", "", _ask("    Pairing code (6 digits): "))
    host = (ip or phone_host()) if VM else "127.0.0.1"
    if not port or not code or not host:
        print("[-] That needs the IP address & Port and the 6-digit code. Once more:")
        return False
    out = _adb(adb, "pair", f"{host}:{port}", code, timeout=30)
    if "Successfully paired" in out:
        if VM:
            _remember(host)
        print("[+] Paired: from now on the bot connects to this phone by itself.")
        return True
    print(f"[-] Pairing didn't work ({out or 'no answer'}). Tap the pairing option again for a new code.")
    return False


def wait_until_connected(adb="adb"):
    """Connect adb to this phone (pairing it the first time), waiting as long as it takes. Every
    later adb / uiautomator2 call goes to it (ANDROID_SERIAL). Returns the serial."""
    interactive = sys.stdin is not None and sys.stdin.isatty()
    shown = None
    while True:
        serial, why = try_connect(adb)
        if serial:
            os.environ["ANDROID_SERIAL"] = serial
            if shown:
                print("[+] Connected to the phone's wireless debugging.")
            return serial
        if interactive:
            try:
                if why == "pair":
                    pair(adb, explain=shown != "pair")
                    shown = "pair"
                    continue
                if VM:                      # "address" / "off": the IP or port may have changed
                    ask_address(explain=shown != "address")
                    shown = "address"
                    continue
            except EOFError:            # no keyboard after all: explain what to do by hand
                interactive = False
        if why != shown:
            print({"pair": PAIR_BY_HAND, "off": WIRELESS_HELP}.get(why) or
                  ADDRESS_HELP + "\n    Or connect by hand once: adb connect IP:PORT. Waiting... (Ctrl+C to stop)")
            shown = why
        time.sleep(3)


def wake_lock(on):
    """Termux's wake lock: Android keeps Termux (and so the bot) running at full speed while the
    game is the open app. (The Terminal app keeps its Linux running while the screen is on.)"""
    if not IN_TERMUX:
        return
    try:
        subprocess.run(["termux-wake-lock" if on else "termux-wake-unlock"], capture_output=True, timeout=10)
    except Exception:
        pass
