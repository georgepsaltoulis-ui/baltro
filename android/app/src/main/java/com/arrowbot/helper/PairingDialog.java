package com.arrowbot.helper;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Settings' "Pair device with pairing code" box, recognised from the texts on screen (read by the
 * accessibility service): its 6-digit code and its "IP address & Port". Only while the user asked
 * the app to pair, and only Settings' windows.
 */
final class PairingDialog {
    private static final Pattern CODE = Pattern.compile("[0-9][0-9    -]{4,14}[0-9]");
    private static final Pattern ADDRESS = Pattern.compile("([0-9A-Fa-f.:\\[\\]%a-z]*[.:][0-9A-Fa-f.:\\[\\]%a-z]*):(\\d{4,5})");

    final String code;
    final String host;
    /** Ports shown in the window, the one nearest the code first (the box's own). */
    final List<Integer> ports;

    private PairingDialog(String code, String host, List<Integer> ports) {
        this.code = code;
        this.host = host;
        this.ports = ports;
    }

    /** The texts of one window, in screen order. Null: not the pairing box. */
    static PairingDialog find(List<String> texts) {
        String code = null;
        int codeAt = -1;
        for (int i = 0; i < texts.size() && code == null; i++) {
            String t = texts.get(i).trim();
            if (!CODE.matcher(t).matches()) continue;
            String digits = t.replaceAll("[^0-9]", "");
            if (digits.length() == 6) {
                code = digits;
                codeAt = i;
            }
        }
        if (code == null) return null;
        List<int[]> found = new ArrayList<>();          // {distance from the code, port}
        List<String> hosts = new ArrayList<>();
        for (int i = 0; i < texts.size(); i++) {
            Matcher m = ADDRESS.matcher(texts.get(i));
            while (m.find()) {
                int port = Integer.parseInt(m.group(2));
                if (port < 1024 || port > 65535) continue;
                found.add(new int[]{Math.abs(i - codeAt), port});
                hosts.add(m.group(1).replace("[", "").replace("]", ""));
            }
        }
        if (found.isEmpty()) return null;
        Integer[] order = new Integer[found.size()];
        for (int i = 0; i < order.length; i++) order[i] = i;
        java.util.Arrays.sort(order, (x, y) -> Integer.compare(found.get(x)[0], found.get(y)[0]));
        List<Integer> ports = new ArrayList<>();
        for (int i : order) if (!ports.contains(found.get(i)[1])) ports.add(found.get(i)[1]);
        return new PairingDialog(code, hosts.get(order[0]), ports);
    }
}
