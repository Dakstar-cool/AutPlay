package app.autplay.desktop.sync

import app.autplay.application.desktop.account.*
import app.autplay.application.securestate.*
import app.autplay.desktop.account.*
import java.io.ByteArrayOutputStream
import java.net.*
import java.security.KeyStore
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.time.Instant
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.util.UUID
import java.util.concurrent.*
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import javax.net.ssl.*
import kotlinx.serialization.json.*

/** The data plane has its own closed registry. D1's 65536-byte control limit is unchanged. */
internal sealed class DomainRoute(val method: String, val path: String, val requestCap: Int,
    val responseCap: Int, val query: Map<String, String> = emptyMap()) {
    class Push : DomainRoute("POST", "/sync/push", 8_388_608, 8_388_608)
    class Pull(query: Map<String, String>) : DomainRoute("GET", "/sync/pull", 0, 8_388_608, query) {
        init { require(query.keys == setOf("protocol_version", "device_id", "server_profile_id", "journal_epoch",
            "limit", "catalog_projection_version", "capabilities") + (if ("cursor" in query) setOf("cursor") else emptySet()))
            require(query["limit"]?.toIntOrNull()?.let { it in 1..100 } == true)
            require(query["catalog_projection_version"] == "1" && query["capabilities"] == "CATALOG_ARTIST_ID_V1") }
    }
    class Bootstrap : DomainRoute("POST", "/sync/bootstrap", 16_384, 8_388_608)
    class Binding : DomainRoute("POST", "/sync-binding/v2/bind", 16_384, 16_384)
    class Status(query: Map<String, String>) : DomainRoute("GET", "/sync/status", 0, 65_536, query) {
        init { require(query.keys == setOf("protocol_version", "device_id", "server_profile_id", "journal_epoch")) }
    }
    class PortableRead(source: String, time: String, id: AccountUuid) : DomainRoute("GET", "/portable-accounts/records", 0,
        524_288, mapOf("source" to source, "after_time" to time, "after_id" to id.value)) {
        init { require(source in AccountPortableV1.sources && time.length <= 40 && Instant.parse(time) >= Instant.EPOCH) }
    }
    class PortableWrite : DomainRoute("POST", "/portable-accounts/records", 524_288, 65_536)
    class PortableCurrent : DomainRoute("GET", "/portable-accounts/current", 0, 16_384)
    class Statistics : DomainRoute("GET", "/social/profile-statistics/owner", 0, 65_536)
    class CatalogView(query: CanonicalQuery) : DomainRoute("GET", query.path, 0, 262_144, query.parameters())
    class ImportReport(job: AccountUuid, after: String?) : DomainRoute("GET", "/imports/${job.value}", 0, 524_288,
        mapOf("limit" to "100") + (after?.let { mapOf("after" to it) } ?: emptyMap())) {
        init { require(after == null || after.length in 1..1000) }
    }
    class ImportReviewContext(job: AccountUuid, entry: AccountUuid) : DomainRoute("GET",
        "/imports/${job.value}/entries/${entry.value}/review-context", 0, 1_048_576)
    class ImportReview(job: AccountUuid, entry: AccountUuid) : DomainRoute("POST",
        "/imports/${job.value}/entries/${entry.value}/review", 4096, 4096)
    class ImportControl(job: AccountUuid, action: String) : DomainRoute("POST", "/imports/${job.value}/$action", 0, 4096) {
        init { require(action in setOf("cancel", "resume")) }
    }
    init {
        require(path.startsWith('/') && '?' !in path && '#' !in path && query.size <= 8)
        require(query.all { (name, value) -> name.length <= 40 && value.length <= 8192 &&
            value.none { it.code < 32 || it.code == 127 } })
        if ("protocol_version" in query) {
            require(query["protocol_version"] == "1")
            listOf("device_id", "server_profile_id", "journal_epoch").forEach { canonicalUuid(requireNotNull(query[it])) }
        }
    }
    override fun toString(): String = "DomainRoute(<redacted>)"
}

internal class DomainResponse(val status: Int, val retryAfterMs: Long, val bytes: ByteArray) : AutoCloseable {
    override fun close(): Unit = bytes.fill(0)
    override fun toString(): String = "DomainResponse(<redacted>)"
}
internal class DomainIoTicket(val id: UUID, private val actual: CompletableFuture<DomainResponse>,
    val deadlineReached: CompletableFuture<Unit>) {
    fun settled(): CompletionStage<DomainResponse> = actual.minimalCompletionStage()
    override fun toString(): String = "DomainIoTicket(<redacted>)"
}

