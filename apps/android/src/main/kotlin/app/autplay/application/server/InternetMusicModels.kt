package app.autplay.application.server

data class InternetMusicCandidate(val id: String, val title: String, val artist: String, val provider: String, val durationMs: Long)
data class InternetMusicSearch(val id: String, val candidates: List<InternetMusicCandidate>)
data class InternetMusicAcquisition(val id: String, val state: String, val refId: String?, val variantId: String?)
