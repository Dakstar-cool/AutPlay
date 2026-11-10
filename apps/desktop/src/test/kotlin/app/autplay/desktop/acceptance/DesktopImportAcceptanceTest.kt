package app.autplay.desktop.acceptance

import app.autplay.application.desktop.DesktopImportedTrack
import app.autplay.application.desktop.DesktopEmbeddedMetadata
import app.autplay.application.desktop.DesktopEmbeddedMetadataLimits
import app.autplay.application.desktop.DesktopImportCandidateMapping
import app.autplay.application.desktop.DesktopImportJob
import app.autplay.application.desktop.DesktopImportJobAttempt
import app.autplay.application.desktop.DesktopImportJobAuthorization
import app.autplay.application.desktop.DesktopImportJobCandidate
import app.autplay.application.desktop.DesktopImportJobFailure
import app.autplay.application.desktop.DesktopImportJobManifest
import app.autplay.application.desktop.DesktopImportJobOutcome
import app.autplay.application.desktop.DesktopImportJobPermit
import app.autplay.application.desktop.DesktopImportJobPhase
import app.autplay.application.desktop.DesktopImportJobReselection
import app.autplay.application.desktop.DesktopImportJobSettlement
import app.autplay.application.desktop.DesktopImportJobStop
import app.autplay.application.desktop.DesktopImportJobStopReason
import app.autplay.application.desktop.DesktopImportProgress
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopListeningEvent
import app.autplay.application.desktop.DesktopPreferences
import app.autplay.application.desktop.DesktopPreferencesStore
import app.autplay.application.desktop.DesktopQueue
import app.autplay.application.desktop.DesktopQueueEntry
import app.autplay.application.desktop.DesktopTrack
import app.autplay.data.desktop.DesktopStorageException
import app.autplay.data.desktop.DesktopEmbeddedMetadataReader
import app.autplay.data.desktop.DesktopSettingsTransferCodec
import app.autplay.data.desktop.openDesktopPreferencesStore
import app.autplay.desktop.library.DesktopFileImporter
import app.autplay.desktop.playback.desktopSnapshotUnavailable
import app.autplay.desktop.settings.DesktopSettingsController
import app.autplay.desktop.settings.DesktopSettingsFileException
import app.autplay.desktop.settings.readSettingsImport
import app.autplay.desktop.settings.writeSettingsExportNew
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.channels.FileChannel
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption.CREATE_NEW
import java.nio.file.StandardOpenOption.SPARSE
import java.nio.file.StandardOpenOption.WRITE
import java.nio.file.attribute.FileTime
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancel
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout

class DesktopImportAcceptanceTest {
    @get:Rule val deadline: Timeout = Timeout.seconds(45)

