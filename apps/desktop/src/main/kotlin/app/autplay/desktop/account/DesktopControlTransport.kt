package app.autplay.desktop.account

import app.autplay.application.securestate.*
import java.net.Authenticator
import java.net.CookieHandler
import java.net.PasswordAuthentication
import java.net.InetAddress
import java.net.Proxy
import java.net.Socket
import java.net.URI
import java.security.KeyStore
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.UUID
import java.util.concurrent.*
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.ssl.*

public enum class AccountControlFailure {
    TRUST_REQUIRED, CONTEXT_CHANGED, BUSY, CLOSED, DEADLINE, TRANSPORT_UNAVAILABLE,
    REDIRECT_REFUSED, AUTHENTICATION_REQUIRED, FORBIDDEN, CONFLICT, EXPIRED,
    CAPACITY, UNSUPPORTED, SERVER_UNAVAILABLE, INVALID_RESPONSE, BINDING_RECONCILIATION_REQUIRED,
}
public class AccountControlException(public val code: AccountControlFailure) :
    IllegalStateException("ACCOUNT_CONTROL_$code") {
    override fun toString(): String = "ACCOUNT_CONTROL_$code"
}
internal fun controlRequire(value: Boolean, code: AccountControlFailure = AccountControlFailure.INVALID_RESPONSE) {
    if (!value) throw AccountControlException(code)
}

