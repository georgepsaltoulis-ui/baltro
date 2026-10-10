package com.arrowbot.helper;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.util.Base64;
import android.util.Log;

import com.flyfishxu.kadb.Kadb;
import com.flyfishxu.kadb.cert.KadbCert;
import com.flyfishxu.kadb.cert.KadbCertPolicy;
import com.flyfishxu.kadb.cert.OkioFilePrivateKeyStore;
import com.flyfishxu.kadb.exception.AdbPairAuthException;
import com.flyfishxu.kadb.mdns.KadbMdnsAndroid;
import com.flyfishxu.kadb.mdns.MdnsConfig;
import com.flyfishxu.kadb.mdns.MdnsDiscoveryState;
import com.flyfishxu.kadb.mdns.MdnsEndpoint;
import com.flyfishxu.kadb.mdns.MdnsServiceType;
import com.flyfishxu.kadb.stream.AdbStream;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import kotlin.coroutines.Continuation;
import kotlin.coroutines.EmptyCoroutineContext;
import kotlinx.coroutines.BuildersKt;
import okio.Buffer;
import okio.FileSystem;
import okio.Path;

/**
 * adb from inside the app, to this phone's own adbd (Kadb, https://github.com/flyfishxu/Kadb): the
 * same access a computer on USB has (scrcpy's server, the screen's power), without a computer. Two
 * ways in:
 *  - Wireless debugging (Developer options; Android switches it on only while on Wi-Fi). Found by
 *    itself; the first time it has to be paired with the 6-digit code Android shows.
 *  - adbd on port 5555, after `adb tcpip 5555` from a computer (until the phone restarts): no
 *    Wi-Fi needed. The phone asks "Allow USB debugging?" once.
 * The app's key is made once and kept (Kadb derives its certificate from it), so pairing /
 * allowing is a one-time thing.
 */
final class Adb {
    private static final String TAG = HelperService.TAG;
    private static final int A_CNXN = 0x4e584e43, A_AUTH = 0x48545541, A_STLS = 0x534c5453;
    private static Adb instance;
    /** What happened last time (shown in the app). */
    static volatile String status = "not tried yet";
    static volatile boolean paired = false;

