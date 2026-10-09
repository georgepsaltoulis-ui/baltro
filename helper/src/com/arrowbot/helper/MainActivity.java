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

/**
 * Setup screen: turn on the accessibility service, start screen capture, answer a waiting
 * "Allow?" and stop the bot. The bot opens it by itself (EXTRA_CAPTURE) when it needs screen
 * capture and it isn't running.
 */
public class MainActivity extends Activity {
    static final String EXTRA_CAPTURE = "capture";
    private static final int REQ_CAPTURE = 1;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private TextView status;
    private Button allow, deny;
    private boolean finishAfterCapture;

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
        title.setText("ArrowBot Helper");
        title.setTextSize(24);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        box.addView(title);

        TextView about = new TextView(this);
        about.setText("Lets the Arrow bot running in Termux on this phone play the game: no computer, "
                + "no Wi-Fi. Set it up once:\n\n"
                + "1. Turn on \"ArrowBot Helper\" in Accessibility. If Android says the setting is "
                + "restricted: Settings > Apps > ArrowBot Helper > ⋮ (top right) > Allow restricted "
                + "settings, then try again.\n"
                + "2. Start the bot in Termux: python ArrowBot.py\n"
                + "3. Allow it when asked (a notification), and allow screen capture (\"Start now\"; "
                + "pick \"Entire screen\" if asked).\n\n"
                + "To stop the bot: \"Stop bot\" in the notification.");
        about.setPadding(0, pad, 0, pad);
        box.addView(about);

        status = new TextView(this);
        status.setTypeface(Typeface.MONOSPACE);
        status.setPadding(0, 0, 0, pad);
        box.addView(status);

        box.addView(button("1. Open Accessibility settings",
                v -> startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))));
        box.addView(button("Start screen capture", v -> requestCapture()));
        allow = button("Allow the waiting program", v -> Approvals.decide(Approvals.pending, true));
        deny = button("Deny it", v -> Approvals.decide(Approvals.pending, false));
        box.addView(allow);
        box.addView(deny);
        box.addView(button("Stop bot", v -> HelperService.requestStop()));
        box.addView(button("Stop screen capture", v -> stopService(new Intent(this, CaptureService.class))));
        box.addView(button("Forget allowed programs", v -> Approvals.forgetAll(this)));

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
        if (requestCode == REQ_CAPTURE) {
            if (resultCode == RESULT_OK && data != null) CaptureService.start(this, resultCode, data);
            if (finishAfterCapture) {
                finishAfterCapture = false;
                finish();
            }
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
        status.setText("Accessibility service: " + (HelperService.instance != null ? "ON" : "off  <- turn it on (1.)")
                + "\nScreen capture:        " + (CaptureService.running() ? "ON" : "off")
                + "\nAllowed programs:      " + Approvals.count(this)
                + (waiting ? "\nA program asks to be allowed!" : "")
                + "\nBot:                   " + CaptureService.botState);
        allow.setVisibility(waiting ? View.VISIBLE : View.GONE);
        deny.setVisibility(waiting ? View.VISIBLE : View.GONE);
    }
}
