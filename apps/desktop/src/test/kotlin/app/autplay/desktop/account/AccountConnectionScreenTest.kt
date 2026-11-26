package app.autplay.desktop.account

import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.ImageComposeScene
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsNode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.text.AnnotatedString
import app.autplay.desktop.presentation.unavailableAccountWorkspace
import app.autplay.ui.desktop.*
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalComposeUiApi::class)
class AccountConnectionScreenTest {
    @Test fun ordinaryLoginTakesThreeSubmissionsOnTheDefaultAccountScreen() {
        val review = DesktopTransportReview("r", "https://api.example", "https://media.example", "example-key",
            null, null, apiUsesSystemTrust = true, streamUsesSystemTrust = true)
        val base = unavailableAccountWorkspace()
        val ready = DesktopAccountCondition.Ready
        fun screen(revision: Long, connection: DesktopConnectionView, vararg permits: DesktopAccountPermit) =
            DesktopAccountWorkspaceState(DesktopAccountScope("window", revision), permits.toList(), emptyList(),
                base.portable, connection, base.selfPairing, base.serverRecovery, base.devices, base.privacy,
                base.catalog, base.downloads, base.statistics)
        val state = mutableStateOf(screen(1, DesktopConnectionView(ready),
            DesktopAccountPermit("discover", DesktopAccountAction.Discover)))
        val submissions = mutableListOf<DesktopAccountAction>()
        val sink = DesktopAccountActionSink { intent ->
            assertEquals(state.value.scope, intent.scope)
            assertTrue(state.value.permits.any { it === intent.permit && it.acceptsCommand(intent.command) })
            submissions += intent.command.action
            when (val command = intent.command) {
                is DesktopAccountCommand.Discover -> {
                    command.enteredOrigin.consume { assertEquals(review.normalizedApiOrigin, it.concatToString()) }
                    state.value = screen(2, DesktopConnectionView(ready, transportReview = review, localChoiceSelected = true),
                        DesktopAccountPermit("trust", DesktopAccountAction.ConfirmTransport, review.id))
                }
                is DesktopAccountCommand.ConfirmTransport -> {
                    assertEquals(review.id, command.targetId)
                    assertTrue(command.apiConfirmed && command.streamConfirmed && command.identityConfirmed)
                    state.value = screen(3, DesktopConnectionView(ready, serverSelected = true),
                        DesktopAccountPermit("login", DesktopAccountAction.LoginPortable, requiresPassword = true))
                }
                is DesktopAccountCommand.LoginPortable -> {
                    assertEquals("listener", command.publicId)
                    command.password.consume { assertEquals("example-password", it.concatToString()) }
                    state.value = screen(4, DesktopConnectionView(ready, bindingId = "bound", accountPublicId = "listener"))
                }
                else -> fail("Unexpected action ${command.action}")
            }
        }
        val scene = ImageComposeScene(width = 1080, height = 740) {
            DesktopAccountWorkspace(state.value, sink, DesktopLanguage.Russian)
        }
        try {
            fun nodes(): List<SemanticsNode> {
                scene.render().close()
                fun flatten(node: SemanticsNode): List<SemanticsNode> = listOf(node) + node.children.flatMap(::flatten)
                return flatten(scene.semanticsOwners.single().rootSemanticsNode)
            }
            fun enter(index: Int, value: String) {
                val fields = nodes().mapNotNull { it.config.getOrNull(SemanticsActions.SetText)?.action }
                assertTrue(fields[index](AnnotatedString(value)))
            }
            fun nodeText(node: SemanticsNode) = node.config.getOrNull(SemanticsProperties.Text).orEmpty().map { it.text }
            fun click(label: String) {
                val node = nodes().single { label in nodeText(it) && it.config.contains(SemanticsActions.OnClick) }
                assertFalse(node.config.contains(SemanticsProperties.Disabled))
                assertTrue(node.config[SemanticsActions.OnClick].action!!.invoke())
            }
            enter(0, review.normalizedApiOrigin)
            click("Продолжить")
            assertTrue(nodes().any { review.normalizedStreamOrigin in nodeText(it) || nodeText(it).any { line -> line.contains(review.normalizedStreamOrigin) } })
            click("Доверять указанным адресам и ключу сервера")
            enter(0, "listener")
            enter(1, "example-password")
            click("Войти и синхронизировать")
            assertTrue(nodes().any { "Аккаунт подключён" in nodeText(it) })
            assertEquals(listOf(DesktopAccountAction.Discover, DesktopAccountAction.ConfirmTransport,
                DesktopAccountAction.LoginPortable), submissions)
        } finally {
            scene.close()
        }
    }
}
