package app.autplay.application.devicecompatibility

/**
 * Network-free syntax seam for the frozen v2 codecs. Keeps the original spelling; each caller
 * owns its minimum port, DNS length and canonical-spelling policy. This is not transport trust.
 * URI.getHost() uses a narrower last-label grammar (JDK URI#getHost), rejecting e.g. node.123.
 * Raw splitting plus explicit checks follows RFC3986 section 3.2 and Python urllib.parse's
 * documented requirement for caller validation; no URL handler, address lookup or dependency.
 */
internal class OriginAuthorityV2 private constructor(
    val scheme: String, val host: String, val portText: String?, val port: Int?,
) {
    val isIpv6: Boolean get() = host.startsWith("[")

    fun hasDnsName(maxLength: Int): Boolean {
        val name = host.removeSuffix(".")
        return name.length in 1..maxLength && name.split('.').all { label.matches(it) }
    }

    override fun toString(): String = "OriginAuthorityV2"

    companion object {
        private val syntax = Regex("(https?)://(\\[[0-9A-Fa-f:.]+\\]|[A-Za-z0-9.-]+)(?::([0-9]+))?")
        private val label = Regex("[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
        private val octet = Regex("0|[1-9][0-9]{0,2}")
        private val word = Regex("[0-9A-Fa-f]{1,4}")

        fun parse(value: String): OriginAuthorityV2? {
            val match = syntax.matchEntire(value) ?: return null
            val host = match.groupValues[2]
            val portText = match.groups[3]?.value
            val port = if (portText == null) null else {
                // Bound the numeric value without overflowing on a zero-padded spelling.
                val number = portText.trimStart('0').ifEmpty { "0" }.toIntOrNull() ?: return null
                if (number !in 0..65535) return null
                number
            }
            if (host.startsWith("[") && !ipv6(host.substring(1, host.length - 1))) return null
            return OriginAuthorityV2(match.groupValues[1], host, portText, port)
        }

        private fun ipv6(value: String): Boolean {
            if (':' !in value) return false
            val hex = if ('.' in value) {
                val suffix = value.substringAfterLast(':')
                val parts = suffix.split('.')
                // Python IPv6Address uses strict dotted-quad suffixes, including no leading zeroes.
                if (parts.size != 4 || parts.any { !octet.matches(it) || it.toInt() > 255 }) return false
                value.substringBeforeLast(':') + ":0:0"
            } else value
            val halves = hex.split("::")
            if (halves.size > 2) return false
            val groups = halves.flatMap { if (it.isEmpty()) emptyList() else it.split(':') }
            return groups.all { word.matches(it) } &&
                (if (halves.size == 2) groups.size < 8 else groups.size == 8)
        }
    }
}
