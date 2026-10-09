#!/usr/bin/env python3
"""
Packs src/ into the single-file ArrowBot.py (what gets run, on a PC or on the phone):
src/launcher.py + every other file in src/ as a zip (LZMA, base64 in comment lines). The BUILD
stamp is the zip's hash, so the bot re-unpacks its files only when something in it changed.

    python build.py
"""
import base64, hashlib, io, os, zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
OUT = os.path.join(ROOT, "ArrowBot.py")
LINE = 2000
STAMP = (2026, 1, 1, 0, 0, 0)   # fixed: the same files always give the same zip (and BUILD)


def payload():
    files = []
    for top, dirs, names in os.walk(SRC):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for n in sorted(names):
            rel = os.path.relpath(os.path.join(top, n), SRC).replace(os.sep, "/")
            if rel != "launcher.py" and not n.endswith(".pyc"):
                files.append(rel)
    files.sort(key=lambda p: (p.count("/"), p))   # bot.py & co. first, then the folders
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_LZMA) as z:
        for rel in files:
            info = zipfile.ZipInfo(rel, STAMP)
            info.compress_type = zipfile.ZIP_LZMA
            info.external_attr = 0o100644 << 16
            with open(os.path.join(SRC, rel), "rb") as f:
                z.writestr(info, f.read())
    return buf.getvalue()


def main():
    data = payload()
    build = hashlib.sha256(data).hexdigest()[:16]
    with open(os.path.join(SRC, "launcher.py"), encoding="utf-8") as f:
        launcher = f.read()
    head, sep, tail = launcher.partition('BUILD = "')
    launcher = head + sep + build + tail[tail.index('"'):]
    text = base64.b64encode(data).decode("ascii")
    lines = ["#" + text[i:i + LINE] for i in range(0, len(text), LINE)]
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(launcher)
        f.write("# ---- compressed payload (zip, base64): the bot, its templates, scrcpy + adb ----\n")
        f.write("#PAYLOAD-BEGIN\n" + "\n".join(lines) + "\n#PAYLOAD-END\n")
    print(f"[+] {os.path.relpath(OUT, ROOT)}: build {build}, {len(data) / 2**20:.1f} MB payload")


if __name__ == "__main__":
    main()
