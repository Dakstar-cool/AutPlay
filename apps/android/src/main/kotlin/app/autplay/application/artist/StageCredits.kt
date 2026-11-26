package app.autplay.application.artist

/** Exact performer credits supplied in the shared catalog spelling table. */
internal class StageCredit(val stage: String, aliases: List<String>) {
    private val publicName = Regex.escape(stage)
    private val other = aliases.joinToString("|", "(?:", ")", transform = { Regex.escape(it) })
    private val single = "(?:$publicName|$other)"
    private val join = """(?:\s*[,;/&+|–—-]\s*|\s+)"""
    private val pair = """(?:$publicName(?:$join$other|\s*\(\s*$other\s*\))|$other(?:$join$publicName|\s*\(\s*$publicName\s*\)))"""
    private val key = Regex("(?:$pair|$single)", RegexOption.IGNORE_CASE)
    private val tail = Regex("""(?:\s+[–—|-]\s*(?:$pair|$single)|\s+$pair)$""", RegexOption.IGNORE_CASE)

    fun matches(credit: String) = key.matches(credit)

    fun trimTitle(title: String): String {
        val suffix = tail.find(title)
        if (suffix == null || suffix.range.first == 0) return title
        val rest = title.substring(0, suffix.range.first).trimEnd()
        return if (guestEnd.containsMatchIn(rest)) title else rest
    }
}

private val guestEnd = Regex("""(?:^|\s)(?:feat\.?|ft\.?|featuring|with|и|&)$""", RegexOption.IGNORE_CASE)
private val explicitEnd = Regex("""\s+[-–—|]\s*$""")

internal fun trimRepeatedCredit(title: String, credit: String): String {
    if (!title.endsWith(credit, ignoreCase = true) || title.length <= credit.length + 1) return title
    val lead = title.dropLast(credit.length)
    val separator = explicitEnd.find(lead) ?: return title
    val rest = lead.substring(0, separator.range.first).trimEnd()
    return if (rest.isNotEmpty() && !guestEnd.containsMatchIn(rest)) rest else title
}

internal object StageCredits {
    private val entries = requireNotNull(javaClass.getResourceAsStream("/autplay-artist-credits.tsv"))
        .bufferedReader(Charsets.UTF_8).useLines { lines ->
            lines.map { line ->
                val names = line.split('\t')
                require(names.size >= 2 && names.none { it.isBlank() })
                StageCredit(names.first(), names.drop(1))
            }.toList()
        }

    private val realName = Regex("""(.+?)\s*\(\s*(?:настоящее имя|real name|legal name)\s*[:–—-]?\s+([^()]+)\)""", RegexOption.IGNORE_CASE)
    private val stageName = Regex("""(.+?)\s*\(\s*(?:сценическое имя|псевдоним|stage name)\s*[:–—-]?\s+([^()]+)\)""", RegexOption.IGNORE_CASE)
    private val collaboration = Regex("""[,;]|\s(?:feat\.?|ft\.?|featuring|/|&|\+)\s""", RegexOption.IGNORE_CASE)

    fun declared(label: String?): StageCredit? {
        if (label == null || '(' !in label) return null
        val real = realName.matchEntire(label)
        val match = real ?: stageName.matchEntire(label) ?: return null
        val (first, second) = match.destructured
        if (listOf(first, second).any { it.isBlank() || collaboration.containsMatchIn(it) }) return null
        return if (real != null) StageCredit(first.trim(), listOf(second.trim()))
            else StageCredit(second.trim(), listOf(first.trim()))
    }

    fun single(credit: String?): StageCredit? = credit?.let { value -> entries.firstOrNull { it.matches(value) } }
    private val bracketedGuest = Regex("""(.+?)(\s*\(\s*(?:feat\.?|ft\.?|featuring)\s+)([^()]+)(\s*\))""", RegexOption.IGNORE_CASE)
    private val guestJoin = Regex("""\s+(?:feat\.?|ft\.?|featuring)\s+""", RegexOption.IGNORE_CASE)

    private fun performer(label: String): String = (declared(label) ?: single(label))?.stage ?: label

    fun display(credit: String): String {
        bracketedGuest.matchEntire(credit)?.let { match ->
            val (main, marker, guests, close) = match.destructured
            return display(main) + marker + display(guests) + close
        }
        return formatPerformers(credit)
    }

    private fun formatPerformers(credit: String): String = buildString {
        var start = 0
        for (join in guestJoin.findAll(credit)) {
            append(performer(credit.substring(start, join.range.first)))
            append(join.value)
            start = join.range.last + 1
        }
        append(performer(credit.substring(start)))
    }
}
