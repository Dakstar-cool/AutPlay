package app.autplay.application.profilepairing

import java.net.IDN
import java.net.URI

/** Exact M5 origin canonicalization. It intentionally accepts no path, credentials, query or fragment. */
object OriginNormalizer {
    fun normalize(raw: String, allowUnsafeDevelopmentHttp: Boolean = false): String {
        val uri = try { URI(raw) } catch (_: Exception) { throw IllegalArgumentException("ORIGIN_INVALID") }
        val scheme = uri.scheme?.lowercase() ?: throw IllegalArgumentException("ORIGIN_INVALID")
        require(uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null) { "ORIGIN_COMPONENT_FORBIDDEN" }
        require(uri.rawPath.isNullOrEmpty() || uri.rawPath == "/") { "ORIGIN_PATH_FORBIDDEN" }
        val authority = uri.rawAuthority ?: throw IllegalArgumentException("ORIGIN_HOST_REQUIRED")
        require(!authority.contains('%')) { "ORIGIN_PERCENT_ENCODED_HOST" }
        val (host, explicitPort) = authorityHostAndPort(authority)
        require(scheme == "https" || (allowUnsafeDevelopmentHttp && scheme == "http" && isDevelopmentHost(host))) { "ORIGIN_TRANSPORT_FORBIDDEN" }
        val normalizedHost = if (host.contains(':')) "[${host.lowercase()}]" else IDN.toASCII(host, IDN.USE_STD3_ASCII_RULES).lowercase()
        val port = explicitPort?.takeUnless { (scheme == "https" && it == 443) || (scheme == "http" && it == 80) }
        return buildString { append(scheme).append("://").append(normalizedHost); port?.let { append(':').append(it) } }
    }

    private fun authorityHostAndPort(authority: String): Pair<String, Int?> {
        if (authority.startsWith("[")) {
            val closing = authority.indexOf(']')
            require(closing > 1) { "ORIGIN_HOST_REQUIRED" }
            val suffix = authority.substring(closing + 1)
            val port = if (suffix.isEmpty()) null else suffix.removePrefix(":").takeIf { suffix.startsWith(':') }?.toIntOrNull()
            require(suffix.isEmpty() || port != null) { "ORIGIN_INVALID" }
            return authority.substring(1, closing) to port
        }
        val colon = authority.lastIndexOf(':')
        if (colon < 0) return authority to null
        val port = authority.substring(colon + 1).toIntOrNull() ?: throw IllegalArgumentException("ORIGIN_INVALID")
        return authority.substring(0, colon) to port
    }

    private fun isDevelopmentHost(host: String?): Boolean {
        val value = host?.lowercase() ?: return false
        return value == "localhost" || value == "127.0.0.1" || value == "::1" ||
            value.startsWith("10.") || value.matches(Regex("192\\.168\\.\\d{1,3}\\.\\d{1,3}")) ||
            value.matches(Regex("172\\.(1[6-9]|2\\d|3[01])\\.\\d{1,3}\\.\\d{1,3}"))
    }
}
