package com.arrowbot.helper;

/**
 * Where the bot's frames come from: scrcpy's video over adb (ScrcpyEngine: the phone's hardware
 * video decoder, YUV) or Android's screen capture (CaptureService: RGBA).
 */
interface FrameSource {
    /** Pixel layouts of the frames (the bot converts to BGR the frames it uses). */
    int RGBA = 0, I420 = 1, NV12 = 2, NV21 = 3;
    String[] FORMATS = {"RGBA", "I420", "NV12", "NV21"};

    /** Width and height of the frames (= the coordinates taps use). */
    int[] size();

    /** Bytes the largest frame takes. */
    int maxFrameBytes();

    /** Copy the newest frame newer than `after` into dest. No new frame within repeatMs: the
     *  latest one again with age 0 (the screen hasn't changed, it still shows what's there).
     *  Returns {seq, width, height, age_ms, format, bytes} or null (stopped / no frame yet). */
    long[] copyFrame(long after, long repeatMs, byte[] dest);

    boolean alive();
}