/** One retained IO owner. Neither caller cancellation, expiry nor disconnect proves settlement. */
public class DesktopDomainHttp : AutoCloseable {
    private val closed = AtomicBoolean(false)
    private val generation = AtomicLong(1)
    private val workers = ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS, ArrayBlockingQueue<Runnable>(2),
        { task -> Thread(task, "autplay-account-data").apply { isDaemon = true } }, ThreadPoolExecutor.AbortPolicy())
    private val timer = ScheduledThreadPoolExecutor(1) { task -> Thread(task, "autplay-account-deadline").apply { isDaemon = true } }
    private val retained = ConcurrentHashMap<UUID, DomainIoTicket>()

    internal fun start(trust: ConfirmedAccountTransport, route: DomainRoute, body: ByteArray,
        bearer: SecretBytes, beforeSend: () -> Unit, deadlineMs: Long = 20_000): DomainIoTicket {
        require(deadlineMs in 100..20_000 && body.size <= route.requestCap && (route.method != "GET" || body.isEmpty()))
        check(!closed.get()) { "ACCOUNT_DATA_CLOSED" }
        val access = bearer.copy(); val request = body.copyOf()
        require(access.size in 1..8192 && access.all { it.toInt() in 33..126 })
        val captured = generation.get(); val id = UUID.randomUUID()
        val actual = CompletableFuture<DomainResponse>(); val elapsed = CompletableFuture<Unit>()
        val ticket = DomainIoTicket(id, actual, elapsed)
        retained[id] = ticket
        val socket = java.util.concurrent.atomic.AtomicReference<HttpsURLConnection?>()
        val started = System.nanoTime()
        val timeout = try { timer.schedule({ elapsed.complete(Unit); socket.get()?.disconnect() }, deadlineMs, TimeUnit.MILLISECONDS) }
        catch (_: RejectedExecutionException) {
            request.fill(0); access.fill(0); retained.remove(id)
            actual.completeExceptionally(AccountControlException(AccountControlFailure.CLOSED)); return ticket
        }
        try {
            workers.execute {
                var response: DomainResponse? = null
                var failure: AccountControlException? = null
                try {
                    fun current() { check(!closed.get() && captured == generation.get()) { "ACCOUNT_DATA_RETIRED" } }
                    fun remaining(): Int {
                        val left = deadlineMs - TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started)
                        check(left > 0) { "ACCOUNT_DATA_DEADLINE" }; return minOf(5000, left.toInt())
                    }
                    current(); beforeSend(); remaining()
                    check(CookieHandler.getDefault() == null && Authenticator.getDefault() == null && ResponseCache.getDefault() == null) {
                        "ACCOUNT_DATA_GLOBAL_HANDLER_REFUSED"
                    }
                    val query = route.query.entries.joinToString("&") { (key, value) ->
                        URLEncoder.encode(key, Charsets.UTF_8) + "=" + URLEncoder.encode(value, Charsets.UTF_8) }
                    val destination = URI(trust.api.toASCIIString() + "/api/v1" + route.path + if (query.isEmpty()) "" else "?$query")
                    val wire = destination.toURL().openConnection(Proxy.NO_PROXY) as HttpsURLConnection
                    socket.set(wire)
                    wire.instanceFollowRedirects = false; wire.useCaches = false; wire.allowUserInteraction = false
                    wire.requestMethod = route.method; wire.connectTimeout = remaining(); wire.readTimeout = remaining()
                    wire.sslSocketFactory = DomainTls.factory(trust.apiTrust, requireNotNull(destination.host))
                    // JSSE's HTTPS endpoint identification also runs for explicitly confirmed SPKI.
                    wire.setRequestProperty("Accept", "application/json"); wire.setRequestProperty("Accept-Encoding", "identity")
                    wire.setRequestProperty("Cache-Control", "no-store"); wire.setRequestProperty("Pragma", "no-cache")
                    current(); beforeSend()
                    wire.setRequestProperty("Authorization", "Bearer " + access.toString(Charsets.US_ASCII))
                    if (body.isNotEmpty()) {
                        wire.doOutput = true; wire.setFixedLengthStreamingMode(request.size)
                        wire.setRequestProperty("Content-Type", "application/json")
                        wire.outputStream.use { it.write(request); it.flush() }
                    }
                    val status = wire.responseCode; remaining()
                    check(status !in 300..399) { "ACCOUNT_DATA_REDIRECT_REFUSED" }
                    val headers = wire.headerFields.filterKeys { it != null }
                    check(headers.size <= 64 && headers.entries.sumOf { it.key.length + it.value.sumOf(String::length) } <= 16_384)
                    fun header(name: String): String? = headers.entries.filter { it.key.equals(name, true) }
                        .flatMap { it.value }.also { check(it.size <= 1 && it.all { value -> value.length <= 2048 && value.none { c -> c.code < 32 || c.code == 127 } }) }.singleOrNull()
                    check(header("Content-Type")?.substringBefore(';')?.trim()?.lowercase() == "application/json")
                    check(header("Content-Encoding").let { it == null || it.equals("identity", true) })
                    if (route is DomainRoute.Binding || route is DomainRoute.ImportReviewContext) {
                        check(header("Cache-Control")?.split(',')?.any { it.trim().equals("no-store", true) } == true)
                        check(header("Pragma")?.split(',')?.any { it.trim().equals("no-cache", true) } == true)
                        if (route is DomainRoute.ImportReviewContext)
                            check(header("Cache-Control")?.split(',')?.any { it.trim().equals("private", true) } == true)
                    }
                    val maximum = if (status in 200..299) route.responseCap else 65_536
                    check(wire.contentLengthLong in -1..maximum.toLong())
                    val buffer = ByteArray(8192); val collected = ByteArrayOutputStream()
                    try {
                        val input = if (status in 200..299) wire.inputStream else wire.errorStream
                        if (input != null) input.use {
                            while (true) {
                                wire.readTimeout = remaining(); val count = it.read(buffer)
                                if (count < 0) break
                                check(collected.size().toLong() + count <= maximum); collected.write(buffer, 0, count)
                            }
                        }
                        remaining()
                        val bytes = collected.toByteArray()
                        // The exact result is retained even if the lifecycle fenced application meanwhile.
                        response = DomainResponse(status, retryAfter(header("Retry-After")), bytes)
                    } finally { buffer.fill(0); collected.reset() }
                } catch (_: SSLException) { failure = AccountControlException(AccountControlFailure.TRUST_REQUIRED) }
                catch (_: Exception) { failure = AccountControlException(AccountControlFailure.TRANSPORT_UNAVAILABLE) }
                finally {
                    try { socket.getAndSet(null)?.disconnect() } finally {
                        request.fill(0); access.fill(0); timeout.cancel(false)
                        if (failure != null) { response?.close(); actual.completeExceptionally(failure) }
                        else actual.complete(requireNotNull(response))
                        retained.remove(id)
                    }
                }
            }
        } catch (_: RejectedExecutionException) {
            request.fill(0); access.fill(0); timeout.cancel(false); retained.remove(id)
            actual.completeExceptionally(AccountControlException(AccountControlFailure.BUSY))
        }
        return ticket
    }
    public fun retire(): Unit { generation.incrementAndGet() }
    public fun outstandingCount(): Int = retained.size
    public fun drain(): CompletionStage<Unit> = CompletableFuture.allOf(*retained.values.map {
        it.settled().toCompletableFuture().handle { _, _ -> Unit }
    }.toTypedArray()).thenApply { Unit }
    override fun close() { if (closed.compareAndSet(false, true)) { retire(); workers.shutdown(); timer.shutdown() } }
    private fun retryAfter(value: String?): Long {
        if (value == null) return 0
        val seconds = value.toLongOrNull()
        if (seconds != null) return seconds.coerceIn(0, 86_400) * 1000
        return runCatching { (ZonedDateTime.parse(value, DateTimeFormatter.RFC_1123_DATE_TIME).toInstant().toEpochMilli() -
            System.currentTimeMillis()).coerceIn(0, 86_400_000) }.getOrDefault(0)
    }
}

