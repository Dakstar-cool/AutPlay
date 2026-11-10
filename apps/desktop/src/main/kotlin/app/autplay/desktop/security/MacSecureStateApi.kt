package app.autplay.desktop.security

import app.autplay.application.securestate.*
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import kotlinx.serialization.json.*

/** Explicit signed build identity. Community mode never falls back from Developer ID. */
public class MacSecureStateIdentity(public val developerTeam: String, public val provisioningProfileUuid: String) {
    public val community: Boolean get() = provisioningProfileUuid == "community-keychain-1"
    init {
        if (community) secureRequire(developerTeam.matches(Regex("[0-9a-f]{64}")), SecureStateStatus.IDENTITY_UNAVAILABLE)
        else {
            secureRequire(developerTeam.length == 10 && developerTeam.all { it in 'A'..'Z' || it in '0'..'9' }, SecureStateStatus.IDENTITY_UNAVAILABLE)
            secureRequire(provisioningProfileUuid.length == 36 && runCatching { java.util.UUID.fromString(provisioningProfileUuid) }.isSuccess, SecureStateStatus.IDENTITY_UNAVAILABLE)
        }
    }
    override fun toString(): String = "MacSecureStateIdentity(<redacted>)"
}

/**
 * Canonical launcher-relative bundle only. No resource-dir property, path override, extraction,
 * PATH or current-directory search. Native preflight validates main code, library and profile
 * before opening the interprocess lock or Keychain namespace.
 */
public fun macSecureStateApi(identity: MacSecureStateIdentity): SecureNativePort = MacSecureStateLibrary.load(identity)

private object MacSecureStateLibrary {
    private var loaded: Boolean = false
    @Synchronized fun load(identity: MacSecureStateIdentity): SecureNativePort {
        try {
            secureRequire(System.getProperty("os.name") == "Mac OS X" && System.getProperty("os.arch") in setOf("aarch64", "arm64"), SecureStateStatus.IDENTITY_UNAVAILABLE)
            val command = ProcessHandle.current().info().command().orElseThrow { SecureStateFailure(SecureStateStatus.IDENTITY_UNAVAILABLE) }
            val executable = Path.of(command).toRealPath()
            val contents = executable.parent?.parent ?: throw SecureStateFailure(SecureStateStatus.IDENTITY_UNAVAILABLE)
            secureRequire(executable.parent.fileName.toString() == "MacOS" && contents.fileName.toString() == "Contents", SecureStateStatus.IDENTITY_UNAVAILABLE)
            val bundle = contents.parent
            secureRequire(bundle.fileName.toString().endsWith(".app") && bundle == bundle.toRealPath(), SecureStateStatus.IDENTITY_UNAVAILABLE)
            val library = contents.resolve("app/resources/native/libAutPlaySecureState.dylib")
            secureRequire(Files.isRegularFile(library, NOFOLLOW_LINKS) && library.toRealPath() == library && library.startsWith(bundle), SecureStateStatus.IDENTITY_UNAVAILABLE)
            if (!loaded) { System.load(library.toString()); loaded = true }
            secureRequire(MacSecureStateJni.abiVersion() == 1, SecureStateStatus.IDENTITY_UNAVAILABLE)
            return MacSecureStateApi(identity)
        } catch (failure: SecureStateFailure) { throw failure }
        catch (_: Exception) { throw SecureStateFailure(SecureStateStatus.IDENTITY_UNAVAILABLE) }
        catch (_: LinkageError) { throw SecureStateFailure(SecureStateStatus.IDENTITY_UNAVAILABLE) }
    }
}