    @Test fun explicitImportPreservesOriginalDeduplicatesExactBytesAndRejectsMissingOrCorruptCopies(): Unit = runBlocking {
        withTimeout(30_000) {
            DesktopPairingInputAssertions.run()
            mp4MetadataAssertions()
            importReviewAssertions()
            importJobAdapterAssertions()
            DesktopAcceptanceFixture().use { fixture ->
                fixture.openStore()
                val bytes = wave(250)
                val source = Files.write(fixture.sources.resolve("Original.wav"), bytes)
                val twin = Files.write(fixture.sources.resolve("Same bytes.wav"), bytes)
                val importer = DesktopFileImporter(fixture.store, fixture.data)
                val overLimit = importer.importSelected(List(257) { source.toString() })
                assertEquals(0, overLimit.imported)
                assertEquals(1, overLimit.failed)
                assertEquals("import_selection_limit", overLimit.errorCode)
                val report = importer.importSelected(listOf(source.toString(), twin.toString()))
                assertEquals(1, report.imported)
                assertEquals(1, report.duplicates)
                assertEquals(0, report.failed)
                val track = fixture.store.state.value.tracks.single()
                assertEquals(250L, checkNotNull(track.durationMs))
                val managed = Path.of(checkNotNull(fixture.store.resolveManagedFile(track.id)))
                assertEquals("${track.sha256}.wav", managed.fileName.toString())
                assertEquals(fixture.data.resolve("media"), managed.parent)
                assertArrayEquals(bytes, Files.readAllBytes(managed))
                assertArrayEquals(bytes, Files.readAllBytes(source))
                assertArrayEquals(bytes, Files.readAllBytes(twin))
                val retry = importer.importSelected(listOf(source.toString()))
                assertEquals(1, retry.duplicates)
                assertEquals(track.id, fixture.store.state.value.tracks.single().id)

                // An explicit duplicate import retains collection removal and the managed bytes.
                fixture.store.removeTrackFromCollection(track.id)
                val removed = fixture.store.state.value.tracks.single()
                assertEquals(track.copy(collectionState = "REMOVED"), removed)
                val removedRetry = importer.importSelected(listOf(twin.toString()))
                assertEquals(0, removedRetry.imported)
                assertEquals(1, removedRetry.duplicates)
                assertEquals(0, removedRetry.failed)
                assertEquals(removed, fixture.store.state.value.tracks.single())
                assertEquals(managed.toString(), fixture.store.resolveManagedFile(track.id))
                assertArrayEquals(bytes, Files.readAllBytes(managed))
                assertArrayEquals(bytes, Files.readAllBytes(source))
                fixture.reopenStore()
                assertEquals(removed, fixture.store.state.value.tracks.single())
                fixture.store.restoreTrackToCollection(track.id)
                assertEquals(track, fixture.store.state.value.tracks.single())

                val missing = DesktopFileImporter(fixture.store, fixture.data)
                    .importSelected(listOf(fixture.sources.resolve("Missing.wav").toString()))
                assertEquals(1, missing.failed)
                assertEquals("import_source_unreadable", missing.errorCode)
                Files.delete(managed)
                assertNull(fixture.store.resolveManagedFile(track.id))
                fixture.reopenStore()
                assertEquals(track, fixture.store.state.value.tracks.single())
                assertNull(fixture.store.resolveManagedFile(track.id))
                val repaired = DesktopFileImporter(fixture.store, fixture.data).importSelected(listOf(source.toString()))
                assertEquals(1, repaired.duplicates)
                assertArrayEquals(bytes, Files.readAllBytes(managed))

                // Explicit reimport must refuse to overwrite a corrupt published copy.
                Files.write(managed, byteArrayOf(99))
                val sizeMismatch = try { fixture.store.resolveManagedFile(track.id); null }
                    catch (error: DesktopStorageException) { error }
                assertEquals("desktop_media_integrity_failed", sizeMismatch?.code)
                val corrupt = DesktopFileImporter(fixture.store, fixture.data).importSelected(listOf(source.toString()))
                assertEquals(1, corrupt.failed)
                assertEquals("import_managed_integrity_failed", corrupt.errorCode)
                assertArrayEquals(byteArrayOf(99), Files.readAllBytes(managed))
                assertArrayEquals(bytes, Files.readAllBytes(source))
                assertEquals(1, fixture.store.state.value.tracks.size)
                Files.list(managed.parent).use { files -> assertTrue(files.noneMatch { it.fileName.toString().endsWith(".part") }) }

                // A pre-format locator is repaired from real bytes without replacing its identity/history.
                val legacyBytes = wave(500)
                val legacySource = Files.write(fixture.sources.resolve("Legacy.wav"), legacyBytes)
                val legacyHash = MessageDigest.getInstance("SHA-256").digest(legacyBytes)
                    .joinToString("") { "%02x".format(it.toInt() and 255) }
                val legacyCopy = Files.write(managed.parent.resolve("$legacyHash.media"), legacyBytes)
                val legacyTrack = fixture.store.addImportedTrack(DesktopImportedTrack("legacy-track", "Legacy title", "",
                    null, legacyHash, legacyBytes.size.toLong(), legacyCopy.fileName.toString()))
                val legacyQueue = DesktopQueue("legacy-queue", listOf(DesktopQueueEntry("legacy-entry", legacyTrack.id)),
                    "legacy-entry", positionMs = 120, sessionId = "legacy-session", sessionStartedAtMs = 100, playedMs = 120)
                val legacyEvent = DesktopListeningEvent("legacy-session", "legacy-entry", legacyTrack.id, 100, 120, 120, false)
                fixture.store.saveQueue(legacyQueue, legacyEvent)
                val beforeLegacyRepair = fixture.store.state.value
                val recognizedCopy = Path.of(checkNotNull(fixture.store.resolveManagedFile(legacyTrack.id)))
                assertEquals("$legacyHash.wav", recognizedCopy.fileName.toString())
                assertEquals(beforeLegacyRepair, fixture.store.state.value)
                assertArrayEquals(legacyBytes, Files.readAllBytes(recognizedCopy))
                assertArrayEquals(legacyBytes, Files.readAllBytes(legacyCopy))
                Files.delete(recognizedCopy)
                val legacyReimport = DesktopFileImporter(fixture.store, fixture.data).importSelected(listOf(legacySource.toString()))
                assertEquals(0, legacyReimport.imported)
                assertEquals(1, legacyReimport.duplicates)
                assertEquals(0, legacyReimport.failed)
                assertEquals(beforeLegacyRepair, fixture.store.state.value)
                fixture.reopenStore()
                assertEquals(beforeLegacyRepair, fixture.store.state.value)
                assertEquals(recognizedCopy.toString(), fixture.store.resolveManagedFile(legacyTrack.id))
                assertArrayEquals(legacyBytes, Files.readAllBytes(recognizedCopy))
                assertArrayEquals(legacyBytes, Files.readAllBytes(legacyCopy))
                assertArrayEquals(legacyBytes, Files.readAllBytes(legacySource))
                Files.list(managed.parent).use { files -> assertTrue(files.noneMatch { it.fileName.toString().endsWith(".part") }) }

                // Unknown legacy bytes remain retained until explicit format-named reimport.
                // This is a storage fixture, not evidence that the opaque bytes decode as MP3.
                val unknownBytes = byteArrayOf(6, 7, 8, 9)
                val unknownHash = MessageDigest.getInstance("SHA-256").digest(unknownBytes)
                    .joinToString("") { "%02x".format(it.toInt() and 255) }
                val unknownCopy = Files.write(managed.parent.resolve("$unknownHash.media"), unknownBytes)
                val unknownTrack = fixture.store.addImportedTrack(DesktopImportedTrack("unknown-track", "Retained", "",
                    null, unknownHash, unknownBytes.size.toLong(), unknownCopy.fileName.toString()))
                val retainedUnknown = fixture.store.state.value
                val unknownFailure = try { fixture.store.resolveManagedFile(unknownTrack.id); null }
                    catch (error: DesktopStorageException) { error }
                assertEquals("desktop_legacy_media_format_unknown", unknownFailure?.code)
                assertEquals(retainedUnknown, fixture.store.state.value)
                assertArrayEquals(unknownBytes, Files.readAllBytes(unknownCopy))
                val explicitSource = Files.write(fixture.sources.resolve("Explicit opaque.mp3"), unknownBytes)
                val explicitRepair = DesktopFileImporter(fixture.store, fixture.data).importSelected(listOf(explicitSource.toString()))
                assertEquals(0, explicitRepair.imported)
                assertEquals(1, explicitRepair.duplicates)
                assertEquals(0, explicitRepair.failed)
                assertEquals(retainedUnknown, fixture.store.state.value)
                fixture.reopenStore()
                assertEquals(retainedUnknown, fixture.store.state.value)
                val explicitCopy = Path.of(checkNotNull(fixture.store.resolveManagedFile(unknownTrack.id)))
                assertEquals("$unknownHash.mp3", explicitCopy.fileName.toString())
                assertArrayEquals(unknownBytes, Files.readAllBytes(explicitCopy))
                assertArrayEquals(unknownBytes, Files.readAllBytes(unknownCopy))
                assertArrayEquals(unknownBytes, Files.readAllBytes(explicitSource))

                // Hold after a real commit: cancellation cannot undo it or start the next source.
                val cancellationBytes = wave(700)
                val cancellationSource = Files.write(fixture.sources.resolve("Cancelled batch first.wav"), cancellationBytes)
                val nextSource = Files.write(fixture.sources.resolve("Cancelled batch next.wav"), wave(900))
                val retainedStore = fixture.store
                val oldIds = retainedStore.state.value.tracks.map { it.id }.toSet()
                val committed = CompletableDeferred<DesktopTrack>()
                val heldStore = object : DesktopLibraryStore by retainedStore {
                    override suspend fun commitImportJobCandidate(
                        settlement: DesktopImportJobSettlement, permit: DesktopImportJobPermit, track: DesktopImportedTrack,
                    ): DesktopImportJob {
                        val job = retainedStore.commitImportJobCandidate(settlement, permit, track)
                        val trackId = job.candidates.single { it.candidate.id == settlement.candidateId }.trackId
                        committed.complete(retainedStore.state.value.tracks.single { it.id == trackId })
                        awaitCancellation()
                    }
                }
                val cancelledBatch = launch {
                    DesktopFileImporter(heldStore, fixture.data).importSelected(listOf(cancellationSource.toString(), nextSource.toString()))
                }
                val retainedTrack = committed.await()
                cancelledBatch.cancelAndJoin()
                assertTrue(cancelledBatch.isCancelled)
                assertEquals(listOf(retainedTrack.id), retainedStore.state.value.tracks.filter { it.id !in oldIds }.map { it.id })
                val retainedCopy = Path.of(checkNotNull(retainedStore.resolveManagedFile(retainedTrack.id)))
                assertArrayEquals(cancellationBytes, Files.readAllBytes(retainedCopy))
                assertArrayEquals(cancellationBytes, Files.readAllBytes(cancellationSource))
                Files.list(retainedCopy.parent).use { files -> assertTrue(files.noneMatch { it.fileName.toString().endsWith(".part") }) }
                val resumed = DesktopFileImporter(retainedStore, fixture.data).importSelected(listOf(cancellationSource.toString(), nextSource.toString()))
                assertEquals(1, resumed.duplicates)
                assertEquals(1, resumed.imported)
                assertEquals(0, resumed.failed)
                assertEquals(retainedTrack.id, retainedStore.state.value.tracks.single { it.sha256 == retainedTrack.sha256 }.id)

                // Header metadata is optional: importing verified bytes is not decoder acceptance.
                fun alteredWave(change: (ByteBuffer) -> Unit): ByteArray = bytes.copyOf().also {
                    change(ByteBuffer.wrap(it).order(ByteOrder.LITTLE_ENDIAN))
                }
                fun withChunks(chunks: ByteArray, leading: Boolean = false): ByteArray {
                    val combined = if (leading) bytes.copyOfRange(0, 12) + chunks + bytes.copyOfRange(12, bytes.size) else bytes + chunks
                    ByteBuffer.wrap(combined).order(ByteOrder.LITTLE_ENDIAN).putInt(4, combined.size - 8)
                    return combined
                }
                val stereo8 = alteredWave { it.putShort(22, 2.toShort()); it.putShort(34, 8.toShort()) }
                val fractional = alteredWave { it.putInt(24, 11_025); it.putInt(28, 22_050) }
                val paddedJunk = ByteBuffer.allocate(10).order(ByteOrder.LITTLE_ENDIAN).apply {
                    put("JUNK".toByteArray(Charsets.US_ASCII)); putInt(1); put(1.toByte()); put(0.toByte())
                }.array()
                val oversizedJunk = ByteBuffer.allocate(8 + 256 * 1024).order(ByteOrder.LITTLE_ENDIAN).apply {
                    put("JUNK".toByteArray(Charsets.US_ASCII)); putInt(256 * 1024)
                }.array()
                val manyChunks = ByteBuffer.allocate(1_023 * 8).order(ByteOrder.LITTLE_ENDIAN).apply {
                    repeat(1_023) { put("JUNK".toByteArray(Charsets.US_ASCII)); putInt(0) }
                }.array()
                val aboveCap = wave(40_000).also { ByteBuffer.wrap(it).order(ByteOrder.LITTLE_ENDIAN).putInt(24, 1).putInt(28, 2) }
                val metadataCases = listOf(
                    Triple("Stereo 8 bit", stereo8, 250L),
                    Triple("Fractional frame time", fractional, 362L),
                    Triple("Padded metadata", withChunks(paddedJunk, leading = true), 250L),
                    Triple("Compressed header", alteredWave { it.putShort(20, 2.toShort()) }, null),
                    Triple("Wrong byte rate", alteredWave { it.putInt(28, 1) }, null),
                    Triple("Partial frame", alteredWave { it.putInt(40, bytes.size - 45) }, null),
                    Triple("Truncated container", bytes.copyOf(bytes.size - 1), null),
                    Triple("Duplicate format", withChunks(bytes.copyOfRange(12, 36)), null),
                    Triple("Nonzero padding", withChunks(paddedJunk.copyOf().also { it[9] = 1 }, leading = true), null),
                    Triple("Oversized metadata", withChunks(oversizedJunk, leading = true), null),
                    Triple("Chunk limit", withChunks(manyChunks, leading = true), null),
                    Triple("Duration cap", aboveCap, null),
                )
                metadataCases.forEach { (name, mediaBytes, expectedDuration) ->
                    val metadataSource = Files.write(fixture.sources.resolve("$name.wav"), mediaBytes)
                    val metadataReport = DesktopFileImporter(retainedStore, fixture.data).importSelected(listOf(metadataSource.toString()))
                    assertEquals(1, metadataReport.imported)
                    assertEquals(0, metadataReport.failed)
                    assertEquals(expectedDuration, retainedStore.state.value.tracks.single { it.title == name }.durationMs)
                    assertArrayEquals(mediaBytes, Files.readAllBytes(metadataSource))
                }
                val withMetadata = retainedStore.state.value
                fixture.reopenStore()
                assertEquals(withMetadata, fixture.store.state.value)
                settingsTransferAssertions(fixture)
                meteredPreferenceAssertions(fixture)
            }
        }
    }

    @Test fun symlinkSelectionsAndManagedCapabilitiesAreRejectedWithoutTouchingOriginal(): Unit = runBlocking {
        withTimeout(30_000) {
            DesktopAcceptanceFixture().use { fixture ->
                fixture.openStore()
                settingsLifecycleAssertions(fixture)
                val trackId = fixture.importWav(durationMs = 250)
                val source = fixture.sources.resolve("Synthetic tone.wav")
                val bytes = Files.readAllBytes(source)
                val link = fixture.sources.resolve("Selected link.wav")
                try { Files.createSymbolicLink(link, source) } catch (failure: Exception) {
                    // Windows can deny symlink creation without developer mode/admin capability.
                    if (!System.getProperty("os.name").startsWith("Windows")) throw failure
                    assumeTrue("Windows host lacks symlink fixture capability", false)
                }
                val result = DesktopFileImporter(fixture.store, fixture.data).importSelected(listOf(link.toString()))
                assertEquals(1, result.failed)
                assertEquals("import_symlink_rejected", result.errorCode)
                val reviewed = Files.write(fixture.sources.resolve("Reviewed symlink.wav"), wave(260))
                val reviewer = DesktopFileImporter(fixture.store, fixture.data)
                val preview = reviewer.prepareSelected(listOf(reviewed.toString()))
                assertEquals(1, preview.files.size)
                Files.delete(reviewed)
                Files.createSymbolicLink(reviewed, source)
                val changed = reviewer.importPrepared(preview.id) { }
                assertEquals(0, changed.imported)
                assertEquals(1, changed.failed)
                assertEquals("import_source_changed", changed.errorCode)
                assertEquals("import_preview_expired", reviewer.importPrepared(preview.id) { }.errorCode)
                assertArrayEquals(bytes, Files.readAllBytes(source))
                val managed = Path.of(checkNotNull(fixture.store.resolveManagedFile(trackId)))
                Files.delete(managed)
                Files.createSymbolicLink(managed, source)
                assertNull(fixture.store.resolveManagedFile(trackId))
                val reimport = DesktopFileImporter(fixture.store, fixture.data).importSelected(listOf(source.toString()))
                assertEquals(1, reimport.failed)
                assertEquals("import_symlink_rejected", reimport.errorCode)
                assertTrue(Files.isSymbolicLink(managed))
                assertArrayEquals(bytes, Files.readAllBytes(source))
                val settingsRead = runCatching { readSettingsImport(link) }.exceptionOrNull()
                assertTrue(settingsRead is DesktopSettingsFileException)
                val settingsWrite = runCatching { writeSettingsExportNew(link, byteArrayOf(1)) }.exceptionOrNull()
                assertEquals("desktop_settings_export_target_exists", (settingsWrite as? DesktopSettingsFileException)?.code)
                assertArrayEquals(bytes, Files.readAllBytes(source))
            }
        }
    }

