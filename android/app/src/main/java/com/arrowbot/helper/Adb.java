package com.arrowbot.helper;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.Build;
import android.util.Log;

import org.bouncycastle.asn1.x500.X500Name;
import org.bouncycastle.cert.X509v3CertificateBuilder;
import org.bouncycastle.cert.jcajce.JcaX509CertificateConverter;
import org.bouncycastle.cert.jcajce.JcaX509v3CertificateBuilder;
import org.bouncycastle.operator.ContentSigner;
import org.bouncycastle.operator.jcajce.JcaContentSignerBuilder;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.math.BigInteger;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.PrivateKey;
import java.security.cert.Certificate;
import java.security.cert.CertificateFactory;
import java.security.spec.PKCS8EncodedKeySpec;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import io.github.muntashirakon.adb.AbsAdbConnectionManager;
import io.github.muntashirakon.adb.AdbStream;
import io.github.muntashirakon.adb.android.AdbMdns;

/**
 * adb from inside the app, to this phone's own adbd (libadb-android): the same access a computer
 * on USB has (scrcpy's server, the screen's power), without a computer. Two ways in:
 *  - Wireless debugging (Developer options; Android switches it on only while on Wi-Fi). Found by
 *    itself; the first time it has to be paired with the 6-digit code Android shows.
 *  - adbd on port 5555, after `adb tcpip 5555` from a computer (until the phone restarts): no
 *    Wi-Fi needed. The phone asks "Allow USB debugging?" once.
 * The app's key and certificate are made once and kept, so pairing / allowing is a one-time thing.
 */
final class Adb extends AbsAdbConnectionManager {
    private static final String TAG = HelperService.TAG;
    private static final int A_CNXN = 0x4e584e43, A_AUTH = 0x48545541, A_STLS = 0x534c5453;
    private static Adb instance;
    /** What happened last time (shown in the app). */
    static volatile String status = "not tried yet";
    static volatile boolean paired = false;

    private final Context context;
    private final PrivateKey key;
    private final Certificate cert;

    static synchronized Adb get(Context c) throws Exception {
        if (instance == null) instance = new Adb(c.getApplicationContext());
        return instance;
    }

    private Adb(Context c) throws Exception {
        context = c;
        setApi(Build.VERSION.SDK_INT);
        setTimeout(10, TimeUnit.SECONDS);
        File keyFile = new File(c.getFilesDir(), "adb_key.pk8"), certFile = new File(c.getFilesDir(), "adb_cert.der");
        if (keyFile.exists() && certFile.exists()) {
            key = KeyFactory.getInstance("RSA").generatePrivate(new PKCS8EncodedKeySpec(Files.readAllBytes(keyFile.toPath())));
            cert = CertificateFactory.getInstance("X.509").generateCertificate(
                    new ByteArrayInputStream(Files.readAllBytes(certFile.toPath())));
        } else {
            KeyPairGenerator g = KeyPairGenerator.getInstance("RSA");
            g.initialize(2048);
            KeyPair kp = g.generateKeyPair();
            X500Name name = new X500Name("CN=ArrowBot");
            long now = System.currentTimeMillis();
            X509v3CertificateBuilder b = new JcaX509v3CertificateBuilder(name, BigInteger.valueOf(now),
                    new Date(now - 86_400_000L), new Date(now + 36500L * 86_400_000L), name, kp.getPublic());
            ContentSigner signer = new JcaContentSignerBuilder("SHA256withRSA").build(kp.getPrivate());
            cert = new JcaX509CertificateConverter().getCertificate(b.build(signer));
            key = kp.getPrivate();
            Files.write(keyFile.toPath(), key.getEncoded());
            Files.write(certFile.toPath(), cert.getEncoded());
        }
        paired = new File(c.getFilesDir(), "adb_paired").exists();
    }

    @Override
    protected PrivateKey getPrivateKey() {
        return key;
    }

    @Override
    protected Certificate getCertificate() {
        return cert;
    }

    @Override
    protected String getDeviceName() {
        return "ArrowBot";
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
        if (isConnected()) return true;
        boolean wifi = onWifi(context);
        // 1. Wireless debugging, the way Android Studio finds it (mDNS; quick when it's on)
        if (wifi) {
            try {
                if (connectTls(context, 2500)) return connected("Wireless debugging");
            } catch (Exception e) {
                Log.i(TAG, "adb mdns: " + e);
            }
        }
        // 2. every port on this phone that answers like adbd: Wireless debugging again (found
        //    without mDNS), and 5555 opened with `adb tcpip 5555` from a computer
        boolean tlsSeen = false;
        for (int[] p : localAdbd()) {
            if (p[1] == A_STLS) {
                tlsSeen = true;
                try {
                    if (connect("127.0.0.1", p[0])) return connected("Wireless debugging");
                } catch (Exception e) {
                    Log.i(TAG, "adb tls " + p[0] + ": " + e);
                }
            } else {
                say.accept("On the phone: tap \"Allow\" in \"Allow USB debugging?\" (tick \"Always allow\").");
                setTimeout(60, TimeUnit.SECONDS);       // time to tap Allow
                try {
                    if (connect("127.0.0.1", p[0])) return connected("adb on port " + p[0]);
                } catch (Exception e) {
                    Log.i(TAG, "adb tcp " + p[0] + ": " + e);
                } finally {
                    setTimeout(10, TimeUnit.SECONDS);
                }
            }
        }
        status = tlsSeen ? "Wireless debugging is on, but this app isn't paired with it yet"
                : wifi ? "Wireless debugging is off" : "no Wi-Fi, so no Wireless debugging";
        return false;
    }

