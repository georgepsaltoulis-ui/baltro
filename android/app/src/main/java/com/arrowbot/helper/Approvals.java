package com.arrowbot.helper;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/**
 * Which programs may use the helper. A program introduces itself with a random token; the first
 * time, the phone asks (a notification with Allow / Deny, also shown in the app), like adb's
 * "Allow USB debugging?". Allowed tokens are remembered.
 */
final class Approvals {
    static final String CHANNEL = "approvals";
    static final int NOTIFICATION_ID = 2;
    private static final String PREFS = "approvals";
    private static final Map<String, Boolean> decisions = new HashMap<>();
    static volatile String pending;     // token waiting for an answer (shown in MainActivity)

    private Approvals() {
    }

    private static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static boolean isAllowed(Context c, String token) {
        return token.equals(builtinToken(c)) || prefs(c).getStringSet("tokens", new HashSet<>()).contains(token);
    }

    /** The bot inside this app (BotService, another process) needs no asking: it gets this token,
     *  kept in a file both processes read. */
    static synchronized String builtinToken(Context c) {
        java.io.File f = new java.io.File(c.getFilesDir(), "builtin_token");
        try {
            if (f.exists()) {
                byte[] b = java.nio.file.Files.readAllBytes(f.toPath());
                String t = new String(b, java.nio.charset.StandardCharsets.UTF_8).trim();
                if (!t.isEmpty()) return t;
            }
            String t = java.util.UUID.randomUUID().toString().replace("-", "")
                    + java.util.UUID.randomUUID().toString().replace("-", "");
            java.io.File tmp = new java.io.File(c.getFilesDir(), "builtin_token.tmp");
            java.nio.file.Files.write(tmp.toPath(), t.getBytes(java.nio.charset.StandardCharsets.UTF_8));
            if (!f.exists()) tmp.renameTo(f);
            return new String(java.nio.file.Files.readAllBytes(f.toPath()), java.nio.charset.StandardCharsets.UTF_8).trim();
        } catch (java.io.IOException e) {
            return "";
        }
    }

    static int count(Context c) {
        return prefs(c).getStringSet("tokens", new HashSet<>()).size();
    }

    static void forgetAll(Context c) {
        prefs(c).edit().remove("tokens").apply();
    }

    /** Ask on the phone and wait for the answer (or the timeout: no). */
    static boolean request(Context c, String token, long timeoutMs) {
        pending = token;
        notify(c, token);
        boolean allowed = false;
        synchronized (decisions) {
            long end = System.currentTimeMillis() + timeoutMs;
            while (!decisions.containsKey(token)) {
                long left = end - System.currentTimeMillis();
                if (left <= 0) break;
                try {
                    decisions.wait(left);
                } catch (InterruptedException e) {
                    break;
                }
            }
            Boolean d = decisions.remove(token);
            allowed = d != null && d;
        }
        if (allowed) {
            Set<String> tokens = new HashSet<>(prefs(c).getStringSet("tokens", new HashSet<>()));
            tokens.add(token);
            prefs(c).edit().putStringSet("tokens", tokens).apply();
        }
        if (token.equals(pending)) pending = null;
        c.getSystemService(NotificationManager.class).cancel(NOTIFICATION_ID);
        return allowed;
    }

    static void decide(String token, boolean allow) {
        if (token == null) return;
        synchronized (decisions) {
            decisions.put(token, allow);
            decisions.notifyAll();
        }
    }

    private static void notify(Context c, String token) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Allow a program",
                    NotificationManager.IMPORTANCE_HIGH));
        }
        PendingIntent allow = PendingIntent.getBroadcast(c, 10,
                new Intent(c, ActionReceiver.class).setAction(ActionReceiver.ALLOW).putExtra(ActionReceiver.EXTRA_TOKEN, token),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        PendingIntent deny = PendingIntent.getBroadcast(c, 11,
                new Intent(c, ActionReceiver.class).setAction(ActionReceiver.DENY).putExtra(ActionReceiver.EXTRA_TOKEN, token),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        PendingIntent open = PendingIntent.getActivity(c, 12, new Intent(c, MainActivity.class),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(c, CHANNEL) : new Notification.Builder(c);
        Notification n = b.setSmallIcon(android.R.drawable.ic_dialog_alert)
                .setContentTitle("Allow the Arrow bot to control this phone?")
                .setContentText("A program on this phone (the Arrow bot in Termux?) wants to tap and see the screen.")
                .setContentIntent(open)
                .setAutoCancel(false)
                .addAction(new Notification.Action.Builder((android.graphics.drawable.Icon) null, "Allow", allow).build())
                .addAction(new Notification.Action.Builder((android.graphics.drawable.Icon) null, "Deny", deny).build())
                .build();
        nm.notify(NOTIFICATION_ID, n);
    }
}
