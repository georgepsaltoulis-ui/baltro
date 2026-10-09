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

## On the phone: no Wi-Fi, no computer (recommended)

This runs in **Termux** together with the small **ArrowBot Helper** app. The app does the tapping
and sees the screen through Android's accessibility and screen-capture features, so the bot needs
no `adb`, no Wi-Fi, no hotspot and no computer. Mobile data is only used for downloading.

1. **Install the helper app.** In the phone's browser, open
   https://github.com/georgepsaltoulis-ui/baltro/raw/HEAD/ArrowBotHelper.apk and install it. Allow
   your browser to install apps if Android asks, and tap *Install anyway* if Play Protect warns:
   it's a sideloaded app that uses accessibility.
2. **Turn it on.** Open *ArrowBot Helper* and tap *1. Open Accessibility settings*, then turn on
   *ArrowBot Helper*. If Android says the setting is restricted, go to Settings > Apps >
   ArrowBot Helper, open the menu at the top right, tap *Allow restricted settings*, and try again.
3. **Start the bot in Termux:**

   ```sh
   pkg install -y python
   python -c "import urllib.request as u; u.urlretrieve('https://raw.githubusercontent.com/georgepsaltoulis-ui/baltro/HEAD/ArrowBot.py', 'ArrowBot.py')"
   python ArrowBot.py
   ```

   The first start installs what it needs: OpenCV (from Termux's `x11-repo`) and NumPy.
4. **Answer the phone's questions.**
   - The first time, tap *Allow* in the "Allow the Arrow bot to control this phone?"
     notification.
   - Each time the bot starts, tap *Start now* to allow screen capture. Pick *Entire screen* if it
     asks.

The bot then opens the game and plays. The screen stays on while it plays.

To stop it, tap **Stop bot** in the *Arrow bot* notification. You can also switch to Termux (the
bot pauses) and press ESC, `q` or Ctrl+C.

The helper app only listens to programs on the phone itself (127.0.0.1), and only after you
tapped *Allow*. Its source is in `helper/`. Rebuild it with `helper/build_apk.sh`.

## On the phone with Wi-Fi: Android's Terminal app

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

The bot reaches the phone through its own **Wireless debugging** (Developer options). Wireless
debugging only switches on while the phone is on **Wi-Fi**, not mobile data; it doesn't need
internet.

1. The bot first tries to find the phone by itself. If it can't, it asks for the *IP address & Port*:
   tap the words *Wireless debugging* (not the switch) and it's at the top of that screen. Don't use
   `127.0.0.1`: in the Terminal app that's its own Linux, not the phone.
2. It asks you to pair once: tap *Pair device with pairing code* and type the address and code
   shown there. Split screen (Settings next to the Terminal) makes this easy.
3. After that it connects by itself and opens the game.

Tips:

- **The bot stops when you switch apps:** the Terminal app may have recreated its window. Run the
  bot inside `tmux` (`sudo apt install tmux`, then `tmux`, then start the bot).
- **The phone's Debian is version 12:** its `adb` is too old to pair. The bot tells you how to get a
  newer one.
- **`/mnt/shared` is empty:** give the Terminal app access to your files in Android's settings.

Termux works as well, and there it never needs an address (it uses `127.0.0.1`). Install Termux,
then run `pkg install python` and `python ArrowBot.py`. The bot installs the rest itself, including
turning on Termux's `x11-repo`, which is where `opencv-python` lives. Without
PyAV it looks at the screen through screenshots, which is slower than live video.

### No Wi-Fi?

Wireless debugging only switches on while the phone is connected to a Wi-Fi network. It doesn't need
internet, so there are two ways round it:

- **Any Wi-Fi network:** for example another phone's hotspot. Some phones also allow it with their
  own hotspot on.
- **A computer, once per phone restart:** plug the phone in by USB and run
  `python ArrowBot.py --no-wifi-setup` on the computer. Then unplug it and start the bot in Termux
  on the phone. It connects without Wi-Fi until the phone restarts. The phone asks
  "Allow USB debugging?" once; tap Allow.

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
