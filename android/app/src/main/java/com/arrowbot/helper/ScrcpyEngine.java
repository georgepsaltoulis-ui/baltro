package com.arrowbot.helper;

import android.content.Context;
import android.graphics.Rect;
import android.media.Image;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaFormat;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.SystemClock;
import android.util.Log;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.security.MessageDigest;
import java.util.Random;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Consumer;

import com.flyfishxu.kadb.stream.AdbStream;

/**
 * What ArrowBot.py does with scrcpy on a computer, done inside the app over adb (Adb): scrcpy's
 * server runs on the phone, streams the screen as H.264 (hardware encoded), and takes touches as
 * raw control messages. Here the phone's hardware decoder (MediaCodec) turns the video back into
 * frames, and the newest one is kept for the bot - as YUV, which the bot converts only for the
 * frames it uses. With adb, the screen can also be switched off while the game keeps running
 * (scrcpy's "turn screen off": the power button turns it back on).
 */
final class ScrcpyEngine implements FrameSource {
    private static final String TAG = HelperService.TAG;
    /** Version of assets/bot/Scrcpy/scrcpy-server (the server only starts with its own version;
     *  the same file and version as bot.py's SCRCPY_VERSION). */
    static final String VERSION = "5.0";
    static final String SERVER_ASSET = "bot/Scrcpy/scrcpy-server";
    static final String REMOTE = "/data/local/tmp/arrowbot-scrcpy-server.jar";
    static final int MAX_FPS = 60, BIT_RATE = 8_000_000;
    private static final int MSG_INJECT_TOUCH = 2, MSG_SET_DISPLAY_POWER = 10;
    private static final int DOWN = 0, UP = 1, MOVE = 2;
    private static final long FINGER = -2;      // POINTER_GENERIC_FINGER
    /** No bot holds it and nobody used it (no frames asked for, no taps) for this long: stopped
     *  (the bot is gone). Long enough for the bot's first start (unpacking Python). */
    private static final long IDLE_STOP_MS = 180_000;
    /** No new picture for this long (scrcpy repeats it every 100 ms even when nothing moves):
     *  the server or the connection died without saying so. */
    private static final long STALL_MS = 6_000;
    /** Bot connections using it (streaming, screen off, keeping awake). */
    static final AtomicInteger holders = new AtomicInteger();

    static volatile ScrcpyEngine instance;
    static volatile String status = "off";

    private final Context context;
    private final Adb adb;
    private AdbStream server, video, control;
    private OutputStream controlOut;
    private volatile boolean alive;
    private final AtomicBoolean stopped = new AtomicBoolean();
    private volatile long lastUse = SystemClock.uptimeMillis();
    private final ExecutorService touches = Executors.newSingleThreadExecutor();
    private HandlerThread codecThread;
    private Handler codecHandler;
    private volatile MediaCodec codec;
    private final Object codecLock = new Object();
    /** Free decoder input buffers: (codec, index), so one of a codec that was replaced is never used. */
    private final LinkedBlockingQueue<Object[]> freeInputs = new LinkedBlockingQueue<>();
    private volatile int videoW, videoH;        // video size = touch coordinates

    // the newest frame (front), and the one being filled (back)
    private final Object lock = new Object();
    private byte[] front = new byte[0], back = new byte[0];
    private int frontW, frontH, frontFormat, frontBytes;
    private long seq = 0, frontNs = 0;
    private volatile long lastFrameAt = SystemClock.uptimeMillis();

    private ScrcpyEngine(Context c, Adb adb) {
        this.context = c.getApplicationContext();
        this.adb = adb;
    }

    static ScrcpyEngine current() {
        ScrcpyEngine e = instance;
        return e != null && e.alive ? e : null;
    }