    /** Authored metadata containers only: these bytes make no audio/decoder claim. */
    private suspend fun mp4MetadataAssertions(): Unit {
        DesktopAcceptanceFixture(unicodeDataRoot = true).use { fixture ->
            fixture.openStore()
            fun read(encoded: ByteArray): DesktopEmbeddedMetadata {
                val path = Files.write(fixture.sources.resolve("item-list.bin"), encoded)
                val modified = Files.getLastModifiedTime(path)
                val result = DesktopEmbeddedMetadataReader.read(path, encoded.size.toLong())
                assertArrayEquals(encoded, Files.readAllBytes(path))
                assertEquals(modified, Files.getLastModifiedTime(path))
                assertEquals("MP4", result.format)
                return result
            }
            fun rejected(encoded: ByteArray, status: String = "INVALID"): Unit {
                val result = read(encoded)
                assertEquals(status, result.status)
                assertNull(result.title)
                assertNull(result.artist)
                assertNull(result.album)
                assertNull(result.artwork)
            }
            val title = "  \u041c\u0443\u0437\u044b\u043a\u0430 \uD83C\uDFB5  "
            val titleItem = mp4Item("\u00a9nam", title.toByteArray(Charsets.UTF_8))
            val artistItem = mp4Item("\u00a9ART", "Caf\u00e9 artist".toByteArray(Charsets.UTF_16BE), type = 2)
            val albumItem = mp4Item("\u00a9alb", "Album evidence".toByteArray(Charsets.UTF_8))
            fun picture(format: String, width: Int = 16, height: Int = 8): ByteArray {
                val image = java.awt.image.BufferedImage(width, height, java.awt.image.BufferedImage.TYPE_INT_RGB)
                return try {
                    java.io.ByteArrayOutputStream().use { output ->
                        assertTrue(javax.imageio.ImageIO.write(image, format, output))
                        output.toByteArray()
                    }
                } finally { image.flush() }
            }
            val png = picture("png", 640, 320)
            val valid = mp4File(titleItem + artistItem + albumItem + mp4Item("covr", png, 14))
            val parsed = read(valid)
            assertEquals("AVAILABLE", parsed.status)
            assertEquals(title, parsed.title)
            assertEquals("Caf\u00e9 artist", parsed.artist)
            assertEquals("Album evidence", parsed.album)
            val cover = checkNotNull(parsed.artwork)
            assertEquals(512, cover.width)
            assertEquals(256, cover.height)
            assertTrue(cover.byteSize <= DesktopEmbeddedMetadataLimits.MAX_THUMBNAIL_BYTES)
            val thumbnail = cover.copyPngBytes()
            assertArrayEquals(byteArrayOf(-119, 80, 78, 71, 13, 10, 26, 10), thumbnail.copyOf(8))
            assertEquals(MessageDigest.getInstance("SHA-256").digest(thumbnail).joinToString("") {
                "%02x".format(it.toInt() and 255)
            }, cover.sha256)
            cover.copyPngBytes().fill(0)
            assertArrayEquals(thumbnail, cover.copyPngBytes())
            val jpegResult = read(mp4File(titleItem + mp4Item("covr", picture("jpeg"), 13)))
            assertEquals(16, checkNotNull(jpegResult.artwork).width)
            assertEquals(title, jpegResult.title)
            assertEquals("NO_TAGS", read(mp4File(byteArrayOf())).status)
            assertEquals("NO_TAGS", read(mp4Ftyp() + mp4Atom("moov", byteArrayOf())).status)
            assertEquals("NO_TAGS", read(mp4File(mp4Item("\u00a9nam", byteArrayOf()))).status)
            assertEquals(title, read(mp4File(titleItem + mp4Item("covr", byteArrayOf(1, 2), 14))).title)
            assertNull(read(mp4File(titleItem + mp4Item("covr", byteArrayOf(1, 2), 14))).artwork)
            val oversizedCover = read(mp4File(titleItem + mp4Item("covr",
                ByteArray(DesktopEmbeddedMetadataLimits.MAX_IMAGE_BYTES + 1), 14)))
            assertEquals(title, oversizedCover.title)
            assertNull(oversizedCover.artwork)
            assertNull(read(mp4File(titleItem + mp4Item("covr",
                picture("png", DesktopEmbeddedMetadataLimits.MAX_IMAGE_DIMENSION + 1, 1), 14))).artwork)

            rejected(mp4File(titleItem + titleItem))
            rejected(mp4File(mp4Item("covr", png, 14) + mp4Item("covr", png, 14)))
            rejected(mp4File(mp4Atom("\u00a9nam", mp4Atom("data", ByteArray(7)))))
            rejected(mp4File(mp4Atom("\u00a9nam", byteArrayOf())))
            rejected(mp4File(mp4Atom("\u00a9nam", mp4Data("one".toByteArray()) + mp4Data("two".toByteArray()))))
            rejected(mp4File(mp4Item("\u00a9nam", byteArrayOf(-61, 40))))
            rejected(mp4File(mp4Item("\u00a9ART", byteArrayOf(0), 2)))
            rejected(mp4File(mp4Item("\u00a9nam", byteArrayOf(0))))
            rejected(mp4File(mp4Item("\u00a9nam", "x".repeat(4097).toByteArray())))
            rejected(mp4File(mp4Item("\u00a9nam", ByteArray(16_389))))
            rejected(mp4File(mp4Item("\u00a9nam", byteArrayOf(1), 21)), "UNSUPPORTED")
            rejected(mp4File(mp4Item("covr", byteArrayOf(1), 27)), "UNSUPPORTED")
            rejected(mp4File(mp4Item("\u00a9nam", "localized".toByteArray(), locale = 1)), "UNSUPPORTED")
            rejected(valid.copyOf(valid.size - 1))
            rejected(mp4Ftyp() + mp4Atom("moov", mp4U32(7) + "udta".toByteArray()))
            rejected(mp4Ftyp() + mp4Atom("moov", mp4U32(0) + "udta".toByteArray()))
            rejected(mp4Ftyp() + mp4U32(1) + "moov".toByteArray() + mp4U64(Long.MAX_VALUE))
            rejected(mp4Ftyp() + mp4U32(1) + "moov".toByteArray() + ByteArray(8) { -1 })
            rejected(mp4Ftyp() + mp4U32(1) + "moov".toByteArray() + mp4U64(8))
            rejected(mp4Ftyp() + mp4U32(1) + "moov".toByteArray() + byteArrayOf(0))
            rejected(valid + mp4Atom("moov", byteArrayOf()))
            rejected(valid + mp4Ftyp())
            rejected(mp4Atom("ftyp", "M4P ".toByteArray() + mp4U32(0)) + mp4Atom("moov", mp4Meta(titleItem)), "UNSUPPORTED")
            rejected(mp4Ftyp() + mp4Atom("moov", mp4Atom("udta", titleItem)), "UNSUPPORTED")
            rejected(mp4Ftyp() + mp4Atom("moov", mp4Meta(titleItem) + mp4Meta(titleItem)))
            val handler = mp4Handler()
            val itemList = mp4Atom("ilst", titleItem)
            rejected(mp4Ftyp() + mp4Atom("moov", mp4Atom("udta",
                mp4Atom("meta", mp4U32(0) + handler + itemList + itemList))))
            rejected(mp4Ftyp() + mp4Atom("moov", mp4Atom("udta",
                mp4Atom("meta", mp4U32(0) + handler + handler + itemList))))
            rejected(mp4Ftyp() + mp4Atom("moov", mp4Atom("udta",
                mp4Atom("meta", mp4U32(0) + itemList))))
            rejected(mp4Ftyp() + mp4Atom("moov", mp4Atom("udta",
                mp4Atom("meta", handler + itemList))), "UNSUPPORTED")
            rejected(mp4Ftyp() + mp4Atom("moov", mp4Atom("udta",
                mp4Atom("meta", mp4U32(1) + handler + itemList))), "UNSUPPORTED")
            rejected(mp4Ftyp() + mp4Atom("moov", mp4Atom("udta",
                mp4Atom("meta", mp4U32(0) + mp4Handler("mdta") + itemList))), "UNSUPPORTED")
            rejected(mp4Ftyp() + mp4Atom("moov", mp4Atom("udta",
                mp4Atom("meta", mp4U32(0) + handler + mp4Atom("keys", byteArrayOf()) + itemList))), "UNSUPPORTED")
            var nested = titleItem
            repeat(32) { nested = mp4Atom("moov", nested) }
            rejected(mp4Ftyp() + nested, "UNSUPPORTED")
            val excessiveParts = java.io.ByteArrayOutputStream().apply {
                repeat(DesktopEmbeddedMetadataLimits.MAX_PARTS) { write(mp4Atom("free", byteArrayOf())) }
            }.toByteArray()
            rejected(mp4File(titleItem) + excessiveParts)
            rejected(mp4File(mp4Item("unkn", ByteArray(DesktopEmbeddedMetadataLimits.MAX_METADATA_BYTES))))
            listOf("moof", "pssh", "sidx", "uuid").forEach {
                rejected(valid + mp4Atom(it, byteArrayOf()), "UNSUPPORTED")
            }
            listOf("mvex", "cmov", "rmra").forEach {
                rejected(mp4Ftyp() + mp4Atom("moov", mp4Atom(it, byteArrayOf()) + mp4Meta(titleItem)), "UNSUPPORTED")
            }
            fun track(sample: ByteArray): ByteArray = mp4Atom("trak", mp4Atom("mdia", mp4Atom("minf",
                mp4Atom("stbl", mp4Atom("stsd", mp4U32(0) + mp4U32(1) + sample)))))
            val soundHeader = ByteArray(28).also { it[7] = 1 }
            val sample = mp4Atom("mp4a", soundHeader + mp4Atom("esds", byteArrayOf()))
            assertEquals(title, read(mp4Ftyp() + mp4Atom("moov", track(sample) + mp4Meta(titleItem))).title)
            listOf("enca", "drms", "encv").forEach {
                rejected(mp4Ftyp() + mp4Atom("moov", track(mp4Atom(it, soundHeader)) + mp4Meta(titleItem)), "UNSUPPORTED")
            }
            rejected(mp4Ftyp() + mp4Atom("moov", track(mp4Atom("mp4a",
                soundHeader + mp4Atom("sinf", byteArrayOf()))) + mp4Meta(titleItem)), "UNSUPPORTED")

            // Extended sizes and a legal top-level size-zero final atom remain bounded.
            val extended = mp4Ftyp(extended = true) + mp4Atom("moov", mp4Meta(titleItem), extended = true)
            assertEquals(title, read(extended).title)
            assertEquals(title, read(mp4File(mp4Atom("\u00a9nam",
                mp4Atom("data", mp4U32(1) + mp4U32(0) + title.toByteArray(Charsets.UTF_8), extended = true),
                extended = true))).title)
            assertEquals(title, read(valid + mp4U32(0) + "mdat".toByteArray() + byteArrayOf(7, 8)).title)
            listOf("free", "mdat").forEach { kind ->
                val path = fixture.sources.resolve("sparse-$kind.m4a")
                val prefix = mp4Ftyp()
                val gap = 0x1_0000_0040L
                val tail = mp4Atom("moov", mp4Meta(titleItem))
                FileChannel.open(path, CREATE_NEW, WRITE, SPARSE).use { channel ->
                    var offset = 0L
                    fun put(value: ByteArray): Unit {
                        val buffer = ByteBuffer.wrap(value)
                        var stalls = 0
                        while (buffer.hasRemaining()) {
                            val written = channel.write(buffer, offset)
                            if (written == 0) { check(++stalls <= 3) } else { stalls = 0; offset += written }
                        }
                    }
                    put(prefix)
                    put(mp4U32(1) + kind.toByteArray() + mp4U64(gap))
                    offset = prefix.size + gap
                    put(tail)
                }
                val snapshot = Files.getLastModifiedTime(path)
                assertEquals(title, DesktopEmbeddedMetadataReader.read(path, Files.size(path)).title)
                assertEquals(prefix.size + gap + tail.size, Files.size(path))
                assertEquals(snapshot, Files.getLastModifiedTime(path))
            }

            // Import uppercase M4A through the same read-only managed capability as older tags.
            val source = Files.write(fixture.sources.resolve("\u041c\u0443\u0437\u044b\u043a\u0430.M4A"), valid)
            val report = DesktopFileImporter(fixture.store, fixture.data).importSelected(listOf(source.toString()))
            assertEquals(1, report.imported)
            assertEquals(0, report.failed)
            val stored = fixture.store.state.value
            val imported = stored.tracks.single()
            val managed = Path.of(checkNotNull(fixture.store.resolveMetadataFile(imported.id)))
            assertTrue(managed.fileName.toString().endsWith(".m4a"))
            assertEquals(title, DesktopEmbeddedMetadataReader.read(managed, imported.byteSize).title)
            assertEquals(stored, fixture.store.state.value)
            assertArrayEquals(valid, Files.readAllBytes(source))
            assertArrayEquals(valid, Files.readAllBytes(managed))
            // Deterministic stale-size seam; no claim to catch an adversary restoring attributes.
            val mutable = Files.write(fixture.sources.resolve("mutable.m4a"), valid)
            Files.write(mutable, valid + byteArrayOf(0))
            assertEquals("UNAVAILABLE", DesktopEmbeddedMetadataReader.read(mutable, valid.size.toLong()).status)
            Files.delete(mutable)
            assertEquals("UNAVAILABLE", DesktopEmbeddedMetadataReader.read(mutable, valid.size.toLong()).status)
            Thread.currentThread().interrupt()
            try {
                try { DesktopEmbeddedMetadataReader.read(managed, imported.byteSize); fail("Interrupted MP4 read accepted") }
                catch (expected: InterruptedException) { assertTrue(Thread.currentThread().isInterrupted) }
            } finally { Thread.interrupted() }
            fixture.reopenStore()
            assertEquals(stored, fixture.store.state.value)
        }
    }

