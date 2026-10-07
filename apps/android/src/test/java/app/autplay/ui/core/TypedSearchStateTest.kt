package app.autplay.ui.core

import app.autplay.application.search.LibrarySearchKind
import app.autplay.application.search.searchOwnerContextKey
import app.autplay.application.sync.ClientEventBinding
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import org.junit.Assert.*
import org.junit.Test

class TypedSearchStateTest {
    @Test fun versionThreeRoundTripsEveryKindAndVersionTwoRetainsDetailAndBothAnchors() {
        LibrarySearchKind.entries.forEach { kind ->
            val state = CoreProductSavedState(query = "live / тест", searchKind = kind,
                scopes = setOf(SearchScope.Local, SearchScope.Vault), librarySection = LibrarySection.Albums,
                selectedDetail = DetailTarget(DetailKind.Release, "metadata-album:native:jamendo:album:42"),
                searchListAnchor = ListAnchor("search:album", "result:one", 12),
                libraryListAnchor = ListAnchor("library:albums", "album:42", 4))
            assertEquals(state, CoreProductSavedState.decode(state.encode()))
            val versionTwo = state.encode().take(10).toMutableList().also { it[0] = "2" }
            assertEquals(state.copy(searchKind = LibrarySearchKind.All), CoreProductSavedState.decode(versionTwo))
            val futureKind = state.encode().toMutableList().also { it[10] = "future-kind" }
            assertEquals(state.copy(searchKind = LibrarySearchKind.All), CoreProductSavedState.decode(futureKind))
        }
    }

    @Test fun kindAndSameProfileUserOrDeviceSwitchImmediatelyHideBothStoresAndFenceLateRows() {
        val original = ClientEventBinding(UserId("00000000-0000-0000-0000-000000000001"),
            DeviceId("00000000-0000-0000-0000-000000000002"), ServerProfileId("00000000-0000-0000-0000-000000000003"))
        val scopes = setOf(SearchScope.Local, SearchScope.Vault)
        val guard = SearchGenerationGuard()
        val local = SearchResultStore<String>()
        val vault = SearchResultStore<String>()
        val old = guard.begin("song", scopes, original.searchOwnerContextKey(), LibrarySearchKind.Track)
        local.start(old); vault.start(old)
        assertTrue(local.accept(old, listOf("local-private")))
        assertTrue(vault.accept(old, listOf("vault-private")))
        listOf(original.copy(userId = UserId("00000000-0000-0000-0000-000000000004")),
            original.copy(deviceId = DeviceId("00000000-0000-0000-0000-000000000005")))
            .forEach { switched ->
                assertTrue(local.visibleFor("song", scopes, switched.searchOwnerContextKey(), LibrarySearchKind.Track).isEmpty())
                assertTrue(vault.visibleFor("song", scopes, switched.searchOwnerContextKey(), LibrarySearchKind.Track).isEmpty())
            }
        assertTrue(local.visibleFor("song", scopes, original.searchOwnerContextKey(), LibrarySearchKind.Album).isEmpty())
        assertTrue(vault.visibleFor("song", scopes, original.searchOwnerContextKey(), LibrarySearchKind.Album).isEmpty())
        val current = guard.begin("song", scopes, original.searchOwnerContextKey(), LibrarySearchKind.Album)
        local.start(current); vault.start(current)
        assertFalse(guard.accepts(old))
        assertFalse(local.accept(old, listOf("late-local")))
        assertFalse(vault.accept(old, listOf("late-vault")))
        assertTrue(local.results.isEmpty()); assertTrue(vault.results.isEmpty())
        assertTrue(local.accept(current, listOf("new-local")))
        assertEquals(listOf("new-local"), local.visibleFor("song", scopes, original.searchOwnerContextKey(), LibrarySearchKind.Album))
    }
}