    /** scrcpy over adb, started if needed. Null: no adb here (Wireless debugging off / not paired;
     *  see status) or it didn't start - then the app uses Android's screen capture instead. */
    static synchronized ScrcpyEngine ensure(Context c, Consumer<String> say) {
        ScrcpyEngine e = current();
        if (e != null) return e;
        try {
            Adb adb = Adb.get(c);
            if (!adb.connectAny(say)) {
                status = Adb.status;
                return null;
            }
            say.accept("Starting the live picture over adb...");
            e = new ScrcpyEngine(c, adb);
            e.start();
            instance = e;
            status = "on (" + e.videoW + "x" + e.videoH + ", " + MAX_FPS + " fps)";
            return e;
        } catch (Exception ex) {
            Log.w(TAG, "scrcpy over adb", ex);
            if (e != null) e.stop("couldn't start (" + ex.getMessage() + ")");
            else status = "couldn't start (" + ex.getMessage() + ")";
            return null;
        }
    }

    /** (Stops the server over the network: not on the main thread.) */
    static void stopAll() {
        ScrcpyEngine e = instance;
        if (e != null) new Thread(() -> e.stop("stopped"), "scrcpy-stop").start();
    }

    // ------------------------------------------------------------------ start / stop

    private void start() throws Exception {
        byte[] jar;
        try (InputStream in = context.getAssets().open(SERVER_ASSET)) {
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            byte[] buf = new byte[65536];
            int n;
            while ((n = in.read(buf)) > 0) b.write(buf, 0, n);
            jar = b.toByteArray();
        }
        String sha = hex(MessageDigest.getInstance("SHA-256").digest(jar));
        if (!adb.shell("sha256sum " + REMOTE + " 2>/dev/null").startsWith(sha)) {
            adb.push(jar, REMOTE);
            if (!adb.shell("sha256sum " + REMOTE).startsWith(sha)) throw new IOException("copying scrcpy's server failed");
        }

        int scid = new Random().nextInt(0x7FFFFFFF);
        String name = String.format("scrcpy_%08x", scid);
        server = adb.open("shell:CLASSPATH=" + REMOTE + " app_process / com.genymobile.scrcpy.Server " + VERSION
                + String.format(" scid=%08x", scid) + " log_level=info tunnel_forward=true audio=false control=true"
                + " video_codec=h264 max_fps=" + MAX_FPS + " video_bit_rate=" + BIT_RATE
                + " send_device_meta=false send_dummy_byte=false clipboard_autosync=false cleanup=true");
        alive = true;
        thread("scrcpy-log", this::serverLog);

        // the server listens a moment after it starts: until then, opening its socket is refused
        long end = SystemClock.uptimeMillis() + 8000;
        Thread.sleep(300);
        while (video == null) {
            try {
                video = adb.open("localabstract:" + name);
            } catch (IOException e) {
                if (SystemClock.uptimeMillis() > end || !alive) throw new IOException("scrcpy's server didn't start");
                Thread.sleep(100);
            }
        }
        control = adb.open("localabstract:" + name);
        controlOut = control.getSink().outputStream();

        codecThread = new HandlerThread("decoder");
        codecThread.start();
        codecHandler = new Handler(codecThread.getLooper());
        thread("scrcpy-video", this::readVideo);
        thread("scrcpy-control", this::drainControl);
        thread("scrcpy-idle", this::idleWatch);
        synchronized (lock) {
            long until = SystemClock.uptimeMillis() + 8000;
            while (seq == 0 && alive && SystemClock.uptimeMillis() < until) lock.wait(200);
            if (seq == 0) throw new IOException(alive ? "no picture from scrcpy" : "scrcpy stopped");
        }
    }

    /** Stop everything (the server's cleanup turns the screen back on). Any thread, any number of
     *  times; `why` is shown in the app. */
    void stop(String why) {
        alive = false;
        if (!stopped.compareAndSet(false, true)) {
            releaseCodec();                 // (a decoder started while it was stopping)
            return;
        }
        if (instance == this) instance = null;
        status = why;
        for (AdbStream s : new AdbStream[]{video, control, server}) {
            try {
                if (s != null) s.close();
            } catch (Exception ignored) {
            }
        }
        touches.shutdownNow();
        releaseCodec();
        if (codecThread != null) codecThread.quitSafely();
        synchronized (lock) {
            lock.notifyAll();
        }
    }

