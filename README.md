# ArrowBot

Plays the Android game "Arrows" (`com.arrow.out`). `ArrowBot.py` is the whole bot in one file; it
unpacks itself into `ArrowBot_files/` on the first run. It runs on a computer with the phone on USB,
or on the phone itself with no computer.

The phone's screen stays on while the bot plays. When the bot stops, the phone's normal screen
timeout is put back.

## On the phone (no computer)

Needs Android 11 or newer and [Termux](https://termux.dev). In Termux:

```sh
pkg install python android-tools python-numpy opencv-python python-pillow python-lxml
pip install uiautomator2
termux-setup-storage                          # once: lets Termux see your Downloads
cp ~/storage/downloads/ArrowBot.py ~ && python ArrowBot.py
```

Optional: live video is faster than screenshots. To get it, run
`pkg install ffmpeg build-essential && pip install av`.

The bot reaches the phone through its own **Wireless debugging** (Developer options; it needs
Wi-Fi but not internet):

1. The first time, the bot explains how to turn Wireless debugging on.
2. It asks for the pairing port and code. Put Settings and Termux side by side (split screen) and
   open *Wireless debugging > Pair device with pairing code*.
3. After that it finds the phone by itself every time and opens the game.

To pause the bot, switch to Termux. Press Ctrl+C there to stop it. Switch back to the game to let it
carry on.

Tips if Android stops Termux in the background:

- Set Termux's battery use to *Unrestricted*.
- On Android 14 and newer, turn on *Disable child process restrictions* in Developer options.
- On Android 12L and 13, run `adb shell settings put global settings_enable_monitor_phantom_procs false`
  once in Termux.

## On a computer (Windows)

```sh
pip install opencv-python numpy uiautomator2 av
python ArrowBot.py            # --show: also show the phone screen in a window
```

## Changing the bot

The source is in `src/`:

- `launcher.py`: the top of `ArrowBot.py`.
- `bot.py`: the bot.
- `onphone.py`: the on-phone connection.
- Templates and scrcpy.

After editing, run `python build.py` to pack them into `ArrowBot.py` again.