    private fun mp4U32(value: Long): ByteArray = ByteBuffer.allocate(4).putInt(value.toInt()).array()
    private fun mp4U64(value: Long): ByteArray = ByteBuffer.allocate(8).putLong(value).array()
    private fun mp4Atom(type: String, payload: ByteArray, extended: Boolean = false): ByteArray =
        (if (extended) mp4U32(1) + type.toByteArray(Charsets.ISO_8859_1) + mp4U64(payload.size + 16L)
        else mp4U32(payload.size + 8L) + type.toByteArray(Charsets.ISO_8859_1)) + payload
    private fun mp4Ftyp(extended: Boolean = false): ByteArray = mp4Atom("ftyp",
        "M4A ".toByteArray() + mp4U32(0) + "isommp42".toByteArray(), extended)
    private fun mp4Handler(type: String = "mdir"): ByteArray = mp4Atom("hdlr",
        mp4U32(0) + mp4U32(0) + type.toByteArray() + ByteArray(12))
    private fun mp4Data(value: ByteArray, type: Long = 1, locale: Long = 0): ByteArray =
        mp4Atom("data", mp4U32(type) + mp4U32(locale) + value)
    private fun mp4Item(name: String, value: ByteArray, type: Long = 1, locale: Long = 0): ByteArray =
        mp4Atom(name, mp4Data(value, type, locale))
    private fun mp4Meta(items: ByteArray): ByteArray = mp4Atom("udta",
        mp4Atom("meta", mp4U32(0) + mp4Handler() + mp4Atom("ilst", items)))
    private fun mp4File(items: ByteArray): ByteArray = mp4Ftyp() + mp4Atom("moov", mp4Meta(items))

