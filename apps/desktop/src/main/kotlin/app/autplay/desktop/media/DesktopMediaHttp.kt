package app.autplay.desktop.media

import app.autplay.application.desktop.media.*
import app.autplay.application.securestate.SecureJson
import java.io.ByteArrayOutputStream
import java.net.InetAddress
import java.net.Proxy
import java.net.Socket
import java.net.URI
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.*

/** Closed control destinations. Streaming bytes are exclusively owned by the native helper. */
internal sealed interface MediaControlRoute {
    val method: String
    val path: String
    val maximum: Int get() = 4096
    class Snapshot(id: MediaId) : MediaControlRoute {
        override val method = "GET"
        override val path = "/api/v1/media-source/user-tracks/${id.value}/snapshot"
    }
    object Profile : MediaControlRoute {
        override val method = "GET"; override val path = "/api/v1/profile/capabilities"; override val maximum = 65_536
    }
    class Resource(val operation: MediaId?, val action: String) : MediaControlRoute {
        init { require(action in setOf("acquire", "poll", "renew", "release", "attachments")) }
        override val method = if (action == "poll") "GET" else "POST"
        override val path: String = "/api/v1/account/resource-admissions" + when (action) {
            "acquire" -> "".also { require(operation == null) }
            "poll" -> "/${requireNotNull(operation).value}"
            else -> "/${requireNotNull(operation).value}/$action"
        }
    }
}

