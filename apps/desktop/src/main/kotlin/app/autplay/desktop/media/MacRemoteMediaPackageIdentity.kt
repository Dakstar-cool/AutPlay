package app.autplay.desktop.media

import app.autplay.desktop.security.MacSecureStateIdentity
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path

/** Public code identity only. JNI shares the already identity-checked K library, never custody. */
internal class MacRemoteMediaPackageIdentity(private val identity: MacSecureStateIdentity) : RemoteMediaPackageIdentity {
    private class Witness(val bundle: Path, val executable: Path, val bytes: ByteArray,
        val fileKey: Any?, val length: Long) : RemoteMediaPackageWitness {
        override fun toString(): String = "RemoteMediaPackageWitness(<redacted>)"
    }
    override fun verify(canonicalBundle: Path, canonicalExecutable: Path): RemoteMediaPackageWitness? = try {
        if (System.getProperty("os.name") != "Mac OS X" || System.getProperty("os.arch") !in setOf("aarch64", "arm64")) null
        else {
            val attributes = Files.readAttributes(canonicalExecutable, java.nio.file.attribute.BasicFileAttributes::class.java, NOFOLLOW_LINKS)
            val bytes = MacRemoteMediaIdentityJni.staticWitness(canonicalBundle.toString(), canonicalExecutable.toString(), identity.developerTeam)
            if (bytes == null || bytes.size !in 40..256) null
            else Witness(canonicalBundle, canonicalExecutable, bytes, attributes.fileKey(), attributes.size())
        }
    } catch (_: Exception) { null } catch (_: LinkageError) { null }
    override fun stillMatches(witness: RemoteMediaPackageWitness, process: Process, canonicalExecutable: Path): Boolean = try {
        val expected = witness as? Witness
        if (expected == null || !process.isAlive || canonicalExecutable != expected.executable) false
        else {
            val attributes = Files.readAttributes(canonicalExecutable, java.nio.file.attribute.BasicFileAttributes::class.java, NOFOLLOW_LINKS)
            attributes.fileKey() == expected.fileKey && attributes.size() == expected.length &&
                MacRemoteMediaIdentityJni.runningMatches(expected.bundle.toString(), canonicalExecutable.toString(),
                    identity.developerTeam, process.pid(), expected.bytes)
        }
    } catch (_: Exception) { false } catch (_: LinkageError) { false }
}

/** Linked into libAutPlaySecureState, separate fixed public verification surface; no key/secret API. */
internal object MacRemoteMediaIdentityJni {
    external fun staticWitness(bundle: String, executable: String, developerTeam: String): ByteArray?
    external fun runningMatches(bundle: String, executable: String, developerTeam: String, pid: Long, witness: ByteArray): Boolean
}
