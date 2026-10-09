package com.arrowbot.helper;

import android.app.RemoteInput;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;

/** The notification buttons: Stop bot, Allow, Deny, and the pairing code typed into a notification. */
public class ActionReceiver extends BroadcastReceiver {
    static final String STOP_BOT = "com.arrowbot.helper.STOP_BOT";
    static final String ALLOW = "com.arrowbot.helper.ALLOW";
    static final String DENY = "com.arrowbot.helper.DENY";
    static final String PAIR = "com.arrowbot.helper.PAIR";
    static final String EXTRA_TOKEN = "token";

    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent.getAction();
        if (STOP_BOT.equals(action)) {
            HelperService.requestStop();
        } else if (ALLOW.equals(action)) {
            Approvals.decide(intent.getStringExtra(EXTRA_TOKEN), true);
        } else if (DENY.equals(action)) {
            Approvals.decide(intent.getStringExtra(EXTRA_TOKEN), false);
        } else if (PAIR.equals(action)) {
            Bundle r = RemoteInput.getResultsFromIntent(intent);
            CharSequence code = r != null ? r.getCharSequence(Pairing.KEY_CODE) : null;
            if (code == null) return;
            Context c = context.getApplicationContext();
            // (can take longer than a receiver may: the accessibility service keeps the app running)
            new Thread(() -> Pairing.pair(c, code.toString()), "pair").start();
        }
    }
}