/** Closed destinations. IDs are canonical UUIDs; no caller-provided URL/path/query/HTTP method. */
public enum class AccountEndpoint(
    public val method: String, private val route: String, public val secretHeader: String? = null,
    public val bearer: Boolean = false, public val maximum: Int = 65_536,
) {
    M5_DISCOVERY("GET", "/pairing/discovery", maximum = 16_384),
    M5_CAPABILITIES("GET", "/profile/capabilities", bearer = true),
    D1_DISCOVERY("GET", "/device-compatibility/v2/discovery", maximum = 16_384),
    D1_CAPABILITIES("GET", "/device-compatibility/v2/capabilities", bearer = true),
    B1_DISCOVERY("GET", "/sync-binding/v2/discovery", maximum = 16_384),
    B1_CAPABILITIES("GET", "/sync-binding/v2/capabilities", bearer = true),
    B1_BIND("POST", "/sync-binding/v2/bind", bearer = true),
    ENROLL("POST", "/device-compatibility/v2/enrollment/exchanges"),
    ADMISSION_SUBMIT("POST", "/device-compatibility/v2/admission/requests"),
    ADMISSION_POLL("POST", "/device-compatibility/v2/admission/requests/{id}/poll", "X-AutPlay-Admission-Poll"),
    ADMISSION_RECOVER("POST", "/device-compatibility/v2/admission/requests/{id}/recover"),
    ADMISSION_EXCHANGE("POST", "/device-compatibility/v2/admission/requests/{id}/exchange", "X-AutPlay-Admission-Poll"),
    TRUSTED_CHALLENGE("POST", "/device-compatibility/v2/trusted/challenge"),
    TRUSTED_EXCHANGE("POST", "/device-compatibility/v2/trusted/exchanges"),
    ROTATE("POST", "/device-compatibility/v2/sessions/rotate"),
    PORTABLE_REGISTER_CHALLENGE("POST", "/device-compatibility/v2/portable/register-challenge", maximum = 16_384),
    PORTABLE_LOGIN_CHALLENGE("POST", "/device-compatibility/v2/portable/login-challenge", maximum = 16_384),
    PORTABLE_REGISTER("POST", "/device-compatibility/v2/portable/register", maximum = 16_384),
    PORTABLE_LOGIN("POST", "/device-compatibility/v2/portable/login", maximum = 16_384),
    PORTABLE_CURRENT("GET", "/portable-accounts/current", bearer = true, maximum = 16_384),
    PORTABLE_ROTATION_CHALLENGE("POST", "/portable-accounts/rotation-challenge", maximum = 16_384),
    PORTABLE_ROTATE("POST", "/portable-accounts/complete", maximum = 16_384),
    PORTABLE_REAUTH_CHALLENGE("POST", "/portable-accounts/reauth-challenge", bearer = true, maximum = 16_384),
    SELF_START("POST", "/device-compatibility/v2/self/ceremonies", bearer = true),
    SELF_STATUS("GET", "/device-compatibility/v2/self/ceremonies/{id}", bearer = true),
    SELF_CLAIM("POST", "/device-compatibility/v2/self/ceremonies/{id}/claim", "X-AutPlay-Pairing-Secret"),
    SELF_POLL("POST", "/device-compatibility/v2/self/ceremonies/{id}/poll", "X-AutPlay-Pairing-Secret"),
    SELF_DECISION("POST", "/device-compatibility/v2/self/ceremonies/{id}/decision", bearer = true),
    SELF_EXCHANGE("POST", "/device-compatibility/v2/self/ceremonies/{id}/exchange", "X-AutPlay-Pairing-Secret"),
    RECOVERY_STATUS("GET", "/account/recovery", bearer = true, maximum = 4096),
    RECOVERY_CONFIGURE("POST", "/account/recovery", bearer = true, maximum = 4096),
    RECOVERY_PREVIEW("POST", "/device-compatibility/v2/recovery/preview", "X-AutPlay-Recovery-Code"),
    RECOVERY_COMMIT("POST", "/device-compatibility/v2/recovery/commit", "X-AutPlay-Recovery-Code"),
    RECOVERY_OUTCOME("POST", "/device-compatibility/v2/recovery/outcome", "X-AutPlay-Recovery-Refresh"),
    DELETION_STATUS("GET", "/account/deletion", bearer = true, maximum = 4096),
    DELETION_REQUEST("POST", "/device-compatibility/v2/deletion/requests", "X-AutPlay-Recovery-Code", bearer = true),
    DELETION_RECEIPT("POST", "/device-compatibility/v2/deletion/request-receipt", "X-AutPlay-Recovery-Code"),
    DELETION_RESOLVE("POST", "/device-compatibility/v2/deletion/request-resolve", "X-AutPlay-Recovery-Code"),
    DELETION_PREVIEW("POST", "/device-compatibility/v2/deletion/cancel/preview", "X-AutPlay-Recovery-Code"),
    DELETION_CANCEL("POST", "/device-compatibility/v2/deletion/cancel/commit", "X-AutPlay-Recovery-Code"),
    DELETION_OUTCOME("POST", "/device-compatibility/v2/deletion/cancel/outcome", "X-AutPlay-Recovery-Refresh"),
    DEVICES("GET", "/account/devices", bearer = true),
    SESSIONS("GET", "/account/sessions", bearer = true),
    LOGOUT_CURRENT("POST", "/account/sessions/current/logout", bearer = true),
    LOGOUT_ALL("POST", "/account/sessions/logout-all", bearer = true),
    REVOKE_DEVICE("POST", "/account/devices/{id}/revoke", bearer = true),
    INVITATION_CREATE("POST", "/pairing/enrollment/invitations", bearer = true),
    INVITATION_CANCEL("POST", "/pairing/enrollment/invitations/{id}/cancel", bearer = true),
    CONSENT_GET("GET", "/privacy/shared-training", bearer = true, maximum = 4096),
    CONSENT_DECIDE("PUT", "/privacy/shared-training", bearer = true, maximum = 4096),
    VISIBILITY_GET("GET", "/social/profile-statistics/settings", bearer = true, maximum = 4096),
    VISIBILITY_DECIDE("PUT", "/social/profile-statistics/settings", bearer = true, maximum = 4096),
    MEDIA_SNAPSHOT("GET", "/media-source/user-tracks/{id}/snapshot", bearer = true),
    ;
    internal fun path(id: String?): String {
        controlRequire(("{id}" in route) == (id != null))
        id?.let { canonicalUuid(it) }
        return "/api/v1" + route.replace("{id}", id.orEmpty())
    }
}

