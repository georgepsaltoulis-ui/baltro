# ArrowBot

Plays the Android game "Arrows" (`com.arrow.out`). `ArrowBot.py` is the whole bot in one file; it
unpacks itself into `ArrowBot_files/` on the first run. It runs on a computer with the phone on USB,
or on the phone itself with no computer.

The phone's screen stays on while the bot plays. When the bot stops, the phone's normal screen
timeout is put back.

## Stopping it

- **Stop button:** while the bot plays, the phone shows an *Arrow bot is playing* notification. Pull
  down the notification shade and tap it (or swipe it away) to stop the bot, even with the game open.
- **In the terminal:** open the terminal the bot runs in. The bot pauses while it's open. Tap **ESC**
  in the key row above the keyboard (or press `q`) to stop it. Go back to the game to let it carry on.
- **Ctrl+C** works too.

## On the phone (no computer)

You need Android 11 or newer and Android's built-in **Terminal** app:

1. Settings > System > Developer options > turn on **Linux development environment**.
2. Open the Terminal app and let it install.
3. Put `ArrowBot.py` in your phone's **Download** folder.

Then, in the Terminal app, type just:

```sh
python3 /mnt/shared/ArrowBot.py
```

(`/mnt/shared` is your phone's Download folder. If your browser saved the file as
`ArrowBot (1).py`, delete the old copies in the Files app or use that name.)

The first start installs what the bot needs, so it takes a few minutes:

- `adb` and `pip`, with `apt`.
- `opencv-python-headless`, `numpy`, `av` and `uiautomator2`, with `pip` (about 100 MB).

It also finishes any install that was cut short. It unpacks itself into `~/ArrowBot_files`. Don't
install Debian's own `python3-opencv`: it pulls in hundreds of MB of extras and takes a very long
time.

The bot reaches the phone through its own **Wireless debugging** (Developer options; it needs
Wi-Fi but not internet):

1. The first time, the bot explains how to turn Wireless debugging on. It asks you to type the
   *IP address & Port* that Wireless debugging shows.
2. It then asks you to pair once: tap *Pair device with pairing code* and type the address and
   code shown there. Split screen (Settings next to the Terminal) makes this easy.
3. After that it finds the phone by itself and opens the game. If it can't (for example on another
   Wi-Fi network), it asks for the address again.

Tips:

- **The bot stops when you switch apps:** the Terminal app may have recreated its window. Run the
  bot inside `tmux` (`sudo apt install tmux`, then `tmux`, then start the bot).
- **The phone's Debian is version 12:** its `adb` is too old to pair. The bot tells you how to get a
  newer one.
- **`/mnt/shared` is empty:** give the Terminal app access to your files in Android's settings.

Termux works as well. In Termux, run:

```sh
pkg install python android-tools python-numpy opencv-python python-pillow python-lxml
pip install uiautomator2
```

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
