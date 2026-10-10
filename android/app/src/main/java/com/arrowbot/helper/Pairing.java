package com.arrowbot.helper;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.RemoteInput;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;

/**
 * Pairing the app with Wireless debugging, once. Android shows the code in a box that closes (and
 * gets a new code) when you switch apps, so the app reads the code and port from that box itself,
 * through its accessibility service, while it's open. Typing the code into the app's notification
 * (pulled down over Settings) is the fallback.
 */
final class Pairing {
    static final String CHANNEL = "pairing";
    static final int NOTIFICATION_ID = 4;
    static final String KEY_CODE = "code";
    static final long WATCH_MS = 5 * 60_000;
    static volatile String result = "";

    /** Look for the pairing box, show the instructions (with a field for the code), and open
     *  Settings at Wireless debugging. */
    static void start(Context c) {
        boolean auto = HelperService.watchForPairing(WATCH_MS);
        result = auto ? "waiting for the pairing box in Settings" : "";
        if (auto) {
            notify(c, "Pairing: open \"Pair device with pairing code\"",
                    "In Settings: Wireless debugging > \"Pair device with pairing code\". Keep that box open: "
                            + "ArrowBot reads the code from it and pairs by itself. (If it doesn't, type the code "
                            + "here.)", true);
        } else {
            notify(c, "Pair: type the code from Wireless debugging",
                    "Turn ArrowBot on in Accessibility and it reads the code by itself. Or: in Settings, "
                            + "Wireless debugging > \"Pair device with pairing code\", then pull this down and type "
                            + "the 6-digit code here.", true);
        }
        Bundle highlight = new Bundle();
        highlight.putString(":settings:fragment_args_key", "toggle_adb_wireless");
        Intent s = new Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS)
                .putExtra(":settings:fragment_args_key", "toggle_adb_wireless")
                .putExtra(":settings:show_fragment_args", highlight)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            c.startActivity(s);
        } catch (RuntimeException e) {
            c.startActivity(new Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        }
    }

    /** The pairing box is open (read by the accessibility service): pair with its code and port.
     *  True when paired. */
    static boolean pairFromDialog(Context c, PairingDialog d) {
        result = "pairing (code " + d.code + " from Settings)...";
        notify(c, "Pairing with Wireless debugging...", "Code " + d.code + ", read from Settings.", false);
        String error = null;
        try {
            Adb adb = Adb.get(c);
            for (int port : d.ports) {
                // this phone itself first; the box's address if that can't be reached
                String e = adb.pairWith(d.code, "127.0.0.1", port);
                if (e == Adb.UNREACHABLE && !d.host.isEmpty()) e = adb.pairWith(d.code, d.host, port);
                if (e == null) {
                    done(c);
                    return true;
                }
                if (error == null || error == Adb.UNREACHABLE) error = e;
            }
        } catch (Exception e) {
            error = e.getMessage();
        }
        if (error == Adb.UNREACHABLE) error = "the pairing box's port didn't answer";
        result = "didn't work: " + error;
        notify(c, "Not paired yet",
                "Pairing didn't work (" + error + "). Close the pairing box and open it again (it shows a new "
                        + "code): ArrowBot tries again by itself.", true);
        return false;
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
            done(c);
        } else {
            result = error;
            notify(c, "Not paired yet", error, true);
        }
    }

    /** Paired: say so, and connect right away (the app's screen then shows it works). */
    private static void done(Context c) {
        HelperService.stopPairWatch();
        result = "paired";
        notify(c, "ArrowBot is paired with Wireless debugging",
                "From now on the bot uses it by itself whenever Wireless debugging is on.", false);
        try {
            Adb.get(c).connectAny(msg -> {
            });
        } catch (Exception ignored) {
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
