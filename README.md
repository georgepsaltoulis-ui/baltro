# ArrowBot

Plays the Android games "Arrows" (`com.arrow.out`) and "Amaze GO!" (`com.oakever.arrows`), the same
kind of puzzle: tap the arrows whose way out is clear. `ArrowBot.py` is the whole bot in one file; it
unpacks itself into `ArrowBot_files/` on the first run. It runs on a computer with the phone on USB,
or on the phone itself with no computer.

The phone's screen goes off while the bot plays, and the game keeps running. Press the power button
to turn the screen back on; it then stays on until the bot stops. When the bot stops, the screen
comes back on and the phone's normal screen timeout is put back. To keep the screen on the whole
time, turn off **Screen off while the bot plays** in the ArrowBot app, above *Start bot*. Turning
it off while the bot plays turns the screen on right away. On a computer, set `SCREEN_OFF = False`
in `src/bot.py`.

## On the phone: just the ArrowBot app (recommended)

One app with the bot inside. You don't need Termux, Wi-Fi, a hotspot or a computer. Mobile data is
only needed to download it.

1. **Install it.** In the phone's browser, open
   https://github.com/georgepsaltoulis-ui/baltro/raw/HEAD/ArrowBot.apk and install it (44 MB, for
   64-bit phones such as Pixels).
   - Allow your browser to install apps if Android asks.
   - If Play Protect warns, tap *Install anyway*. It's a sideloaded app that uses accessibility.
   - If you had the earlier *ArrowBot Helper*, this installs over it.
2. **Turn it on, once.** Open *ArrowBot*, tap *Turn on in Accessibility*, and turn on *ArrowBot*.
   If Android says the setting is restricted, go to Settings > Apps > ArrowBot, open the menu at the
   top right, tap *Allow restricted settings*, and try again.
3. **Choose the game** (tap *Game:* — every app on the phone is listed, Arrows and Amaze GO! first)
   and **tap "Start bot".** If Wireless debugging is on (see below), the bot uses it and nothing is
   asked: through adb the app allows itself to share the screen. Otherwise Android asks to allow
   screen capture, every time the bot starts: choose **A single app**, then the game, and tap
   *Start*.

The bot then opens the game and plays, and the screen goes off. The app's screen shows what the bot
is doing; press the power button to see it.

**Stopping it:** tap **Stop bot** in the app or in its notification. Opening the app while the bot
plays pauses it; going back to the game lets it carry on. The power button turns the screen on
first.

It's the same `bot.py` as on a computer, with Python 3.10, NumPy and OpenCV inside the app. The app
streams the screen at 60 frames a second and the bot always takes the newest frame. Taps go out at
the bot's usual pace, one every 50 ms, queued so they never cut each other off.

The app plays in one of two ways, and picks by itself:

- **With Wireless debugging (preferred):** the app runs scrcpy over its own adb connection to the
  phone, like `ArrowBot.py` does on a computer: taps go through scrcpy, and the screen's panel
  really switches off. The bot's picture comes from Android's screen share, which the app allows
  itself through adb: it's exact, while scrcpy's video is compressed (on zoomed-out boards the thin
  lines and faint dots lose their colour in it). If the share isn't allowed or stops (the phone
  locked), the bot uses scrcpy's video, decoded by the phone's hardware decoder; it reads Amaze's
  lines and dots mainly by their brightness, which the video keeps.
