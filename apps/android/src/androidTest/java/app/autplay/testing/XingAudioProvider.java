package app.autplay.testing;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.ParcelFileDescriptor;
import android.os.ProxyFileDescriptorCallback;
import android.os.SystemClock;
import android.os.storage.StorageManager;
import android.provider.OpenableColumns;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.concurrent.atomic.AtomicInteger;

/** Seekable MP3 with staged reads to expose separate header and EOF timelines. */
public final class XingAudioProvider extends ContentProvider {
    public static final int AUDIO_FRAMES = 384;
    private final byte[] audio = bytes();
    private final byte[] shortAudio = bytes(230);
    private final AtomicInteger readOpens = new AtomicInteger();
    private final AtomicInteger tailReads = new AtomicInteger();

    /** Valid silent MPEG1 Layer III frames; Xing has a duration but no TOC, one frame short. */
    public static byte[] bytes() {
        return bytes(AUDIO_FRAMES);
    }

    public static byte[] bytes(int audioFrames) {
        byte[] bytes = new byte[417 * (audioFrames + 1)];
        for (int offset = 0; offset < bytes.length; offset += 417) {
            ByteBuffer.wrap(bytes, offset, 4).putInt(0xfffb9000);
        }
        ByteBuffer.wrap(bytes, 36, 12).putInt(0x58696e67).putInt(1).putInt(audioFrames - 1);
        return bytes;
    }

    @Override public boolean onCreate() { return true; }

    @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        final byte[] audio = selected(uri);
        HandlerThread thread = new HandlerThread("XingFixtureReads");
        thread.start();
        try {
            return getContext().getSystemService(StorageManager.class).openProxyFileDescriptor(
                ParcelFileDescriptor.MODE_READ_ONLY, new ProxyFileDescriptorCallback() {
                    private boolean started;
                    private boolean delayed;
                    private boolean reachedTail;
                    @Override public long onGetSize() { return audio.length; }
                    @Override public int onRead(long offset, int size, byte[] data) {
                        if (!started) { started = true; readOpens.incrementAndGet(); }
                        if (offset >= 4096 && !delayed) {
                            delayed = true;
                            // Give the service time to install the header-derived clipping item.
                            // Every reprepare gets the same header -> EOF sequence.
                            SystemClock.sleep(250);
                        }
                        if (offset >= audio.length) return 0;
                        int count = Math.min(size, audio.length - (int) offset);
                        System.arraycopy(audio, (int) offset, data, 0, count);
                        if (offset + count == audio.length && !reachedTail) {
                            reachedTail = true;
                            tailReads.incrementAndGet();
                        }
                        return count;
                    }
                    @Override public void onRelease() { thread.quitSafely(); }
                }, new Handler(thread.getLooper()));
        } catch (IOException error) {
            thread.quitSafely();
            FileNotFoundException failure = new FileNotFoundException("TEST_XING_OPEN_FAILED");
            failure.initCause(error);
            throw failure;
        }
    }

    @Override public Bundle call(String method, String arg, Bundle extras) {
        if ("reset".equals(method)) { readOpens.set(0); tailReads.set(0); }
        Bundle result = new Bundle();
        result.putInt("readOpens", readOpens.get());
        result.putInt("tailReads", tailReads.get());
        return result;
    }
    @Override public Cursor query(Uri uri, String[] projection, String selection, String[] args, String sortOrder) {
        MatrixCursor cursor = new MatrixCursor(new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE});
        cursor.addRow(new Object[] {"xing-no-toc.mp3", selected(uri).length});
        return cursor;
    }
    @Override public String getType(Uri uri) { return "audio/mpeg"; }
    private byte[] selected(Uri uri) {
        return uri.getPathSegments().contains("short") ? shortAudio : audio;
    }
    @Override public Uri insert(Uri uri, ContentValues values) { return null; }
    @Override public int delete(Uri uri, String selection, String[] args) { return 0; }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] args) { return 0; }
}
