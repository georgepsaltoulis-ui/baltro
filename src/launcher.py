#!/usr/bin/env python3
"""
Arrows bot - plays the Android game "Arrows" (com.arrow.out) on a phone connected by USB, or on
the phone itself with no computer at all (see ON THE PHONE below).

    python ArrowBot.py             start (asks whether to show the phone screen on this PC)
    python ArrowBot.py --show      start and show the phone screen in a window (view only)
    python ArrowBot.py --no-show   start without the window
    --allow-helper                 allow the uiautomator2 helper on the phone without asking
    --on-phone                     run on the phone itself (found by itself in Android's Terminal app)
    --no-wifi-setup                on the computer, phone on USB: lets the bot on the phone connect
                                   without Wi-Fi until the phone restarts (adb tcpip 5555)

The first time, it asks before putting uiautomator2's small helper on the phone (needed to control
it). If adb isn't set up, it shows where to get it; if the phone isn't found (USB debugging off,
computer not allowed yet, ...), it says what to do and waits until the phone is there.
The phone's screen stays on while the bot plays. To stop it, tap the "Arrow bot is playing"
notification on the phone (or Esc / q / Ctrl+C here); the phone's normal screen timeout is then restored.

Everything the bot needs is inside this file: on the first run it's unpacked into ArrowBot_files/
next to this file (again only when this file changes). Needs Python 3 with:
    pip install opencv-python numpy uiautomator2 av
(uiautomator2 is installed automatically if it's missing). adb comes bundled.

ON THE PHONE, NO WI-FI, NO COMPUTER: Termux + the ArrowBot Helper app (ArrowBotHelper.apk, from
https://github.com/georgepsaltoulis-ui/baltro/raw/HEAD/ArrowBotHelper.apk). Install it, open it and
turn it on in Accessibility; then in Termux: pkg install python, python ArrowBot.py. The phone asks
once to allow the bot and to allow screen capture. "Stop bot" in its notification stops the bot.

ON THE PHONE (no computer) in Android's Terminal app: Settings > System > Developer options >
"Linux development environment" on, open the Terminal app, put ArrowBot.py in Downloads, then:
    python3 /mnt/shared/ArrowBot.py
The first start installs what it needs: adb and pip (apt), then opencv-python-headless, numpy, av
and uiautomator2 (pip, about 100 MB), and unpacks itself into ~/ArrowBot_files.
The bot works through the phone's own "Wireless debugging" (Developer options, Android 11+; needs
Wi-Fi, not internet). The first time it shows how to turn that on and asks for the address and
pairing code it shows; after that it connects by itself and opens the game. To stop it, tap the
"Arrow bot is playing" notification. Opening the Terminal app pauses it (ESC there stops it).
Termux works too (no address to type there): pkg install python, then python ArrowBot.py - it
installs the rest itself (android-tools, opencv-python, ... with pkg).
"""
import base64, io, os, runpy, shutil, subprocess, sys, zipfile

BUILD = "83eadb560a052dd9"
HERE = os.path.dirname(os.path.abspath(__file__))
# Started straight from the phone's Download folder (Android's Terminal app sees it as /mnt/shared):
# unpack into the home folder instead (that folder is Android storage: slow, picky about permissions)
DATA = os.path.join(os.path.expanduser("~") if HERE.startswith("/mnt/shared") else HERE, "ArrowBot_files")
WINDOWS_ONLY = (".exe", ".dll")   # scrcpy's PC window and adb for Windows: not unpacked on the phone

ADB_HELP = """[-] adb (Android Debug Bridge) wasn't found or doesn't work. The bot needs it to talk to the phone.
    1. Download Google's platform-tools, unzip them, add that folder to your PATH:
         https://developer.android.com/tools/releases/platform-tools
       (what adb is and how it works: https://developer.android.com/tools/adb)
    2. On the phone, turn on Developer options and USB debugging:
         https://developer.android.com/studio/debug/dev-options
    3. Plug the phone in by USB, allow the computer when the phone asks, and run this again.
    (Running this on the phone itself, in Android's Terminal app? Start it with:  python3 ArrowBot.py --on-phone)"""