/** Uses the existing confirmed transport type, PKI policy and exact DER SPKI; no trust learning. */
private object DomainTls {
    fun factory(policy: AccountTlsTrust, expectedHost: String): SSLSocketFactory {
        val trustFactory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(null as KeyStore?) }
        val system = trustFactory.trustManagers.filterIsInstance<X509ExtendedTrustManager>().single()
        fun leaf(chain: Array<out X509Certificate>?) {
            if (chain.isNullOrEmpty() || chain.size > 16 || chain.any { it.encoded.size > 65_536 }) throw CertificateException("ACCOUNT_TLS_REFUSED")
            chain[0].checkValidity()
            val host = expectedHost.removePrefix("[").removeSuffix("]").lowercase()
            val ip = ':' in host || host.matches(Regex("[0-9]+(?:\\.[0-9]+){3}"))
            val matchingSan = chain[0].subjectAlternativeNames.orEmpty().any { san ->
                val kind = san.getOrNull(0) as? Int; val name = san.getOrNull(1) as? String
                if (name == null) false
                else if (ip && kind == 7 && name.all { it in '0'..'9' || it in 'a'..'f' || it in 'A'..'F' || it == ':' || it == '.' }) runCatching {
                    InetAddress.getByName(host).address.contentEquals(InetAddress.getByName(name).address)
                }.getOrDefault(false)
                else if (!ip && kind == 2) name.lowercase().let { dns -> dns == host ||
                    dns.startsWith("*.") && dns.count { it == '.' } >= 2 &&
                    host.substringAfter('.', "") == dns.substring(2) && host.count { it == '.' } == dns.count { it == '.' } }
                else false
            }
            if (!matchingSan) throw CertificateException("ACCOUNT_TLS_REFUSED")
            if (policy is AccountTlsTrust.ExactSpki && !chain[0].publicKey.encoded.contentEquals(policy.bytes)) throw CertificateException("ACCOUNT_TLS_REFUSED")
        }
        val manager = object : X509ExtendedTrustManager() {
            override fun getAcceptedIssuers(): Array<X509Certificate> = system.acceptedIssuers
            override fun checkServerTrusted(c: Array<out X509Certificate>?, a: String?, s: Socket?) {
                leaf(c); if (policy == AccountTlsTrust.SystemPki) system.checkServerTrusted(c, a, s)
            }
            override fun checkServerTrusted(c: Array<out X509Certificate>?, a: String?, e: SSLEngine?) {
                leaf(c); if (policy == AccountTlsTrust.SystemPki) system.checkServerTrusted(c, a, e)
            }
            override fun checkServerTrusted(c: Array<out X509Certificate>?, a: String?) { leaf(c); if (policy == AccountTlsTrust.SystemPki) system.checkServerTrusted(c, a) }
            override fun checkClientTrusted(c: Array<out X509Certificate>?, a: String?): Unit = throw CertificateException("ACCOUNT_TLS_REFUSED")
            override fun checkClientTrusted(c: Array<out X509Certificate>?, a: String?, s: Socket?): Unit = throw CertificateException("ACCOUNT_TLS_REFUSED")
            override fun checkClientTrusted(c: Array<out X509Certificate>?, a: String?, e: SSLEngine?): Unit = throw CertificateException("ACCOUNT_TLS_REFUSED")
        }
        val delegate = SSLContext.getInstance("TLS").apply { init(null, arrayOf(manager), null) }.socketFactory
        return object : SSLSocketFactory() {
            fun configured(socket: Socket): Socket = (socket as SSLSocket).apply {
                sslParameters = sslParameters.apply { protocols = arrayOf("TLSv1.3", "TLSv1.2"); endpointIdentificationAlgorithm = "HTTPS" }
            }
            override fun getDefaultCipherSuites(): Array<String> = delegate.defaultCipherSuites
            override fun getSupportedCipherSuites(): Array<String> = delegate.supportedCipherSuites
            override fun createSocket(s: Socket, h: String, p: Int, c: Boolean): Socket = configured(delegate.createSocket(s, h, p, c))
            override fun createSocket(h: String, p: Int): Socket = configured(delegate.createSocket(h, p))
            override fun createSocket(h: String, p: Int, l: InetAddress, lp: Int): Socket = configured(delegate.createSocket(h, p, l, lp))
            override fun createSocket(h: InetAddress, p: Int): Socket = configured(delegate.createSocket(h, p))
            override fun createSocket(h: InetAddress, p: Int, l: InetAddress, lp: Int): Socket = configured(delegate.createSocket(h, p, l, lp))
        }
    }
}
