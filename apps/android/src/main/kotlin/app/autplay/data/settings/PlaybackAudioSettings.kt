package app.autplay.data.settings

/** Device-local audio preferences. Unknown modes are retained and rendered as OFF. */
data class PlaybackAudioSettings(
    val replayGainMode: String = "OFF",
    val preampDb: Int = 0,
    val preventClipping: Boolean = true,
    val equalizerEnabled: Boolean = false,
    val equalizerBandsDb: List<Int> = List(5) { 0 },
) {
    init {
        require(preampDb in -12..12)
        require(equalizerBandsDb.size == 5 && equalizerBandsDb.all { it in -12..12 })
    }

    companion object {
        val BAND_FREQUENCIES_HZ: List<Int> = listOf(63, 250, 1_000, 4_000, 16_000)
    }
}