public sealed interface AccountTlsTrust {
    public data object SystemPki : AccountTlsTrust
    /** Exact DER SubjectPublicKeyInfo supplied by an explicit user confirmation, never learned. */
    public class ExactSpki(bytes: ByteArray) : AccountTlsTrust {
        internal val bytes: ByteArray = bytes.copyOf()
        init { controlRequire(bytes.size in 32..4096, AccountControlFailure.TRUST_REQUIRED) }
        override fun toString(): String = "ExactSpki(<redacted>)"
    }
}
public class ConfirmedAccountTransport private constructor(
    internal val api: URI, internal val stream: URI,
    internal val apiTrust: AccountTlsTrust, internal val streamTrust: AccountTlsTrust,
    internal val discoveryOnly: Boolean = false,
) {
    override fun toString(): String = "ConfirmedAccountTransport(<redacted>)"
    internal fun protectedPolicy(revision: Long): ProtectedTransportPolicy {
        controlRequire(!discoveryOnly, AccountControlFailure.TRUST_REQUIRED)
        fun mode(value: AccountTlsTrust): String = if (value is AccountTlsTrust.ExactSpki) "CONFIRMED_SPKI" else "PLATFORM_PKI"
        fun pin(value: AccountTlsTrust): String? = (value as? AccountTlsTrust.ExactSpki)?.let { secureSha256(it.bytes) }
        return ProtectedTransportPolicy(revision, mode(apiTrust), pin(apiTrust), mode(streamTrust), pin(streamTrust), System.currentTimeMillis())
    }
    internal fun matches(policy: ProtectedTransportPolicy): Boolean {
        val candidate = protectedPolicy(policy.revision)
        return policy.known && policy.apiMode == candidate.apiMode && policy.apiPin == candidate.apiPin &&
            policy.streamMode == candidate.streamMode && policy.streamPin == candidate.streamPin
    }
    public companion object {
        /** Public M5 GET only. This object cannot publish a policy or authorize protected IO. */
        public fun discoveryProbe(apiOrigin: String): ConfirmedAccountTransport {
            val api = origin(apiOrigin)
            return ConfirmedAccountTransport(api, api, AccountTlsTrust.SystemPki, AccountTlsTrust.SystemPki, discoveryOnly = true)
        }
        /** UI must present BOTH origins and BOTH policies before invoking this confirmation. */
        public fun confirm(apiOrigin: String, streamOrigin: String, api: AccountTlsTrust,
            stream: AccountTlsTrust, apiConfirmed: Boolean, streamConfirmed: Boolean): ConfirmedAccountTransport {
            controlRequire(apiConfirmed && streamConfirmed, AccountControlFailure.TRUST_REQUIRED)
            return ConfirmedAccountTransport(origin(apiOrigin), origin(streamOrigin), api, stream)
        }
        private fun origin(text: String): URI {
            val uri = try { URI(text) } catch (_: Exception) { throw AccountControlException(AccountControlFailure.TRUST_REQUIRED) }
            controlRequire(text.length <= 2048 && uri.scheme == "https" && uri.host != null &&
                uri.rawPath.isNullOrEmpty() && uri.rawUserInfo == null && uri.rawQuery == null &&
                uri.rawFragment == null && uri.port in -1..65535 && uri.port != 0 &&
                text.all { it.code in 33..126 }, AccountControlFailure.TRUST_REQUIRED)
            return uri
        }
    }
}

public class AccountControlResponse internal constructor(
    public val cacheControl: String, public val pragma: String, bytes: ByteArray,
) : AutoCloseable {
    private val body = SecretBytes(bytes)
    public fun bodyCopy(): ByteArray = body.copy()
    public fun document(): kotlinx.serialization.json.JsonObject {
        val copy = body.copy()
        return try { SecureJson.parse(copy, 65_536, canonical = false) } finally { copy.fill(0) }
    }
    override fun close(): Unit = body.close()
    override fun toString(): String = "AccountControlResponse(<redacted>)"
}

