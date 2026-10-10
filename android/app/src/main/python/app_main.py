"""
Inside the ArrowBot app (Chaquopy): BotService calls run() in the app's ":bot" process. The bot is
the same bot.py as everywhere else, talking to the app's accessibility / screen-capture part over
127.0.0.1 (bridge.py) - like the Termux version, with a built-in token instead of "Allow?".
Everything it prints goes to Temp/app.log, which the app's screen shows.
"""
import os
import sys


class _Log:
    """print() -> the log file the app shows (and logcat, through Chaquopy's own stdout)."""

    def __init__(self, path, stream):
        self.file = open(path, "w", encoding="utf-8", buffering=1)
        self.stream = stream

    def write(self, text):
        self.file.write(text)
        try:
            self.stream.write(text)
        except Exception:
            pass
        return len(text)

    def flush(self):
        self.file.flush()

    def isatty(self):
        return False


def run(bot_dir, token, package, game="arrows", game_package=""):
    os.environ.update(ARROWBOT_ON_PHONE="1", ARROWBOT_BRIDGE="1", ARROWBOT_HELPER_TOKEN=token,
                      ARROWBOT_APP_PACKAGE=package, ARROWBOT_GAME=game, ARROWBOT_GAME_PACKAGE=game_package)
    os.makedirs(os.path.join(bot_dir, "Temp"), exist_ok=True)
    sys.stdout = sys.stderr = _Log(os.path.join(bot_dir, "Temp", "app.log"), sys.stdout)
    sys.path.insert(0, bot_dir)
    import bot
    bot.run_embedded()
