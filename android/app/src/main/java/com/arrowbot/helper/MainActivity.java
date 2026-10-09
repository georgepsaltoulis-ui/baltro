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
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.File;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;

/**
 * The app's screen: Start / Stop the bot, what it's doing (its log), and the one-time setup
 * (Accessibility). Screen capture is asked for when the bot starts. The bot (or the Termux
 * version of it) opens this screen by itself with EXTRA_CAPTURE when it needs screen capture.
 */
public class MainActivity extends Activity {
    static final String EXTRA_CAPTURE = "capture";
    private static final int REQ_CAPTURE = 1;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private TextView status, log;
    private Button allow, deny;
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
        about.setText("Plays Arrows on this phone: no computer, no Wi-Fi.\n\n"
                + "Once: tap \"Turn on in Accessibility\" and turn \"ArrowBot\" on. If Android says the "
                + "setting is restricted: Settings > Apps > ArrowBot > ⋮ (top right) > Allow restricted "
                + "settings, then try again.\n\n"
                + "Then: \"Start bot\", and allow screen capture (\"Start now\"; pick \"Entire screen\" if "
                + "asked). The bot opens the game and plays; the screen stays on. To stop it: \"Stop bot\" "
                + "here or in its notification. Opening this app while it plays pauses it.");
        about.setPadding(0, pad, 0, pad);
        box.addView(about);

        Button start = button("▶  Start bot", v -> startBot());
        start.setTextSize(20);
        box.addView(start);
        Button stop = button("■  Stop bot", v -> BotService.stop(this));
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
        log = new TextView(this);
        log.setTypeface(Typeface.MONOSPACE);
        log.setTextSize(11);
        log.setTextIsSelectable(true);
        box.addView(log);

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

    private void startBot() {
        if (HelperService.instance == null) {
            status.setText("Turn on ArrowBot in Accessibility first (the button below).");
            startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
            return;
        }
        if (!CaptureService.running()) {
            startAfterCapture = true;       // screen capture first, then the bot
            requestCapture();
            return;
        }
        launchBot();
    }

    private void launchBot() {
        CaptureService.setBotState("starting");
        BotService.start(this);
    }

    private void requestCapture() {
        if (CaptureService.running()) return;
        MediaProjectionManager mpm = getSystemService(MediaProjectionManager.class);
        Intent i = Build.VERSION.SDK_INT >= 34
                ? mpm.createScreenCaptureIntent(MediaProjectionConfig.createConfigForDefaultDisplay())
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
            if (ok) waitForCaptureThenStart(50);
        }
        if (finishAfterCapture) {
            finishAfterCapture = false;
            finish();
        }
    }

    /** The capture service takes a moment to come up: the bot starts once it runs (it would ask again). */
    private void waitForCaptureThenStart(int triesLeft) {
        if (CaptureService.running()) {
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
        status.setText("Accessibility:  " + (HelperService.instance != null ? "ON" : "off  <- turn it on (below)")
                + "\nScreen capture: " + (CaptureService.running() ? "ON" : "off (asked for at Start)")
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
