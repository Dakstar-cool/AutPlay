package app.autplay.desktop

import app.autplay.desktop.security.MacSecureStateIdentity
import app.autplay.desktop.sourceaccess.MacSourceAccessIdentity
import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.util.UUID

/** Public packaging inputs only. Native code independently verifies sealed code and profile. */
internal class DesktopSignedBundle(val bundle: Path, developerTeam: String, private val profileUuid: String?) {
    val sourceIdentity: MacSourceAccessIdentity = MacSourceAccessIdentity(developerTeam)
    val identity: MacSecureStateIdentity? get() = profileUuid?.let {
        MacSecureStateIdentity(sourceIdentity.developerTeam, it)
    }
    override fun toString(): String = "DesktopSignedBundle(<redacted>)"
}

/** No environment, runtime property, working directory or user-file identity override. */
internal fun readDesktopSignedBundle(): DesktopSignedBundle? = try {
    if (System.getProperty("os.name") != "Mac OS X" ||
        System.getProperty("os.arch") !in setOf("aarch64", "arm64")) null
    else {
        val command = ProcessHandle.current().info().command().orElse(null)
        if (command == null) null else {
            val executable = Path.of(command).toRealPath()
            val contents = executable.parent?.parent
            val bundle = contents?.parent
            if (contents == null || bundle == null || executable.parent.fileName.toString() != "MacOS" ||
                contents.fileName.toString() != "Contents" || !bundle.fileName.toString().endsWith(".app") ||
                bundle != bundle.toRealPath()) null
            else {
                val record = contents.resolve("app/resources/native/AutPlaySignedIdentity")
                if (!Files.isRegularFile(record, NOFOLLOW_LINKS) || record != record.toRealPath() ||
                    !record.startsWith(bundle)) null
                else FileChannel.open(record, StandardOpenOption.READ, NOFOLLOW_LINKS).use { input ->
                    if (input.size() !in 1..256) null else {
                        val buffer = ByteBuffer.allocate(257)
                        while (buffer.hasRemaining() && input.read(buffer) >= 0) { }
                        if (buffer.position() !in 1..256 || input.size() != buffer.position().toLong()) null
                        else {
                            buffer.flip()
                            val text = StandardCharsets.UTF_8.newDecoder()
                                .onMalformedInput(CodingErrorAction.REPORT)
                                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(buffer).toString()
                            val fields = text.split('\n')
                            if (fields.size != 4 || fields[0] != "autplay-signed-identity-1" ||
                                fields[3].isNotEmpty() || (fields[2] != "-" && (fields[2].length != 36 ||
                                    !UUID.fromString(fields[2]).toString().equals(fields[2], ignoreCase = true)))) null
                            else DesktopSignedBundle(bundle, fields[1], fields[2].takeUnless { it == "-" })
                        }
                    }
                }
            }
        }
    }
} catch (_: Exception) { null } catch (_: LinkageError) { null }
