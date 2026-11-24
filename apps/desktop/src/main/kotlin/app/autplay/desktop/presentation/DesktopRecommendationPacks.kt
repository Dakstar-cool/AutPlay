package app.autplay.desktop.presentation

import app.autplay.application.desktop.account.AccountUuid
import app.autplay.application.recommendation.*
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.nio.file.attribute.PosixFilePermissions
import kotlinx.serialization.json.*

internal data class PackScope(val store: AccountUuid, val scope: AccountUuid, val owner: RecommendationPackOwner)

internal class DesktopRecommendationPacks(private val root: Path) {
    private fun directory(scope: PackScope, create: Boolean): Path? {
        var path = root
        check(path.toRealPath() == path.toAbsolutePath().normalize())
        val owner = scope.owner
        for (part in listOf("recommendation-packs", scope.store.value, scope.scope.value,
                AccountUuid(owner.profile).value, AccountUuid(owner.user).value, AccountUuid(owner.device).value)) {
            path = path.resolve(part)
            if (create && !Files.exists(path, NOFOLLOW_LINKS)) {
                val attrs = if (Files.getFileStore(path.parent).supportsFileAttributeView("posix"))
                    arrayOf(PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------"))) else emptyArray()
                try { Files.createDirectory(path, *attrs) } catch (_: java.nio.file.FileAlreadyExistsException) { }
            }
            if (!Files.exists(path, NOFOLLOW_LINKS)) return null
            check(Files.isDirectory(path, NOFOLLOW_LINKS) && path.toRealPath() == path)
        }
        return path
    }

    private fun name(mode: String): String {
        require(mode in setOf("GENERAL", "WORK", "SLEEP"))
        return "$mode.json"
    }

    fun read(scope: PackScope, mode: String, now: Long, expiry: OfflinePackExpiryPolicy = OfflinePackExpiryPolicy.FreshOnly): DecodedOfflinePack? {
        val file = directory(scope, false)?.resolve(name(mode)) ?: return null
        if (!Files.isRegularFile(file, NOFOLLOW_LINKS)) return null
        val bytes = Files.newInputStream(file, NOFOLLOW_LINKS).use { it.readNBytes(MAX_RESPONSE + 1) }
        if (bytes.size > MAX_RESPONSE) return null
        return try { decode(Json.parseToJsonElement(bytes.decodeToString(throwOnInvalidSequence = true)).jsonObject, scope, mode, now, expiry) }
        catch (_: IllegalArgumentException) { null }
        catch (_: java.nio.charset.CharacterCodingException) { null }
    }

    fun store(scope: PackScope, mode: String, document: JsonObject, now: Long, current: () -> Boolean): DecodedOfflinePack {
        val decoded = decode(document, scope, mode, now, OfflinePackExpiryPolicy.FreshOnly)
        val bytes = document.toString().encodeToByteArray()
        require(bytes.size <= MAX_RESPONSE)
        check(current())
        val dir = checkNotNull(directory(scope, true))
        val temp = Files.createTempFile(dir, ".pack-", ".tmp")
        try {
            Files.write(temp, bytes)
            check(current())
            Files.move(temp, dir.resolve(name(mode)), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        } finally { Files.deleteIfExists(temp) }
        return decoded
    }

    private fun decode(document: JsonObject, scope: PackScope, mode: String, now: Long, expiry: OfflinePackExpiryPolicy): DecodedOfflinePack {
        fun field(key: String): JsonPrimitive = document[key] as? JsonPrimitive ?: throw IllegalArgumentException("PACK_FIELD_INVALID")
        fun text(key: String): String = field(key).also { require(it.isString) }.content
        fun number(key: String): Long = field(key).also { require(!it.isString) }.long
        val download = DownloadedRecommendationPack(text("offline_pack_id"), text("recommendation_request_id"),
            field("payload_version").also { require(!it.isString) }.int, text("payload_encoding"), text("payload_base64"), text("payload_sha256"),
            number("created_at_ms"), number("expires_at_ms"))
        val envelope = BoundRecommendationPackCodec.entityFromDownload(download, scope.owner)
        return BoundRecommendationPackCodec.decode(envelope, scope.owner, now, expiry).also {
            require(it.request.context == mode && !it.request.shadow)
        }
    }

    companion object { const val MAX_RESPONSE = 720_896 }
}
