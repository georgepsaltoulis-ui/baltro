package com.arrowbot.helper;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Typeface;
import android.media.projection.MediaProjectionConfig;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.View;
import android.text.InputType;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.app.AlertDialog;
import android.content.pm.ResolveInfo;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;

import java.io.File;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;

/**
 * The app's screen: Start / Stop the bot, what it's doing (its log), and the one-time setup
 * (Accessibility; optionally pairing with Wireless debugging). At Start, with Wireless debugging
 * the taps and the screen go through scrcpy over adb; the live picture comes from the screen share
 * (exact; with adb the app allows it itself), or scrcpy's video if there's no share. The bot (or
 * the Termux version of it) opens this screen by itself with EXTRA_CAPTURE when it needs screen
 * capture.
 */
public class MainActivity extends Activity {
    static final String EXTRA_CAPTURE = "capture";
    private static final int REQ_CAPTURE = 1;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private TextView status, log;
    private Button allow, deny, start;
    private EditText code;
    private Button game;
    private boolean finishAfterCapture, startAfterCapture;

    private final Runnable refresh = new Runnable() {
        @Override
        public void run() {
            update();
            handler.postDelayed(this, 1000);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(pad, pad * 3, pad, pad);

        TextView title = new TextView(this);
        title.setText("ArrowBot");
        title.setTextSize(24);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        box.addView(title);

        TextView about = new TextView(this);
        about.setText("Plays Arrows, Amaze GO! and games like them on this phone: no computer needed. "
                + "Choose the game below (any app on the phone; it applies at the next start).\n\n"
                + "Once: tap \"Turn on in Accessibility\" and turn \"ArrowBot\" on. If Android says the "
                + "setting is restricted: Settings > Apps > ArrowBot > ⋮ (top right) > Allow restricted "
                + "settings, then try again.\n\n"
                + "Then: \"Start bot\". The bot opens the game and plays. With the switch below on, the "
                + "screen goes off (black) while it plays: press the power button to turn it back on. To stop "
                + "it: \"Stop bot\" here or in its notification. Opening this app while it plays pauses it.\n\n"
                + "With Wireless debugging on (see the end of this page) the bot works like scrcpy on a "
                + "computer, and shares the screen by itself for an exact picture. Without it (no Wi-Fi), "
                + "Android asks to allow screen capture at each start: choose \"A single app\" > the game "
                + "(with \"Entire screen\" the screen has to stay on).");
        about.setPadding(0, pad, 0, pad);
        box.addView(about);

        game = button("", v -> chooseGame());
        showGame();
        box.addView(game);

        Switch screenOff = new Switch(this);
        screenOff.setText("Screen off while the bot plays (the power button turns it back on). Off: the "
                + "screen stays on, to watch the game.");
        screenOff.setChecked(Prefs.screenOff(this));
        screenOff.setPadding(0, pad / 2, 0, pad / 2);
        screenOff.setOnCheckedChangeListener((b, on) -> {
            Prefs.setScreenOff(this, on);
            if (!on) HelperService.keepScreenOnNow();   // (playing now: on right away)
        });
        box.addView(screenOff);

        start = button("▶  Start bot", v -> startBot());
        start.setTextSize(20);
        box.addView(start);
        Button stop = button("■  Stop bot", v -> {
            BotService.stop(this);
            ScrcpyEngine.stopAll();
        });
        stop.setTextSize(20);
        box.addView(stop);

        status = new TextView(this);
        status.setTypeface(Typeface.MONOSPACE);
        status.setPadding(0, pad, 0, pad);
        box.addView(status);

        box.addView(button("Turn on in Accessibility",
                v -> startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))));
        allow = button("Allow the waiting program", v -> Approvals.decide(Approvals.pending, true));
        deny = button("Deny it", v -> Approvals.decide(Approvals.pending, false));
        box.addView(allow);
        box.addView(deny);

        TextView logTitle = new TextView(this);
        logTitle.setText("\nWhat the bot says:");
        logTitle.setTypeface(Typeface.DEFAULT_BOLD);
        box.addView(logTitle);
        box.addView(button("Copy the bot's log (to paste in a chat)", v -> {
            android.content.ClipboardManager cb = getSystemService(android.content.ClipboardManager.class);
            cb.setPrimaryClip(android.content.ClipData.newPlainText("ArrowBot log",
                    tail(BotService.logFile(this), 60_000)));
            android.widget.Toast.makeText(this, "Copied the bot's log", android.widget.Toast.LENGTH_SHORT).show();
        }));
        log = new TextView(this);
        log.setTypeface(Typeface.MONOSPACE);
        log.setTextSize(11);
        log.setTextIsSelectable(true);
        box.addView(log);

