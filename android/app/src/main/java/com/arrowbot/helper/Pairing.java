package com.arrowbot.helper;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.RemoteInput;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.provider.Settings;

/**
 * Pairing the app with Wireless debugging, once. Android shows the code in a box that closes when
 * you switch apps, so the code is typed into this app's notification instead (pull down the
 * notifications over Settings; the box stays open).
 */
final class Pairing {
    static final String CHANNEL = "pairing";
    static final int NOTIFICATION_ID = 4;
    static final String KEY_CODE = "code";
    static volatile String result = "";

    /** The "type the code here" notification, and Settings at Wireless debugging. */
    static void start(Context c) {
        notify(c, "Pair: type the code from Wireless debugging",
                "In Settings: Wireless debugging > \"Pair device with pairing code\". Then pull this down and "
                        + "type the 6-digit code here.", true);
        Intent s = new Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS)
                .putExtra(":settings:fragment_args_key", "toggle_adb_wireless")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            c.startActivity(s);
        } catch (RuntimeException e) {
            c.startActivity(new Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        }
    }

    /** The code was typed (notification or the app's screen): pair, then say how it went. */
    static void pair(Context c, String typed) {
        result = "pairing...";
        notify(c, "Pairing...", "", false);
        String error;
        try {
            error = Adb.get(c).pairWireless(typed, 8000);
        } catch (Exception e) {
            error = "Pairing didn't work (" + e.getMessage() + ").";
        }
        if (error == null) {
            result = "paired";
            notify(c, "ArrowBot is paired with Wireless debugging",
                    "From now on the bot uses it by itself whenever Wireless debugging is on.", false);
        } else {
            result = error;
            notify(c, "Not paired yet", error, true);
        }
    }

    private static void notify(Context c, String title, String text, boolean askCode) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Pairing with Wireless debugging",
                    NotificationManager.IMPORTANCE_HIGH));
        }
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(c, CHANNEL) : new Notification.Builder(c);
        b.setSmallIcon(android.R.drawable.ic_menu_edit)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setOnlyAlertOnce(false)
                .setAutoCancel(!askCode);
        if (askCode) {
            RemoteInput input = new RemoteInput.Builder(KEY_CODE).setLabel("6-digit pairing code").build();
            int flags = PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0);
            PendingIntent pi = PendingIntent.getBroadcast(c, 6,
                    new Intent(c, ActionReceiver.class).setAction(ActionReceiver.PAIR), flags);
            b.addAction(new Notification.Action.Builder(null, "Type the code", pi).addRemoteInput(input).build());
        }
        nm.notify(NOTIFICATION_ID, b.build());
    }
}
