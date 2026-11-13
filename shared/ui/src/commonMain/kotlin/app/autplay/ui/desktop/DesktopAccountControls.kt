package app.autplay.ui.desktop

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.paneTitle
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp

internal enum class DesktopAccountRoute(val label: DesktopAccountText) {
    Portable(DesktopAccountText.Portable), Connection(DesktopAccountText.Connection), Self(DesktopAccountText.Self),
    Devices(DesktopAccountText.Devices), Privacy(DesktopAccountText.Privacy), Catalog(DesktopAccountText.Catalog),
    Downloads(DesktopAccountText.Downloads), Statistics(DesktopAccountText.Statistics), ImportMatching(DesktopAccountText.ImportMatching),
    Recovery(DesktopAccountText.ServerRecovery),
}

/** Identity rather than data-class equality prevents an old event from targeting a replacement owner. */
internal class DesktopAccountSinkKey(private val sink: DesktopAccountActionSink) {
    override fun equals(other: Any?): Boolean = other is DesktopAccountSinkKey && sink === other.sink
    override fun hashCode(): Int = 0
}

internal class DesktopAccountUiOwner(
    val state: DesktopAccountWorkspaceState,
    val sensitiveOutput: DesktopAccountSensitiveOutput?,
    private val sink: DesktopAccountActionSink,
    private val latestState: () -> DesktopAccountWorkspaceState,
    private val latestSink: () -> DesktopAccountActionSink,
) {
    private var active: Boolean = true
    private val used: MutableSet<String> = mutableSetOf()
    fun retire(): Unit { active = false }
    fun permit(action: DesktopAccountAction, targetId: String?): DesktopAccountPermit? =
        state.permits.singleOrNull { it.action == action && it.targetId == targetId }
    fun available(action: DesktopAccountAction, targetId: String? = null): Boolean =
        active && latestState() === state && latestSink() === sink &&
            permit(action, targetId)?.let { it.id !in used } == true

    fun send(command: DesktopAccountCommand): Unit {
        try {
            if (!available(command.action, command.targetId)) return
            val permit = permit(command.action, command.targetId) ?: return
            used.add(permit.id)
            sink.dispatch(DesktopAccountIntent(state.scope, permit, command))
        } finally {
            when (command) {
                is DesktopAccountCommand.Secret -> command.clear()
                is DesktopAccountCommand.CreatePortable -> command.password.clear()
                is DesktopAccountCommand.LoginPortable -> command.password.clear()
                is DesktopAccountCommand.Discover -> command.enteredOrigin.clear()
                is DesktopAccountCommand.RecoveryPreview -> command.clear()
                is DesktopAccountCommand.PinnedTransport -> command.clear()
                else -> Unit
            }
        }
    }
}

/**
 * Root integration entry point. The host supplies prepared, confirmed snapshots and a real sink.
 * This surface owns no IO, credential authority, clipboard, persistence or playback adapter.
 */
