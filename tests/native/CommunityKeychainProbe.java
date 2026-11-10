package app.autplay.desktop.security;

import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.security.spec.X509EncodedKeySpec;
import java.util.*;
import java.time.Instant;
import java.util.concurrent.TimeUnit;
import app.autplay.desktop.media.MacRemoteMediaIdentityJni;
import app.autplay.desktop.sourceaccess.SourceAccessJni;

/** Runs only in a separately packaged CI app on a disposable macOS keychain. */
public final class CommunityKeychainProbe {
    private static final String TAG = "app.autplay.desktop.device-key.v1.253941ea-bd4a-4d93-bf1b-a97173c8527b";
    private static final String VALUE = "5dff3323-df7e-40a8-a589-f6414ba6a1f6";
    private static final byte[] PAYLOAD = "synthetic-account-session-only".getBytes(StandardCharsets.UTF_8);
    private static native void checkNonExportable(String pin);
    private static native void lockKeychain();
    private static byte[] control(int revision, String state) {
        return ("{\"format\":\"autplay-secure-control-1\",\"revision\":" + revision +
            ",\"key_intents\":[{\"tag\":\"" + TAG + "\",\"state\":\"" + state +
            "\"}],\"cleanup\":[{\"value_id\":\"" + VALUE + "\",\"length\":" + PAYLOAD.length +
            "}],\"selected_slots\":[]}").getBytes(StandardCharsets.UTF_8);
    }
    private static void check(boolean value) { if (!value) throw new AssertionError("COMMUNITY_PROBE_FAILED"); }
    public static void main(String[] args) throws Exception {
        Path contents = Path.of(ProcessHandle.current().info().command().orElseThrow()).getParent().getParent();
        Path nativeDir = contents.resolve("app/resources/native");
        String pin = Files.readAllLines(nativeDir.resolve("AutPlaySignedIdentity")).get(1);
        System.load(nativeDir.resolve("libAutPlaySecureState.dylib").toString());
        System.load(nativeDir.resolve("libCommunityProbeChecks.dylib").toString());
        if (args[0].equals("create")) {
            String bundle = contents.getParent().toString();
            Path helper = nativeDir.resolve("AutPlayRemoteMediaHelper");
            var media = MacRemoteMediaIdentityJni.INSTANCE;
            byte[] witness = media.staticWitness(bundle, helper.toString(), pin);
            check(witness != null && witness.length >= 40);
            check(media.staticWitness(bundle, helper.toString(), "0".repeat(64)) == null);
            Process child = new ProcessBuilder(helper.toString()).start();
            try {
                check(media.runningMatches(bundle, helper.toString(), pin, child.pid(), witness));
                check(!media.runningMatches(bundle, helper.toString(), pin, ProcessHandle.current().pid(), witness));
            } finally {
                child.getOutputStream().close();
                if (!child.waitFor(5, TimeUnit.SECONDS)) {
                    child.destroyForcibly(); check(child.waitFor(5, TimeUnit.SECONDS));
                }
            }
            System.load(nativeDir.resolve("libAutPlaySourceAccess.dylib").toString());
            Path managed = Files.createTempDirectory("autplay-community-source-").toRealPath();
            var sources = SourceAccessJni.INSTANCE;
            long source = sources.open(1, pin, managed.toString());
            try { sources.verify(source); } finally { sources.close(source); Files.delete(managed); }
        }
        if (args[0].startsWith("owner-")) {
            var owner = new DesktopSecureStateOwner(MacSecureStateApiKt.macSecureStateApi(
                new MacSecureStateIdentity(pin, "community-keychain-1")), Instant::now);
            try {
                var control = owner.open().get(30, TimeUnit.SECONDS);
                if (args[0].equals("owner-create")) {
                    check(control == null);
                    control = owner.initializeEmpty(false).get(30, TimeUnit.SECONDS);
                }
                check(control != null && control.getRevision() == 0 && control.getSelected().isEmpty());
                check(owner.snapshot().get(30, TimeUnit.SECONDS).equals(control));
            } finally { owner.close().get(30, TimeUnit.SECONDS); }
            System.out.println("AUTPLAY_COMMUNITY_PROBE_PASSED " + args[0]);
            return;
        }
        MacSecureStateJni api = MacSecureStateJni.INSTANCE;
        long handle = api.open(1, pin, "community-keychain-1");
        try {
            if (args[0].equals("create")) {
                check(api.readControl(handle) == null);
                api.initializeControl(handle, control(0, "CREATING"));
                api.stageValue(handle, VALUE, PAYLOAD);
                check(api.createNewKey(handle, TAG).length == 65);
                api.publishControl(handle, control(0, "CREATING"), control(1, "BOUND"));
            }
            check(Arrays.equals(api.readControl(handle), control(1, "BOUND")));
            check(Arrays.equals(api.readValue(handle, VALUE), PAYLOAD));
            byte[] point = api.publicKey(handle, TAG);
            byte[] prefix = HexFormat.of().parseHex("3059301306072a8648ce3d020106082a8648ce3d030107034200");
            byte[] spki = Arrays.copyOf(prefix, prefix.length + point.length);
            System.arraycopy(point, 0, spki, prefix.length, point.length);
            PublicKey key = KeyFactory.getInstance("EC").generatePublic(new X509EncodedKeySpec(spki));
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(PAYLOAD);
            byte[] proof = api.sign(handle, TAG, 7, digest);
            Signature verifier = Signature.getInstance("SHA256withECDSA");
            verifier.initVerify(key);
            verifier.update("autplay:device-compatibility:session:rotate:v2\n".getBytes(StandardCharsets.US_ASCII));
            verifier.update(digest);
            check(verifier.verify(proof));
            checkNonExportable(pin);
            if (args[0].equals("lock")) {
                lockKeychain();
                try { api.readControl(handle); throw new AssertionError("LOCK_BYPASSED"); }
                catch (IllegalStateException expected) { check("SECURE_STATE_LOCKED".equals(expected.getMessage())); }
                try { api.sign(handle, TAG, 7, digest); throw new AssertionError("LOCK_BYPASSED"); }
                catch (IllegalStateException expected) { check("SECURE_STATE_LOCKED".equals(expected.getMessage())); }
            }
        } finally { api.close(handle); }
        System.out.println("AUTPLAY_COMMUNITY_PROBE_PASSED " + args[0]);
    }
}
