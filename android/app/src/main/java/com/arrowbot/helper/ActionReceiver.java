package com.arrowbot.helper;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** The notification buttons: Stop bot, Allow, Deny. */
public class ActionReceiver extends BroadcastReceiver {
    static final String STOP_BOT = "com.arrowbot.helper.STOP_BOT";
    static final String ALLOW = "com.arrowbot.helper.ALLOW";
    static final String DENY = "com.arrowbot.helper.DENY";
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
        }
    }
}