PHONE_HELP = {
    "none": """[!] No phone found. adb works, so the usual reason is that USB debugging is off on the phone:
    1. Settings > About phone > tap "Build number" 7 times (this turns on Developer options).
    2. Settings > System > Developer options > turn on "USB debugging".
       (menu names vary by phone; step by step: https://developer.android.com/studio/debug/dev-options)
    3. Plug the phone in with a USB cable that carries data (some only charge). If the phone asks
       what the USB connection is for, choose "File transfer".
    4. Windows: still not found? The phone may need its USB driver:
       https://developer.android.com/studio/run/oem-usb
    Waiting for the phone... (Esc to stop)""",
    "unauthorized": """[!] The phone is connected but hasn't allowed this computer yet. Unlock the phone: it asks
    "Allow USB debugging?" - tick "Always allow from this computer" and tap Allow.
    No question on the phone? Developer options > "Revoke USB debugging authorizations", then unplug
    the cable and plug it back in.
    Waiting... (Esc to stop)""",
    "offline": """[!] The phone shows up as "offline": unplug the cable and plug it back in (or restart the phone).
    Waiting... (Esc to stop)""",
    "no permissions": """[!] adb isn't allowed to use the phone's USB connection (Linux: udev rules):
       https://developer.android.com/studio/run/device#setting-up
    Waiting... (Esc to stop)""",
    "several": """[!] More than one phone / emulator is connected: unplug the others so only the game phone is left.
    Waiting... (Esc to stop)""",
}

HELPER_ASK = """[?] The bot controls the phone with uiautomator2, which needs a small helper ON THE PHONE:
      - a file, /data/local/tmp/u2.jar, that runs while the bot plays (uiautomator2 3.x), or
      - an app called "ATX" (com.github.uiautomator) with older uiautomator2 versions.
    It's put there the first time the bot connects. You can remove it any time:
      adb shell rm /data/local/tmp/u2.jar        (or uninstall the ATX app on the phone)
    Allow the bot to put it on your phone? (y/N): """


def _payload():
    with open(os.path.abspath(__file__), "rb") as f:
        text = f.read()
    a = text.index(b"\n#PAYLOAD-BEGIN") + len(b"\n#PAYLOAD-BEGIN")
    b = text.index(b"\n#PAYLOAD-END")
    lines = text[a:b].split(b"\n")
    return base64.b64decode(b"".join(l.strip()[1:] for l in lines if l.strip()))


def unpack():
    stamp = os.path.join(DATA, ".build")
    try:
        with open(stamp) as f:
            if f.read().strip() == BUILD:
                return
    except OSError:
        pass
    print("[*] First start: unpacking the bot's files ...")
    os.makedirs(DATA, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(_payload())) as z:
        z.extract("onphone.py", DATA)          # first: it knows whether this is the phone itself
        import onphone
        skip = WINDOWS_ONLY if onphone.ON_PHONE else ()
        z.extractall(DATA, [n for n in z.namelist() if not n.lower().endswith(skip)])
    with open(stamp, "w") as f:
        f.write(BUILD)


def _keys_ok():
    return os.name == "nt" and sys.stdin is not None and sys.stdin.isatty()


def _esc_pressed():
    if not _keys_ok():
        return False
    import msvcrt
    while msvcrt.kbhit():
        if msvcrt.getwch() == "\x1b":
            return True
    return False


def _ask_yes_no(prompt):
    """One key: y = yes, n / Enter = no, Esc = quit."""
    if not _keys_ok():
        try:
            return input(prompt).strip().lower().startswith("y")
        except EOFError:
            return False
    import msvcrt
    print(prompt, end="", flush=True)
    while True:
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            msvcrt.getwch()
        elif ch == "\x1b":
            print("\n[*] Stopped.")
            sys.exit(0)
        elif ch in "yY":
            print("y")
            return True
        elif ch in "nN\r\n":
            print("n")
            return False


def _importable(m):
    try:
        __import__(m)
        return True
    except ImportError:
        return False


