package com.arrowbot.helper;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.content.res.AssetManager;
import android.os.Build;
import android.os.IBinder;
import android.os.Process;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.PrintWriter;
import java.io.StringWriter;

/**
 * The bot itself (Python, Chaquopy), in a process of its own (":bot", see the manifest), the way
 * ArrowBot.py runs it as a child process on a computer: it talks to this app's accessibility and
 * screen-capture part (HelperService, CaptureService) over 127.0.0.1 like the Termux version does.
 * Stopping it ends that process: nothing of the bot is left running, the next start is fresh.
 */
public class BotService extends Service {
    static final String ACTION_STOP = "com.arrowbot.helper.STOP_BOT_PROCESS";
    static final String CHANNEL = "bot";
    static final int NOTIFICATION_ID = 3;
    private static Thread bot;

    static void start(Context c) {
        Intent i = new Intent(c, BotService.class);
        if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(i);
        else c.startService(i);
    }

    static void stop(Context c) {
        HelperService.requestStop();            // the bot's own way out (it polls this)...
        CaptureService.setBotState("stopped");
        Intent i = new Intent(c, BotService.class).setAction(ACTION_STOP);
        try {
            c.startService(i);                  // ...and its process ends right away anyway
        } catch (RuntimeException ignored) {
        }
    }

    static File botDir(Context c) {
        return new File(c.getFilesDir(), "bot");
    }

    static File logFile(Context c) {
        return new File(botDir(c), "Temp/app.log");
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopForeground(true);
            stopSelf();
            Process.killProcess(Process.myPid());
            return START_NOT_STICKY;
        }
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(NOTIFICATION_ID, notification());
        }
        if (bot == null) {
            bot = new Thread(this::run, "bot");
            bot.start();
        }
        return START_NOT_STICKY;
    }

    private void run() {
        File dir = botDir(this);
        try {
            copyAssets(getAssets(), "bot", dir);
            new File(dir, "Temp").mkdirs();
            if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
            Python.getInstance().getModule("app_main").callAttr("run", dir.getAbsolutePath(),
                    Approvals.builtinToken(this), getPackageName());
        } catch (Throwable t) {
            StringWriter sw = new StringWriter();
            t.printStackTrace(new PrintWriter(sw));
            try (OutputStream o = new FileOutputStream(logFile(this), true)) {
                o.write(("\n[!!] The bot stopped with an error:\n" + sw).getBytes());
            } catch (IOException ignored) {
            }
        } finally {
            stopSelf();
            Process.killProcess(Process.myPid());
        }
    }

    /** The bot's files (assets/bot) to internal storage: OpenCV reads the templates from files, and
     *  the bot writes its log and settings next to them (Temp/). Copied fresh on every start. */
    static void copyAssets(AssetManager am, String path, File to) throws IOException {
        String[] names = am.list(path);
        if (names == null || names.length == 0) {
            to.getParentFile().mkdirs();
            try (InputStream in = am.open(path); OutputStream out = new FileOutputStream(to)) {
                byte[] buf = new byte[65536];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            }
            return;
        }
        to.mkdirs();
        for (String name : names) copyAssets(am, path + "/" + name, new File(to, name));
    }

    private Notification notification() {
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Arrow bot", NotificationManager.IMPORTANCE_LOW));
        }
        PendingIntent stop = PendingIntent.getService(this, 4,
                new Intent(this, BotService.class).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE);
        PendingIntent open = PendingIntent.getActivity(this, 5, new Intent(this, MainActivity.class),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL) : new Notification.Builder(this);
        return b.setSmallIcon(android.R.drawable.ic_media_play)
                .setContentTitle("Arrow bot is playing")
                .setContentText("Tap \"Stop bot\" to stop it.")
                .setContentIntent(open)
                .setOngoing(true)
                .addAction(new Notification.Action.Builder((android.graphics.drawable.Icon) null, "Stop bot", stop).build())
                .build();
    }
}
