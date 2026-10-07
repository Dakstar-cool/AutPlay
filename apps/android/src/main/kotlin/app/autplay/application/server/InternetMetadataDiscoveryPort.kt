package app.autplay.application.server

/** Private catalogue browsing and the existing, separately admitted audio-source search. */
interface InternetMetadataDiscoveryPort {
    suspend fun discoverMusic(query: String, kind: InternetMetadataDiscoveryKind,
        limit: Int = 25, offset: Int = 0): InternetMetadataDiscoveryPage
    suspend fun discoveryArtistTracks(id: String, limit: Int = 25, offset: Int = 0): InternetMetadataDiscoveryPage
    suspend fun discoveryReleaseTracks(id: String, limit: Int = 25, offset: Int = 0): InternetMetadataDiscoveryPage
    suspend fun createMusicCatalogueContext(selection: MusicCatalogueContextRequest): MusicCatalogueContext
    suspend fun searchInternetMusic(query: String, operationId: String, catalogueContextId: String? = null): InternetMusicSearch
}
