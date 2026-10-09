package com.arrowbot.helper;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.content.Intent;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

/**
 * The accessibility service: taps, swipes and pinches for the bot (Android's gesture API, no adb),
 * Back / Home, opening the game, which app is open, whether the status bar shows, and keeping the
 * screen on. It also runs the little server the bot talks to: 127.0.0.1 only (nothing outside the
 * phone can reach it), and every program has to be allowed once on the phone (Approvals).
 *
 * Protocol: one text line per request, one text line back ("OK ..." / "ERR ..."); FRAME answers
 * with a header line followed by the raw RGBA pixels. Commands: see serve().
 */
public class HelperService extends AccessibilityService {
    static final String TAG = "ArrowBotHelper";
    static final int PORT = 47123;
    static final String VERSION = "ArrowBotHelper 2";

    static volatile HelperService instance;
    /** When the user last tapped "Stop bot" (ms, wall clock): bot sessions started before that stop. */
    static volatile long stopRequestedAt = 0;

    private ServerSocket server;
    private Handler main;
    private HandlerThread gestureThread;
    private Handler gestureHandler;
    /** All gestures run one after another here (a new gesture would cancel one in progress). Taps
     *  are queued and answered at once, so the bot taps at its own pace (like over scrcpy); drags,
     *  pinches and BACK wait for their turn and their end. */
    private final ExecutorService gestures = Executors.newSingleThreadExecutor();
    private volatile String lastPackage = "";
    private View keepOnView;
    private int keepOnCount = 0;