        TextView adbTitle = new TextView(this);
        adbTitle.setText("\nWireless debugging (optional, needs Wi-Fi)");
        adbTitle.setTypeface(Typeface.DEFAULT_BOLD);
        box.addView(adbTitle);
        TextView adbHelp = new TextView(this);
        adbHelp.setText("With it, the bot plays like scrcpy on a computer: taps through scrcpy, the screen "
                + "really off, and the picture from the screen share (exact; scrcpy's compressed video "
                + "only if the share isn't allowed). It's used by itself whenever it's on; without it, "
                + "screen capture and Android's gestures are used.\n"
                + "Any Wi-Fi works (no internet needed). Once: Settings > System > Developer options > turn on "
                + "Wireless debugging. Then tap the button below: Settings opens. Tap \"Wireless debugging\", "
                + "then \"Pair device with pairing code\", and keep that box open: ArrowBot reads the code "
                + "and pairs by itself (no switching apps - that changes the code). If it doesn't, pull down "
                + "the notifications and type the code into ArrowBot's.");
        box.addView(adbHelp);
        box.addView(button("Pair with Wireless debugging", v -> Pairing.start(this)));
        code = new EditText(this);
        code.setHint("or type the code here (split screen)");
        code.setInputType(InputType.TYPE_CLASS_NUMBER);
        box.addView(code);
        box.addView(button("Pair with this code", v -> {
            String typed = code.getText().toString();
            new Thread(() -> Pairing.pair(getApplicationContext(), typed), "pair").start();
        }));

        TextView more = new TextView(this);
        more.setText("\nMore");
        more.setTypeface(Typeface.DEFAULT_BOLD);
        box.addView(more);
        box.addView(button("Stop screen capture", v -> stopService(new Intent(this, CaptureService.class))));
        box.addView(button("Forget allowed programs (Termux)", v -> Approvals.forgetAll(this)));

