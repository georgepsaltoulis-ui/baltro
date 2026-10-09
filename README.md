# ArrowBot

Plays the Android game "Arrows" (`com.arrow.out`). `ArrowBot.py` is the whole bot in one file; it
unpacks itself into `ArrowBot_files/` on the first run. It runs on a computer with the phone on USB,
or on the phone itself with no computer.

The phone's screen stays on while the bot plays. When the bot stops, the phone's normal screen
timeout is put back.

## On the phone: just the ArrowBot app (recommended)

One app with the bot inside. You don't need Termux, Wi-Fi, a hotspot or a computer. Mobile data is
only needed to download it.

1. **Install it.** In the phone's browser, open
   https://github.com/georgepsaltoulis-ui/baltro/raw/HEAD/ArrowBot.apk and install it (36 MB, for
   64-bit phones such as Pixels).
   - Allow your browser to install apps if Android asks.
   - If Play Protect warns, tap *Install anyway*. It's a sideloaded app that uses accessibility.
   - If you had the earlier *ArrowBot Helper*, this installs over it.
2. **Turn it on, once.** Open *ArrowBot*, tap *Turn on in Accessibility*, and turn on *ArrowBot*.
   If Android says the setting is restricted, go to Settings > Apps > ArrowBot, open the menu at the
   top right, tap *Allow restricted settings*, and try again.
3. **Tap "Start bot".** Then tap *Start now* to allow screen capture, choosing *Entire screen* if
   it asks. Android asks this every time the bot starts.

The bot then opens the game and plays, and the screen stays on. The app's screen shows what the bot
is doing.

**Stopping it:** tap **Stop bot** in the app or in its notification. Opening the app while the bot
plays pauses it; going back to the game lets it carry on.

It plays the same way as on a computer. It's the same `bot.py`, with Python 3.10, NumPy and OpenCV
inside the app:

- The app streams the screen at 60 frames a second, like scrcpy did, and the bot always takes the
  newest frame.
- Taps go out at the bot's usual pace, one every 50 ms, queued so they never cut each other off.

The bot runs in a separate process inside the app, the way `ArrowBot.py` runs it as a child process
on a computer. Stopping it ends that process.

**Termux instead:** the app also lets the bot run from Termux. Run `pkg install python`, then
`python ArrowBot.py`. It finds the app by itself, and the phone asks once to allow it.

The app only listens to programs on the phone itself (127.0.0.1): its own bot, or one you allowed.

## Stopping it on a computer, or in a terminal

- **Stop button:** while the bot plays, the phone shows an *Arrow bot is playing* notification. Tap
  it (or swipe it away) to stop the bot, even with the game open.
- **In the terminal:** the bot pauses while the terminal is open. Press **ESC** (or `q`) to stop it.
- **Ctrl+C** works too.

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

The bot is in `src/`:

- `launcher.py`: the top of `ArrowBot.py`.
- `bot.py`: the bot.
- `onphone.py`: the on-phone connection.
- `bridge.py`: talks to the app.
- Templates and scrcpy.

After editing, run `python build.py` to pack them into `ArrowBot.py` again.

The app is in `android/`: the Java part plus `app/src/main/python/app_main.py`. It takes the bot's
files from `src/` when it's built. It needs the Android SDK (platform 35) and Python 3.10 on the
build machine:

```sh
cd android
ANDROID_HOME=/path/to/android-sdk ./gradlew -PbuildPython=/path/to/python3.10 :app:assembleRelease
cp app/build/outputs/apk/release/app-release.apk ../ArrowBot.apk
```

It's signed with `android/app/helper.keystore`, so updates install over the old app.