/** Actual bounded HTTPS operation. A request returns only after socket/stream cleanup settles. */
internal class DesktopMediaHttp(
    private val credentials: DesktopMediaCredentialOwner,
    private val authority: DesktopMediaAccountAdmission,
    private val clock: DesktopMediaResolutionClock,
    private val work: DesktopMediaWorkOwner,
) : DesktopMediaAuthenticatedControlPort {
    private val transportGate = Any()
    private var renewalFenced = false
    private val active = mutableSetOf<kotlinx.coroutines.CompletableDeferred<Unit>>()
    suspend fun quiesceForRenewal(): Unit {
        val pending = synchronized(transportGate) { renewalFenced = true; active.toList() }
        pending.forEach { it.await() }
    }
    fun resumeAfterRenewal(): Unit = synchronized(transportGate) { renewalFenced = false }
    class Reply(val status: Int, val contentType: String?, val encoding: String?, val cache: String?,
        val pragma: String?, val vary: String?, val headerBytes: Int, val largestHeader: Int, val bytes: ByteArray) : AutoCloseable {
        override fun close(): Unit = bytes.fill(0)
        override fun toString(): String = "MediaControlReply(<redacted>)"
    }
    override fun startSnapshot(capture: SelectionCapture.VaultNetwork, request: MediaSourceSnapshotRequestV1,
        admission: MediaLiveAdmission, deadlineMonotonicMs: Long): MediaOwnedWork<MediaSnapshotHttpReply> = work.start { cancelled ->
        mediaCheck(!cancelled.get(), MediaCode.CANCELLED, MediaStage.RESOLVE)
        execute(capture, MediaControlRoute.Snapshot(request.userTrackRefId), byteArrayOf(), deadlineMonotonicMs).use { response ->
            MediaSnapshotHttpReply(response.status, response.contentType, response.encoding, response.cache,
                response.pragma, response.vary, response.headerBytes, response.largestHeader, response.bytes)
        }
    }

    suspend fun execute(capture: SelectionCapture.VaultNetwork, route: MediaControlRoute,
        body: ByteArray = byteArrayOf(), deadline: Long = clock.monotonicMs() + 15_000): Reply {
        val terminal = synchronized(transportGate) {
            mediaCheck(!renewalFenced, MediaCode.OWNER_STALE, MediaStage.AUTH)
            kotlinx.coroutines.CompletableDeferred<Unit>().also { active.add(it) }
        }
        try { return executeOwned(capture, route, body, deadline) }
        finally { synchronized(transportGate) { active.remove(terminal); terminal.complete(Unit) } }
    }

    private suspend fun executeOwned(capture: SelectionCapture.VaultNetwork, route: MediaControlRoute,
        body: ByteArray, deadline: Long): Reply {
        mediaCheck(body.size <= 4096 && (route.method == "GET") == body.isEmpty(), MediaCode.PROTOCOL_ERROR, MediaStage.ADMISSION)
        val cleanup = route is MediaControlRoute.Resource && route.action in setOf("poll", "release")
        authority.refresh(capture, authority.intentFor(capture), established = route is MediaControlRoute.Resource, cleanup = cleanup)
        val policy = capture.network.apiPolicy
        val destination = URI(policy.origin.value + route.path)
        val credential = credentials.access(capture)
        var connection: HttpsURLConnection? = null
        val started = clock.monotonicMs()
        fun remaining(): Int {
            val left = minOf(deadline - clock.monotonicMs(), 15_000 - (clock.monotonicMs() - started))
            mediaCheck(left > 0, MediaCode.NETWORK_TIMEOUT, MediaStage.RESOLVE)
            return minOf(5_000, left.toInt())
        }
        try {
            mediaCheck(java.net.CookieHandler.getDefault() == null && java.net.Authenticator.getDefault() == null &&
                java.net.ResponseCache.getDefault() == null, MediaCode.OWNER_UNAVAILABLE, MediaStage.RESOLVE)
            authority.withCurrent(MediaAdmissionRequest(capture, null, if (cleanup) MediaAdmissionStep.DRAIN else MediaAdmissionStep.SNAPSHOT,
                null, null, null, null)) { live ->
                mediaCheck(live.networkAvailable && credential.accessExpiresAtMs > System.currentTimeMillis() + 1_000,
                    MediaCode.AUTH_REQUIRED, MediaStage.AUTH)
            }
            val wire = destination.toURL().openConnection(Proxy.NO_PROXY) as HttpsURLConnection
            connection = wire
            wire.instanceFollowRedirects = false; wire.useCaches = false; wire.allowUserInteraction = false
            wire.requestMethod = route.method; wire.connectTimeout = remaining(); wire.readTimeout = remaining()
            wire.sslSocketFactory = tls(policy)
            wire.setRequestProperty("Accept", "application/json"); wire.setRequestProperty("Accept-Encoding", "identity")
            wire.setRequestProperty("Cache-Control", "no-store"); wire.setRequestProperty("Pragma", "no-cache")
            // No global Authenticator, CookieHandler or redirect callback receives this bearer.
            credential.bearer.withBytes { wire.setRequestProperty("Authorization", "Bearer " + it.toString(Charsets.US_ASCII)) }
            if (body.isNotEmpty()) {
                wire.doOutput = true; wire.setFixedLengthStreamingMode(body.size)
                wire.setRequestProperty("Content-Type", "application/json")
                wire.outputStream.use { output -> output.write(body); output.flush() }
            }
            val status = wire.responseCode
            remaining()
            mediaCheck(status !in 300..399, MediaCode.REDIRECT_REJECTED, MediaStage.RESOLVE)
            val headers = wire.headerFields.filterKeys { it != null }
            var headerBytes = 0; var largest = 0
            for ((name, values) in headers) {
                mediaCheck(name.length <= 256 && name.all { it.code in 33..126 } && values.size == 1,
                    MediaCode.PROTOCOL_ERROR, MediaStage.RESOLVE)
                for (value in values) {
                    val bytes = value.toByteArray(Charsets.UTF_8).size
                    mediaCheck(bytes <= 4096 && value.none { it.code < 32 || it.code == 127 }, MediaCode.PROTOCOL_ERROR, MediaStage.RESOLVE)
                    headerBytes = Math.addExact(headerBytes, name.length + bytes); largest = maxOf(largest, bytes)
                    mediaCheck(headerBytes <= 16_384, MediaCode.PROTOCOL_ERROR, MediaStage.RESOLVE)
                }
            }
            fun header(name: String): String? = headers.entries.filter { it.key.equals(name, true) }
                .also { mediaCheck(it.size <= 1, MediaCode.PROTOCOL_ERROR, MediaStage.RESOLVE) }.singleOrNull()?.value?.single()
            val contentType = header("Content-Type"); val encoding = header("Content-Encoding")
            mediaCheck(contentType?.substringBefore(';')?.trim()?.lowercase() == "application/json" &&
                encoding == null && header("Transfer-Encoding") == null && wire.contentLengthLong in 0..route.maximum.toLong(),
                MediaCode.PROTOCOL_ERROR, MediaStage.RESOLVE)
            val expected = wire.contentLengthLong.toInt()
            val buffer = ByteArray(4096); val accumulated = ByteArrayOutputStream()
            try {
                val input = if (status in 200..299) wire.inputStream else wire.errorStream
                mediaCheck(input != null, MediaCode.PROTOCOL_ERROR, MediaStage.RESOLVE)
                requireNotNull(input).use {
                    while (true) {
                        wire.readTimeout = remaining()
                        val count = it.read(buffer)
                        if (count < 0) break
                        mediaCheck(accumulated.size() + count <= expected, MediaCode.PROTOCOL_ERROR, MediaStage.RESOLVE)
                        accumulated.write(buffer, 0, count)
                    }
                }
                mediaCheck(accumulated.size() == expected && expected > 0, MediaCode.PROTOCOL_ERROR, MediaStage.RESOLVE)
                remaining()
                return Reply(status, contentType, encoding, header("Cache-Control"), header("Pragma"), header("Vary"),
                    headerBytes, largest, accumulated.toByteArray())
            } finally { buffer.fill(0); accumulated.reset() }
        } catch (failure: MediaSourceException) { throw failure }
        catch (_: SSLException) { throw mediaFailure(MediaCode.TLS_UNTRUSTED, MediaStage.RESOLVE) }
        catch (_: Exception) { throw mediaFailure(MediaCode.NETWORK_DROPPED, MediaStage.RESOLVE) }
        finally { try { connection?.disconnect() } finally { credential.close() } }
    }

    private fun tls(policy: MediaTransportPolicy): SSLSocketFactory {
        val factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(null as KeyStore?) }
        val system = factory.trustManagers.filterIsInstance<X509ExtendedTrustManager>().single()
        fun manager(chain: Array<out X509Certificate>?): X509ExtendedTrustManager {
            if (chain.isNullOrEmpty() || chain.size > 8 || chain.any { it.encoded.size > 65_536 }) throw CertificateException("TLS_UNTRUSTED")
            chain[0].checkValidity()
            if (policy is MediaTransportPolicy.PlatformPki) return system
            val pinned = policy as MediaTransportPolicy.ConfirmedSpki
            val spki = certificateSpki(chain[0].encoded)
            val actual = try { MessageDigest.getInstance("SHA-256").digest(spki).joinToString("") { "%02x".format(it.toInt() and 255) } }
                finally { spki.fill(0) }
            if (actual != pinned.tlsSpkiSha256) throw CertificateException("TLS_UNTRUSTED")
            val store = KeyStore.getInstance(KeyStore.getDefaultType()).apply { load(null); setCertificateEntry("confirmed-leaf", chain[0]) }
            return TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(store) }
                .trustManagers.filterIsInstance<X509ExtendedTrustManager>().single()
        }
        val trust = object : X509ExtendedTrustManager() {
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
            override fun checkClientTrusted(c: Array<out X509Certificate>?, a: String?): Unit = throw CertificateException("TLS_UNTRUSTED")
            override fun checkClientTrusted(c: Array<out X509Certificate>?, a: String?, s: Socket?): Unit = throw CertificateException("TLS_UNTRUSTED")
            override fun checkClientTrusted(c: Array<out X509Certificate>?, a: String?, e: SSLEngine?): Unit = throw CertificateException("TLS_UNTRUSTED")
            override fun checkServerTrusted(c: Array<out X509Certificate>?, a: String?): Unit = manager(c).checkServerTrusted(c, a)
            override fun checkServerTrusted(c: Array<out X509Certificate>?, a: String?, s: Socket?): Unit = manager(c).checkServerTrusted(c, a, s)
            override fun checkServerTrusted(c: Array<out X509Certificate>?, a: String?, e: SSLEngine?): Unit = manager(c).checkServerTrusted(c, a, e)
        }
        val delegate = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trust), null) }.socketFactory
        return object : SSLSocketFactory() {
            private fun configure(socket: Socket): Socket = (socket as SSLSocket).apply {
                sslParameters = sslParameters.apply { endpointIdentificationAlgorithm = "HTTPS" }
            }
            override fun getDefaultCipherSuites(): Array<String> = delegate.defaultCipherSuites
            override fun getSupportedCipherSuites(): Array<String> = delegate.supportedCipherSuites
            override fun createSocket(s: Socket, h: String, p: Int, close: Boolean): Socket = configure(delegate.createSocket(s, h, p, close))
            override fun createSocket(h: String, p: Int): Socket = configure(delegate.createSocket(h, p))
            override fun createSocket(h: String, p: Int, l: InetAddress, lp: Int): Socket = configure(delegate.createSocket(h, p, l, lp))
            override fun createSocket(h: InetAddress, p: Int): Socket = configure(delegate.createSocket(h, p))
            override fun createSocket(h: InetAddress, p: Int, l: InetAddress, lp: Int): Socket = configure(delegate.createSocket(h, p, l, lp))
        }
    }

    /** Exact certificate SPKI TLV, preserving algorithm/parameters/key bytes. */
    private fun certificateSpki(bytes: ByteArray): ByteArray {
        class Tlv(val tag: Int, val start: Int, val content: Int, val end: Int)
        fun read(offset: Int, bound: Int): Tlv {
            if (offset < 0 || offset + 2 > bound || bound > bytes.size) throw CertificateException("TLS_UNTRUSTED")
            val tag = bytes[offset].toInt() and 255
            var cursor = offset + 1
            val first = bytes[cursor++].toInt() and 255
            val length: Int
            if (first < 128) length = first
            else {
                val count = first and 127
                if (count !in 1..3 || cursor + count > bound || bytes[cursor] == 0.toByte()) throw CertificateException("TLS_UNTRUSTED")
                var value = 0
                repeat(count) { value = (value shl 8) or (bytes[cursor++].toInt() and 255) }
                if (value < 128) throw CertificateException("TLS_UNTRUSTED")
                length = value
            }
            if (length > bound - cursor) throw CertificateException("TLS_UNTRUSTED")
            return Tlv(tag, offset, cursor, cursor + length)
        }
        val certificate = read(0, bytes.size)
        if (certificate.tag != 0x30 || certificate.end != bytes.size) throw CertificateException("TLS_UNTRUSTED")
        val tbs = read(certificate.content, certificate.end)
        if (tbs.tag != 0x30) throw CertificateException("TLS_UNTRUSTED")
        var cursor = tbs.content
        if ((bytes[cursor].toInt() and 255) == 0xa0) cursor = read(cursor, tbs.end).end
        for (tag in listOf(2, 0x30, 0x30, 0x30, 0x30)) {
            val field = read(cursor, tbs.end)
            if (field.tag != tag) throw CertificateException("TLS_UNTRUSTED")
            cursor = field.end
        }
        val spki = read(cursor, tbs.end)
        if (spki.tag != 0x30) throw CertificateException("TLS_UNTRUSTED")
        val algorithm = read(spki.content, spki.end); val key = read(algorithm.end, spki.end)
        if (algorithm.tag != 0x30 || key.tag != 3 || key.end != spki.end || key.content >= key.end || bytes[key.content] != 0.toByte())
            throw CertificateException("TLS_UNTRUSTED")
        return bytes.copyOfRange(spki.start, spki.end)
    }
}