    private void releaseCodec() {
        MediaCodec c;
        synchronized (codecLock) {
            c = codec;
            codec = null;
            freeInputs.clear();
        }
        if (c != null) {
            try {
                c.stop();
            } catch (RuntimeException ignored) {
            }
            try {
                c.release();
            } catch (RuntimeException ignored) {
            }
        }
    }

    private static void thread(String name, Runnable r) {
        Thread t = new Thread(r, name);
        t.setDaemon(true);
        t.start();
    }

    private void serverLog() {
        AdbStream s = server;
        try (InputStream in = s.getSource().inputStream()) {
            ByteArrayOutputStream line = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int n;
            while ((n = in.read(buf)) >= 0) {
                for (int i = 0; i < n; i++) {
                    if (buf[i] != '\n') {
                        line.write(buf[i]);
                        continue;
                    }
                    String text = line.toString("UTF-8").trim();
                    line.reset();
                    Log.i(TAG, "[scrcpy-server] " + text);
                    if (text.contains("ERROR")) status = "scrcpy: " + text;
                }
            }
        } catch (IOException ignored) {
        }
        if (alive) {
            Log.w(TAG, "scrcpy's server ended");
            stop("scrcpy's server ended");
        }
    }

    private void drainControl() {
        try (InputStream in = control.getSource().inputStream()) {
            byte[] buf = new byte[4096];
            while (alive && in.read(buf) >= 0) {
                // device messages (clipboard, acks): not used, just never let them pile up
            }
        } catch (IOException ignored) {
        }
        if (alive) stop("scrcpy's control connection ended");     // (no more taps possible)
    }

    private void idleWatch() {
        while (alive) {
            SystemClock.sleep(1000);
            long now = SystemClock.uptimeMillis();
            if (holders.get() <= 0 && now - lastUse > IDLE_STOP_MS) {
                Log.i(TAG, "scrcpy over adb: not used any more, stopping");
                stop("off (not used)");
            } else if (seq > 0 && now - lastFrameAt > STALL_MS) {
                Log.w(TAG, "scrcpy over adb: no picture for " + (now - lastFrameAt) + " ms, stopping");
                stop("the picture stopped");
            }
        }
    }

    // ------------------------------------------------------------------ video

    /** scrcpy's video stream: codec id, then a session packet (MSB set: flags, width, height) at
     *  each capture start / rotation, and media packets (8-byte PTS with config/key flags, 4-byte
     *  size, the encoder's output). */
    private void readVideo() {
        try (DataInputStream in = new DataInputStream(new BufferedInputStream(video.getSource().inputStream(), 1 << 16))) {
            int codecId = in.readInt();
            if (codecId != 0x68323634) throw new IOException("not H.264: " + Integer.toHexString(codecId));
            byte[] packet = new byte[1 << 20];
            while (alive) {
                long head = in.readLong();
                if (head < 0) {                                 // session: (re)start the decoder
                    int w = (int) head, h = in.readInt();
                    startDecoder(w, h);
                    continue;
                }
                int size = in.readInt();
                if (size < 0 || size > (64 << 20)) throw new IOException("bad packet size " + size);
                if (size > packet.length) packet = new byte[size + (1 << 20)];
                in.readFully(packet, 0, size);
                MediaCodec c = codec;
                if (c == null) continue;
                Object[] free = null;
                while (alive && codec == c && (free = freeInputs.poll(500, TimeUnit.MILLISECONDS)) == null) {
                    // the decoder is busy: wait for an input buffer
                }
                if (free == null || free[0] != c || codec != c) continue;
                int index = (Integer) free[1];
                try {
                    ByteBuffer ib = c.getInputBuffer(index);
                    if (ib == null) continue;               // (decoder restarted meanwhile)
                    if (ib.capacity() < size) throw new IOException("packet too big for the decoder");
                    ib.clear();
                    ib.put(packet, 0, size);
                    boolean config = (head & (1L << 62)) != 0;
                    long pts = head & ((1L << 61) - 1);
                    c.queueInputBuffer(index, 0, size, config ? 0 : pts,
                            config ? MediaCodec.BUFFER_FLAG_CODEC_CONFIG : 0);
                } catch (IllegalStateException ignored) {       // decoder restarted meanwhile
                }
            }
            stop("off");
        } catch (EOFException e) {
            Log.i(TAG, "scrcpy video ended");
            stop("the video ended");
        } catch (Exception e) {
            Log.w(TAG, "scrcpy video", e);
            stop("video stopped (" + e.getMessage() + ")");
        }
    }

