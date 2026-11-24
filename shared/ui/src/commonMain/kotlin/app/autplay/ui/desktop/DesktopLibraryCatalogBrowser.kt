package app.autplay.ui.desktop

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.unit.dp

/** Ordinary Library bridge; all navigation uses an actual current host-issued target. */
@Composable
public fun DesktopLibraryCatalogBrowser(
    state: DesktopAccountWorkspaceState,
    actions: DesktopAccountActionSink,
    kind: DesktopCatalogKind,
    language: DesktopLanguage,
    isNavigationCurrent: () -> Boolean,
): Unit {
    val latestState = rememberUpdatedState(state)
    val latestActions = rememberUpdatedState(actions)
    val latestNavigation = rememberUpdatedState(isNavigationCurrent)
    val guardedActions = remember(DesktopAccountSinkKey(actions), kind) {
        DesktopAccountActionSink { intent ->
            if (latestNavigation.value() && latestActions.value === actions) actions.dispatch(intent)
        }
    }
    val owner = remember(state, DesktopAccountSinkKey(guardedActions), kind) {
        DesktopAccountUiOwner(state, null, guardedActions, { latestState.value }, { guardedActions })
    }
    DisposableEffect(owner) { onDispose { owner.retire() } }
    val catalog = state.catalog
    val destination = catalog.destinations.singleOrNull { it.kind == kind }
    LaunchedEffect(owner, destination?.targetId, catalog.navigationRequested) {
        if (!catalog.navigationRequested && latestNavigation.value() && destination != null)
            owner.send(DesktopAccountCommand.Invoke(DesktopAccountAction.CatalogPage, destination.targetId))
    }
    val ru = language == DesktopLanguage.Russian
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(if (ru) "Источник: каталог текущего аккаунта" else "Source: current account catalog")
        state.operations.filter { it.action == DesktopAccountAction.CatalogPage || it.issue != DesktopAccountIssue.None }
            .forEach { AccountStatus(it.condition, it.issue, language) }
        val scope = catalog.currentAccountScopeId
        if (scope == null) {
            Text(if (ru) "Нет подтверждённой текущей области аккаунта. Выберите или разблокируйте аккаунт в разделе «Аккаунты»."
                else "No confirmed current account scope. Select or unlock an account in Accounts.")
        } else {
            if (catalog.navigationRequested && latestNavigation.value())
                DesktopAccountCatalogContent(owner, language, includeHome = false)
            else Text(if (ru) "Открытие выбранного вида каталога…" else "Opening the selected catalog kind…")
        }
    }
}
