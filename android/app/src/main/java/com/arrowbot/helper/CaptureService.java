package com.arrowbot.helper;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.SystemClock;
import android.util.DisplayMetrics;
import android.util.Log;
import android.view.WindowManager;

import java.nio.ByteBuffer;

/**
 * Screen capture (Android's MediaProjection, allowed once per start on the phone): the newest frame
 * is always kept, and copied out only when the bot asks for one. Runs as a foreground service, so
 * its notification is always there while capturing - with the "Stop bot" button.
 */
public class CaptureService extends Service implements FrameSource {
    static final String EXTRA_CODE = "code";
    static final String EXTRA_DATA = "data";
    static final String CHANNEL = "capture";
    static final int NOTIFICATION_ID = 1;

    static volatile CaptureService instance;
    static volatile String botState = "waiting for the bot";

    private MediaProjection projection;
    private VirtualDisplay display;
    private ImageReader reader;
    private HandlerThread thread;
    private final Object lock = new Object();
    private Image latest;
    private long seq = 0;
    private int width, height;

    static boolean running() {
        return instance != null;
    }

    static void setBotState(String state) {
        botState = state;
        CaptureService s = instance;
        if (s != null) s.getSystemService(NotificationManager.class).notify(NOTIFICATION_ID, s.notification());
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null || instance != null) return START_NOT_STICKY;
        // Android 14+: be a foreground service of type mediaProjection BEFORE getMediaProjection()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
        } else {
            startForeground(NOTIFICATION_ID, notification());
        }
        int code = intent.getIntExtra(EXTRA_CODE, 0);
        Intent data = Build.VERSION.SDK_INT >= 33
                ? intent.getParcelableExtra(EXTRA_DATA, Intent.class) : intent.getParcelableExtra(EXTRA_DATA);
        MediaProjectionManager mpm = getSystemService(MediaProjectionManager.class);
        try {
            projection = mpm.getMediaProjection(code, data);
        } catch (RuntimeException e) {
            Log.w(HelperService.TAG, "no projection", e);
        }
        if (projection == null) {
            stopSelf();
            return START_NOT_STICKY;
        }
        thread = new HandlerThread("capture");
        thread.start();
        Handler handler = new Handler(thread.getLooper());
        projection.registerCallback(new MediaProjection.Callback() {
            @Override
            public void onStop() {      // stopped from the status bar chip, the screen locked, ...
                stopSelf();
            }
        }, handler);

        DisplayMetrics m = new DisplayMetrics();
        WindowManager wm = getSystemService(WindowManager.class);
        wm.getDefaultDisplay().getRealMetrics(m);
        if (Build.VERSION.SDK_INT >= 30) {
            android.graphics.Rect b = wm.getMaximumWindowMetrics().getBounds();
            width = b.width();
            height = b.height();
        } else {
            width = m.widthPixels;
            height = m.heightPixels;
        }
        reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 3);
        reader.setOnImageAvailableListener(r -> {
            Image img;
            try {
                img = r.acquireLatestImage();
            } catch (RuntimeException e) {
                return;
            }
            if (img == null) return;
            synchronized (lock) {
                if (latest != null) latest.close();
                latest = img;
                seq++;
                lock.notifyAll();
            }
        }, handler);
        display = projection.createVirtualDisplay("ArrowBot", width, height, m.densityDpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, reader.getSurface(), null, handler);
        instance = this;
        return START_NOT_STICKY;
    }

    @Override
    public int[] size() {
        return new int[]{width, height};
    }

    @Override
    public int maxFrameBytes() {
        return width * height * 4;
    }

    @Override
    public boolean alive() {
        return instance == this;
    }

    /** Copy the newest frame newer than `after` into dest as tight RGBA rows. The screen is only
     *  drawn again when something on it changes: no new frame within repeatMs means the latest one
     *  still shows the screen as it is, so that one comes again with age 0 (scrcpy repeats frames
     *  the same way). Returns {seq, width, height, age_ms, RGBA, bytes} or null (no capture / no
     *  frame yet). */
    @Override
    public long[] copyFrame(long after, long repeatMs, byte[] dest) {
        synchronized (lock) {
            long end = SystemClock.uptimeMillis() + repeatMs;
            boolean repeat = false;
            while ((latest == null || seq <= after) && instance == this) {
                long left = end - SystemClock.uptimeMillis();
                if (left <= 0) {
                    repeat = latest != null;
                    break;
                }
                try {
                    lock.wait(left);
                } catch (InterruptedException e) {
                    return null;
                }
            }
            if (latest == null || instance != this) return null;
            Image.Plane p = latest.getPlanes()[0];
            ByteBuffer buf = p.getBuffer().duplicate();
            int w = latest.getWidth(), h = latest.getHeight(), row = w * 4, stride = p.getRowStride();
            if (dest.length < row * h) return null;
            if (stride == row) {
                buf.position(0);
                buf.get(dest, 0, row * h);
            } else {
                for (int y = 0; y < h; y++) {
                    buf.position(y * stride);
                    buf.get(dest, y * row, row);
                }
            }
            long age = repeat ? 0 : Math.max(0, Math.min(5000, (System.nanoTime() - latest.getTimestamp()) / 1_000_000));
            return new long[]{seq, w, h, age, RGBA, (long) row * h};
        }
    }

    Notification notification() {
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Screen capture for the bot",
                    NotificationManager.IMPORTANCE_LOW));
        }
        PendingIntent stop = PendingIntent.getBroadcast(this, 1,
                new Intent(this, ActionReceiver.class).setAction(ActionReceiver.STOP_BOT),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        PendingIntent open = PendingIntent.getActivity(this, 2, new Intent(this, MainActivity.class),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL) : new Notification.Builder(this);
        return b.setSmallIcon(android.R.drawable.ic_media_play)
                .setContentTitle("Arrow bot: " + botState)
                .setContentText("Tap \"Stop bot\" to stop it.")
                .setContentIntent(open)
                .setOngoing(true)
                .addAction(new Notification.Action.Builder((android.graphics.drawable.Icon) null, "Stop bot", stop).build())
                .build();
    }

    @Override
    public void onDestroy() {
        instance = null;
        synchronized (lock) {
            if (latest != null) latest.close();
            latest = null;
            lock.notifyAll();
        }
        if (display != null) display.release();
        if (reader != null) reader.close();
        if (projection != null) projection.stop();
        if (thread != null) thread.quitSafely();
        super.onDestroy();
    }

    static void start(Context c, int code, Intent data) {
        Intent i = new Intent(c, CaptureService.class).putExtra(EXTRA_CODE, code).putExtra(EXTRA_DATA, data);
        if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(i);
        else c.startService(i);
    }
}