    private void startDecoder(int w, int h) throws IOException {
        releaseCodec();
        videoW = w;
        videoH = h;
        MediaCodec c = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_AVC);
        c.setCallback(decoderCallback, codecHandler);
        MediaFormat f = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, w, h);
        f.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible);
        f.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, Math.max(2 << 20, w * h));
        f.setInteger(MediaFormat.KEY_PRIORITY, 0);          // real time
        if (Build.VERSION.SDK_INT >= 30) f.setInteger(MediaFormat.KEY_LOW_LATENCY, 1);
        try {
            c.configure(f, null, null, 0);
        } catch (RuntimeException e) {                      // a decoder without low-latency mode
            c.reset();
            c.setCallback(decoderCallback, codecHandler);
            if (Build.VERSION.SDK_INT >= 30) f.removeKey(MediaFormat.KEY_LOW_LATENCY);
            c.configure(f, null, null, 0);
        }
        synchronized (codecLock) {
            if (!alive) {                                   // stopped meanwhile: don't leave it running
                c.release();
                return;
            }
            codec = c;
            c.start();
        }
    }

    private final MediaCodec.Callback decoderCallback = new MediaCodec.Callback() {
            @Override
            public void onInputBufferAvailable(MediaCodec mc, int index) {
                if (mc == codec) freeInputs.offer(new Object[]{mc, index});
            }

            @Override
            public void onOutputBufferAvailable(MediaCodec mc, int index, MediaCodec.BufferInfo info) {
                if (mc != codec) return;
                try {
                    Image img = mc.getOutputImage(index);
                    if (img != null) {
                        publish(img);
                        img.close();
                    }
                    mc.releaseOutputBuffer(index, false);
                } catch (IllegalStateException ignored) {
                }
            }

            @Override
            public void onError(MediaCodec mc, MediaCodec.CodecException e) {
                Log.w(TAG, "decoder", e);
                if (mc == codec && !e.isTransient()) stop("the video decoder failed");
            }

            @Override
            public void onOutputFormatChanged(MediaCodec mc, MediaFormat format) {
            }
    };

    /** One decoded frame -> back buffer (tight YUV rows, the image's crop), then it's the newest. */
    private void publish(Image img) {
        Rect crop = img.getCropRect();
        int w = crop.width() & ~1, h = crop.height() & ~1, cw = w / 2, ch = h / 2;
        Image.Plane[] p = img.getPlanes();
        int ySize = w * h, need = ySize * 3 / 2;
        if (back.length < need) back = new byte[need];
        ByteBuffer yb = p[0].getBuffer().duplicate();
        copyRows(yb, crop.top * p[0].getRowStride() + crop.left, p[0].getRowStride(), w, h, back, 0);

        ByteBuffer ub = p[1].getBuffer().duplicate(), vb = p[2].getBuffer().duplicate();
        int rs = p[1].getRowStride(), ps = p[1].getPixelStride();
        int uStart = (crop.top / 2) * rs + (crop.left / 2) * ps, vStart = (crop.top / 2) * p[2].getRowStride() + (crop.left / 2) * ps;
        int format;
        if (ps == 1 && p[2].getPixelStride() == 1) {        // planar: I420
            copyRows(ub, uStart, rs, cw, ch, back, ySize);
            copyRows(vb, vStart, p[2].getRowStride(), cw, ch, back, ySize + cw * ch);
            format = I420;
        } else if (ps == 2 && p[2].getPixelStride() == 2 && p[2].getRowStride() == rs
                && interleaved(ub, vb, uStart, vStart, rs, cw, ch)) {
            copyRows(ub, uStart, rs, 2 * cw, ch, back, ySize);     // U V U V ...: NV12
            fixLast(vb, vStart + (ch - 1) * rs + 2 * (cw - 1), back, need - 1);
            format = NV12;
        } else if (ps == 2 && p[2].getPixelStride() == 2 && p[2].getRowStride() == rs
                && interleaved(vb, ub, vStart, uStart, rs, cw, ch)) {
            copyRows(vb, vStart, rs, 2 * cw, ch, back, ySize);     // V U V U ...: NV21
            fixLast(ub, uStart + (ch - 1) * rs + 2 * (cw - 1), back, need - 1);
            format = NV21;
        } else {                                            // anything else: sample by sample
            int vrs = p[2].getRowStride(), vps = p[2].getPixelStride();
            for (int y = 0; y < ch; y++) {
                for (int x = 0; x < cw; x++) {
                    back[ySize + y * cw + x] = ub.get(uStart + y * rs + x * ps);
                    back[ySize + cw * ch + y * cw + x] = vb.get(vStart + y * vrs + x * vps);
                }
            }
            format = I420;
        }
        synchronized (lock) {
            byte[] t = front;
            front = back;
            back = t;
            frontW = w;
            frontH = h;
            frontFormat = format;
            frontBytes = need;
            frontNs = System.nanoTime();
            lastFrameAt = SystemClock.uptimeMillis();
            seq++;
            lock.notifyAll();
        }
    }

    private static void copyRows(ByteBuffer b, int start, int stride, int len, int rows, byte[] dst, int off) {
        for (int r = 0; r < rows; r++) {
            int pos = start + r * stride, n = Math.min(len, b.limit() - pos);
            if (n <= 0) return;
            b.position(pos);
            b.get(dst, off + r * len, n);
        }
    }

    /** The last chroma byte can be past the first plane's end (it belongs to the other plane). */
    private static void fixLast(ByteBuffer other, int pos, byte[] dst, int at) {
        if (pos >= 0 && pos < other.limit()) dst[at] = other.get(pos);
    }

    /** `b` is the same memory as `a`, one byte further (semi-planar: a0 b0 a1 b1 ...). */
    private static boolean interleaved(ByteBuffer a, ByteBuffer b, int aStart, int bStart, int rs, int cw, int ch) {
        for (int i = 0; i < 32; i++) {
            int y = (int) ((long) ch * i / 32), x = (int) ((long) (cw - 1) * ((i * 7) % 32) / 31);
            int ai = aStart + y * rs + 2 * x + 1, bi = bStart + y * rs + 2 * x;
            if (ai >= a.limit() || bi >= b.limit() || a.get(ai) != b.get(bi)) return false;
        }
        return true;
    }

    // ------------------------------------------------------------------ FrameSource

    @Override
    public int[] size() {
        return new int[]{videoW, videoH};
    }

    @Override
    public int maxFrameBytes() {
        return Math.max(videoW * videoH * 3 / 2, frontBytes);
    }

    @Override
    public boolean alive() {
        return alive;
    }

    @Override
    public long[] copyFrame(long after, long repeatMs, byte[] dest) {
        lastUse = SystemClock.uptimeMillis();
        synchronized (lock) {
            long end = lastUse + repeatMs;
            boolean repeat = false;
            while (seq <= after && alive) {
                long left = end - SystemClock.uptimeMillis();
                if (left <= 0) {
                    repeat = seq > 0;
                    break;
                }
                try {
                    lock.wait(left);
                } catch (InterruptedException e) {
                    return null;
                }
            }
            if (seq == 0 || !alive || dest.length < frontBytes) return null;
            System.arraycopy(front, 0, dest, 0, frontBytes);
            long age = repeat ? 0 : Math.max(0, (System.nanoTime() - frontNs) / 1_000_000);
            return new long[]{seq, frontW, frontH, age, frontFormat, frontBytes};
        }
    }

    // ------------------------------------------------------------------ touch and screen

    private void touch(int action, long pointer, float x, float y) throws IOException {
        ByteBuffer m = ByteBuffer.allocate(32).order(ByteOrder.BIG_ENDIAN);
        m.put((byte) MSG_INJECT_TOUCH).put((byte) action).putLong(pointer)
                .putInt(Math.round(x)).putInt(Math.round(y)).putShort((short) videoW).putShort((short) videoH)
                .putShort((short) (action == UP ? 0 : 0xFFFF)).putInt(0).putInt(0);
        send(m.array());
    }

    private void send(byte[] msg) throws IOException {
        synchronized (this) {
            controlOut.write(msg);
            controlOut.flush();
        }
    }

    /** Queued, answered at once: done in order, like the taps over scrcpy on a computer. */
    boolean tap(float x, float y, long holdMs) {
        lastUse = SystemClock.uptimeMillis();
        try {
            touches.submit(() -> {
                touch(DOWN, FINGER, x, y);
                Thread.sleep(holdMs);
                touch(UP, FINGER, x, y);
                return null;
            });
            return true;
        } catch (RuntimeException e) {
            return false;
        }
    }

    /** One finger: down, moving and slowing down toward the end (steps of ~12 ms, like bot.py's
     *  ScrcpyLink.drag), held still, up: no speed left, so the game doesn't fling the board. */
    boolean drag(float x0, float y0, float x1, float y1, long moveMs, long holdMs) {
        int steps = (int) Math.max(1, Math.round(moveMs / 12.0));
        return inTurn(() -> {
            touch(DOWN, FINGER, x0, y0);
            for (int i = 1; i <= steps; i++) {
                Thread.sleep(moveMs / steps);
                double f = 1 - Math.pow(1 - (double) i / steps, 2);
                touch(MOVE, FINGER, (float) (x0 + (x1 - x0) * f), (float) (y0 + (y1 - y0) * f));
            }
            Thread.sleep(holdMs);
            touch(UP, FINGER, x1, y1);
            return null;
        });
    }

    /** Two fingers from a0/b0 to a1/b1 (the server turns the 2nd DOWN into a second pointer). */
    boolean pinch(float ax0, float ay0, float bx0, float by0, float ax1, float ay1, float bx1, float by1, long ms) {
        int steps = (int) Math.max(1, Math.round(ms / 12.0));
        return inTurn(() -> {
            touch(DOWN, 10, ax0, ay0);
            touch(DOWN, 11, bx0, by0);
            for (int i = 1; i <= steps; i++) {
                Thread.sleep(ms / steps);
                double f = (double) i / steps;
                touch(MOVE, 10, (float) (ax0 + (ax1 - ax0) * f), (float) (ay0 + (ay1 - ay0) * f));
                touch(MOVE, 11, (float) (bx0 + (bx1 - bx0) * f), (float) (by0 + (by1 - by0) * f));
            }
            Thread.sleep(50);
            touch(UP, 11, bx1, by1);
            touch(UP, 10, ax1, ay1);
            return null;
        });
    }

    private boolean inTurn(java.util.concurrent.Callable<Void> job) {
        lastUse = SystemClock.uptimeMillis();
        try {
            Future<Void> f = touches.submit(job);
            f.get(30, TimeUnit.SECONDS);
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    /** The screen's panel on/off; the phone keeps running as if it were on (the game keeps playing
     *  and streaming). Off: the power button turns it back on. */
    boolean displayPower(boolean on) {
        lastUse = SystemClock.uptimeMillis();
        try {
            send(new byte[]{(byte) MSG_SET_DISPLAY_POWER, (byte) (on ? 1 : 0)});
            return true;
        } catch (IOException e) {
            return false;
        }
    }

    private static String hex(byte[] b) {
        StringBuilder sb = new StringBuilder();
        for (byte x : b) sb.append(String.format("%02x", x));
        return sb.toString();
    }
}
