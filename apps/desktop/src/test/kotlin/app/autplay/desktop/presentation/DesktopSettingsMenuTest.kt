package app.autplay.desktop.presentation

import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.ImageComposeScene
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsNode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import app.autplay.application.desktop.DesktopPreferences
import app.autplay.application.desktop.DesktopPreferencesState
import app.autplay.application.desktop.DesktopSettingsTransferState
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.ui.desktop.*
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalComposeUiApi::class)
class DesktopSettingsMenuTest {
    private fun ImageComposeScene.nodes(): List<SemanticsNode> {
        render().close()
        fun flatten(node: SemanticsNode): List<SemanticsNode> = listOf(node) + node.children.flatMap(::flatten)
        return semanticsOwners.flatMap { flatten(it.rootSemanticsNode) }
    }

    private fun SemanticsNode.hasLabel(label: String): Boolean =
        config.getOrNull(SemanticsProperties.Text).orEmpty().any { it.text == label }

    private fun ImageComposeScene.shows(label: String): Boolean = nodes().any { it.hasLabel(label) }

    private fun ImageComposeScene.click(label: String) {
        val node = nodes().single { it.hasLabel(label) && it.config.contains(SemanticsActions.OnClick) }
        assertFalse(node.config.contains(SemanticsProperties.Disabled))
        assertTrue(node.config[SemanticsActions.OnClick].action!!.invoke())
    }

    @Test fun settingsOpenOneSectionAtATimeAndKeepPendingImportsReachable() {
        var openedAccount = 0
        var openedDownloads = 0
        var smooth: Boolean? = null
        var dismissed: String? = null
        val transfer = mutableStateOf(DesktopSettingsTransferState())
        val settings = DesktopPreferencesState(DesktopPreferences(appLanguage = "RU"))
        val scene = ImageComposeScene(width = 1080, height = 740) {
            AutPlayDesktopShell(DesktopSection.Settings, DesktopLanguage.Russian, DesktopThemeMode.Dark,
                PlaybackPresentationState(), false, {}, {}, {}, DesktopPlaybackCallbacks({}, {}, {}, {}, {}),
                preferencesState = settings, preferencesTransfer = transfer.value,
                preferencesCallbacks = DesktopSettingsCallbacks(onSmoothTrackTransitions = { smooth = it },
                    onDismissImport = { dismissed = it }),
                onOpenAccount = { openedAccount++ }, onOpenDownloads = { openedDownloads++ })
        }
        try {
            val back = "Назад к настройкам"
            listOf("Аккаунт и сервер", "Оформление", "Музыка и загрузки", "Приложение").forEach { assertTrue(it, scene.shows(it)) }
            assertFalse(scene.shows("Акцентный цвет"))
            assertFalse(scene.shows("Импорт настроек…"))
            scene.click("Аккаунт и сервер")
            assertEquals(1, openedAccount)
            scene.click("Оформление")
            assertTrue(scene.shows("Акцентный цвет"))
            assertFalse(scene.shows("Плавные переходы треков"))
            scene.click(back)
            scene.click("Музыка и загрузки")
            scene.click("Плавные переходы треков")
            assertEquals(true, smooth)
            scene.click("Открыть загрузки")
            assertEquals(1, openedDownloads)
            scene.click(back)
            scene.click("Приложение")
            assertTrue(scene.shows("Импорт настроек…"))
            assertTrue(scene.shows("Экспорт настроек…"))
            assertFalse(scene.shows("Предзагрузка Волны"))
            scene.click(back)
            transfer.value = DesktopSettingsTransferState(previewId = "pending-import", preview = DesktopPreferences())
            assertTrue(scene.shows("Применить выбранные настройки?"))
            scene.click("Отмена")
            assertEquals("pending-import", dismissed)
        } finally {
            scene.close()
        }
    }

    @Test fun secondaryAccountSectionsRemainReachableThroughMore() {
        val scene = ImageComposeScene(width = 800, height = 640) {
            DesktopAccountWorkspace(unavailableAccountWorkspace(), DesktopAccountActionSink {}, DesktopLanguage.Russian)
        }
        try {
            listOf("Личный сервер", "Аккаунт", "Устройства", "Ещё").forEach { assertTrue(it, scene.shows(it)) }
            assertFalse(scene.shows("Приватность"))
            scene.click("Ещё")
            assertTrue(scene.shows("Восстановление доступа к серверу"))
            scene.click("Приватность")
            assertTrue(scene.nodes().any { it.config.getOrNull(SemanticsProperties.PaneTitle) == "Приватность" })
            scene.click("Личный сервер")
            assertTrue(scene.shows("Ещё"))
        } finally {
            scene.close()
        }
    }
}