        ScrollView scroll = new ScrollView(this);
        scroll.addView(box);
        setContentView(scroll);

        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 2);
        }
        handleIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        handleIntent(intent);
    }

    private void handleIntent(Intent intent) {
        if (intent != null && intent.getBooleanExtra(EXTRA_CAPTURE, false) && !CaptureService.running()) {
            finishAfterCapture = true;      // asked by the bot: back to where we were afterwards
            requestCapture();
        }
    }

    private Button button(String text, View.OnClickListener l) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setOnClickListener(l);
        return b;
    }

    private void showGame() {
        game.setText("Game: " + Prefs.gameLabel(this) + "  (tap to choose another)");
    }

    /** Every app on the phone that has an icon, Arrows and Amaze GO! first. */
    private void chooseGame() {
        Intent main = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
        java.util.List<ResolveInfo> apps = getPackageManager().queryIntentActivities(main, 0);
        java.util.List<String[]> list = new java.util.ArrayList<>();   // {label, package}
        for (ResolveInfo r : apps) {
            String pkg = r.activityInfo.packageName;
            if (pkg.equals(getPackageName()) || list.stream().anyMatch(e -> e[1].equals(pkg))) continue;
            list.add(new String[]{String.valueOf(r.loadLabel(getPackageManager())), pkg});
        }
        list.sort((a, b) -> {
            int ka = a[1].equals(Prefs.ARROWS) ? 0 : a[1].equals("com.oakever.arrows") ? 1 : 2;
            int kb = b[1].equals(Prefs.ARROWS) ? 0 : b[1].equals("com.oakever.arrows") ? 1 : 2;
            return ka != kb ? Integer.compare(ka, kb) : a[0].compareToIgnoreCase(b[0]);
        });
        CharSequence[] names = new CharSequence[list.size()];
        for (int i = 0; i < names.length; i++) names[i] = list.get(i)[0] + "\n" + list.get(i)[1];
        new AlertDialog.Builder(this)
                .setTitle("Which game should the bot play?")
                .setItems(names, (d, i) -> {
                    Prefs.setGame(this, list.get(i)[1], list.get(i)[0]);
                    showGame();
                })
                .show();
    }

    private void startBot() {
        if (HelperService.instance == null) {
            status.setText("Turn on ArrowBot in Accessibility first (the button below).");
            startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
            return;
        }
        if (CaptureService.running()) {
            launchBot();
            return;
        }
        // adb first (Wireless debugging: taps and the screen through scrcpy, as on a computer), then
        // the screen share for the pictures: they're exact, scrcpy's video is compressed (thin lines
        // and faint dots lose their colour in it). With adb the app allows itself the screen share,
        // so Android doesn't ask; if it asks anyway and it's declined, the bot uses adb's video.
        start.setEnabled(false);
        CaptureService.setBotState("looking for Wireless debugging");
        new Thread(() -> {
            ScrcpyEngine e = ScrcpyEngine.ensure(this, msg -> CaptureService.setBotState(msg));
            if (e != null) allowScreenShare();
            handler.post(() -> {
                start.setEnabled(true);
                startAfterCapture = true;           // the screen share first, then the bot
                requestCapture();
            });
        }, "start").start();
    }

    /** Through adb, like `adb shell appops set <app> PROJECT_MEDIA allow`: Android then starts the
     *  screen share without asking (it still shows that the screen is being shared). */
    private void allowScreenShare() {
        try {
            Adb.get(this).shell("appops set " + getPackageName() + " PROJECT_MEDIA allow");
        } catch (Exception ignored) {
        }
    }

    private void launchBot() {
        CaptureService.setBotState("starting");
        BotService.start(this);
    }

    private void requestCapture() {
        if (CaptureService.running()) {
            if (startAfterCapture) {
                startAfterCapture = false;
                launchBot();
            }
            return;
        }
        MediaProjectionManager mpm = getSystemService(MediaProjectionManager.class);
        // "A single app" (Arrows) lets the screen be black while the bot still sees the game
        Intent i = Build.VERSION.SDK_INT >= 34
                ? mpm.createScreenCaptureIntent(MediaProjectionConfig.createConfigForUserChoice())
                : mpm.createScreenCaptureIntent();
        startActivityForResult(i, REQ_CAPTURE);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQ_CAPTURE) return;
        boolean ok = resultCode == RESULT_OK && data != null;
        if (ok) CaptureService.start(this, resultCode, data);
        if (startAfterCapture) {
            startAfterCapture = false;
            if (ok) {
                waitForCaptureThenStart(50);
            } else if (ScrcpyEngine.current() != null) {
                launchBot();                        // no screen share: adb's video
            }
        }
        if (finishAfterCapture) {
            finishAfterCapture = false;
            finish();
        }
    }

    /** The capture service takes a moment to come up: the bot starts once it runs (it would ask again). */
    private void waitForCaptureThenStart(int triesLeft) {
        if (CaptureService.running() || (triesLeft <= 0 && ScrcpyEngine.current() != null)) {
            launchBot();
        } else if (triesLeft > 0) {
            handler.postDelayed(() -> waitForCaptureThenStart(triesLeft - 1), 100);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        handler.post(refresh);
    }

    @Override
    protected void onPause() {
        handler.removeCallbacks(refresh);
        super.onPause();
    }

    private void update() {
        boolean waiting = Approvals.pending != null;
        String mode = HelperService.mode();
        status.setText("Accessibility:  " + (HelperService.instance != null ? "ON" : "off  <- turn it on (below)")
                + "\nLive picture:   " + (mode.equals("adb") ? "scrcpy over adb (compressed video)"
                        : mode.equals("capture+adb") ? "screen share (exact), taps through adb"
                        : mode.equals("capture") ? "screen capture" : "off (on at Start)")
                + "\nWireless debug: " + Adb.status
                + (Pairing.result.isEmpty() ? "" : "\nPairing:        " + Pairing.result)
                + "\nscrcpy:         " + ScrcpyEngine.status
                + "\nBot:            " + CaptureService.botState
                + (waiting ? "\nA program (Termux?) asks to be allowed!" : ""));
        allow.setVisibility(waiting ? View.VISIBLE : View.GONE);
        deny.setVisibility(waiting ? View.VISIBLE : View.GONE);
        log.setText(tail(BotService.logFile(this), 6000));
    }

    /** The end of the bot's log (what it printed). */
    private static String tail(File f, int bytes) {
        try (RandomAccessFile r = new RandomAccessFile(f, "r")) {
            long start = Math.max(0, r.length() - bytes);
            byte[] b = new byte[(int) (r.length() - start)];
            r.seek(start);
            r.readFully(b);
            String s = new String(b, StandardCharsets.UTF_8);
            int nl = s.indexOf('\n');
            return start > 0 && nl >= 0 ? s.substring(nl + 1) : s;
        } catch (Exception e) {
            return "(nothing yet)";
        }
    }
}
