package com.arrowbot.helper;

import android.content.Context;

/** The app's settings (kept on the phone). */
final class Prefs {
    private static final String FILE = "arrowbot";

    /** Screen off while the bot plays (the power button turns it on), or kept on to watch. */
    static boolean screenOff(Context c) {
        return c.getSharedPreferences(FILE, Context.MODE_PRIVATE).getBoolean("screen_off", true);
    }

    static void setScreenOff(Context c, boolean off) {
        c.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit().putBoolean("screen_off", off).apply();
    }
}