    /** Source-only additions to the existing composite; never opens an OS chooser. */
    private suspend fun importReviewAssertions(): Unit {
        DesktopAcceptanceFixture().use { fixture ->
            fixture.openStore()
            val retainedStore = fixture.store
            val reads = AtomicInteger()
            val writes = AtomicInteger()
            val creates = AtomicInteger()
            val guarded = object : DesktopLibraryStore by retainedStore {
                override val state: StateFlow<DesktopLibrarySnapshot>
                    get() = retainedStore.state.also { reads.incrementAndGet() }
                override suspend fun createImportJob(manifest: DesktopImportJobManifest): DesktopImportJob {
                    creates.incrementAndGet()
                    return retainedStore.createImportJob(manifest)
                }
                override suspend fun commitImportJobCandidate(
                    settlement: DesktopImportJobSettlement, permit: DesktopImportJobPermit, track: DesktopImportedTrack,
                ): DesktopImportJob {
                    writes.incrementAndGet()
                    return retainedStore.commitImportJobCandidate(settlement, permit, track)
                }
            }
            val source = Files.write(fixture.sources.resolve("Reviewed.wav"), wave(311))
            val absentRoot = fixture.sandbox.resolve("preview-must-not-create")
            val observationOnly = DesktopFileImporter(guarded, absentRoot)
            val observed = observationOnly.prepareSelected(listOf(source.toString()))
            assertEquals(1, observed.files.size)
            assertEquals("Reviewed", observed.files.single().title)
            assertEquals(Files.size(source), observed.totalBytes)
            assertEquals(0, reads.get())
            assertEquals(0, writes.get())
            assertEquals(0, creates.get())
            assertNull(retainedStore.loadImportJob(checkNotNull(observed.jobId)))
            assertFalse(Files.exists(absentRoot))
            observationOnly.discardPrepared(observed.id)
            assertEquals("import_preview_expired", observationOnly.importPrepared(observed.id) { }.errorCode)
            val malformed = observationOnly.prepareSelected(listOf("", "\u0000"))
            assertTrue(malformed.files.isEmpty())
            assertEquals(2, malformed.failed)
            val emptyProgress = mutableListOf<DesktopImportProgress>()
            val emptyResult = observationOnly.importPrepared(malformed.id) { emptyProgress.add(it) }
            assertEquals(2, emptyResult.failed)
            assertTrue(emptyProgress.all { it.totalFiles == 0 && it.completedFiles == 0 && it.failed == 2 })
            assertFalse(Files.exists(absentRoot))
            assertEquals(0, reads.get())
            assertEquals(0, writes.get())
            assertEquals(0, creates.get())

            // A stale ID/discard cannot erase a newer exact plan or regenerate a consumed one.
            val importer = DesktopFileImporter(guarded, fixture.data)
            val old = importer.prepareSelected(listOf(fixture.sources.toString()))
            val preview = importer.prepareSelected(listOf(fixture.sources.toString()))
            assertNotEquals(old.id, preview.id)
            importer.discardPrepared(old.id)
            assertEquals("import_preview_expired", importer.importPrepared("invalid") { }.errorCode)
            assertEquals("import_preview_expired", importer.importPrepared(old.id) { }.errorCode)
            val added = Files.write(fixture.sources.resolve("Added after review.wav"), wave(312))
            val progress = mutableListOf<DesktopImportProgress>()
            val result = importer.importPrepared(preview.id) {
                progress.add(it)
                // Observer failures cannot discard counts or interrupt managed-copy cleanup.
                if (it.imported == 1) throw IllegalStateException("synthetic observer failure")
            }
            assertEquals(1, result.imported)
            assertEquals(1, creates.get())
            val durable = checkNotNull(retainedStore.loadImportJob(checkNotNull(preview.jobId)))
            assertEquals(DesktopImportJobPhase.COMPLETED, durable.phase)
            assertEquals(DesktopImportJobOutcome.IMPORTED, durable.candidates.single().outcome)
            assertEquals(0, result.failed)
            assertEquals(listOf("Reviewed"), retainedStore.state.value.tracks.map { it.title })
            assertArrayEquals(wave(312), Files.readAllBytes(added))
            assertEquals(1, progress.last().imported)
            assertEquals(1, progress.last().completedFiles)
            assertTrue(progress.all { it.totalFiles == 1 && it.currentTitle?.contains(fixture.sandbox.toString()) != true })
            assertTrue(progress.zipWithNext().all { (a, b) -> a.completedFiles <= b.completedFiles })
            assertEquals("import_preview_expired", importer.importPrepared(preview.id) { }.errorCode)
            val dismissed = importer.prepareSelected(listOf(source.toString()))
            importer.discardPrepared(dismissed.id)
            assertEquals("import_preview_expired", importer.importPrepared(dismissed.id) { }.errorCode)

            val changing = Files.createDirectory(fixture.sources.resolve("changing"))
            val changed = Files.write(changing.resolve("Changed.wav"), wave(320))
            val missing = Files.write(changing.resolve("Missing.wav"), wave(321))
            val replaced = Files.write(changing.resolve("Replaced.wav"), wave(322))
            val oversize = Files.write(changing.resolve("Oversize.wav"), wave(323))
            val changedPreview = importer.prepareSelected(listOf(changed.toString(), missing.toString(),
                replaced.toString(), oversize.toString()))
            assertEquals(4, changedPreview.files.size)
            Files.write(changed, wave(324))
            Files.delete(missing)
            val replacedTime = Files.getLastModifiedTime(replaced)
            Files.delete(replaced)
            Files.write(replaced, wave(322))
            Files.setLastModifiedTime(replaced, FileTime.fromMillis(replacedTime.toMillis() + 2_000))
            Files.delete(oversize)
            FileChannel.open(oversize, CREATE_NEW, WRITE, SPARSE).use {
                it.position(2L * 1024 * 1024 * 1024)
                it.write(ByteBuffer.wrap(byteArrayOf(0)))
            }
            val unchangedState = retainedStore.state.value
            val changedProgress = mutableListOf<DesktopImportProgress>()
            val rejected = importer.importPrepared(changedPreview.id) { changedProgress.add(it) }
            assertEquals(0, rejected.imported)
            assertEquals(4, rejected.failed)
            assertEquals("import_source_changed", rejected.errorCode)
            assertEquals(4, changedProgress.last().completedFiles)
            assertEquals(unchangedState, retainedStore.state.value)
            assertEquals("import_preview_expired", importer.importPrepared(changedPreview.id) { }.errorCode)
            val tooLarge = importer.prepareSelected(listOf(oversize.toString()))
            assertTrue(tooLarge.files.isEmpty())
            assertEquals("import_file_size_limit", tooLarge.errorCode)
            importer.discardPrepared(tooLarge.id)

            // An active consume fences replacement preparation and a second consume.
            val entered = CompletableDeferred<Unit>()
            val release = CompletableDeferred<Unit>()
            val gated = object : DesktopLibraryStore by retainedStore {
                override suspend fun commitImportJobCandidate(
                    settlement: DesktopImportJobSettlement, permit: DesktopImportJobPermit, track: DesktopImportedTrack,
                ): DesktopImportJob {
                    entered.complete(Unit)
                    withTimeout(15_000) { release.await() }
                    return retainedStore.commitImportJobCandidate(settlement, permit, track)
                }
            }
            val serialized = DesktopFileImporter(gated, fixture.data)
            val activePreview = serialized.prepareSelected(listOf(source.toString()))
            var duplicateCount = 0
            val active = CoroutineScope(currentCoroutineContext()).launch {
                duplicateCount = serialized.importPrepared(activePreview.id) { }.duplicates
            }
            withTimeout(15_000) { entered.await() }
            try {
                val busy = serialized.prepareSelected(listOf(added.toString()))
                assertEquals("import_busy", busy.errorCode)
                assertTrue(busy.files.isEmpty())
                assertEquals("import_preview_expired", serialized.importPrepared(activePreview.id) { }.errorCode)
                serialized.discardPrepared(activePreview.id)
                serialized.discardPrepared(busy.id)
            } finally {
                release.complete(Unit)
                withTimeout(15_000) { active.join() }
            }
            assertEquals(1, duplicateCount)

            // Cancellation directly after an adapter returns a committed row retains settled counts.
            val first = Files.write(fixture.sources.resolve("Settled first.wav"), wave(330))
            val next = Files.write(fixture.sources.resolve("Unstarted next.wav"), wave(331))
            val held = object : DesktopLibraryStore by retainedStore {
                override suspend fun commitImportJobCandidate(
                    settlement: DesktopImportJobSettlement, permit: DesktopImportJobPermit, track: DesktopImportedTrack,
                ): DesktopImportJob {
                    val committed = retainedStore.commitImportJobCandidate(settlement, permit, track)
                    currentCoroutineContext().cancel()
                    return committed
                }
            }
            val cancellable = DesktopFileImporter(held, fixture.data)
            val cancelPreview = cancellable.prepareSelected(listOf(first.toString(), next.toString(), ""))
            val settled = mutableListOf<DesktopImportProgress>()
            var cancellation: CancellationException? = null
            val oldIds = retainedStore.state.value.tracks.map { it.id }.toSet()
            val worker = CoroutineScope(currentCoroutineContext()).launch {
                try {
                    cancellable.importPrepared(cancelPreview.id) { settled.add(it) }
                } catch (failure: CancellationException) {
                    cancellation = failure
                    throw failure
                }
            }
            withTimeout(15_000) { worker.join() }
            assertTrue(worker.isCancelled)
            assertNotNull(cancellation)
            assertEquals(1, settled.last().imported)
            assertEquals(1, settled.last().failed)
            assertEquals(1, settled.last().completedFiles)
            assertEquals(2, settled.last().totalFiles)
            assertTrue(settled.size <= 6)
            val newlyCommitted = retainedStore.state.value.tracks.filter { it.id !in oldIds }
            assertEquals(listOf("Settled first"), newlyCommitted.map { it.title })
            val managed = Path.of(checkNotNull(retainedStore.resolveManagedFile(newlyCommitted.single().id)))
            assertArrayEquals(wave(330), Files.readAllBytes(managed))
            Files.list(managed.parent).use { files -> assertTrue(files.noneMatch { it.fileName.toString().endsWith(".part") }) }
            assertEquals("import_preview_expired", cancellable.importPrepared(cancelPreview.id) { }.errorCode)
            assertArrayEquals(wave(331), Files.readAllBytes(next))
            val cancelledJob = checkNotNull(retainedStore.loadImportJob(checkNotNull(cancelPreview.jobId)))
            assertEquals(DesktopImportJobPhase.CANCELLED, cancelledJob.phase)
            assertEquals(DesktopImportJobOutcome.IMPORTED, cancelledJob.candidates.first().outcome)
            assertNull(cancelledJob.candidates.last().outcome)
            assertEquals(cancelledJob.revision, settled.last().jobRevision)

            // An uncertain commit carries the original cancellation and safe restart marker.
            val markedCancellation = CancellationException("synthetic import cancellation").also {
                it.addSuppressed(DesktopStorageException("desktop_snapshot_unavailable"))
            }
            val uncertain = object : DesktopLibraryStore by retainedStore {
                override suspend fun commitImportJobCandidate(
                    settlement: DesktopImportJobSettlement, permit: DesktopImportJobPermit, track: DesktopImportedTrack,
                ): DesktopImportJob {
                    retainedStore.commitImportJobCandidate(settlement, permit, track)
                    throw markedCancellation
                }
            }
            val uncertainImporter = DesktopFileImporter(uncertain, fixture.data)
            val uncertainPreview = uncertainImporter.prepareSelected(listOf(next.toString()))
            val uncertainProgress = mutableListOf<DesktopImportProgress>()
            val propagated = try {
                uncertainImporter.importPrepared(uncertainPreview.id) { uncertainProgress.add(it) }
                null
            } catch (failure: CancellationException) {
                failure
            }
            assertEquals(markedCancellation.message, checkNotNull(propagated).message)
            assertTrue(desktopSnapshotUnavailable(propagated))
            assertEquals(0, uncertainProgress.last().completedFiles)
            assertEquals(0, uncertainProgress.last().imported)
            assertEquals("import_preview_expired", uncertainImporter.importPrepared(uncertainPreview.id) { }.errorCode)
            val uncertainTrack = retainedStore.state.value.tracks.single { it.title == "Unstarted next" }
            val uncertainCopy = Path.of(checkNotNull(retainedStore.resolveManagedFile(uncertainTrack.id)))
            assertArrayEquals(wave(331), Files.readAllBytes(uncertainCopy))
            Files.list(uncertainCopy.parent).use { files -> assertTrue(files.noneMatch { it.fileName.toString().endsWith(".part") }) }
        }
    }

