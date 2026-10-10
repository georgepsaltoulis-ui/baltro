package com.arrowbot.helper;

import android.content.Context;

/** The app's settings (kept on the phone). */
final class Prefs {
    private static final String FILE = "arrowbot";

    /** Screen off while the bot plays (the power button turns it on), or kept on to watch. */
    static boolean screenOff(Context c) {
        return c.getSharedPreferences(FILE, Context.MODE_PRIVATE).getBoolean("screen_off", true);
    }

    static final String ARROWS = "com.arrow.out";

    /** The app the bot plays (any app on the phone; Arrows unless chosen). */
    static String gamePackage(Context c) {
        return c.getSharedPreferences(FILE, Context.MODE_PRIVATE).getString("game_package", ARROWS);
    }

    static String gameLabel(Context c) {
        return c.getSharedPreferences(FILE, Context.MODE_PRIVATE).getString("game_label", "Arrows");
    }

    static void setGame(Context c, String pkg, String label) {
        c.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit()
                .putString("game_package", pkg).putString("game_label", label).apply();
    }

    /** How the bot reads it: "arrows" (Arrows' look) or "amaze" (Amaze GO!'s: dark lines on a light
     *  board - for every other game). */
    static String game(Context c) {
        return ARROWS.equals(gamePackage(c)) ? "arrows" : "amaze";
    }

    static void setScreenOff(Context c, boolean off) {
        c.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit().putBoolean("screen_off", off).apply();
    }
}
