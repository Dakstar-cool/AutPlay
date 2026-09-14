package app.autplay.testing;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.Bundle;
import android.os.CancellationSignal;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/** A real Binder provider which blocks metadata until Android cancellation reaches it. */
public final class SlowContentProvider extends ContentProvider {
    private final AtomicInteger started = new AtomicInteger();
    private final AtomicInteger cancelled = new AtomicInteger();

    @Override public boolean onCreate() { return true; }

    @Override public Bundle call(String method, String arg, Bundle extras) {
        if ("reset".equals(method)) { started.set(0); cancelled.set(0); }
        Bundle result = new Bundle();
        result.putInt("started", started.get());
        result.putInt("cancelled", cancelled.get());
        return result;
    }

    @Override public Cursor query(Uri uri, String[] projection, String selection, String[] args,
                                  String sortOrder, CancellationSignal signal) {
        CountDownLatch release = new CountDownLatch(1);
        if (signal != null) signal.setOnCancelListener(() -> { cancelled.incrementAndGet(); release.countDown(); });
        started.incrementAndGet();
        try { release.await(10, TimeUnit.SECONDS); }
        catch (InterruptedException error) { Thread.currentThread().interrupt(); }
        if (signal != null) signal.throwIfCanceled();
        return new MatrixCursor(new String[] {"_display_name", "_size"});
    }

    @Override public Cursor query(Uri uri, String[] projection, String selection, String[] args, String sortOrder) {
        return query(uri, projection, selection, args, sortOrder, null);
    }
    @Override public String getType(Uri uri) { return "audio/wav"; }
    @Override public Uri insert(Uri uri, ContentValues values) { return null; }
    @Override public int delete(Uri uri, String selection, String[] args) { return 0; }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] args) { return 0; }
}