    /** Source assertions only, retained inside the existing import composite until checks resume. */
    private suspend fun importJobAdapterAssertions(): Unit {
        DesktopAcceptanceFixture().use { fixture ->
            fixture.openStore()
            val first = Files.write(fixture.sources.resolve("Old first.wav"), wave(401))
            val second = Files.write(fixture.sources.resolve("Old second.wav"), wave(402))
            val initialImporter = DesktopFileImporter(fixture.store, fixture.data)
            val initial = initialImporter.prepareSelected(listOf(first.toString(), second.toString()))
            val jobId = checkNotNull(initial.jobId)
            assertNull(fixture.store.loadImportJob(jobId))
            Files.delete(first)
            Files.delete(second)
            assertEquals(2, initialImporter.importPrepared(initial.id) { }.failed)
            val failed = checkNotNull(fixture.store.loadImportJob(jobId))
            assertEquals(DesktopImportJobPhase.PAUSED, failed.phase)
            assertTrue(failed.candidates.all { it.outcome == DesktopImportJobOutcome.FAILED })
            fixture.reopenStore()
            assertEquals(failed, fixture.store.loadImportJob(jobId))

            val other = Files.write(fixture.sources.resolve("New unrelated.wav"), wave(403))
            val chosen = Files.write(fixture.sources.resolve("New chosen.wav"), wave(404))
            val resumed = DesktopFileImporter(fixture.store, fixture.data)
            assertEquals("import_preview_expired", resumed.importPrepared(initial.id) { }.errorCode)
            val preview = resumed.prepareReselected(jobId, listOf(other.toString(), chosen.toString()))
            assertEquals(failed, preview.job)
            assertEquals(failed, fixture.store.loadImportJob(jobId))
            assertEquals(listOf("Old first", "Old second"), preview.job?.candidates?.map { it.candidate.title })
            assertEquals(listOf("New unrelated", "New chosen"), preview.files.map { it.title })
            val oldSelectionIds = checkNotNull(failed.confirmedAttempt).selections.map { it.selectionId }.toSet()
            assertTrue(preview.files.none { it.id in oldSelectionIds })
            assertNotEquals(failed.candidates[1].candidate.byteSize, preview.files[0].byteSize)
            assertEquals("import_job_mapping_required", resumed.importPrepared(preview.id) { }.errorCode)
            val oldA = failed.candidates[0].candidate.id
            val oldB = failed.candidates[1].candidate.id
            val newA = preview.files[0].id
            val newB = preview.files[1].id
            for (invalid in listOf(
                listOf(DesktopImportCandidateMapping(oldA, newA), DesktopImportCandidateMapping(oldB, newA)),
                listOf(DesktopImportCandidateMapping(oldA, newA), DesktopImportCandidateMapping(oldA, newB)),
                listOf(DesktopImportCandidateMapping(UUID.randomUUID().toString(), newA)),
                listOf(DesktopImportCandidateMapping(oldA, UUID.randomUUID().toString())),
            )) assertEquals("import_job_mapping_invalid", resumed.importReselected(preview.id, invalid) { }.errorCode)
            assertEquals("import_job_mapping_required", resumed.importReselected(preview.id,
                listOf(DesktopImportCandidateMapping(oldA, null))) { }.errorCode)
            assertEquals(failed, fixture.store.loadImportJob(jobId))
            // Explicit changed title/size and subset: the second old candidate maps to the first
            // new selection by the user's IDs, with no inferred mapping for the first candidate.
            val result = resumed.importReselected(preview.id,
                listOf(DesktopImportCandidateMapping(oldA, null), DesktopImportCandidateMapping(oldB, newA))) { }
            assertEquals(1, result.imported)
            assertEquals(0, result.failed)
            val partial = checkNotNull(fixture.store.loadImportJob(jobId))
            assertEquals(DesktopImportJobPhase.PAUSED, partial.phase)
            assertEquals(DesktopImportJobOutcome.FAILED, partial.candidates[0].outcome)
            assertEquals(DesktopImportJobOutcome.IMPORTED, partial.candidates[1].outcome)
            assertEquals(listOf(DesktopImportJobReselection(oldB, newA, "New unrelated", Files.size(other))),
                partial.confirmedAttempt?.selections)
            val track = fixture.store.state.value.tracks.single()
            assertEquals("New unrelated", track.title)
            fixture.store.setTrackPreference(track.id, "DISLIKED")
            fixture.store.removeTrackFromCollection(track.id)
            val retained = fixture.store.state.value
            fixture.reopenStore()
            val duplicateSource = Files.write(fixture.sources.resolve("Different title.wav"), wave(403))
            val finisher = DesktopFileImporter(fixture.store, fixture.data)
            val last = finisher.prepareReselected(jobId, listOf(duplicateSource.toString(), ""))
            assertEquals(1, last.failed)
            assertEquals("import_job_mapping_invalid", finisher.importReselected(last.id,
                listOf(DesktopImportCandidateMapping(oldB, last.files.single().id))) { }.errorCode)
            val duplicate = finisher.importReselected(last.id,
                listOf(DesktopImportCandidateMapping(oldA, last.files.single().id))) { }
            assertEquals(0, duplicate.imported)
            assertEquals(1, duplicate.duplicates)
            assertEquals(1, duplicate.failed)
            assertEquals(retained, fixture.store.state.value)
            val complete = checkNotNull(fixture.store.loadImportJob(jobId))
            assertEquals(DesktopImportJobPhase.COMPLETED, complete.phase)
            assertEquals(DesktopImportJobOutcome.DUPLICATE, complete.candidates[0].outcome)
            assertEquals(track.id, complete.candidates[0].trackId)
            val completedPreview = finisher.prepareReselected(jobId, listOf(duplicateSource.toString()))
            assertEquals("import_job_completed", completedPreview.errorCode)
            assertTrue(completedPreview.files.isEmpty())
            assertEquals("import_job_completed", finisher.importReselected(completedPreview.id,
                listOf(DesktopImportCandidateMapping(oldA, UUID.randomUUID().toString()))) { }.errorCode)
            assertEquals(complete, fixture.store.loadImportJob(jobId))
            assertArrayEquals(wave(403), Files.readAllBytes(other))
            assertArrayEquals(wave(404), Files.readAllBytes(chosen))
        }
        importJobAuthorityAssertions()
        importJobUncertainAssertions()
    }

    private suspend fun importJobAuthorityAssertions(): Unit {
        DesktopAcceptanceFixture().use { fixture ->
            fixture.openStore()
            val source = Files.write(fixture.sources.resolve("Fresh chooser.wav"), wave(421))
            val job = fixture.store.createImportJob(DesktopImportJobManifest(UUID.randomUUID().toString(),
                listOf(DesktopImportJobCandidate(UUID.randomUUID().toString(), "Old observation", 4))))
            val attempt = DesktopImportJobAttempt(job.id, checkNotNull(job.revision), UUID.randomUUID().toString(),
                UUID.randomUUID().toString(), UUID.randomUUID().toString(), listOf(DesktopImportJobReselection(
                    job.candidates.single().candidate.id, UUID.randomUUID().toString(), "Old observation", 4)))
            val granted = fixture.store.confirmImportJobAttempt(attempt)
            val oldPermit = checkNotNull(granted.permit)
            fixture.reopenStore()
            assertNull(fixture.store.confirmImportJobAttempt(attempt).permit)
            val stalePermitFailure = try {
                fixture.store.failImportJobCandidate(DesktopImportJobSettlement(job.id, checkNotNull(granted.job.revision),
                    UUID.randomUUID().toString(), attempt.attemptId, attempt.selections.single().candidateId,
                    attempt.selections.single().selectionId), oldPermit, DesktopImportJobFailure.SOURCE_UNAVAILABLE)
                null
            } catch (failure: DesktopStorageException) { failure.code }
            assertEquals("desktop_import_job_reselection_required", stalePermitFailure)
            val importer = DesktopFileImporter(fixture.store, fixture.data)
            val preview = importer.prepareReselected(job.id, listOf(source.toString()))
            val paused = fixture.store.stopImportJob(DesktopImportJobStop(job.id, checkNotNull(preview.job?.revision),
                UUID.randomUUID().toString(), DesktopImportJobStopReason.PAUSED))
            val rejected = importer.importReselected(preview.id,
                listOf(DesktopImportCandidateMapping(job.candidates.single().candidate.id, preview.files.single().id))) { }
            assertEquals("import_job_conflict", rejected.errorCode)
            assertEquals(0, rejected.failed)
            assertTrue(fixture.store.state.value.tracks.isEmpty())
            assertEquals(paused, fixture.store.loadImportJob(job.id))

            val retainedStore = fixture.store
            val missingPermit = object : DesktopLibraryStore by retainedStore {
                override suspend fun confirmImportJobAttempt(attempt: DesktopImportJobAttempt): DesktopImportJobAuthorization =
                    retainedStore.confirmImportJobAttempt(attempt).copy(permit = null)
            }
            val withoutPermit = DesktopFileImporter(missingPermit, fixture.data)
            val noPermitPreview = withoutPermit.prepareReselected(job.id, listOf(source.toString()))
            val noPermit = withoutPermit.importReselected(noPermitPreview.id, listOf(DesktopImportCandidateMapping(
                job.candidates.single().candidate.id, noPermitPreview.files.single().id))) { }
            assertEquals("import_job_reselection_required", noPermit.errorCode)
            assertTrue(retainedStore.state.value.tracks.isEmpty())
            for (unknown in listOf("unknown_event", "unsupported_version")) {
                val before = retainedStore.loadImportJob(job.id)
                val guarded = object : DesktopLibraryStore by retainedStore {
                    override suspend fun loadImportJob(jobId: String): DesktopImportJob =
                        checkNotNull(before).copy(revision = null, phase = DesktopImportJobPhase.UNSUPPORTED,
                            candidates = emptyList(), errorCode = unknown)
                    override suspend fun confirmImportJobAttempt(attempt: DesktopImportJobAttempt): DesktopImportJobAuthorization =
                        error("Unsupported history must remain read-only")
                }
                val unsupported = DesktopFileImporter(guarded, fixture.data)
                val frozen = unsupported.prepareReselected(job.id, listOf(source.toString()))
                assertEquals("import_job_history_unsupported", frozen.errorCode)
                assertTrue(frozen.files.isEmpty())
                assertEquals("import_job_history_unsupported", unsupported.importReselected(frozen.id,
                    listOf(DesktopImportCandidateMapping(job.candidates.single().candidate.id, null))) { }.errorCode)
                assertEquals(before, retainedStore.loadImportJob(job.id))
            }
            // A wholly fresh chooser/confirmation still works after the refused old capabilities.
            val fresh = importer.prepareReselected(job.id, listOf(source.toString()))
            assertEquals(1, importer.importReselected(fresh.id, listOf(DesktopImportCandidateMapping(
                job.candidates.single().candidate.id, fresh.files.single().id))) { }.imported)
            assertArrayEquals(wave(421), Files.readAllBytes(source))
        }
    }

