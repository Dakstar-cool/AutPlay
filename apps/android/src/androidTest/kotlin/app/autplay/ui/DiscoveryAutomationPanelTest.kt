package app.autplay.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.junit4.StateRestorationTester
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.application.artist.ArtistId
import app.autplay.application.artist.ArtistKey
import app.autplay.application.artist.ArtistSummary
import app.autplay.application.server.DiscoveryPolicyCommand
import app.autplay.application.server.RemoteDiscoveryPolicy
import app.autplay.application.server.RemoteDiscoverySnapshot
import app.autplay.domain.ServerProfileId
import java.util.concurrent.atomic.AtomicReference
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class DiscoveryAutomationPanelTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun autoImportIsNotSubmittedUntilTheConsequenceIsConfirmed() {
        val submitted = AtomicReference<DiscoveryPolicyCommand?>(null)
        val artist = ArtistSummary(
            key = ArtistKey(
                ServerProfileId("11111111-1111-4111-8111-111111111111"),
                ArtistId("22222222-2222-4222-8222-222222222222"),
            ),
            name = "Signal Artist",
            sortName = null,
            artistType = null,
            disambiguation = null,
            countryCode = null,
            identityStatus = "CANONICAL",
        )
        compose.setContent {
            MaterialTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    DiscoveryAutomationPanel(
                        isBound = true,
                        busy = false,
                        localArtists = listOf(artist),
                        state = DiscoveryAutomationUiState(
                            snapshot = RemoteDiscoverySnapshot(policies = emptyList(), runs = emptyList()),
                        ),
                        actions = DiscoveryAutomationActions(savePolicy = submitted::set),
                    )
                }
            }
        }

        compose.onNodeWithTag("discovery_artist_picker").performClick()
        compose.onNodeWithTag("discovery_artist_${artist.key.artistId.value}").performClick()
        compose.onNodeWithText(context.getString(R.string.discovery_automation_provider_artist_id))
            .performTextInput("20")
        compose.onNodeWithText(context.getString(R.string.discovery_automation_auto_import))
            .performClick()
        compose.onNodeWithText(context.getString(R.string.action_save)).performClick()

        assertNull(submitted.get())
        compose.onNodeWithText(context.getString(R.string.discovery_automation_auto_import_consequence))
            .assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.discovery_automation_confirm_auto_import))
            .performClick()

        assertEquals("AUTO_IMPORT", submitted.get()?.importMode)
        assertEquals("22222222-2222-4222-8222-222222222222", submitted.get()?.canonicalArtistId)
    }

    @Test
    fun policyEditorStaysLockedUntilAuthoritativeSnapshotLoads() {
        val artist = artist("22222222-2222-4222-8222-222222222222", "Artist")
        compose.setContent {
            MaterialTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    DiscoveryAutomationPanel(
                        isBound = true,
                        busy = false,
                        localArtists = listOf(artist),
                        state = DiscoveryAutomationUiState(),
                        actions = DiscoveryAutomationActions(),
                    )
                }
            }
        }

        compose.onNodeWithTag("discovery_artist_picker").assertIsNotEnabled()
        compose.onNodeWithText(context.getString(R.string.action_save)).assertIsNotEnabled()
    }

    @Test
    fun unknownPolicyModesCannotBeOverwrittenByKnownEditorControls() {
        val artist = artist("22222222-2222-4222-8222-222222222222", "Artist")
        val policy = RemoteDiscoveryPolicy(
            policyId = "44444444-4444-4444-8444-444444444444",
            canonicalArtistId = artist.key.artistId.value,
            providerArtistId = "20",
            discoveryMode = "FUTURE_DISCOVERY_MODE",
            importMode = "FUTURE_IMPORT_MODE",
            automationEnabled = true,
            revision = 3,
            lastCheckedAt = null,
            nextEligibleAt = null,
        )
        compose.setContent {
            MaterialTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    DiscoveryAutomationPanel(
                        isBound = true,
                        busy = false,
                        localArtists = listOf(artist),
                        state = DiscoveryAutomationUiState(
                            snapshot = RemoteDiscoverySnapshot(policies = listOf(policy), runs = emptyList()),
                        ),
                        actions = DiscoveryAutomationActions(),
                    )
                }
            }
        }

        compose.onNodeWithTag("discovery_artist_picker").performClick()
        compose.onNodeWithTag("discovery_artist_${artist.key.artistId.value}").performClick()

        compose.onNodeWithText(context.getString(R.string.discovery_automation_manual)).assertIsNotEnabled()
        compose.onNodeWithText(context.getString(R.string.action_save)).assertIsNotEnabled()
    }

    @Test
    fun changingArtistClearsProviderMappingAndReturnsToSafeModes() {
        val artistA = artist("22222222-2222-4222-8222-222222222222", "Artist A")
        val artistB = artist("33333333-3333-4333-8333-333333333333", "Artist B")
        compose.setContent {
            MaterialTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    DiscoveryAutomationPanel(
                        isBound = true,
                        busy = false,
                        localArtists = listOf(artistA, artistB),
                        state = DiscoveryAutomationUiState(
                            snapshot = RemoteDiscoverySnapshot(
                                policies = listOf(
                                    RemoteDiscoveryPolicy(
                                        policyId = "44444444-4444-4444-8444-444444444444",
                                        canonicalArtistId = artistA.key.artistId.value,
                                        providerArtistId = "20",
                                        discoveryMode = "SCHEDULED",
                                        importMode = "AUTO_IMPORT",
                                        automationEnabled = true,
                                        revision = 2,
                                        lastCheckedAt = null,
                                        nextEligibleAt = null,
                                    ),
                                ),
                                runs = emptyList(),
                            ),
                        ),
                        actions = DiscoveryAutomationActions(),
                    )
                }
            }
        }

        compose.onNodeWithText(context.getString(R.string.action_edit)).performClick()
        compose.onNodeWithTag("discovery_artist_picker").performClick()
        compose.onNodeWithTag("discovery_artist_${artistB.key.artistId.value}").performClick()

        compose.onNodeWithText(context.getString(R.string.action_save)).assertIsNotEnabled()
    }

    @Test
    fun unresolvedOperationSurvivesSavedStateRestoration() {
        val restoration = StateRestorationTester(compose)
        restoration.setContent {
            var state by rememberSaveable(stateSaver = ServerFeaturesUiStateSaver) {
                mutableStateOf(ServerFeaturesUiState())
            }
            Button(
                onClick = {
                    state = state.copy(
                        discovery = state.discovery.copy(
                            pendingOperation = PendingDiscoveryOperation("RUN|policy", "operation-1"),
                        ),
                    )
                },
            ) { Text("Set pending") }
            state.discovery.pendingOperation?.let { Text(it.operationId) }
        }

        compose.onNodeWithText("Set pending").performClick()
        compose.onNodeWithText("operation-1").assertIsDisplayed()
        restoration.emulateSavedInstanceStateRestore()
        compose.onNodeWithText("operation-1").assertIsDisplayed()
    }

    private fun artist(id: String, name: String) = ArtistSummary(
        key = ArtistKey(
            ServerProfileId("11111111-1111-4111-8111-111111111111"),
            ArtistId(id),
        ),
        name = name,
        sortName = null,
        artistType = null,
        disambiguation = null,
        countryCode = null,
        identityStatus = "CANONICAL",
    )
}
