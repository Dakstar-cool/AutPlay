package app.autplay.application.portableaccount

import app.autplay.data.network.readCancellable
import app.autplay.data.network.withAutPlayRedirectPolicy
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5SessionRotationClient
import app.autplay.data.security.RefreshingSessionCredentials
import app.autplay.domain.ServerProfileId
import java.io.IOException
import java.nio.ByteBuffer
import java.time.Duration
import java.time.Instant
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.erdtman.jcs.JsonCanonicalizer

data class PortableDataCursor(val time: String, val id: String)
data class PortableDataPage(val accountId: String, val records: List<PortableDataRecord>, val cursor: PortableDataCursor?, val next: PortableDataCursor?)

interface PortableAccountDataTransport {
    suspend fun upload(accountId: String, records: List<PortableDataRecord>)
    suspend fun download(accountId: String, source: String, after: PortableDataCursor): PortableDataPage
}

/** Error contains a stable code only; HTTP bodies and account metadata are never diagnostic text. */
class PortableAccountDataException(val code: String, val transient: Boolean = false) : IOException(code)

class OkHttpPortableAccountDataTransport(
    apiBaseUrl: String,
    private val profileId: ServerProfileId,
    credentials: CredentialStore,
    rotation: M5SessionRotationClient? = null,
    client: OkHttpClient = OkHttpClient(),
    private val beforeRequest: suspend () -> Unit = {},
) : PortableAccountDataTransport {
    private val client = client.newBuilder().cache(null).callTimeout(Duration.ofSeconds(20)).build().withAutPlayRedirectPolicy()
    private val sessions = RefreshingSessionCredentials(apiBaseUrl, credentials, this.client, m5Rotation = rotation)
    private val endpoint = (apiBaseUrl.trimEnd('/') + "/portable-accounts/records").toHttpUrl()

    override suspend fun upload(accountId: String, records: List<PortableDataRecord>) {
        PortableAccountDataCodec.uuid(accountId); require(records.size in 1..32)
        val body = buildJsonObject { put("account_id", accountId); put("records", JsonArray(records.map { PortableAccountDataCodec.decode(it.wire()).wire() })) }
        val result = request(Request.Builder().url(endpoint), body)
        require(result.keys == setOf("account_id", "accepted") && result["account_id"]?.jsonPrimitive?.content == accountId) { "ACCOUNT_DATA_RESPONSE_INVALID" }
        val accepted = result["accepted"] as? JsonArray ?: error("ACCOUNT_DATA_RESPONSE_INVALID")
        require(accepted.size == records.size) { "ACCOUNT_DATA_RESPONSE_INVALID" }
        // The server may acknowledge a preexisting logical ID when an alias joins two copies.
        accepted.forEach { PortableAccountDataCodec.uuid(it.jsonPrimitive.content) }
    }

    override suspend fun download(accountId: String, source: String, after: PortableDataCursor): PortableDataPage {
        PortableAccountDataCodec.uuid(accountId); require(source in PortableAccountDataCodec.sources)
        validateCursor(after)
        val url = endpoint.newBuilder().addQueryParameter("source", source).addQueryParameter("after_time", after.time).addQueryParameter("after_id", after.id).build()
        val result = request(Request.Builder().url(url), null)
        return decodePage(accountId, result, after)
    }

    private suspend fun request(builder: Request.Builder, body: JsonObject?): JsonObject = withContext(Dispatchers.IO) {
        beforeRequest()
        var access = sessions.access(profileId)
        try {
            var response = fetch(builder, body, access.token)
            if (response.first == 401) {
                val generation = access.generation; access.close()
                beforeRequest(); access = sessions.refreshAfterRejection(profileId, generation)
                response = fetch(builder, body, access.token)
            }
            beforeRequest()
            val (status, result) = response
            if (status !in 200..299) {
                val raw = ((result["error"] as? JsonObject)?.get("code") as? JsonPrimitive)?.content
                val code = raw?.takeIf { Regex("[a-z][a-z0-9_]{1,80}").matches(it) } ?: "account_data_server_unavailable"
                throw PortableAccountDataException(code, status == 429 || status >= 500)
            }
            result
        } finally { access.close() }
    }

    private suspend fun fetch(builder: Request.Builder, body: JsonObject?, token: ByteArray): Pair<Int, JsonObject> {
        beforeRequest()
        builder.header("Accept", "application/json").header("Cache-Control", "no-store").header("Pragma", "no-cache")
            .header("Authorization", "Bearer " + token.toString(Charsets.UTF_8))
        if (body == null) builder.get() else {
            val bytes = canonicalAccountRequest(body)
            require(bytes.size <= PortableAccountDataCodec.MAX_PAYLOAD_BYTES) { "ACCOUNT_DATA_LIMIT_REACHED" }
            builder.post(bytes.toRequestBody("application/json".toMediaType()))
        }
        return client.newCall(builder.build()).readCancellable { response ->
            val source = response.body.source()
            require(!source.request(PortableAccountDataCodec.MAX_PAYLOAD_BYTES.toLong() + 1)) { "ACCOUNT_DATA_RESPONSE_INVALID" }
            val bytes = source.readByteArray()
            try {
                require(bytes.size in 2..PortableAccountDataCodec.MAX_PAYLOAD_BYTES) { "ACCOUNT_DATA_RESPONSE_INVALID" }
                val text = Charsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(bytes)).toString()
                JsonCanonicalizer(text)
                response.code to Json.parseToJsonElement(text).jsonObject
            } finally { bytes.fill(0) }
        }
    }

    companion object {
        fun decodePage(accountId: String, result: JsonObject, after: PortableDataCursor): PortableDataPage {
            require(result.keys == setOf("account_id", "records", "cursor", "next") && result["account_id"]?.jsonPrimitive?.content == accountId) { "ACCOUNT_DATA_RESPONSE_INVALID" }
            val records = (result["records"] as? JsonArray)?.map { PortableAccountDataCodec.decode(it.jsonObject) } ?: error("ACCOUNT_DATA_RESPONSE_INVALID")
            // Legacy history can contain both raw aliases of a single portable logical record.
            // Keep its raw-source cursor and merge the repeated logical IDs transactionally.
            require(records.size <= 32) { "ACCOUNT_DATA_RESPONSE_INVALID" }
            fun cursor(key: String): PortableDataCursor? {
                if (result[key] == JsonNull) return null
                val c = result[key] as? JsonObject ?: error("ACCOUNT_DATA_RESPONSE_INVALID")
                require(c.keys == setOf("time", "id")) { "ACCOUNT_DATA_RESPONSE_INVALID" }
                return PortableDataCursor(c["time"]!!.jsonPrimitive.content, c["id"]!!.jsonPrimitive.content).also(::validateCursor)
            }
            val cursor = cursor("cursor"); val next = cursor("next")
            require((records.isEmpty()) == (cursor == null) && (next == null || records.size == 32 && next == cursor)) { "ACCOUNT_DATA_RESPONSE_INVALID" }
            cursor?.let {
                val comparison = Instant.parse(it.time).compareTo(Instant.parse(after.time))
                require(comparison > 0 || comparison == 0 && it.id > after.id) { "ACCOUNT_DATA_CURSOR_INVALID" }
            }
            return PortableDataPage(accountId, records, cursor, next)
        }

        private fun validateCursor(cursor: PortableDataCursor) {
            require(cursor.time.length in 20..40) { "ACCOUNT_DATA_CURSOR_INVALID" }
            Instant.parse(cursor.time); PortableAccountDataCodec.uuid(cursor.id)
        }
    }
}
