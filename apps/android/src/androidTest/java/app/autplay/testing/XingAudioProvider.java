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
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Seekable MP3 with staged reads to expose separate header and EOF timelines. */
public final class XingAudioProvider extends ContentProvider {
    public static final int AUDIO_FRAMES = 384;
    private final byte[] audio = bytes();
    private final byte[] shortAudio = bytes(230);
    private static final int MAX_DESCRIPTORS = 64;
    private static final int MAX_EVENTS = 256;
    private final Object evidenceLock = new Object();
    private final Map<String, Integer> sourceOrdinals = new HashMap<>();
    private final List<ReadGeneration> reads = new ArrayList<>();
    private final ArrayList<String> events = new ArrayList<>();
    private int resetGeneration;
    private int readOpens;
    private int tailReads;
    private boolean evidenceOverflow;

    private static final class ReadGeneration {
        final int reset;
        final int descriptor;
        final int source;
        long firstOffset = -1;
        int firstReadSequence;
        boolean tailDelivered;
        boolean eofRequested;
        boolean released;

        ReadGeneration(int reset, int descriptor, int source) {
            this.reset = reset;
            this.descriptor = descriptor;
            this.source = source;
        }
    }

    private void event(ReadGeneration read, String kind, long offset) {
        if (events.size() == MAX_EVENTS) { evidenceOverflow = true; return; }
        events.add("reset=" + read.reset + " source=" + read.source + " descriptor=" +
            read.descriptor + " event=" + kind + " offset=" + offset +
            " elapsedMs=" + SystemClock.elapsedRealtime());
    }

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
        final ReadGeneration read;
        synchronized (evidenceLock) {
            Integer ordinal = sourceOrdinals.get(uri.getLastPathSegment());
            if (ordinal == null || reads.size() == MAX_DESCRIPTORS) {
                evidenceOverflow = true;
                throw new FileNotFoundException("TEST_XING_EVIDENCE_BOUNDARY");
            }
            read = new ReadGeneration(resetGeneration, reads.size() + 1, ordinal);
            reads.add(read);
        }
        HandlerThread thread = new HandlerThread("XingFixtureReads");
        thread.start();
        try {
            return getContext().getSystemService(StorageManager.class).openProxyFileDescriptor(
                ParcelFileDescriptor.MODE_READ_ONLY, new ProxyFileDescriptorCallback() {
                    private boolean delayed;
                    @Override public long onGetSize() { return audio.length; }
                    @Override public int onRead(long offset, int size, byte[] data) {
                        synchronized (evidenceLock) {
                            if (read.reset == resetGeneration && read.firstOffset < 0) {
                                read.firstOffset = offset;
                                readOpens++;
                                read.firstReadSequence = readOpens;
                                event(read, "first-read", offset);
                            }
                        }
                        if (offset >= 4096 && !delayed) {
                            delayed = true;
                            // Give the service time to install the header-derived clipping item.
                            // Every reprepare gets the same header -> EOF sequence.
                            SystemClock.sleep(250);
                        }
                        if (offset >= audio.length) {
                            synchronized (evidenceLock) {
                                if (read.reset == resetGeneration && !read.eofRequested) {
                                    read.eofRequested = true;
                                    event(read, "eof-request", offset);
                                }
                            }
                            return 0;
                        }
                        int count = Math.min(size, audio.length - (int) offset);
                        System.arraycopy(audio, (int) offset, data, 0, count);
                        synchronized (evidenceLock) {
                            if (read.reset == resetGeneration && offset + count == audio.length && !read.tailDelivered) {
                                read.tailDelivered = true;
                                tailReads++;
                                event(read, "tail-delivered", offset + count);
                            }
                        }
                        return count;
                    }
                    @Override public void onRelease() {
                        synchronized (evidenceLock) {
                            if (read.reset == resetGeneration) {
                                read.released = true;
                                event(read, "release", -1);
                            }
                        }
                        thread.quitSafely();
                    }
                }, new Handler(thread.getLooper()));
        } catch (IOException error) {
            thread.quitSafely();
            FileNotFoundException failure = new FileNotFoundException("TEST_XING_OPEN_FAILED");
            failure.initCause(error);
            throw failure;
        }
    }

    @Override public Bundle call(String method, String arg, Bundle extras) {
        synchronized (evidenceLock) {
            if ("reset".equals(method)) {
                resetGeneration++;
                readOpens = 0;
                tailReads = 0;
                evidenceOverflow = false;
                reads.clear();
                events.clear();
                sourceOrdinals.clear();
                ArrayList<String> ids = extras == null ? null : extras.getStringArrayList("fixtureIds");
                if (ids != null && ids.size() == 3) {
                    for (int i = 0; i < ids.size(); i++) sourceOrdinals.put(ids.get(i), i + 1);
                }
            }
            Bundle result = new Bundle();
            result.putInt("resetGeneration", resetGeneration);
            result.putInt("readOpens", readOpens);
            result.putInt("tailReads", tailReads);
            result.putBoolean("evidenceOverflow", evidenceOverflow);
            result.putStringArrayList("events", new ArrayList<>(events));
            Integer ordinal = sourceOrdinals.get(arg);
            int sourceReads = 0;
            ReadGeneration latest = null;
            if (ordinal != null) {
                for (ReadGeneration read : reads) {
                    if (read.source == ordinal && read.firstOffset >= 0) {
                        sourceReads++;
                        if (latest == null || read.firstReadSequence > latest.firstReadSequence) latest = read;
                    }
                }
            }
            result.putInt("sourceReads", sourceReads);
            result.putInt("latestDescriptor", latest == null ? 0 : latest.descriptor);
            result.putLong("firstReadOffset", latest == null ? -1 : latest.firstOffset);
            result.putBoolean("tailDelivered", latest != null && latest.tailDelivered);
            result.putBoolean("eofRequested", latest != null && latest.eofRequested);
            result.putBoolean("released", latest != null && latest.released);
            return result;
        }
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
