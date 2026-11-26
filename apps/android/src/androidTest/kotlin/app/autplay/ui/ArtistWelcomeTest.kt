package app.autplay.ui

import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import app.autplay.application.library.WelcomeArtist
import app.autplay.ui.welcome.ArtistWelcomeDialog
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class ArtistWelcomeTest {
    @get:Rule val ui = createComposeRule()

    @Test fun confirmedImportOpensPickerAndBranchesPreserveEarlierArtists() {
        var imported = false
        var saved: List<String>? = null
        fun cards(vararg names: String) = names.map { WelcomeArtist(it, it, "Song by $it") }
        ui.setContent {
            ArtistWelcomeDialog(false, { true }, { parent, _, _ ->
                check(imported)
                when (parent) {
                    null -> cards("One", "Two", "Three")
                    "One" -> cards("Four", "Five")
                    "Four" -> cards("Six", "Seven")
                    else -> emptyList()
                }
            }, { saved = it }, importFavorites = { close -> Button(onClick = { imported = true; close() }) { Text("Confirm import") } })
        }
        ui.onNodeWithText("Import favorites").performClick()
        ui.onNodeWithText("Confirm import").performClick()
        for (artist in listOf("One", "Four", "Six", "Three")) {
            ui.onNodeWithText(artist).performScrollTo().performClick()
        }
        ui.onNodeWithText("Done · 4").performClick()
        ui.runOnIdle { assertEquals(listOf("One", "Four", "Six", "Three"), saved) }
    }

    @Test fun canSkipBothStepsWithAnEmptyCatalog() {
        var saved: List<String>? = null
        ui.setContent {
            ArtistWelcomeDialog(false, { true }, { _, _, _ -> emptyList() },
                { saved = it }, importFavorites = { error("Unexpected import") })
        }
        ui.onNodeWithText("Choose artists").performClick()
        ui.onNodeWithText("Skip").performClick()
        ui.runOnIdle { assertEquals(emptyList<String>(), saved) }
    }
}
