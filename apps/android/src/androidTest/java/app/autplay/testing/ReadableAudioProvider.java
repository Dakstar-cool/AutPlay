package app.autplay.testing;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.IOException;

/** Deterministic short PCM WAV exposed as a real readable content URI for Media3/source tests. */
public final class ReadableAudioProvider extends ContentProvider {
    private File audio;

    @Override public boolean onCreate() {
        audio = new File(getContext().getCacheDir(), "p08-readable.wav");
        try (FileOutputStream output = new FileOutputStream(audio)) {
            output.write(wavSilence());
            return true;
        } catch (IOException error) {
            throw new IllegalStateException("TEST_AUDIO_CREATE_FAILED", error);
        }
    }

    @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        return ParcelFileDescriptor.open(audio, ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder) {
        MatrixCursor cursor = new MatrixCursor(new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE});
        cursor.addRow(new Object[] {"p08-readable.wav", audio.length()});
        return cursor;
    }

    @Override public String getType(Uri uri) { return "audio/wav"; }
    @Override public Uri insert(Uri uri, ContentValues values) { return null; }
    @Override public int delete(Uri uri, String selection, String[] selectionArgs) { return 0; }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) { return 0; }

    private static byte[] wavSilence() throws IOException {
        int samples = 320_000;
        int payloadBytes = samples * 2;
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        DataOutputStream data = new DataOutputStream(output);
        data.writeBytes("RIFF"); writeLeInt(data, 36 + payloadBytes); data.writeBytes("WAVEfmt "); writeLeInt(data, 16);
        writeLeShort(data, 1); writeLeShort(data, 1); writeLeInt(data, 8_000); writeLeInt(data, 16_000);
        writeLeShort(data, 2); writeLeShort(data, 16); data.writeBytes("data"); writeLeInt(data, payloadBytes);
        data.write(new byte[payloadBytes]);
        data.flush();
        return output.toByteArray();
    }

    private static void writeLeInt(DataOutputStream data, int value) throws IOException {
        data.writeByte(value & 0xff); data.writeByte((value >>> 8) & 0xff);
        data.writeByte((value >>> 16) & 0xff); data.writeByte((value >>> 24) & 0xff);
    }

    private static void writeLeShort(DataOutputStream data, int value) throws IOException {
        data.writeByte(value & 0xff); data.writeByte((value >>> 8) & 0xff);
    }
}
