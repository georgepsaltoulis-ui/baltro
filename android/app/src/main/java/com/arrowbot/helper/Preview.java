package com.arrowbot.helper;

import android.graphics.Bitmap;

/**
 * A small live picture of the hidden screen the bot plays on (background play), for the app's own
 * screen: every `step`-th pixel of scrcpy's newest decoded frame, YUV to RGB by hand (a few ms at
 * a third of the size, a few times a second, only while the app is open).
 */
final class Preview {
    private byte[] buf = new byte[0];
    private int[] argb = new int[0];
    private long lastSeq = -1;

    /** The newest frame `step` times smaller, or null (no frame, or no new one since last time). */
    Bitmap grab(ScrcpyEngine e, int step) {
        int need = e.maxFrameBytes();
        if (buf.length < need) buf = new byte[need];
        long[] f = e.peek(buf);
        if (f == null || f[0] == lastSeq) return null;
        lastSeq = f[0];
        int w = (int) f[1], h = (int) f[2], format = (int) f[4];
        int pw = w / step, ph = h / step;
        if (pw <= 0 || ph <= 0) return null;
        if (argb.length < pw * ph) argb = new int[pw * ph];
        int ySize = w * h, cw = w / 2, ch = h / 2;
        boolean nv12 = format == FrameSource.NV12, nv21 = format == FrameSource.NV21;
        for (int py = 0; py < ph; py++) {
            int y = py * step, cy = y >> 1, out = py * pw;
            for (int px = 0; px < pw; px++) {
                int x = px * step, cx = x >> 1;
                int u, v;
                if (nv12 || nv21) {                     // U V U V ... (NV12) or V U V U ... (NV21)
                    int i = ySize + cy * 2 * cw + 2 * cx;
                    int a = buf[i] & 0xFF, b = buf[i + 1] & 0xFF;
                    u = nv12 ? a : b;
                    v = nv12 ? b : a;
                } else {                                // I420: all U, then all V
                    int i = cy * cw + cx;
                    u = buf[ySize + i] & 0xFF;
                    v = buf[ySize + cw * ch + i] & 0xFF;
                }
                int c = Math.max(0, (buf[y * w + x] & 0xFF) - 16) * 298, d = u - 128, ee = v - 128;
                int r = (c + 409 * ee + 128) >> 8;
                int g = (c - 100 * d - 208 * ee + 128) >> 8;
                int bl = (c + 516 * d + 128) >> 8;
                argb[out + px] = 0xFF000000 | (clamp(r) << 16) | (clamp(g) << 8) | clamp(bl);
            }
        }
        return Bitmap.createBitmap(argb, 0, pw, pw, ph, Bitmap.Config.ARGB_8888);
    }

    private static int clamp(int c) {
        return c < 0 ? 0 : Math.min(c, 255);
    }
}