@Composable
public fun DesktopAccountWorkspace(
    state: DesktopAccountWorkspaceState,
    actions: DesktopAccountActionSink,
    language: DesktopLanguage,
    modifier: Modifier = Modifier,
    sensitiveOutput: DesktopAccountSensitiveOutput? = null,
): Unit {
    var route by remember(state.scope.lifetime) { mutableStateOf(DesktopAccountRoute.Portable) }
    val currentState = rememberUpdatedState(state)
    val currentActions = rememberUpdatedState(actions)
    val owner = remember(state, DesktopAccountSinkKey(actions), route, sensitiveOutput) {
        DesktopAccountUiOwner(state, sensitiveOutput, actions, { currentState.value }, { currentActions.value })
    }
    DisposableEffect(owner) { onDispose { owner.retire() } }
    Column(modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(DesktopAccountText.Workspace.text(language), style = MaterialTheme.typography.headlineMedium,
            modifier = Modifier.semantics { heading() })
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            DesktopAccountRoute.entries.forEach { destination ->
                FilterChip(selected = route == destination, onClick = {
                    if (route != destination) { owner.retire(); route = destination }
                }, label = { Text(destination.label.text(language)) }, modifier = Modifier.heightIn(min = 48.dp))
            }
        }
        Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState())
            .semantics { paneTitle = route.label.text(language) }, verticalArrangement = Arrangement.spacedBy(16.dp)) {
            // Scope revisions dispose transient input and frozen confirmations, including A -> B -> A.
            key(state.scope, route, DesktopAccountSinkKey(actions)) {
                when (route) {
                    DesktopAccountRoute.Portable -> DesktopPortableAccountContent(owner, language)
                    DesktopAccountRoute.Connection -> DesktopAccountConnectionContent(owner, language)
                    DesktopAccountRoute.Self -> DesktopAccountSelfContent(owner, language)
                    DesktopAccountRoute.Devices -> DesktopAccountDevicesContent(owner, language)
                    DesktopAccountRoute.Privacy -> DesktopAccountPrivacyContent(owner, language)
                    DesktopAccountRoute.Catalog -> DesktopAccountCatalogContent(owner, language)
                    DesktopAccountRoute.Downloads -> DesktopAccountDownloadsContent(owner, language)
                    DesktopAccountRoute.Statistics -> DesktopAccountStatisticsContent(owner, language)
                    DesktopAccountRoute.ImportMatching -> state.matching?.let { matching ->
                        val sink = remember(owner, matching) { DesktopMatchingActions { capturedOwner, target, input ->
                            if(capturedOwner == matching.owner)owner.send(DesktopMatchingUiCommand(target,input))
                        } }
                        DesktopImportMatchingContent(matching,sink,language)
                    } ?: AccountStatus(DesktopAccountCondition.Unavailable,DesktopAccountIssue.None,language)
                    DesktopAccountRoute.Recovery -> DesktopAccountServerRecoveryContent(owner, language)
                }
                if (state.operations.isNotEmpty()) AccountCard(DesktopAccountText.PendingOperations, language) {
                    state.operations.forEach { operation ->
                        Text(desktopAccountActionText(operation.action, language), style = MaterialTheme.typography.titleMedium)
                        AccountStatus(operation.condition, operation.issue, language)
                        if (operation.resumable) AccountButton(owner, DesktopAccountAction.ResumeOperation, language, operation.id)
                        if (operation.cancellable) AccountButton(owner, DesktopAccountAction.CancelOperation, language, operation.id)
                    }
                }
            }
        }
    }
}

@Composable
internal fun AccountCard(title: DesktopAccountText, language: DesktopLanguage, content: @Composable ColumnScope.() -> Unit): Unit {
    Surface(shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.surfaceContainerLow,
        modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(title.text(language), style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics { heading() })
            content()
        }
    }
}