- **Without it:** taps and swipes go through Android's accessibility service, and the bot sees the
  screen through screen capture. "Screen off" is a black cover over the screen at the lowest
  brightness (on the Pixel's OLED screen, black pixels are off). The bot still sees the game under
  it because the capture shows only the Arrows app. If you choose *Entire screen* instead, the
  capture would show the black cover, so the screen stays on and the app tells you why.

### Playing in the background

With Wireless debugging on, turn on **Play in the background** in the app before *Start bot*. The
game then runs on a hidden screen of its own: scrcpy's virtual display, which Android keeps unlocked
and awake by itself. The bot watches and taps that screen over adb, so you can use the phone for
anything else meanwhile, or turn its screen off. *Stop bot* closes the game with the hidden screen.
To check on it, open the app: it shows a small live picture of the hidden screen (tap it for a
bigger one), next to what the bot says.

- It needs Wireless debugging (an app can't make such a screen without adb). With the switch on and
  Wireless debugging off, the bot doesn't start, and it never falls back to your own screen.
- The bot sees the hidden screen through scrcpy's compressed video (the screen share only shows the
  phone's own screen), so it relies on the brightness-based vision described above.
- The hidden screen has no camera cutout or status bar. If the game lays out around them, its header
  sits higher there: the bot finds the header wherever it is and shifts the spots it reads (lives,
  "Hard") to match. In Amaze it also learns where the board's edge stops when the camera can't
  scroll further.
- Don't open the same game on your own screen while it plays in the background: Android would move
  it there, and the bot would move it back.

### Wireless debugging (optional)

It needs the phone to be on a Wi-Fi network, any network, with or without internet. Android only
switches Wireless debugging on while on Wi-Fi. Without Wi-Fi, the app uses the other way by itself.

Once:

1. Settings > System > Developer options > turn on **Wireless debugging**. (Developer options:
   Settings > About phone > tap *Build number* 7 times.)
2. In ArrowBot, tap **Pair with Wireless debugging**. Settings opens.
3. Tap **Wireless debugging**, then **Pair device with pairing code**, and leave that box open.
   ArrowBot reads the code and port from it and pairs by itself, within a second or two. This
   needs ArrowBot turned on in Accessibility. Don't switch apps: the box closes and its code
   changes.
4. If it doesn't pair by itself, pull down the notifications and type the code into ArrowBot's
   notification, still with the box open. In split screen, you can type it into the app instead.

After that, *Start bot* uses it by itself whenever Wireless debugging is on. The app's screen shows
which way it's playing (*Live picture: screen share (exact), taps through adb*, *scrcpy over adb*
or *screen capture*).

If the phone was set up with `adb tcpip 5555` from a computer (see *No Wi-Fi?* below), the app uses
that too, with no Wi-Fi. The phone asks "Allow USB debugging?" once.

The bot runs in a separate process inside the app, the way `ArrowBot.py` runs it as a child process
on a computer. Stopping it ends that process.

**Termux instead:** the app also lets the bot run from Termux. Run `pkg install python`, then
`python ArrowBot.py`. It finds the app by itself, and the phone asks once to allow it.

The app only listens to programs on the phone itself (127.0.0.1): its own bot, or one you allowed.
Its adb key is its own, made on the phone, and is only used to connect to this phone.

## Stopping it on a computer, or in a terminal

- **Stop button:** while the bot plays, the phone shows an *Arrow bot is playing* notification. Tap
  it (or swipe it away) to stop the bot, even with the game open. Press the power button first to
  turn the screen on.
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

## Amaze GO!

Same solver as for Arrows. Only the look differs (dark lines on a light board), along with the
buttons and the package. The bot:

- zooms out at the start of each level, a pinch at a time, until the whole board is on screen
  (Amaze lets you zoom out much further than needed; past that, the arrows get too small to read);
- taps *Play* / *Hard*, *Next Level* (orange or purple), *Continue* on the daily streak, and
  **Restart** when out of lives (never the "Continue" that gives more lives);
- counts the blue drops as lives, and doesn't tap an arrow again that cost one.

In the app, choose *Amaze GO!* above *Start bot*. On a computer or in Termux:
`python ArrowBot.py --game amaze`.

Any other app can be chosen too (`--package <app id>` on a computer): the bot opens it and reads
it like Amaze GO! (dark lines on a light board), so it works for look-alikes of Amaze GO! with
the same buttons. Each button also has a purple version, in case a game or a level uses that.

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
files from `src/` when it's built. Its adb client is [Kadb](https://github.com/flyfishxu/Kadb)
(Maven Central). Building it needs the Android SDK (platform `android-37.0`) and Python 3.10 on
the build machine:

```sh
cd android
ANDROID_HOME=/path/to/android-sdk ./gradlew -PbuildPython=/path/to/python3.10 :app:assembleRelease
cp app/build/outputs/apk/release/app-release.apk ../ArrowBot.apk
```

It's signed with `android/app/helper.keystore`, so updates install over the old app.