    @Override
    protected void onServiceConnected() {
        instance = this;
        main = new Handler(Looper.getMainLooper());
        gestureThread = new HandlerThread("gestures");
        gestureThread.start();
        gestureHandler = new Handler(gestureThread.getLooper());
        startServer();
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event.getEventType() == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED && event.getPackageName() != null) {
            lastPackage = event.getPackageName().toString();
        }
    }

    @Override
    public void onInterrupt() {
    }

    @Override
    public void onDestroy() {
        instance = null;
        try {
            if (server != null) server.close();
        } catch (IOException ignored) {
        }
        if (gestureThread != null) gestureThread.quitSafely();
        gestures.shutdownNow();
        super.onDestroy();
    }

    static void requestStop() {
        stopRequestedAt = System.currentTimeMillis();
        CaptureService.setBotState("stop requested");
    }

    // ------------------------------------------------------------------ server

    private void startServer() {
        new Thread(() -> {
            try {
                server = new ServerSocket();
                server.setReuseAddress(true);
                server.bind(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), PORT));
                while (!server.isClosed()) {
                    Socket s = server.accept();
                    Thread t = new Thread(() -> serve(s), "client");
                    t.setDaemon(true);
                    t.start();
                }
            } catch (IOException e) {
                Log.w(TAG, "server stopped", e);
            }
        }, "server").start();
    }

    private static String readLine(InputStream in) throws IOException {
        StringBuilder sb = new StringBuilder();
        int c;
        while ((c = in.read()) != -1) {
            if (c == '\n') return sb.toString();
            if (c != '\r') sb.append((char) c);
            if (sb.length() > 4096) throw new IOException("line too long");
        }
        return sb.length() > 0 ? sb.toString() : null;
    }

    private static void send(OutputStream out, String line) throws IOException {
        out.write((line + "\n").getBytes(StandardCharsets.UTF_8));
        out.flush();
    }

    private void serve(Socket socket) {
        boolean keepOn = false;
        boolean allowed = false;
        long helloAt = 0;
        byte[] frame = null;
        try (Socket s = socket) {
            s.setTcpNoDelay(true);
            InputStream in = new BufferedInputStream(s.getInputStream());
            OutputStream out = new BufferedOutputStream(s.getOutputStream(), 1 << 16);
            String line;
            while ((line = readLine(in)) != null) {
                String[] a = line.trim().split("\\s+");
                String cmd = a[0];
                if (cmd.isEmpty()) continue;
                if (!allowed && !cmd.equals("HELLO") && !cmd.equals("PING")) {
                    send(out, "ERR say HELLO first");
                    continue;
                }
                try {
                    switch (cmd) {
                        case "PING":
                            send(out, "OK " + VERSION);
                            break;
                        case "HELLO": {     // HELLO <token>: asks on the phone the first time
                            if (a.length < 2) { send(out, "ERR no token"); break; }
                            if (!Approvals.isAllowed(this, a[1])) {
                                send(out, "WAIT");
                                if (!Approvals.request(this, a[1], 300_000)) {
                                    send(out, "DENIED");
                                    return;
                                }
                            }
                            allowed = true;
                            helloAt = System.currentTimeMillis();
                            send(out, "OK " + VERSION);
                            break;
                        }
                        case "INFO": {
                            int[] wh = screenSize();
                            send(out, "OK " + wh[0] + " " + wh[1] + " " + (CaptureService.running() ? 1 : 0));
                            break;
                        }
                        case "CAPTURE":     // make sure the screen is being captured (asks on the phone)
                            if (CaptureService.running()) {
                                send(out, "OK 1");
                            } else {
                                Intent i = new Intent(this, MainActivity.class)
                                        .putExtra(MainActivity.EXTRA_CAPTURE, true)
                                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                                startActivity(i);
                                send(out, "OK 0");
                            }
                            break;
                        case "FRAME": {     // FRAME <after_seq>: the newest frame newer than that
                            CaptureService cap = CaptureService.instance;
                            if (cap == null) { send(out, "NONE nocapture"); break; }
                            int[] wh = cap.size();
                            int need = wh[0] * wh[1] * 4;
                            if (frame == null || frame.length < need) frame = new byte[need];
                            long[] f = cap.copyFrame(Long.parseLong(a[1]), 100, frame);
                            if (f == null) { send(out, "NONE timeout"); break; }
                            send(out, "FRAME " + f[0] + " " + f[1] + " " + f[2] + " " + f[3]);
                            out.write(frame, 0, (int) (f[1] * f[2] * 4));
                            out.flush();
                            break;
                        }
                        case "STREAM": {    // STREAM <max_fps>: from now on, every new frame (like scrcpy's video)
                            int fps = a.length > 1 ? Math.max(1, Integer.parseInt(a[1])) : 60;
                            long gapNs = 1_000_000_000L / fps, lastNs = 0, last = 0;
                            s.setSendBufferSize(4 << 20);
                            while (true) {
                                CaptureService cap = CaptureService.instance;
                                if (cap == null) { send(out, "NONE nocapture"); return; }
                                long wait = lastNs + gapNs - System.nanoTime();
                                if (wait > 0) Thread.sleep(wait / 1_000_000, (int) (wait % 1_000_000));
                                int[] wh = cap.size();
                                int need = wh[0] * wh[1] * 4;
                                if (frame == null || frame.length < need) frame = new byte[need];
                                long[] f = cap.copyFrame(last, 100, frame);
                                if (f == null) continue;
                                lastNs = System.nanoTime();
                                last = f[0];
                                send(out, "FRAME " + f[0] + " " + f[1] + " " + f[2] + " " + f[3]);
                                out.write(frame, 0, (int) (f[1] * f[2] * 4));
                                out.flush();
                            }
                        }
                        case "TAP":         // TAP x y hold_ms
                            send(out, tap(f(a[1]), f(a[2]), l(a[3])) ? "OK" : "ERR cancelled");
                            break;
                        case "DRAG":        // DRAG x0 y0 x1 y1 move_ms hold_ms
                            send(out, drag(f(a[1]), f(a[2]), f(a[3]), f(a[4]), l(a[5]), l(a[6])) ? "OK" : "ERR cancelled");
                            break;
                        case "PINCH":       // PINCH ax0 ay0 bx0 by0 ax1 ay1 bx1 by1 ms
                            send(out, pinch(f(a[1]), f(a[2]), f(a[3]), f(a[4]), f(a[5]), f(a[6]), f(a[7]), f(a[8]),
                                    l(a[9])) ? "OK" : "ERR cancelled");
                            break;
                        case "BACK":
                            send(out, inTurn(() -> performGlobalAction(GLOBAL_ACTION_BACK)) ? "OK" : "ERR");
                            break;
                        case "HOME":
                            send(out, inTurn(() -> performGlobalAction(GLOBAL_ACTION_HOME)) ? "OK" : "ERR");
                            break;
                        case "LAUNCH": {    // LAUNCH <package>
                            Intent i = getPackageManager().getLaunchIntentForPackage(a[1]);
                            if (i == null) { send(out, "ERR not installed"); break; }
                            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
                            startActivity(i);
                            send(out, "OK");
                            break;
                        }
                        case "FG":          // the app in front
                            send(out, "OK " + foreground());
                            break;
                        case "STATUSBAR":
                            send(out, "OK " + (statusBarShowing() ? 1 : 0));
                            break;
                        case "AWAKE": {     // AWAKE 1|0: keep the screen on while this connection lasts
                            boolean on = a[1].equals("1");
                            if (on != keepOn) {
                                keepOn = on;
                                keepScreenOn(on);
                            }
                            send(out, "OK");
                            break;
                        }
                        case "POLL":        // did the user tap "Stop bot" since this bot started?
                            send(out, stopRequestedAt > helloAt ? "OK stop" : "OK run");
                            break;
                        case "STATE":       // STATE <words>: shown in the notification
                            CaptureService.setBotState(line.trim().substring(5).trim());
                            send(out, "OK");
                            break;
                        default:
                            send(out, "ERR unknown command " + cmd);
                    }
                } catch (RuntimeException e) {
                    send(out, "ERR " + e);
                } catch (InterruptedException e) {
                    return;
                }
            }
        } catch (IOException ignored) {
        } finally {
            if (keepOn) keepScreenOn(false);
        }
    }

    private static float f(String s) {
        return Float.parseFloat(s);
    }

    private static long l(String s) {
        return Math.max(1, (long) Float.parseFloat(s));
    }

    int[] screenSize() {
        WindowManager wm = getSystemService(WindowManager.class);
        if (android.os.Build.VERSION.SDK_INT >= 30) {
            Rect b = wm.getMaximumWindowMetrics().getBounds();
            return new int[]{b.width(), b.height()};
        }
        android.util.DisplayMetrics m = new android.util.DisplayMetrics();
        wm.getDefaultDisplay().getRealMetrics(m);
        return new int[]{m.widthPixels, m.heightPixels};
    }

    // ------------------------------------------------------------------ gestures

    private float[] clamp(float x, float y) {
        int[] wh = screenSize();
        return new float[]{Math.max(0, Math.min(wh[0] - 1, x)), Math.max(0, Math.min(wh[1] - 1, y))};
    }

    /** Run on the gesture worker, after everything queued before it, and wait for the result. */
    private boolean inTurn(java.util.concurrent.Callable<Boolean> job) {
        try {
            Future<Boolean> f = gestures.submit(job);
            return f.get(30, TimeUnit.SECONDS);
        } catch (Exception e) {
            return false;
        }
    }

    /** Dispatch one gesture and wait until it's done (on the gesture worker: a new gesture would
     *  cancel one in progress). */
    private boolean dispatch(GestureDescription g, long ms) {
        final boolean[] ok = {false};
        final CountDownLatch done = new CountDownLatch(1);
        GestureResultCallback cb = new GestureResultCallback() {
            @Override
            public void onCompleted(GestureDescription d) {
                ok[0] = true;
                done.countDown();
            }

            @Override
            public void onCancelled(GestureDescription d) {
                done.countDown();
            }
        };
        if (!dispatchGesture(g, cb, gestureHandler)) return false;
        try {
            done.await(ms + 3000, TimeUnit.MILLISECONDS);
        } catch (InterruptedException e) {
            return false;
        }
        return ok[0];
    }

    /** Queued: answered at once, done in order right after what's queued before it. */
    boolean tap(float x, float y, long holdMs) {
        float[] p = clamp(x, y);
        Path path = new Path();
        path.moveTo(p[0], p[1]);
        GestureDescription g = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(path, 0, holdMs)).build();
        gestures.submit(() -> dispatch(g, holdMs));
        return true;
    }

    /** One finger from (x0,y0) to (x1,y1) in moveMs, then held still for holdMs before lifting:
     *  no speed left when it lets go, so the game doesn't fling the board. */
    boolean drag(float x0, float y0, float x1, float y1, long moveMs, long holdMs) {
        float[] a = clamp(x0, y0), b = clamp(x1, y1);
        Path move = new Path();
        move.moveTo(a[0], a[1]);
        move.lineTo(b[0], b[1]);
        GestureDescription.StrokeDescription s1 = new GestureDescription.StrokeDescription(move, 0, moveMs, true);
        Path hold = new Path();
        hold.moveTo(b[0], b[1]);
        GestureDescription.StrokeDescription s2 = s1.continueStroke(hold, 0, holdMs, false);
        return inTurn(() -> dispatch(new GestureDescription.Builder().addStroke(s1).build(), moveMs)
                && dispatch(new GestureDescription.Builder().addStroke(s2).build(), holdMs));
    }

    boolean pinch(float ax0, float ay0, float bx0, float by0, float ax1, float ay1, float bx1, float by1, long ms) {
        float[] a0 = clamp(ax0, ay0), b0 = clamp(bx0, by0), a1 = clamp(ax1, ay1), b1 = clamp(bx1, by1);
        Path pa = new Path();
        pa.moveTo(a0[0], a0[1]);
        pa.lineTo(a1[0], a1[1]);
        Path pb = new Path();
        pb.moveTo(b0[0], b0[1]);
        pb.lineTo(b1[0], b1[1]);
        GestureDescription g = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(pa, 0, ms))
                .addStroke(new GestureDescription.StrokeDescription(pb, 0, ms)).build();
        return inTurn(() -> dispatch(g, ms));
    }

    // ------------------------------------------------------------------ what's on screen

    String foreground() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root != null && root.getPackageName() != null) return root.getPackageName().toString();
        return lastPackage;
    }

    boolean statusBarShowing() {
        int[] wh = screenSize();
        List<AccessibilityWindowInfo> windows = getWindows();
        Rect r = new Rect();
        for (AccessibilityWindowInfo w : windows) {
            if (w.getType() != AccessibilityWindowInfo.TYPE_SYSTEM) continue;
            w.getBoundsInScreen(r);
            if (r.top <= 0 && r.height() > 0 && r.height() < wh[1] / 8 && r.width() >= wh[0] * 9 / 10) return true;
        }
        return false;
    }

    /** A 1x1 invisible overlay that keeps the screen on (nothing to restore afterwards). */
    void keepScreenOn(boolean on) {
        main.post(() -> {
            keepOnCount = Math.max(0, keepOnCount + (on ? 1 : -1));
            WindowManager wm = getSystemService(WindowManager.class);
            if (keepOnCount > 0 && keepOnView == null) {
                keepOnView = new View(this);
                WindowManager.LayoutParams lp = new WindowManager.LayoutParams(1, 1,
                        WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                                | WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON,
                        PixelFormat.TRANSLUCENT);
                lp.gravity = Gravity.TOP | Gravity.START;
                try {
                    wm.addView(keepOnView, lp);
                } catch (RuntimeException e) {
                    Log.w(TAG, "keep-screen-on overlay failed", e);
                    keepOnView = null;
                }
            } else if (keepOnCount == 0 && keepOnView != null) {
                try {
                    wm.removeView(keepOnView);
                } catch (RuntimeException ignored) {
                }
                keepOnView = null;
            }
        });
    }
}