    private boolean connected(String how) {
        status = "connected (" + how + ")";
        markPaired();
        return true;
    }

    private void markPaired() {
        paired = true;
        try {
            new File(context.getFilesDir(), "adb_paired").createNewFile();
        } catch (IOException ignored) {
        }
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
        Matcher m = Pattern.compile("\\d+").matcher(typed == null ? "" : typed);
        while (m.find()) {
            String g = m.group();
            if (g.length() == 6 && code == null) code = g;
            else if (g.length() >= 4 && g.length() <= 5) port = Integer.parseInt(g);
        }
        if (code == null) return "Type the 6-digit code from \"Pair device with pairing code\".";
        String host = "127.0.0.1";
        if (port < 0) {
            AtomicInteger p = new AtomicInteger(-1);
            AtomicReference<String> h = new AtomicReference<>(null);
            CountDownLatch found = new CountDownLatch(1);
            AdbMdns mdns = new AdbMdns(context, AdbMdns.SERVICE_TYPE_TLS_PAIRING, (address, pt) -> {
                if (address != null && pt > 0) {
                    h.set(address.getHostAddress());
                    p.set(pt);
                    found.countDown();
                }
            });
            mdns.start();
            try {
                found.await(timeoutMs, TimeUnit.MILLISECONDS);
            } catch (InterruptedException ignored) {
            } finally {
                mdns.stop();
            }
            if (p.get() < 0) {
                return "The pairing box wasn't found. Keep \"Pair device with pairing code\" open while you "
                        + "type the code, or type its port too (the number after the last \":\").";
            }
            host = h.get();
            port = p.get();
        }
        try {
            if (!pair(host, port, code)) return "Pairing didn't work. Check the code and try again.";
        } catch (Exception e) {
            return "Pairing didn't work (" + e.getMessage() + "). Check the code and try again.";
        }
        markPaired();
        status = "paired";
        return null;
    }

    // ------------------------------------------------------------------ using it

    /** Run a shell command and return what it printed. */
    String shell(String command) throws Exception {
        AdbStream s = openStream("shell:" + command);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        try (InputStream in = s.openInputStream()) {
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        } catch (IOException ignored) {     // the stream ends when the command does
        }
        return out.toString(StandardCharsets.UTF_8.name());
    }

    /** Copy data to a file on the phone (adb's own file transfer, like `adb push`). */
    void push(byte[] data, String remote) throws Exception {
        AdbStream s = openStream("sync:");
        try {
            OutputStream out = s.openOutputStream();
            InputStream in = s.openInputStream();
            byte[] spec = (remote + ",33188").getBytes(StandardCharsets.UTF_8);    // 0100644
            out.write(syncHeader("SEND", spec.length));
            out.write(spec);
            for (int off = 0; off < data.length; off += 65536) {
                int n = Math.min(65536, data.length - off);
                out.write(syncHeader("DATA", n));
                out.write(data, off, n);
            }
            out.write(syncHeader("DONE", (int) (System.currentTimeMillis() / 1000)));
            out.flush();
            byte[] reply = new byte[8];
            int got = 0;
            while (got < 8) {
                int r = in.read(reply, got, 8 - got);
                if (r < 0) throw new IOException("adb push: no answer");
                got += r;
            }
            String id = new String(reply, 0, 4, StandardCharsets.US_ASCII);
            if (!id.equals("OKAY")) {
                int len = ByteBuffer.wrap(reply, 4, 4).order(ByteOrder.LITTLE_ENDIAN).getInt();
                byte[] msg = new byte[Math.max(0, Math.min(len, 1024))];
                got = 0;
                while (got < msg.length) {
                    int r = in.read(msg, got, msg.length - got);
                    if (r < 0) break;
                    got += r;
                }
                throw new IOException("adb push: " + new String(msg, 0, got, StandardCharsets.UTF_8));
            }
            out.write(syncHeader("QUIT", 0));
            out.flush();
        } finally {
            s.close();
        }
    }

    private static byte[] syncHeader(String id, int n) {
        return ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
                .put(id.getBytes(StandardCharsets.US_ASCII)).putInt(n).array();
    }
}