    private static final ScheduledExecutorService GUARD = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "adb-guard");
        t.setDaemon(true);
        return t;
    });

    private final Context context;
    /** The connected phone (null: not connected). */
    private volatile Kadb kadb;

    static synchronized Adb get(Context c) throws Exception {
        if (instance == null) instance = new Adb(c.getApplicationContext());
        return instance;
    }

    private Adb(Context c) throws Exception {
        context = c;
        File key = new File(c.getFilesDir(), "adb_key.pem");
        File older = new File(c.getFilesDir(), "adb_key.pk8");     // the key of earlier versions
        if (!key.exists() && older.exists()) {                      // (keeps their pairing)
            String b64 = Base64.encodeToString(Files.readAllBytes(older.toPath()), Base64.NO_WRAP);
            StringBuilder pem = new StringBuilder("-----BEGIN PRIVATE KEY-----\n");
            for (int i = 0; i < b64.length(); i += 64) pem.append(b64, i, Math.min(b64.length(), i + 64)).append('\n');
            pem.append("-----END PRIVATE KEY-----\n");
            Files.write(key.toPath(), pem.toString().getBytes(StandardCharsets.US_ASCII));
        }
        KadbCert.INSTANCE.configure(new OkioFilePrivateKeyStore(Path.get(key.getPath()), FileSystem.SYSTEM),
                new KadbCertPolicy(), Collections.emptyList());
        paired = new File(c.getFilesDir(), "adb_paired").exists();
    }

    static boolean onWifi(Context c) {
        ConnectivityManager cm = c.getSystemService(ConnectivityManager.class);
        if (cm == null) return false;
        for (Network n : cm.getAllNetworks()) {
            NetworkCapabilities nc = cm.getNetworkCapabilities(n);
            if (nc != null && nc.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) return true;
        }
        return false;
    }

    // ------------------------------------------------------------------ connecting

    /** Connect to this phone's adbd, any way that works. False: no adbd to talk to (see status). */
    synchronized boolean connectAny(Consumer<String> say) {
        Kadb k = kadb;
        if (k != null && k.connectionCheck()) return true;
        disconnect();
        boolean wifi = onWifi(context);
        boolean notPaired = false;
        // 1. Wireless debugging, the way Android Studio finds it (mDNS; quick when it's on)
        if (wifi) {
            for (MdnsEndpoint e : discover(MdnsServiceType.TLS_CONNECT, 2500)) {
                int r = tryConnect(e.getHost(), e.getPort(), true);
                if (r > 0) return connected("Wireless debugging");
                notPaired |= r < 0;
            }
        }
        // 2. every port on this phone that answers like adbd: Wireless debugging again (found
        //    without mDNS), and 5555 opened with `adb tcpip 5555` from a computer
        boolean tlsSeen = false;
        for (int[] p : localAdbd()) {
            if (p[1] == A_STLS) {
                tlsSeen = true;
                int r = tryConnect("127.0.0.1", p[0], true);
                if (r > 0) return connected("Wireless debugging");
                notPaired |= r < 0;
            } else {
                // (the first time only, the phone asks; a key it already allows gets in at once)
                ScheduledFuture<?> ask = GUARD.schedule(() -> say.accept(
                        "On the phone: tap \"Allow\" in \"Allow USB debugging?\" (tick \"Always allow\")."),
                        1500, TimeUnit.MILLISECONDS);
                try {
                    if (tryConnect("127.0.0.1", p[0], false) > 0) return connected("adb on port " + p[0]);
                } finally {
                    ask.cancel(false);
                }
            }
        }
        status = notPaired || tlsSeen ? "Wireless debugging is on, but this app isn't paired with it yet"
                : wifi ? "Wireless debugging is off" : "no Wi-Fi, so no Wireless debugging";
        return false;
    }

    /** 1: connected (kept in `kadb`), -1: Wireless debugging but not paired, 0: didn't work.
     *  The first try has a time limit on every read, since the phone may wait for an "Allow" that
     *  never comes (closing a Kadb doesn't stop a connection being made). Wireless debugging then
     *  gets a connection without one, which may sit idle between uses; a port opened with `adb
     *  tcpip` keeps the first one (without "Always allow" the phone would ask again) - the video
     *  keeps it busy while the bot plays. */
    private int tryConnect(String host, int port, boolean tls) {
        Kadb first = new Kadb(host, port, 5000, tls ? 15_000 : 60_000);
        int r = check(first, host, port);
        if (r <= 0 || !tls) {
            if (r > 0) kadb = first;
            return r;
        }
        first.close();
        Kadb k = new Kadb(host, port, 5000, 0);
        if (check(k, host, port) <= 0) return 0;
        kadb = k;
        return 1;
    }

    /** Connect (with the first command): 1 works, -1 not paired, 0 didn't work (then closed). */
    private static int check(Kadb k, String host, int port) {
        try {
            if (k.shell("echo ok").getOutput().trim().equals("ok")) return 1;
        } catch (Exception e) {                 // (Kotlin: no checked exceptions declared)
            for (Throwable t = e; t != null; t = t.getCause()) {
                if (t instanceof AdbPairAuthException) {
                    Log.i(TAG, "adb " + host + ":" + port + ": not paired");
                    k.close();
                    return -1;
                }
            }
            Log.i(TAG, "adb " + host + ":" + port + ": " + e);
        }
        k.close();
        return 0;
    }

    private boolean connected(String how) {
        status = "connected (" + how + ")";
        markPaired();
        return true;
    }

    synchronized void disconnect() {
        Kadb k = kadb;
        kadb = null;
        if (k != null) k.close();
    }

    private void markPaired() {
        paired = true;
        try {
            new File(context.getFilesDir(), "adb_paired").createNewFile();
        } catch (IOException ignored) {
        }
    }

    /** Wireless debugging services of this type found by mDNS within waitMs (stops at the first). */
    private List<MdnsEndpoint> discover(MdnsServiceType type, long waitMs) {
        KadbMdnsAndroid mdns = new KadbMdnsAndroid(context, new MdnsConfig(Collections.singleton(type), true));
        try {
            mdns.start();
            long end = System.currentTimeMillis() + waitMs;
            while (System.currentTimeMillis() < end) {
                MdnsDiscoveryState s = mdns.getState().getValue();
                List<MdnsEndpoint> found = type == MdnsServiceType.TLS_PAIRING ? s.getPairDevices() : s.getConnectDevices();
                if (!found.isEmpty()) return new ArrayList<>(found);
                Thread.sleep(100);
            }
        } catch (Exception e) {
            Log.i(TAG, "mdns: " + e);
        } finally {
            mdns.close();
        }
        return Collections.emptyList();
    }

    /** Ports on this phone that answer an adb CONNECT: {port, A_STLS (Wireless debugging) or
     *  A_AUTH/A_CNXN (adb tcpip)}. Closed ports answer at once, so trying them all is quick. */
    static List<int[]> localAdbd() {
        List<int[]> out = new ArrayList<>();
        for (int port : openPorts()) {
            if (port == HelperService.PORT) continue;
            int kind = adbdKind(port);
            if (kind == A_STLS || kind == A_AUTH || kind == A_CNXN) out.add(new int[]{port, kind});
        }
        return out;
    }

    private static List<Integer> openPorts() {
        int lo = 32768, hi = 60999;
        try {
            String[] r = new String(Files.readAllBytes(new File("/proc/sys/net/ipv4/ip_local_port_range").toPath()))
                    .trim().split("\\s+");
            lo = Math.min(lo, Integer.parseInt(r[0]));
            hi = Math.max(hi, Integer.parseInt(r[1]));
        } catch (Exception ignored) {
        }
        List<Integer> ports = new ArrayList<>();
        ports.add(5555);
        for (int p = lo; p <= hi; p++) if (p != 5555) ports.add(p);
        List<Integer> found = new ArrayList<>();
        for (int i = 0; i < ports.size(); i += 500) {
            try (Selector sel = Selector.open()) {
                List<SocketChannel> chans = new ArrayList<>();
                for (int port : ports.subList(i, Math.min(ports.size(), i + 500))) {
                    try {
                        SocketChannel ch = SocketChannel.open();
                        chans.add(ch);
                        ch.configureBlocking(false);
                        if (ch.connect(new InetSocketAddress("127.0.0.1", port))) found.add(port);
                        else ch.register(sel, SelectionKey.OP_CONNECT, port);
                    } catch (IOException ignored) {
                    }
                }
                long end = System.currentTimeMillis() + 500;
                while (!sel.keys().isEmpty() && System.currentTimeMillis() < end) {
                    sel.select(Math.max(1, end - System.currentTimeMillis()));
                    for (SelectionKey k : sel.selectedKeys()) {
                        try {
                            if (((SocketChannel) k.channel()).finishConnect()) found.add((Integer) k.attachment());
                        } catch (IOException ignored) {
                        }
                        k.cancel();
                    }
                    sel.selectedKeys().clear();
                    sel.selectNow();
                }
                for (SocketChannel ch : chans) {
                    try {
                        ch.close();
                    } catch (IOException ignored) {
                    }
                }
            } catch (IOException ignored) {
            }
        }
        return found;
    }

    /** What the port answers to an adb CONNECT (A_STLS, A_AUTH, A_CNXN), or 0: not adbd. */
    private static int adbdKind(int port) {
        byte[] payload = "host::\0".getBytes(StandardCharsets.US_ASCII);
        int sum = 0;
        for (byte b : payload) sum += b & 0xFF;
        ByteBuffer m = ByteBuffer.allocate(24 + payload.length).order(ByteOrder.LITTLE_ENDIAN);
        m.putInt(A_CNXN).putInt(0x01000001).putInt(256 * 1024).putInt(payload.length).putInt(sum)
                .putInt(~A_CNXN).put(payload);
        try (Socket s = new Socket()) {
            s.connect(new InetSocketAddress("127.0.0.1", port), 1000);
            s.setSoTimeout(1500);
            s.getOutputStream().write(m.array());
            byte[] head = new byte[4];
            int n = 0;
            InputStream in = s.getInputStream();
            while (n < 4) {
                int r = in.read(head, n, 4 - n);
                if (r < 0) return 0;
                n += r;
            }
            return ByteBuffer.wrap(head).order(ByteOrder.LITTLE_ENDIAN).getInt();
        } catch (IOException e) {
            return 0;
        }
    }

    // ------------------------------------------------------------------ pairing

    /** Pair with Wireless debugging's "Pair device with pairing code" box: the 6-digit code, and
     *  optionally the port shown there (found by itself otherwise, while the box is open).
     *  Returns null when paired, else what went wrong. */
    synchronized String pairWireless(String typed, long timeoutMs) {
        String code = null;
        int port = -1;
        typed = typed == null ? "" : typed;
        String digits = typed.replaceAll("\\D", "");
        if (digits.length() == 6) {
            code = digits;                              // "123456", "123 456"
        } else {                                        // "123456 37099": code and port
            Matcher m = Pattern.compile("\\d+").matcher(typed);
            while (m.find()) {
                String g = m.group();
                if (g.length() == 6 && code == null) code = g;
                else if (g.length() >= 4 && g.length() <= 5) port = Integer.parseInt(g);
            }
        }
        if (code == null) return "Type the 6-digit code from \"Pair device with pairing code\".";
        String host = "127.0.0.1";
        if (port < 0) {
            List<MdnsEndpoint> found = discover(MdnsServiceType.TLS_PAIRING, timeoutMs);
            if (found.isEmpty()) {
                return "The pairing box wasn't found. Keep \"Pair device with pairing code\" open while you "
                        + "type the code, or type its port too (the number after the last \":\").";
            }
            host = found.get(0).getHost();
            port = found.get(0).getPort();
        }
        String error = pairWith(code, host, port);
        if (error == UNREACHABLE) error = "the pairing box's port didn't answer";
        return error == null ? null : "Pairing didn't work (" + error + "). Check the code and try again.";
    }

    /** pairWith's answer when nothing answered at that address (try another one). */
    static final String UNREACHABLE = "unreachable";

    /** Pair with Wireless debugging's pairing box at host:port. Null: paired; UNREACHABLE: nothing
     *  answered there; else what went wrong. */
    synchronized String pairWith(String code, String host, int port) {
        try {
            BuildersKt.runBlocking(EmptyCoroutineContext.INSTANCE,
                    (scope, cont) -> Kadb.Companion.pair(host, port, code, "ArrowBot", (Continuation) cont));
        } catch (Exception e) {
            for (Throwable t = e; t != null; t = t.getCause()) {
                if (t instanceof java.net.ConnectException || t instanceof java.net.NoRouteToHostException) {
                    return UNREACHABLE;
                }
            }
            Log.i(TAG, "pair " + host + ":" + port + ": " + e);
            return e.getMessage() != null ? e.getMessage() : e.toString();
        }
        markPaired();
        status = "paired";
        return null;
    }

    // ------------------------------------------------------------------ using it

    private Kadb kadb() throws IOException {
        Kadb k = kadb;
        if (k == null) throw new IOException("not connected to adb");
        return k;
    }

    /** Open an adb stream ("shell:...", "localabstract:...", ...). A refused service throws. */
    AdbStream open(String destination) throws IOException {
        return kadb().open(destination);
    }

    /** Run a shell command and return what it printed. */
    String shell(String command) throws IOException {
        return kadb().shell(command).getOutput();
    }

    /** Copy data to a file on the phone (adb's own file transfer, like `adb push`), mode 0644. */
    void push(byte[] data, String remote) throws IOException {
        kadb().push(new Buffer().write(data), remote, 0100644, System.currentTimeMillis());
    }
}
