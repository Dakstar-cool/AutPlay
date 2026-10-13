package app.autplay.desktop.acceptance

import app.autplay.desktop.library.DesktopFileImporter
import java.nio.file.Files
import java.nio.file.Path
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout

class DesktopImportAcceptanceTest {
    @get:Rule val deadline: Timeout = Timeout.seconds(45)

    @Test fun explicitImportPreservesOriginalDeduplicatesExactBytesAndRejectsMissingOrCorruptCopies(): Unit = runBlocking {
        withTimeout(30_000) {
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
                val managed = Path.of(checkNotNull(fixture.store.resolveManagedFile(track.id)))
                assertEquals("${track.sha256}.media", managed.fileName.toString())
                assertEquals(fixture.data.resolve("media"), managed.parent)
                assertArrayEquals(bytes, Files.readAllBytes(managed))
                assertArrayEquals(bytes, Files.readAllBytes(source))
                assertArrayEquals(bytes, Files.readAllBytes(twin))
                val retry = importer.importSelected(listOf(source.toString()))
                assertEquals(1, retry.duplicates)
                assertEquals(track.id, fixture.store.state.value.tracks.single().id)

                val missing = importer.importSelected(listOf(fixture.sources.resolve("Missing.wav").toString()))
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
                val corrupt = DesktopFileImporter(fixture.store, fixture.data).importSelected(listOf(source.toString()))
                assertEquals(1, corrupt.failed)
                assertEquals("import_managed_integrity_failed", corrupt.errorCode)
                assertArrayEquals(byteArrayOf(99), Files.readAllBytes(managed))
                assertArrayEquals(bytes, Files.readAllBytes(source))
                assertEquals(1, fixture.store.state.value.tracks.size)
                Files.list(managed.parent).use { files -> assertTrue(files.noneMatch { it.fileName.toString().endsWith(".part") }) }
            }
        }
    }

    @Test fun symlinkSelectionsAndManagedCapabilitiesAreRejectedWithoutTouchingOriginal(): Unit = runBlocking {
        withTimeout(30_000) {
            DesktopAcceptanceFixture().use { fixture ->
                fixture.openStore()
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
                val managed = Path.of(checkNotNull(fixture.store.resolveManagedFile(trackId)))
                Files.delete(managed)
                Files.createSymbolicLink(managed, source)
                assertNull(fixture.store.resolveManagedFile(trackId))
                val reimport = DesktopFileImporter(fixture.store, fixture.data).importSelected(listOf(source.toString()))
                assertEquals(1, reimport.failed)
                assertEquals("import_symlink_rejected", reimport.errorCode)
                assertTrue(Files.isSymbolicLink(managed))
                assertArrayEquals(bytes, Files.readAllBytes(source))
            }
        }
    }
}