PIP_NAMES = {"cv2": "opencv-python", "av": "av", "uiautomator2": "uiautomator2", "numpy": "numpy"}
PHONE_PKGS = {   # Termux: prebuilt packages (pip would have to compile them)
    "termux": {"numpy": "pkg install python-numpy",
               "cv2": "pkg install x11-repo && apt update && pkg install opencv-python",
               "uiautomator2": "pkg install python-lxml python-pillow && pip install uiautomator2",
               "av": "pkg install ffmpeg build-essential && pip install av"},
}
# Android's Terminal app (Debian): pip has ready-made ARM builds of all of them (~100 MB together);
# Debian's own python3-opencv drags in hundreds of MB more (GDAL, VTK, Qt) and takes ages to install.
# Debian's Python only takes pip packages per user, with --break-system-packages ("externally managed").
DEBIAN_PIP = ["--user", "--break-system-packages", "--no-warn-script-location"]
DEBIAN_NAMES = {"cv2": "opencv-python-headless"}


TERMUX_PKG = {"numpy": ["python-numpy"], "cv2": ["opencv-python"], "uiautomator2": ["python-lxml", "python-pillow"]}


def _need(mods, phone=None):
    """phone: None on a computer, else "termux" / "debian" (what gets installed, which hints fit)."""
    if phone == "termux":
        # Termux has these prebuilt (pip would have to compile them); uiautomator2 itself comes from pip
        want = [p for m in mods if not _importable(m) for p in TERMUX_PKG.get(m, [])]
        if want:
            if "opencv-python" in want:
                # Termux keeps OpenCV in its "x11-repo" package list, which isn't on by default
                print("[*] Turning on Termux's x11-repo (where OpenCV is) ...")
                subprocess.run(["pkg", "install", "-y", "x11-repo"])
                subprocess.run(["apt", "update"])
            print(f"[*] Installing {' '.join(want)} (pkg install) ...")
            subprocess.run(["pkg", "install", "-y", *want])
            import importlib
            importlib.invalidate_caches()
    if phone == "debian":
        auto = [m for m in mods + ["av"] if not _importable(m)]      # everything (av for live video)
    else:
        auto = [m for m in ["uiautomator2"] if m in mods and not _importable(m)]
    if auto:
        # (uiautomator2's helper app on the phone is installed by uiautomator2 itself on the first connect)
        extra = DEBIAN_PIP if phone == "debian" else []
        names = [(DEBIAN_NAMES if phone == "debian" else {}).get(m, PIP_NAMES[m]) for m in auto]
        if not _importable("pip"):
            print("[-] pip isn't installed. Install it with:\n"
                  "      sudo apt install -y --no-install-recommends python3-pip\n    then run this again.")
            sys.exit(1)
        cmd = [sys.executable, "-m", "pip", "install", *extra, *names]
        print(f"[*] Installing what the bot needs ({' '.join(['python'] + cmd[1:])}) ...")
        subprocess.run(cmd)
        import importlib, site
        importlib.invalidate_caches()
        if extra and site.getusersitepackages() not in sys.path:
            sys.path.append(site.getusersitepackages())     # (a first --user install: not on the path yet)
    missing = [m for m in mods if not _importable(m)]
    if missing:
        print("[-] Missing Python packages: " + ", ".join(missing))
        if phone == "termux":
            print("    Install them with:")
            for m in missing:
                print("      " + PHONE_PKGS[phone][m])
        elif phone == "debian":
            print("    Install them with:  pip install " + " ".join(DEBIAN_PIP)
                  + " " + " ".join(DEBIAN_NAMES.get(m, PIP_NAMES[m]) for m in missing))
        else:
            print("    Install them with:  pip install " + " ".join(PIP_NAMES[m] for m in missing))
        sys.exit(1)
    if phone and not _importable("av"):
        print("[*] PyAV (av) isn't installed, so the bot looks at the screen through screenshots (slower\n"
              "    than live video). For live video: " + (PHONE_PKGS["termux"]["av"] if phone == "termux" else
                                                         "pip install " + " ".join(DEBIAN_PIP) + " av"))