    private suspend fun importJobUncertainAssertions(): Unit {
        for (committedBeforeFailure in listOf(false, true)) DesktopAcceptanceFixture().use { fixture ->
            fixture.openStore()
            val source = Files.write(fixture.sources.resolve("Uncertain first.wav"), wave(431))
            val next = Files.write(fixture.sources.resolve("Must stay unstarted.wav"), wave(432))
            val retainedStore = fixture.store
            val calls = AtomicInteger()
            val failureCalls = AtomicInteger()
            var stoppedAt: Long? = null
            val uncertain = object : DesktopLibraryStore by retainedStore {
                override suspend fun commitImportJobCandidate(
                    settlement: DesktopImportJobSettlement, permit: DesktopImportJobPermit, track: DesktopImportedTrack,
                ): DesktopImportJob {
                    calls.incrementAndGet()
                    if (committedBeforeFailure) retainedStore.commitImportJobCandidate(settlement, permit, track)
                    throw java.io.IOException("synthetic lost storage response")
                }
                override suspend fun failImportJobCandidate(
                    settlement: DesktopImportJobSettlement, permit: DesktopImportJobPermit, failure: DesktopImportJobFailure,
                ): DesktopImportJob {
                    failureCalls.incrementAndGet()
                    error("An uncertain commit must never become a failed candidate")
                }
                override suspend fun stopImportJob(stop: DesktopImportJobStop): DesktopImportJob {
                    assertEquals(retainedStore.loadImportJob(stop.jobId)?.revision, stop.expectedRevision)
                    stoppedAt = stop.expectedRevision
                    return retainedStore.stopImportJob(stop)
                }
            }
            val importer = DesktopFileImporter(uncertain, fixture.data)
            val preview = importer.prepareSelected(listOf(source.toString(), next.toString()))
            val progress = mutableListOf<DesktopImportProgress>()
            val result = importer.importPrepared(preview.id) { progress.add(it) }
            assertEquals("import_job_reconciliation_required", result.errorCode)
            assertEquals(0, result.imported)
            assertEquals(0, result.failed)
            assertEquals(0, progress.last().completedFiles)
            assertEquals(1, calls.get())
            assertEquals(0, failureCalls.get())
            val job = checkNotNull(retainedStore.loadImportJob(checkNotNull(preview.jobId)))
            assertEquals(DesktopImportJobPhase.PAUSED, job.phase)
            assertEquals(checkNotNull(stoppedAt) + 1, job.revision)
            assertEquals(if (committedBeforeFailure) DesktopImportJobOutcome.IMPORTED else null, job.candidates[0].outcome)
            assertNull(job.candidates[1].outcome)
            val hash = MessageDigest.getInstance("SHA-256").digest(wave(431))
                .joinToString("") { "%02x".format(it.toInt() and 255) }
            val published = fixture.data.resolve("media/$hash.wav")
            assertArrayEquals(wave(431), Files.readAllBytes(published))
            assertArrayEquals(wave(432), Files.readAllBytes(next))
            fixture.reopenStore()
            assertEquals(job, fixture.store.loadImportJob(job.id))
            assertArrayEquals(wave(431), Files.readAllBytes(published))
            val resume = DesktopFileImporter(fixture.store, fixture.data)
            val chosen = if (committedBeforeFailure) next else source
            val candidate = job.candidates[if (committedBeforeFailure) 1 else 0].candidate.id
            val fresh = resume.prepareReselected(job.id, listOf(chosen.toString()))
            assertEquals("import_job_mapping_required", resume.importPrepared(fresh.id) { }.errorCode)
            assertEquals(1, resume.importReselected(fresh.id,
                listOf(DesktopImportCandidateMapping(candidate, fresh.files.single().id))) { }.imported)
        }
    }

    /** Appended to the existing composite, with real settings storage and no OS chooser. */
    private suspend fun settingsTransferAssertions(fixture: DesktopAcceptanceFixture): Unit {
        val library = fixture.store.state.value
        val root = Files.createDirectory(fixture.sandbox.resolve("settings-transfer"))
        val ownFile = root.resolve("desktop-settings-v1.json")
        val defaults = DesktopPreferences()
        val seed = DesktopSettingsTransferCodec.encode(defaults).decodeToString()
            .replace("\"schema_version\":3", "\"schema_version\":1").dropLast(1) + ",\"future_local\":{\"enabled\":true}}"
        Files.write(ownFile, seed.encodeToByteArray())
        val backing = openDesktopPreferencesStore(root)
        val scopeJob = SupervisorJob()
        val settings = DesktopSettingsController(backing, CoroutineScope(scopeJob + Dispatchers.Default))
        try {
            val portable = DesktopPreferences("RU", "SYSTEM", "BLUE", true, "NEXT_3", true)
            val transferBytes = (DesktopSettingsTransferCodec.encode(portable).decodeToString().dropLast(1) +
                ",\"private_url\":\"synthetic-private-origin\",\"credentials\":\"synthetic-secret\"}").encodeToByteArray()
            val selected = Files.write(fixture.sources.resolve("Selected settings.json"), transferBytes)
            assertArrayEquals(transferBytes, readSettingsImport(selected))
            settings.importSelectedSettings(selected)
            eventually("settings preview without apply") { settings.transfer.value.previewId != null && !settings.transfer.value.busy }
            val token = checkNotNull(settings.transfer.value.previewId)
            assertEquals(portable, settings.transfer.value.preview)
            assertEquals(defaults, backing.state.value.value)
            assertArrayEquals(seed.encodeToByteArray(), Files.readAllBytes(ownFile))
            settings.confirmImport("stale-token")
            settings.dismissImport("stale-token")
            settings.importSelectedSettings(fixture.sources.resolve("Another selection.json"))
            assertEquals(token, settings.transfer.value.previewId)
            assertEquals(defaults, backing.state.value.value)
            // Replacing the selected file after preview cannot change what Apply writes.
            Files.write(selected, "{".encodeToByteArray())
            settings.confirmImport(token)
            eventually("exact frozen settings apply") { !settings.transfer.value.busy && settings.transfer.value.previewId == null }
            assertEquals(portable, backing.state.value.value)
            assertEquals("desktop_settings_saved", settings.transfer.value.messageCode)
            val local = Json.parseToJsonElement(Files.readAllBytes(ownFile).decodeToString()).jsonObject
            assertEquals(Json.parseToJsonElement("{\"enabled\":true}"), local["future_local"])
            assertFalse("private_url" in local || "credentials" in local)

            val exported = fixture.sources.resolve("New export.json")
            settings.exportSelectedSettings(exported)
            eventually("confirmed settings export") { !settings.transfer.value.busy }
            assertEquals("desktop_settings_exported", settings.transfer.value.messageCode)
            val encoded = Files.readAllBytes(exported)
            val document = Json.parseToJsonElement(encoded.decodeToString()).jsonObject
            assertEquals(setOf("schema_version", "app_language", "appearance_mode", "accent_palette",
                "download_on_metered_network", "wave_prefetch_mode", "smooth_track_transitions"), document.keys)
            assertEquals("3", document.getValue("schema_version").toString())
            assertEquals(portable, DesktopSettingsTransferCodec.decode(encoded, defaults))
            val exists = runCatching { writeSettingsExportNew(exported, byteArrayOf(1)) }.exceptionOrNull()
            assertEquals("desktop_settings_export_target_exists", (exists as? DesktopSettingsFileException)?.code)
            settings.exportSelectedSettings(exported)
            eventually("existing settings export refused") { !settings.transfer.value.busy }
            assertEquals("desktop_settings_export_target_exists", settings.transfer.value.errorCode)
            assertArrayEquals(encoded, Files.readAllBytes(exported))

            val legacy = """{"schema_version":1,"appearance_mode":"LIGHT","accent_palette":"GREEN","sync_on_metered_network":true,"wave_prefetch_mode":"OFF"}"""
            assertEquals(portable.copy(appearanceMode = "LIGHT", accentPalette = "GREEN", wavePrefetchMode = "OFF"),
                DesktopSettingsTransferCodec.decode(legacy.encodeToByteArray(), portable))
            val legacyV2 = legacy.replace("schema_version\":1", "schema_version\":2")
                .replace("sync_on_metered_network", "download_on_metered_network")
            assertEquals(DesktopSettingsTransferCodec.decode(legacy.encodeToByteArray(), portable),
                DesktopSettingsTransferCodec.decode(legacyV2.encodeToByteArray(), portable))
            val invalid = listOf("[]", "{", encoded.decodeToString().replace("schema_version\":3", "schema_version\":4"),
                encoded.decodeToString().replace("\"SYSTEM\"", "true"), encoded.decodeToString().replace("\"BLUE\"", "1"),
                encoded.decodeToString().replace("\"NEXT_3\"", "\"FUTURE\""),
                encoded.decodeToString().replace("\"smooth_track_transitions\":true", "\"smooth_track_transitions\":\"true\""))
            invalid.forEach { assertTrue(runCatching { DesktopSettingsTransferCodec.decode(it.encodeToByteArray(), portable) }.isFailure) }
            listOf("{".encodeToByteArray(), ByteArray(DesktopSettingsTransferCodec.MAX_BYTES + 1) { 32 }).forEach { rejected ->
                Files.write(selected, rejected)
                settings.importSelectedSettings(selected)
                eventually("invalid settings import refused") { !settings.transfer.value.busy }
                assertEquals("desktop_settings_import_failed", settings.transfer.value.errorCode)
                assertNull(settings.transfer.value.preview)
                assertEquals(portable, backing.state.value.value)
                assertArrayEquals(rejected, Files.readAllBytes(selected))
            }
            Files.write(selected, transferBytes)
            settings.importSelectedSettings(selected)
            eventually("dismissable settings preview") { settings.transfer.value.previewId != null && !settings.transfer.value.busy }
            val dismissed = checkNotNull(settings.transfer.value.previewId)
            settings.dismissImport(dismissed)
            settings.confirmImport(dismissed)
            assertNull(settings.transfer.value.previewId)
            assertEquals(portable, backing.state.value.value)
            settings.setSmoothTrackTransitions(false)
            eventually("smooth transition setter uses the settings owner") { !backing.state.value.value.smoothTrackTransitions }
            assertEquals(portable.copy(smoothTrackTransitions = false), backing.state.value.value)
            settings.setSmoothTrackTransitions(true)
            eventually("smooth transition setter restores the portable value") { backing.state.value.value.smoothTrackTransitions }
            assertEquals(portable, backing.state.value.value)
            assertEquals(library, fixture.store.state.value)
        } finally {
            withContext(NonCancellable) { try { settings.close() } finally { scopeJob.cancelAndJoin() } }
        }
    }