public sealed interface AccountIoOutcome {
    public class Completed(public val response: AccountControlResponse) : AccountIoOutcome
    public class Failed(public val code: AccountControlFailure) : AccountIoOutcome
}
/** Keep the ticket until settled completes. Await cancellation never cancels or forgets the IO. */
public class AccountIoTicket internal constructor(
    public val id: UUID, private val observation: CompletableFuture<AccountIoOutcome>,
    private val actual: CompletableFuture<AccountIoOutcome>,
) {
    public fun observe(): CompletionStage<AccountIoOutcome> = observation.minimalCompletionStage()
    public fun settled(): CompletionStage<AccountIoOutcome> = actual.minimalCompletionStage()
    override fun toString(): String = "AccountIoTicket(<redacted>)"
}

/** JDK17 HTTPS owner. No cookies, proxy discovery, redirects, retries or raw RPC escape hatch. */
public class DesktopControlTransport : AutoCloseable {
    private val workers = ThreadPoolExecutor(2, 2, 0, TimeUnit.MILLISECONDS, ArrayBlockingQueue(8),
        { work -> Thread(work, "autplay-account-io").apply { isDaemon = true } }, ThreadPoolExecutor.AbortPolicy())
    private val clock = ScheduledThreadPoolExecutor(1) { work -> Thread(work, "autplay-account-deadline").apply { isDaemon = true } }
    private val closed = AtomicBoolean(false)
    private val admitting = AtomicBoolean(true)
    private val generation = java.util.concurrent.atomic.AtomicLong(1)
    private val retained = ConcurrentHashMap<UUID, AccountIoTicket>()
    private val admissionGate = Any()