@Composable
internal fun AccountStatus(condition: DesktopAccountCondition, issue: DesktopAccountIssue, language: DesktopLanguage): Unit {
    Column(Modifier.semantics { liveRegion = LiveRegionMode.Polite }, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(desktopAccountConditionText(condition, language), style = MaterialTheme.typography.bodyMedium)
        if (issue != DesktopAccountIssue.None) Text(desktopAccountIssueText(issue, language),
            color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
internal fun AccountButton(
    owner: DesktopAccountUiOwner,
    action: DesktopAccountAction,
    language: DesktopLanguage,
    targetId: String? = null,
    enabled: Boolean = true,
    label: String = desktopAccountActionText(action, language),
    command: () -> DesktopAccountCommand = { DesktopAccountCommand.Invoke(action, targetId) },
): Unit {
    OutlinedButton(onClick = { if (enabled) owner.send(command()) },
        enabled = enabled && owner.available(action, targetId), modifier = Modifier.heightIn(min = 48.dp).fillMaxWidth()) {
        Text(label)
    }
}

@Composable
internal fun AccountDestructiveButton(owner: DesktopAccountUiOwner, action: DesktopAccountAction,
    language: DesktopLanguage, targetId: String? = null, subject: String? = null): Unit {
    var open by remember { mutableStateOf(false) }
    var password by remember { mutableStateOf("") }
    DisposableEffect(Unit) { onDispose { password = "" } }
    val requiresPassword = owner.permit(action, targetId)?.requiresPassword == true
    OutlinedButton(onClick = { open = true }, enabled = owner.available(action, targetId),
        modifier = Modifier.heightIn(min = 48.dp).fillMaxWidth()) { Text(desktopAccountActionText(action, language)) }
    if (open) AlertDialog(onDismissRequest = { open = false; password = "" },
        title = { Text(DesktopAccountText.ConfirmTitle.text(language)) },
        text = { Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(desktopAccountActionText(action, language))
            subject?.let { Text(it) }
            Text(DesktopAccountText.LogoutWarning.text(language))
            if (requiresPassword) OutlinedTextField(password, { if (it.length <= 16_384) password = it },
                label = { Text(DesktopAccountText.Password.text(language)) }, singleLine = true,
                visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
        } },
        confirmButton = { Button(enabled = owner.available(action, targetId) && (!requiresPassword || password.isNotEmpty()), onClick = {
            val command = if (requiresPassword) DesktopAccountCommand.Secret(action, DesktopAccountSecret(password), targetId = targetId)
                else DesktopAccountCommand.Invoke(action, targetId)
            open = false; password = ""; owner.send(command)
        }) { Text(DesktopAccountText.Confirm.text(language)) } },
        dismissButton = { TextButton(onClick = { open = false; password = "" }) { Text(DesktopAccountText.Cancel.text(language)) } })
}

@Composable
internal fun AccountCheck(checked: Boolean, label: String, enabled: Boolean = true, onChange: (Boolean) -> Unit): Unit {
    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).toggleable(value = checked, enabled = enabled,
        role = Role.Checkbox, onValueChange = onChange).padding(vertical = 4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Checkbox(checked = checked, onCheckedChange = null, enabled = enabled)
        Text(label, modifier = Modifier.weight(1f).padding(top = 12.dp))
    }
}

/** Plain remember only. Strings are cleared on submit/disposal and never enter saved state. */
@Composable
internal fun AccountSecretForm(owner: DesktopAccountUiOwner, action: DesktopAccountAction,
    language: DesktopLanguage, firstLabel: DesktopAccountText, secondLabel: DesktopAccountText? = null,
    targetId: String? = null, requireMatching: Boolean = false): Unit {
    var first by remember { mutableStateOf("") }
    var second by remember { mutableStateOf("") }
    DisposableEffect(Unit) { onDispose { first = ""; second = "" } }
    val available = owner.available(action, targetId)
    OutlinedTextField(value = first, onValueChange = { if (it.length <= 16_384) first = it },
        label = { Text(firstLabel.text(language)) }, singleLine = true, enabled = available,
        visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp))
    secondLabel?.let { label ->
        OutlinedTextField(value = second, onValueChange = { if (it.length <= 16_384) second = it },
            label = { Text(label.text(language)) }, singleLine = true, enabled = available,
            visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp))
    }
    AccountButton(owner, action, language, targetId,
        enabled = first.isNotEmpty() && (secondLabel == null || second.isNotEmpty()) && (!requireMatching || first == second),
        command = {
            val command = DesktopAccountCommand.Secret(action, DesktopAccountSecret(first),
                if (secondLabel != null && !requireMatching) DesktopAccountSecret(second) else null, targetId)
            first = ""; second = ""
            command
        })
    if (first.isNotEmpty() || second.isNotEmpty()) TextButton(onClick = { first = ""; second = "" },
        modifier = Modifier.heightIn(min = 48.dp)) { Text(DesktopAccountText.Clear.text(language)) }
}