def _debian_setup():
    """Android's Terminal app: put adb and pip on with apt if they're missing (a few MB with
    --no-install-recommends). First finishes an install that was interrupted (dpkg --configure -a),
    and waits for one that's still running instead of failing on its lock."""
    need = [pkg for pkg, ok in (("adb", shutil.which("adb")), ("python3-pip", _importable("pip"))) if not ok]
    if not need:
        return
    print(f"[*] Installing {' and '.join(need)} (sudo apt-get install --no-install-recommends ...),"
          " this takes a minute or two ...")
    apt = ["sudo", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-o", "DPkg::Lock::Timeout=600"]
    for cmd in (["sudo", "dpkg", "--configure", "-a"],
                apt + ["update"],
                apt + ["install", "-y", "--no-install-recommends", *need]):
        subprocess.run(cmd)
    import importlib
    importlib.invalidate_caches()
    if shutil.which("adb") is None or not _importable("pip"):
        print("[-] That didn't work (no Wi-Fi? an install still running?). Try it by hand:\n"
              "      sudo dpkg --configure -a\n"
              "      sudo apt-get update && sudo apt-get install -y --no-install-recommends adb python3-pip\n"
              "    then run this again.")
        sys.exit(1)


def _phone_route(onphone):
    """Termux: "helper" (the ArrowBot Helper app: no Wi-Fi, no computer) or "adb" (Wireless
    debugging, or a port opened with --no-wifi-setup) - whichever shows up first."""
    import time
    import bridge
    if bridge.available():
        return "helper"
    print(bridge.SETUP_HELP + "\n    (With Wi-Fi, Wireless debugging works instead: the bot finds that by itself too.)")
    adb, last = shutil.which("adb"), 0.0
    try:
        while True:
            if bridge.available():
                return "helper"
            if adb and time.time() - last > 15:
                last = time.time()
                serial, why = onphone.try_connect(adb)
                if serial or why == "pair":
                    return "adb"
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n[*] Stopped.")
        sys.exit(0)


def _termux_setup():
    """Termux: put adb on with pkg (android-tools) if it's missing."""
    if shutil.which("adb") is None:
        print("[*] Installing adb (pkg install android-tools) ...")
        subprocess.run(["pkg", "install", "-y", "android-tools"])


def _no_wifi_setup():
    """Computer, phone on USB: `adb tcpip 5555`. The phone's adb then also listens on port 5555 -
    with or without Wi-Fi - until it restarts, so the bot running on the phone itself (Termux) can
    connect to it at 127.0.0.1:5555. No Wireless debugging needed."""
    adb = _check_adb()
    phone = _wait_for_phone(adb)[0]
    out = subprocess.run([adb, "-s", phone, "tcpip", "5555"], capture_output=True, text=True, timeout=60)
    text = (out.stdout + out.stderr).strip()
    if out.returncode != 0 or "error" in text.lower():
        print(f"[-] That didn't work: {text}")
        sys.exit(1)
    print("""[+] Done: until it restarts, the phone lets the bot on it connect without Wi-Fi.
    1. Unplug the phone.
    2. On the phone, in Termux:  python ArrowBot.py
       The first time it asks "Allow USB debugging?" for Termux: tick "Always allow" and tap Allow.
    After the phone restarts, plug it in here and run this again.""")


def _check_adb(help_text=ADB_HELP):
    """adb must run (else: where to get it)."""
    exe = shutil.which("adb")
    try:
        ok = exe is not None and subprocess.run([exe, "version"], capture_output=True,
                                                timeout=20).returncode == 0
    except Exception:
        ok = False
    if not ok:
        print(help_text)
        sys.exit(1)
    return exe


def _phones(adb):
    """[(serial, state)] from `adb devices` (state: device / unauthorized / offline / ...)."""
    try:
        out = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    found = []
    for line in out.splitlines()[1:]:
        if "\t" in line:
            serial, state = line.split("\t", 1)
            found.append((serial.strip(), state.strip()))
    return found


def _wait_for_phone(adb):
    """Wait until exactly one phone is connected and allows this computer, with help that fits
    what adb sees (nothing at all usually means USB debugging is off). Returns its serial."""
    import time
    shown = None
    try:
        while True:
            phones = _phones(adb)
            ready = [s for s, st in phones if st == "device"]
            if len(ready) == 1:
                if shown:
                    print("[+] Phone connected.")
                return ready
            if len(ready) > 1:
                key = "several"
            elif phones:
                states = {st for _, st in phones}
                key = next((k for k in ("unauthorized", "offline", "no permissions")
                            if any(k in st for st in states)), "none")
            else:
                key = "none"
            if key != shown:
                print(PHONE_HELP[key])
                shown = key
            for _ in range(20):                     # check again in 2s; Esc quits meanwhile
                if _esc_pressed():
                    print("[*] Stopped.")
                    sys.exit(0)
                time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n[*] Stopped.")
        sys.exit(0)


def _helper_on_phone(adb, serial):
    """The uiautomator2 helper is already there (then nothing new gets put on the phone)."""
    def sh(*cmd):
        try:
            return subprocess.run([adb, "-s", serial, "shell", *cmd], capture_output=True, text=True,
                                  timeout=20).stdout
        except Exception:
            return ""
    return ("/data/local/tmp/u2.jar" in sh("ls", "/data/local/tmp/u2.jar")
            or "com.github.uiautomator" in sh("pm", "list", "packages", "com.github.uiautomator"))


def _helper_permission(adb, devices, allowed_by_flag):
    """Ask once before uiautomator2 puts its helper on the phone; remembered in ArrowBot_files/."""
    flag = os.path.join(DATA, ".helper_allowed")
    if os.path.exists(flag):
        return
    if allowed_by_flag or (len(devices) == 1 and _helper_on_phone(adb, devices[0])):
        open(flag, "w").close()
        return
    if not _ask_yes_no(HELPER_ASK):
        print("[-] Not allowed, so the bot won't start (it can't control the phone without it).\n"
              "    Nothing was put on your phone. To allow it later: python ArrowBot.py --allow-helper")
        sys.exit(1)
    open(flag, "w").close()


def main():
    args = sys.argv[1:]
    if any(a not in ("--show", "--no-show", "--allow-helper", "--on-phone", "--no-wifi-setup") for a in args):
        print(__doc__)
        return
    allow = "--allow-helper" in args
    no_wifi_setup = "--no-wifi-setup" in args
    if "--on-phone" in args:
        os.environ["ARROWBOT_ON_PHONE"] = "1"      # (seen by onphone.py here and in the bot)
    args = [a for a in args if a not in ("--allow-helper", "--on-phone", "--no-wifi-setup")]
    sys.path.insert(0, DATA)
    unpack()
    import onphone
    helper_app = False
    if onphone.ON_PHONE and onphone.IN_TERMUX:
        # no Wi-Fi, no computer: the ArrowBot Helper app instead of adb, if it's there (bridge.py)
        _need(["numpy", "cv2"], "termux")
        helper_app = _phone_route(onphone) == "helper"
    if helper_app:
        import bridge
        os.environ["ARROWBOT_BRIDGE"] = "1"         # (seen by the bot)
        try:
            bridge.Helper().connect()               # the first time, the phone asks "Allow?"
        except KeyboardInterrupt:
            print("\n[*] Stopped.")
            sys.exit(0)
        except Exception as e:
            print(f"[-] The helper app didn't let the bot in: {e}")
            sys.exit(1)
    elif onphone.ON_PHONE:
        # no computer: adb here connects to the phone's own wireless debugging (onphone.py)
        if onphone.IN_TERMUX:
            _termux_setup()
        else:
            _debian_setup()
        _need(["numpy", "cv2", "uiautomator2"], "termux" if onphone.IN_TERMUX else "debian")
        adb = _check_adb(onphone.ADB_HELP)
        old = onphone.adb_too_old(adb)
        if old:
            print(onphone.ADB_TOO_OLD.format(version=old))
            sys.exit(1)
        try:
            devices = [onphone.wait_until_connected(adb)]
        except KeyboardInterrupt:
            print("\n[*] Stopped.")
            sys.exit(0)
    else:
        scrcpy_dir = os.path.join(DATA, "Scrcpy")
        if shutil.which("adb") is None and os.path.isdir(scrcpy_dir):
            os.environ["PATH"] = scrcpy_dir + os.pathsep + os.environ.get("PATH", "")
        if no_wifi_setup:
            _no_wifi_setup()
            return
        _need(["numpy", "cv2", "uiautomator2", "av"])
        adb = _check_adb()
        devices = _wait_for_phone(adb)
    if not helper_app:
        _helper_permission(adb, devices, allow)
    bot = os.path.join(DATA, "bot.py")
    sys.argv = [bot] + args
    runpy.run_path(bot, run_name="__main__")


if __name__ == "__main__":
    main()