    /** Future disposable persistence/policy assertions; remains inside the existing composite. */
    private suspend fun meteredPreferenceAssertions(fixture: DesktopAcceptanceFixture): Unit {
        val root = Files.createDirectory(fixture.sandbox.resolve("settings-metered"))
        val backing = openDesktopPreferencesStore(root)
        val writesDenied = java.util.concurrent.atomic.AtomicBoolean(false)
        val policyDenied = java.util.concurrent.atomic.AtomicBoolean(false)
        val holdRestriction = java.util.concurrent.atomic.AtomicBoolean(false)
        val entered = CompletableDeferred<Unit>()
        val release = CompletableDeferred<Unit>()
        val policies = java.util.concurrent.ConcurrentLinkedQueue<Pair<Boolean, Boolean>>()
        val commits = java.util.concurrent.ConcurrentLinkedQueue<Boolean>()
        val guarded = object : DesktopPreferencesStore by backing {
            override suspend fun setDownloadOnMeteredNetwork(value: Boolean): Unit {
                check(!writesDenied.get()) { "synthetic_settings_write_refused" }
                backing.setDownloadOnMeteredNetwork(value)
            }
        }
        val scopeJob = SupervisorJob()
        val scope = CoroutineScope(scopeJob + Dispatchers.Default)
        val settings = DesktopSettingsController(guarded, scope,
            applyMeteredPolicy = { allowed ->
                policies.add(allowed to backing.state.value.value.downloadOnMeteredNetwork)
                if (!allowed && holdRestriction.compareAndSet(true, false)) {
                    entered.complete(Unit)
                    withContext(NonCancellable) { release.await() }
                }
                check(!policyDenied.get()) { "synthetic_policy_dispatch_refused" }
            }, meteredPreferenceCommitted = { commits.add(it) })
        try {
            settings.setAppLanguage("RU")
            settings.setDownloadOnMeteredNetwork(true)
            assertTrue(policies.isNotEmpty() && policies.all { it == (true to true) })
            assertEquals(true, commits.last())
            assertEquals("RU", backing.state.value.value.appLanguage)

            policies.clear(); commits.clear()
            settings.setDownloadOnMeteredNetwork(false)
            assertEquals(false to true, policies.first())
            assertTrue(policies.all { !it.first })
            assertFalse(backing.state.value.value.downloadOnMeteredNetwork)
            assertEquals(false, commits.last())

            policies.clear(); commits.clear(); writesDenied.set(true)
            assertTrue(runCatching { settings.setDownloadOnMeteredNetwork(true) }.isFailure)
            assertFalse(backing.state.value.value.downloadOnMeteredNetwork)
            assertTrue(policies.isEmpty() && commits.isEmpty())
            writesDenied.set(false)

            policyDenied.set(true)
            assertTrue(runCatching { settings.setDownloadOnMeteredNetwork(true) }.isFailure)
            // A committed file is retained even when the following native request fails.
            assertTrue(backing.state.value.value.downloadOnMeteredNetwork)
            assertTrue(policies.isNotEmpty() && policies.all { it == (true to true) })
            assertTrue(commits.isEmpty())
            assertNull(settings.transfer.value.messageCode)
            policyDenied.set(false)
            settings.setDownloadOnMeteredNetwork(true)
            assertEquals(true, commits.last())

            holdRestriction.set(true)
            val caller = scope.launch { settings.setDownloadOnMeteredNetwork(false) }
            entered.await()
            caller.cancel()
            assertFalse(caller.isCompleted)
            assertTrue(backing.state.value.value.downloadOnMeteredNetwork)
            release.complete(Unit)
            caller.join()
            assertFalse(backing.state.value.value.downloadOnMeteredNetwork)
            assertEquals(false, commits.last())
        } finally {
            release.complete(Unit)
            withContext(NonCancellable) { try { settings.close() } finally { scopeJob.cancelAndJoin() } }
        }
        val reopened = openDesktopPreferencesStore(root)
        try {
            assertFalse(reopened.state.value.value.downloadOnMeteredNetwork)
            assertEquals("RU", reopened.state.value.value.appLanguage)
        } finally { reopened.close() }
    }

    /** Cancellation fences queued/active Apply; shutdown rejects and discards pending mutations. */
    private suspend fun settingsLifecycleAssertions(fixture: DesktopAcceptanceFixture): Unit {
        val root = Files.createDirectory(fixture.sandbox.resolve("settings-lifecycle"))
        val backing = openDesktopPreferencesStore(root)
        val firstEntered = CompletableDeferred<Unit>()
        val firstRelease = CompletableDeferred<Unit>()
        val applyEntered = CompletableDeferred<Unit>()
        val applyRelease = CompletableDeferred<Unit>()
        val closeEntered = CompletableDeferred<Unit>()
        val closeRelease = CompletableDeferred<Unit>()
        val applied = AtomicInteger()
        val closed = AtomicInteger()
        val held = object : DesktopPreferencesStore by backing {
            override suspend fun setAppLanguage(value: String): Unit {
                if (value == "RU") { firstEntered.complete(Unit); firstRelease.await() }
                else { closeEntered.complete(Unit); closeRelease.await() }
                currentCoroutineContext().ensureActive()
                backing.setAppLanguage(value)
            }
            override suspend fun applyPortable(value: DesktopPreferences): Unit {
                applyEntered.complete(Unit)
                applyRelease.await()
                currentCoroutineContext().ensureActive()
                applied.incrementAndGet()
                backing.applyPortable(value)
            }
            override suspend fun close(): Unit { closed.incrementAndGet(); backing.close() }
        }
        val scopeJob = SupervisorJob()
        val settings = DesktopSettingsController(held, CoroutineScope(scopeJob + Dispatchers.Default))
        try {
            val selected = Files.write(fixture.sources.resolve("Lifecycle settings.json"),
                DesktopSettingsTransferCodec.encode(DesktopPreferences("RU", "LIGHT", "GREEN", true, "OFF", true)))
            settings.importSelectedSettings(selected)
            eventually("queued confirmation preview") { settings.transfer.value.previewId != null && !settings.transfer.value.busy }
            val queued = checkNotNull(settings.transfer.value.previewId)
            settings.setAppLanguage("RU")
            firstEntered.await()
            settings.confirmImport(queued)
            assertTrue(settings.transfer.value.busy)
            settings.cancelTransfer()
            settings.confirmImport(queued)
            firstRelease.complete(Unit)
            settings.setAccentPalette("BLUE")
            eventually("post-cancel actor fence") { backing.state.value.value.accentPalette == "BLUE" }
            assertEquals(0, applied.get())
            assertEquals(DesktopPreferences(appLanguage = "RU", accentPalette = "BLUE"), backing.state.value.value)
            settings.importSelectedSettings(selected)
            eventually("active confirmation preview") { settings.transfer.value.previewId != null && !settings.transfer.value.busy }
            settings.confirmImport(checkNotNull(settings.transfer.value.previewId))
            applyEntered.await()
            settings.setSmoothTrackTransitions(true)
            assertFalse(backing.state.value.value.smoothTrackTransitions)
            settings.cancelTransfer()
            applyRelease.complete(Unit)
            eventually("cancelled Apply cleanup") { !settings.transfer.value.busy }
            assertEquals(0, applied.get())
            assertNull(settings.transfer.value.previewId)
            val beforeClose = backing.state.value
            settings.setAppLanguage("EN")
            closeEntered.await()
            settings.setAccentPalette("GREEN")
            settings.setAppearanceMode("LIGHT")
            settings.setSmoothTrackTransitions(true)
            settings.beginClose()
            settings.setSmoothTrackTransitions(true)
            settings.setAppLanguage("RU")
            settings.importSelectedSettings(selected)
            closeRelease.complete(Unit)
            settings.close()
            settings.close()
            assertEquals(beforeClose.value, backing.state.value.value)
            assertEquals(1, closed.get())
            assertTrue(scopeJob.isActive)
            assertEquals(0, applied.get())
        } finally {
            withContext(NonCancellable) { try { settings.close() } finally { scopeJob.cancelAndJoin() } }
        }
    }
}