    public fun submit(trust: ConfirmedAccountTransport, endpoint: AccountEndpoint,
        body: ByteArray = byteArrayOf(), bearer: ByteArray = byteArrayOf(), secret: ByteArray = byteArrayOf(),
        id: String? = null, deadlineMs: Long = 20_000): AccountIoTicket = synchronized(admissionGate) {
        controlRequire(!closed.get() && admitting.get(), AccountControlFailure.CLOSED)
        controlRequire(!trust.discoveryOnly || endpoint == AccountEndpoint.M5_DISCOVERY &&
            body.isEmpty() && bearer.isEmpty() && secret.isEmpty() && id == null, AccountControlFailure.TRUST_REQUIRED)
        controlRequire(deadlineMs in 100..30_000 && body.size <= 32_768)
        controlRequire((endpoint.method == "GET") == body.isEmpty())
        controlRequire(endpoint.bearer == bearer.isNotEmpty() && (endpoint.secretHeader != null) == secret.isNotEmpty())
        for (value in listOf(bearer, secret)) controlRequire(value.size <= 8192 && value.all { it.toInt() in 33..126 })
        if (body.isNotEmpty()) SecureJson.parse(body, 32_768, canonical = false)
        val destination = URI(trust.api.toASCIIString() + endpoint.path(id))
        val request = body.copyOf(); val access = bearer.copyOf(); val header = secret.copyOf()
        val captured = generation.get(); val identifier = UUID.randomUUID()
        val observed = CompletableFuture<AccountIoOutcome>(); val actual = CompletableFuture<AccountIoOutcome>()
        val ticket = AccountIoTicket(identifier, observed, actual)
        retained[identifier] = ticket
        val start = System.nanoTime()
        val deadline = clock.schedule({ observed.complete(AccountIoOutcome.Failed(AccountControlFailure.DEADLINE)) }, deadlineMs, TimeUnit.MILLISECONDS)
        try {
            workers.execute {
                var connection: HttpsURLConnection? = null
                var outcome: AccountIoOutcome = AccountIoOutcome.Failed(AccountControlFailure.TRANSPORT_UNAVAILABLE)
                try {
                    controlRequire(captured == generation.get() && !closed.get(), AccountControlFailure.CONTEXT_CHANGED)
                    fun remaining(): Int {
                        val left = deadlineMs - TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start)
                        controlRequire(left > 0, AccountControlFailure.DEADLINE)
                        return left.toInt()
                    }
                    controlRequire(CookieHandler.getDefault() == null, AccountControlFailure.TRANSPORT_UNAVAILABLE)
                    connection = destination.toURL().openConnection(Proxy.NO_PROXY) as HttpsURLConnection
                    val wire = connection
                    controlRequire(CookieHandler.getDefault() == null, AccountControlFailure.TRANSPORT_UNAVAILABLE)
                    wire.setAuthenticator(object : Authenticator() {
                        override fun getPasswordAuthentication(): PasswordAuthentication? = null
                    })
                    wire.instanceFollowRedirects = false; wire.useCaches = false; wire.allowUserInteraction = false
                    wire.connectTimeout = remaining(); wire.readTimeout = remaining(); wire.requestMethod = endpoint.method
                    wire.sslSocketFactory = tls(trust.apiTrust, requireNotNull(destination.host))
                    wire.setRequestProperty("Accept", "application/json")
                    wire.setRequestProperty("Accept-Encoding", "identity")
                    wire.setRequestProperty("Cache-Control", "no-store"); wire.setRequestProperty("Pragma", "no-cache")
                    if (access.isNotEmpty()) wire.setRequestProperty("Authorization", "Bearer " + access.toString(Charsets.US_ASCII))
                    endpoint.secretHeader?.let { wire.setRequestProperty(it, header.toString(Charsets.US_ASCII)) }
                    if (request.isNotEmpty()) {
                        wire.doOutput = true; wire.setFixedLengthStreamingMode(request.size)
                        wire.setRequestProperty("Content-Type", "application/json")
                        wire.outputStream.use { it.write(request); it.flush() }
                    }
                    val status = wire.responseCode
                    // Status precedence: never parse a secret error payload into ordinary diagnostics.
                    if (status !in 200..299) throw AccountControlException(statusFailure(status))
                    fun single(name: String): String {
                        val values = wire.headerFields.entries.filter { it.key?.equals(name, true) == true }.flatMap { it.value }
                        controlRequire(values.size <= 1 && values.sumOf { it.length } <= 2048)
                        return values.singleOrNull().orEmpty()
                    }
                    controlRequire(wire.headerFields.size <= 64 && wire.headerFields.entries.sumOf {
                        (it.key?.length ?: 0) + it.value.sumOf(String::length) } <= 16_384)
                    val contentType = single("Content-Type").lowercase()
                    controlRequire(contentType.substringBefore(';').trim() == "application/json")
                    controlRequire(single("Content-Encoding").let { it.isEmpty() || it.equals("identity", true) })
                    controlRequire(wire.contentLengthLong in -1..endpoint.maximum.toLong())
                    val cache = single("Cache-Control"); val pragma = single("Pragma")
                    controlRequire(cache.split(',').any { it.trim().equals("no-store", true) })
                    controlRequire(pragma.split(',').any { it.trim().equals("no-cache", true) })
                    val bytes = ByteArray(endpoint.maximum + 1)
                    var count = 0
                    val buffer = ByteArray(4096)
                    try {
                        wire.inputStream.use { input ->
                            while (true) {
                                wire.readTimeout = remaining()
                                val read = input.read(buffer)
                                if (read < 0) break
                                controlRequire(count + read <= endpoint.maximum)
                                buffer.copyInto(bytes, count, 0, read); count += read
                            }
                        }
                        val response = bytes.copyOf(count)
                        try {
                            remaining()
                            controlRequire(captured == generation.get() && !closed.get(), AccountControlFailure.CONTEXT_CHANGED)
                            SecureJson.parse(response, endpoint.maximum, canonical = false)
                            outcome = AccountIoOutcome.Completed(AccountControlResponse(cache, pragma, response))
                        } finally { response.fill(0) }
                    } finally { buffer.fill(0); bytes.fill(0) }
                } catch (failure: AccountControlException) { outcome = AccountIoOutcome.Failed(failure.code) }
                catch (_: SSLException) { outcome = AccountIoOutcome.Failed(AccountControlFailure.TRUST_REQUIRED) }
                catch (_: Exception) { outcome = AccountIoOutcome.Failed(AccountControlFailure.TRANSPORT_UNAVAILABLE) }
                finally {
                    // A failed/disconnected socket remains owned until this worker actually returns.
                    try { connection?.disconnect() } catch (_: Exception) { }
                    request.fill(0); access.fill(0); header.fill(0); deadline.cancel(false)
                    actual.complete(outcome); observed.complete(outcome)
                    // Caller owns a completed response; retention of unsettled tickets is separate.
                    retained.remove(identifier)
                }
            }
        } catch (_: RejectedExecutionException) {
            request.fill(0); access.fill(0); header.fill(0); deadline.cancel(false); retained.remove(identifier)
            val failed = AccountIoOutcome.Failed(AccountControlFailure.BUSY)
            actual.complete(failed); observed.complete(failed)
        }
        ticket
    }

    /** Stop new effects synchronously. Existing owned tickets still deliver actual settlement. */
    public fun retire(): Unit = synchronized(admissionGate) { admitting.set(false); generation.incrementAndGet(); Unit }
    public fun unsettled(): List<AccountIoTicket> = retained.values.toList()
    public fun drain(): CompletionStage<Unit> = synchronized(admissionGate) {
        controlRequire(!admitting.get(), AccountControlFailure.CONTEXT_CHANGED)
        CompletableFuture.allOf(*retained.values.map { it.settled().toCompletableFuture() }.toTypedArray()).thenApply { Unit }
    }
    override fun close() {
        if (closed.compareAndSet(false, true)) { retire(); workers.shutdown(); clock.shutdown() }
    }

    private fun statusFailure(status: Int): AccountControlFailure = when (status) {
        in 300..399 -> AccountControlFailure.REDIRECT_REFUSED
        401 -> AccountControlFailure.AUTHENTICATION_REQUIRED
        403 -> AccountControlFailure.FORBIDDEN
        404, 405, 501 -> AccountControlFailure.UNSUPPORTED
        409 -> AccountControlFailure.CONFLICT
        410 -> AccountControlFailure.EXPIRED
        413, 429 -> AccountControlFailure.CAPACITY
        in 500..599 -> AccountControlFailure.SERVER_UNAVAILABLE
        else -> AccountControlFailure.INVALID_RESPONSE
    }

    private fun tls(policy: AccountTlsTrust, host: String): SSLSocketFactory {
        val factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
        factory.init(null as KeyStore?)
        val system = factory.trustManagers.filterIsInstance<X509ExtendedTrustManager>().single()
        val manager = object : X509ExtendedTrustManager() {
            private fun leaf(chain: Array<out X509Certificate>?) {
                if (chain.isNullOrEmpty() || chain.size > 16) throw CertificateException("ACCOUNT_TLS_REFUSED")
                chain[0].checkValidity()
                val usages = chain[0].extendedKeyUsage
                if (chain[0].hasUnsupportedCriticalExtension() || (usages != null && "1.3.6.1.5.5.7.3.1" !in usages) ||
                    chain[0].keyUsage?.let { it.isEmpty() || !it[0] } == true) throw CertificateException("ACCOUNT_TLS_REFUSED")
                if (!matchesHost(host, chain[0])) throw CertificateException("ACCOUNT_TLS_REFUSED")
                if (policy is AccountTlsTrust.ExactSpki && !chain[0].publicKey.encoded.contentEquals(policy.bytes))
                    throw CertificateException("ACCOUNT_TLS_REFUSED")
            }
            override fun checkServerTrusted(c: Array<out X509Certificate>?, a: String?, s: Socket?) {
                leaf(c); if (policy == AccountTlsTrust.SystemPki) system.checkServerTrusted(c, a, s)
            }
            override fun checkServerTrusted(c: Array<out X509Certificate>?, a: String?, e: SSLEngine?) {
                leaf(c); if (policy == AccountTlsTrust.SystemPki) system.checkServerTrusted(c, a, e)
            }
            override fun checkServerTrusted(c: Array<out X509Certificate>?, a: String?) {
                leaf(c); if (policy == AccountTlsTrust.SystemPki) system.checkServerTrusted(c, a)
            }
            override fun checkClientTrusted(c: Array<out X509Certificate>?, a: String?): Unit = throw CertificateException("ACCOUNT_TLS_REFUSED")
            override fun checkClientTrusted(c: Array<out X509Certificate>?, a: String?, s: Socket?): Unit = throw CertificateException("ACCOUNT_TLS_REFUSED")
            override fun checkClientTrusted(c: Array<out X509Certificate>?, a: String?, e: SSLEngine?): Unit = throw CertificateException("ACCOUNT_TLS_REFUSED")
            override fun getAcceptedIssuers(): Array<X509Certificate> = system.acceptedIssuers
        }
        val context = SSLContext.getInstance("TLS").apply { init(null, arrayOf(manager), null) }
        val delegate = context.socketFactory
        return object : SSLSocketFactory() {
            private fun configure(socket: Socket): Socket {
                val ssl = socket as SSLSocket
                ssl.sslParameters = ssl.sslParameters.apply {
                    protocols = arrayOf("TLSv1.3", "TLSv1.2"); endpointIdentificationAlgorithm = "HTTPS"
                }
                return ssl
            }
            override fun getDefaultCipherSuites(): Array<String> = delegate.defaultCipherSuites
            override fun getSupportedCipherSuites(): Array<String> = delegate.supportedCipherSuites
            override fun createSocket(s: Socket, h: String, p: Int, close: Boolean): Socket = configure(delegate.createSocket(s, h, p, close))
            override fun createSocket(h: String, p: Int): Socket = configure(delegate.createSocket(h, p))
            override fun createSocket(h: String, p: Int, local: InetAddress, lp: Int): Socket = configure(delegate.createSocket(h, p, local, lp))
            override fun createSocket(h: InetAddress, p: Int): Socket = configure(delegate.createSocket(h, p))
            override fun createSocket(h: InetAddress, p: Int, local: InetAddress, lp: Int): Socket = configure(delegate.createSocket(h, p, local, lp))
        }
    }

    private fun matchesHost(raw: String, certificate: X509Certificate): Boolean {
        val host = raw.removePrefix("[").removeSuffix("]").lowercase()
        val ip = ':' in host || host.matches(Regex("[0-9]+(?:\\.[0-9]+){3}"))
        return certificate.subjectAlternativeNames.orEmpty().any { san ->
            val kind = san.getOrNull(0) as? Int; val name = san.getOrNull(1) as? String
            if (ip && kind == 7 && name != null) {
                // Both operands are literal IP SANs; no DNS name is ever resolved here.
                runCatching { InetAddress.getByName(host).address.contentEquals(InetAddress.getByName(name).address) }.getOrDefault(false)
            } else if (!ip && kind == 2 && name != null) {
                val dns = name.lowercase()
                dns == host || (dns.startsWith("*.") && dns.count { it == '.' } >= 2 &&
                    host.substringAfter('.', "") == dns.substring(2) && host.count { it == '.' } == dns.count { it == '.' })
            } else false
        }
    }
}