private class MacSecureStateApi(private val identity: MacSecureStateIdentity) : SecureNativePort {
    override fun open(): Long = guarded { MacSecureStateJni.open(1, identity.developerTeam, identity.provisioningProfileUuid).also { secureRequire(it > 0, SecureStateStatus.IDENTITY_UNAVAILABLE) } }
    override fun inventory(handle: Long): SecureNamespaceInventory = guarded {
        val bytes = MacSecureStateJni.inventory(handle)
        try {
            val root = SecureJson.parse(bytes, 32768, canonical = false)
            root.exact("has_control", "values", "keys")
            val values = root.array("values").map {
                val obj = it as JsonObject; obj.exact("id", "length"); val id = obj.string("id"); canonicalUuid(id)
                val length = obj.number("length"); secureRequire(length <= SecureStateLimits.VALUE)
                id to length.toInt()
            }
            val keys = root.array("keys").map { (it as JsonPrimitive).also { secureRequire(it.isString) }.content.also(::deviceKeyTag) }
            secureRequire(values.size <= 64 && values.map { it.first }.distinct().size == values.size && keys.size <= 32 && keys.distinct().size == keys.size)
            SecureNamespaceInventory(values.toMap(), keys.toSet(), root.boolean("has_control"))
        } finally { bytes.fill(0) }
    }
    override fun readControl(handle: Long): ByteArray? = guarded { MacSecureStateJni.readControl(handle) }
    override fun initializeControl(handle: Long, bytes: ByteArray): Unit = guarded { requireControl(bytes); MacSecureStateJni.initializeControl(handle, bytes) }
    override fun publishControl(handle: Long, expected: ByteArray, next: ByteArray): Unit = guarded { requireControl(expected); requireControl(next); MacSecureStateJni.publishControl(handle, expected, next) }
    override fun readValue(handle: Long, valueId: String): ByteArray? = guarded { canonicalUuid(valueId); MacSecureStateJni.readValue(handle, valueId) }
    override fun stageValue(handle: Long, valueId: String, bytes: ByteArray): Unit = guarded { canonicalUuid(valueId); secureRequire(bytes.size <= SecureStateLimits.VALUE); MacSecureStateJni.stageValue(handle, valueId, bytes) }
    override fun deleteValue(handle: Long, valueId: String): Unit = guarded { canonicalUuid(valueId); MacSecureStateJni.deleteValue(handle, valueId) }
    override fun createNewDeviceKey(handle: Long, tag: String): ByteArray = guarded { deviceKeyTag(tag); DeviceSignatureCodec.spkiFromPoint(MacSecureStateJni.createNewKey(handle, tag)) }
    override fun publicDeviceKey(handle: Long, tag: String): ByteArray = guarded { deviceKeyTag(tag); DeviceSignatureCodec.spkiFromPoint(MacSecureStateJni.publicKey(handle, tag)) }
    override fun signDeviceDigest(handle: Long, tag: String, operation: DeviceProofOperation, digest: ByteArray): ByteArray = guarded {
        deviceKeyTag(tag); secureRequire(digest.size == 32); MacSecureStateJni.sign(handle, tag, operation.ordinal, digest)
    }
    override fun deleteRetiredDeviceKey(handle: Long, tag: String): Unit = guarded { deviceKeyTag(tag); MacSecureStateJni.deleteKey(handle, tag) }
    override fun close(handle: Long): Unit = guarded { MacSecureStateJni.close(handle) }
    private fun requireControl(bytes: ByteArray) { secureRequire(bytes.size <= SecureStateLimits.CONTROL); SecureControlCodec.decode(bytes) }
    private inline fun <T> guarded(block: () -> T): T = try { block() }
    catch (failure: SecureStateFailure) { throw failure }
    catch (failure: Exception) {
        val status = SecureStateStatus.entries.find { failure.message == "SECURE_STATE_$it" } ?: SecureStateStatus.UNAVAILABLE
        throw SecureStateFailure(status)
    }
}

/** Exact JNI ABI1 descriptors; no overloads, arbitrary service/query/domain or private-key API. */
internal object MacSecureStateJni {
    external fun abiVersion(): Int
    external fun open(abi: Int, developerTeam: String, profileUuid: String): Long
    external fun inventory(handle: Long): ByteArray
    external fun readControl(handle: Long): ByteArray?
    external fun initializeControl(handle: Long, bytes: ByteArray)
    external fun publishControl(handle: Long, expected: ByteArray, next: ByteArray)
    external fun readValue(handle: Long, valueId: String): ByteArray?
    external fun stageValue(handle: Long, valueId: String, bytes: ByteArray)
    external fun deleteValue(handle: Long, valueId: String)
    external fun createNewKey(handle: Long, tag: String): ByteArray
    external fun publicKey(handle: Long, tag: String): ByteArray
    external fun sign(handle: Long, tag: String, operation: Int, digest: ByteArray): ByteArray
    external fun deleteKey(handle: Long, tag: String)
    external fun close(handle: Long)
}
