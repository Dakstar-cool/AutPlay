package app.autplay.ui.desktop

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

public data class DesktopMatchingTarget(public val id: String, public val label: String)
public data class DesktopMatchingCandidate(public val rank: Int, public val recordingId: String,
    public val title: String, public val artist: String, public val duration: String?, public val confidence: String?,
    public val tier: String, public val schema: String, public val evidenceSha256: String,
    public val hardConflicts: Boolean, public val available: Boolean, public val actions: List<DesktopMatchingTarget>)
public data class DesktopMatchingEntry(public val id: String, public val jobId: String, public val entryId: String,
    public val predecessor: String?, public val state: String, public val resolver: String?,
    public val candidates: List<DesktopMatchingCandidate>, public val actions: List<DesktopMatchingTarget>,
    public val inspectTarget: String?, public val cancelTarget: String?, public val resumeTarget: String?)
public data class DesktopMatchingPending(public val operationId: String, public val action: String,
    public val rank: Int?, public val predecessor: String, public val state: String,
    public val decisionId: String?, public val recordingId: String?, public val replayTarget: String?,
    public val workState: String, public val attempts: Int, public val retryAt: String?, public val retryWindow: Boolean,
    public val localJobId: String? = null, public val localEntryId: String? = null,
    public val serverJobId: String? = null, public val serverEntryId: String? = null, public val initialEntry: Boolean = false)
public data class DesktopMatchingConfirmation(public val id: String, public val jobId: String,
    public val entryId: String, public val predecessor: String, public val action: String,
    public val candidate: DesktopMatchingCandidate?, public val operationId: String,
    public val localJobId: String? = null, public val localEntryId: String? = null)
public data class DesktopMatchingConflict(public val id: String, public val aggregateType: String,
    public val localId: String, public val serverId: String?, public val kind: String,
    public val state: String, public val baseVersion: Long?, public val observedVersion: Long?,
    public val document: String)
public data class DesktopMatchingLinkConfirmation(public val id: String,public val localJobId: String,
    public val localCandidateId: String,public val serverJobId: String,public val serverEntryId: String,
    public val predecessor: String,public val rank: Int)
public data class DesktopMatchingView(public val owner: String, public val busy: Boolean,
    public val available: Boolean, public val paused: Boolean, public val entries: List<DesktopMatchingEntry>,
    public val pending: List<DesktopMatchingPending>, public val confirmation: DesktopMatchingConfirmation?,
    public val failure: String?, public val refreshTarget: String?, public val nextTarget: String?,
    public val pauseTarget: String?, public val resumeTarget: String?, public val settleTarget: String?,
    public val mappingUnavailable: Boolean = false, public val pendingNextTarget: String? = null,
    public val conflicts: List<DesktopMatchingConflict> = emptyList(), public val conflictNextTarget: String? = null,
    public val remoteLookupTarget: String? = null, public val localLookupTarget: String? = null,
    public val ownedEntries: List<DesktopMatchingTarget> = emptyList(), public val ownedNextTarget: String? = null,
    public val localCandidates: List<DesktopMatchingTarget> = emptyList(), public val localNextTarget: String? = null,
    public val selectedLocalCandidate: String? = null, public val linkChoices: List<DesktopMatchingTarget> = emptyList(),
    public val linkConfirmation: DesktopMatchingLinkConfirmation? = null,
    public val linkCandidates: List<DesktopMatchingCandidate> = emptyList(), public val linkUnavailable: String? = null)
public fun interface DesktopMatchingActions { public fun invoke(owner: String, target: String, input: String?): Unit }
public class DesktopMatchingUiCommand(override val targetId: String,public val input: String? = null): DesktopAccountCommand {
    init {require(targetId.length in 1..100 && (input==null || input.length<=36))}
    override val action: DesktopAccountAction = DesktopAccountAction.ImportMatching
    override fun toString(): String = "DesktopMatchingUiCommand"
}

/** Each control carries one host-frozen target. Recomposition retires all prior callbacks. */
@Composable
public fun DesktopImportMatchingContent(state: DesktopMatchingView, actions: DesktopMatchingActions,
    language: DesktopLanguage, modifier: Modifier = Modifier): Unit {
    val latestState = rememberUpdatedState(state); val latestActions = rememberUpdatedState(actions)
    var active by remember(state,actions) { mutableStateOf(true) }
    val used = remember(state,actions) { mutableSetOf<String>() }
    DisposableEffect(state,actions) { onDispose { active=false } }
    fun text(ru: String,en: String): String = if(language == DesktopLanguage.Russian) ru else en
    fun send(target: String,input: String? = null) {
        if(active && latestState.value === state && latestActions.value === actions && used.add(target)) actions.invoke(state.owner,target,input)
    }
    @Composable fun control(label: String,target: String?) {
        if(target != null) OutlinedButton(onClick={send(target)},enabled=!state.busy && target !in used) { Text(label) }
    }
    Column(modifier.fillMaxWidth().heightIn(max=640.dp).verticalScroll(rememberScrollState()),verticalArrangement=Arrangement.spacedBy(8.dp)) {
        Text(text("Сопоставление импорта","Import matching"),style=MaterialTheme.typography.titleLarge)
        if(state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
        state.failure?.let { Text(it,color=MaterialTheme.colorScheme.error) }
        if(!state.available) Text(text("Сопоставление сейчас недоступно.","Matching is currently unavailable."))
        if(state.mappingUnavailable) Text(text("Нет явно связанных принадлежащих вам записей импорта. Локальные файлы остаются без удалённого задания.",
            "No explicitly linked owned import entries. Local files remain independent of remote jobs."))
        var knownJob by remember(state.owner) { mutableStateOf("") }
        var localJob by remember(state.owner) { mutableStateOf("") }
        if(state.remoteLookupTarget != null) {
            OutlinedTextField(value=knownJob,onValueChange={if(it.length<=36)knownJob=it},singleLine=true,
                label={Text(text("UUID существующего задания сервера","Existing server job UUID"))})
            OutlinedButton(onClick={send(state.remoteLookupTarget,knownJob)},enabled=!state.busy && knownJob.length==36) {
                Text(text("Прочитать принадлежащий вам отчёт","Read owned report"))
            }
        }
        if(state.localLookupTarget != null) {
            OutlinedTextField(value=localJob,onValueChange={if(it.length<=36)localJob=it},singleLine=true,
                label={Text(text("UUID сохранённого локального импорта","Saved local import UUID"))})
            OutlinedButton(onClick={send(state.localLookupTarget,localJob)},enabled=!state.busy && localJob.length==36) {
                Text(text("Прочитать локальный manifest","Read local manifest"))
            }
        }
        state.localCandidates.forEach { control(it.label,it.id) }
        control(text("Далее по локальному manifest","Next local manifest page"),state.localNextTarget)
        state.selectedLocalCandidate?.let { Text("Local candidate: $it") }
        state.ownedEntries.forEach { control(it.label,it.id) }
        control(text("Далее по отчёту сервера","Next owned report page"),state.ownedNextTarget)
        state.linkUnavailable?.let {Text(it)}
        if(state.linkCandidates.isNotEmpty()) Text(text("Подписи из текущего каталога для явной связи:","Current catalog display for explicit linking:"))
        state.linkCandidates.forEach { Candidate(it,language) }
        state.linkChoices.forEach { control(it.label,it.id) }
        Row(Modifier.horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(8.dp)) {
            control(text("Обновить","Refresh"),state.refreshTarget); control(text("Следующая страница","Next page"),state.nextTarget)
            control(text("Приостановить просмотр","Pause review"),state.pauseTarget); control(text("Продолжить просмотр","Resume review"),state.resumeTarget)
            control(text("Сверить сохранённый результат","Reconcile retained result"),state.settleTarget)
        }
        state.entries.forEach { entry -> key(entry.id) {
            Surface(tonalElevation=1.dp) { Column(Modifier.fillMaxWidth().padding(12.dp),verticalArrangement=Arrangement.spacedBy(6.dp)) {
                Text("${entry.jobId} / ${entry.entryId}")
                Text("${entry.state} / ${entry.resolver ?: "?"}")
                entry.predecessor?.let { Text("Predecessor: $it") }
                Row(Modifier.horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                    control(text("Кандидаты","Candidates"),entry.inspectTarget)
                    control(text("Отменить известное задание","Cancel known job"),entry.cancelTarget)
                    control(text("Продолжить известное задание","Resume known job"),entry.resumeTarget)
                }
                if(entry.candidates.isNotEmpty()) Text(text("Подписи из текущего каталога; evidence относится к сохранённому решению.",
                    "Labels are current catalog display; evidence belongs to the retained decision."))
                entry.candidates.forEach { candidate -> key(candidate.rank,candidate.recordingId) {
                    Candidate(candidate,language)
                    Row(Modifier.horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                        candidate.actions.forEach { control(it.label,it.id) }
                    }
                } }
                Row(Modifier.horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                    entry.actions.forEach { control(it.label,it.id) }
                }
            } }
        } }
        state.pending.forEach { pending -> key(pending.operationId) {
            Text("${pending.operationId}: ${pending.action} ${pending.rank ?: ""} / ${pending.state}")
            pending.localJobId?.let { Text("Local: $it / ${pending.localEntryId}") }
            pending.serverJobId?.let { Text("Server: $it / ${pending.serverEntryId}") }
            if(pending.initialEntry) Text(text("Решение по записи без ранжированного кандидата.","Entry decision with no ranked candidate."))
            Text("${pending.workState}; ${text("попытки","attempts")}: ${pending.attempts} / 8")
            pending.retryAt?.let {Text(text("Следующая попытка не раньше: ","Retry no earlier than: ")+it)}
            Text("Predecessor: ${pending.predecessor}")
            pending.decisionId?.let { Text("Decision: $it") }; pending.recordingId?.let { Text("Recording: $it") }
            control(if(pending.retryWindow)text("Открыть новое ограниченное окно повторов","Reopen bounded retry window") else
                text("Повторить точное решение","Replay exact decision"),pending.replayTarget)
        } }
        control(text("Следующая страница решений","Next decisions page"),state.pendingNextTarget)
        state.conflicts.forEach { conflict -> key(conflict.id) {
            Text(text("Сохранённый конфликт синхронизации","Retained sync conflict"),style=MaterialTheme.typography.titleMedium)
            Text("${conflict.aggregateType}: ${conflict.localId} / ${conflict.serverId ?: "?"}")
            Text("${conflict.kind} / ${conflict.state}; ${conflict.baseVersion ?: "?"} -> ${conflict.observedVersion ?: "?"}")
            var part by remember(conflict) { mutableStateOf(0) }
            val count=maxOf(1,(conflict.document.length+4095)/4096)
            Text("${part+1} / $count")
            fun boundary(value: Int): Int = minOf(value,conflict.document.length).let { index ->
                if(index>0 && index<conflict.document.length && conflict.document[index].isLowSurrogate()) index-1 else index
            }
            Text(conflict.document.substring(boundary(part*4096),boundary((part+1)*4096)),style=MaterialTheme.typography.bodySmall)
            Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick={part--},enabled=part>0) {Text(text("Назад","Previous"))}
                OutlinedButton(onClick={part++},enabled=part+1<count) {Text(text("Далее","Next"))}
            }
        } }
        control(text("Следующий сохранённый конфликт","Next retained conflict"),state.conflictNextTarget)
    }
    state.confirmation?.let { confirmation ->
        AlertDialog(onDismissRequest={send("dismiss:${confirmation.id}")},title={Text(
            if(confirmation.localJobId != null) text("Подтвердите связь и решение","Confirm link and decision") else text("Подтвердите решение","Confirm decision"))},
            text={Column(Modifier.heightIn(max=400.dp).verticalScroll(rememberScrollState()),verticalArrangement=Arrangement.spacedBy(8.dp)) {
                if(confirmation.localJobId != null) {
                    Text("Local: ${confirmation.localJobId} / ${confirmation.localEntryId}")
                    Text(text("Ранжированных кандидатов нет; rank и candidate отсутствуют. Связь и решение сохраняются вместе.",
                        "There are no ranked candidates; rank and candidate are absent. The link and decision are retained together."))
                }
                Text("${confirmation.jobId} / ${confirmation.entryId}"); Text("Predecessor: ${confirmation.predecessor}")
                Text(confirmation.action); confirmation.candidate?.let { Candidate(it,language) }
                Text("Operation: ${confirmation.operationId}")
                Text(text("Подтверждение относится только к этому показанному решению.","Confirmation applies only to this displayed decision."))
            }},confirmButton={TextButton(onClick={send("confirm:${confirmation.id}")},enabled=!state.busy) {Text(text("Подтвердить","Confirm"))}},
            dismissButton={TextButton(onClick={send("dismiss:${confirmation.id}")},enabled=!state.busy) {Text(text("Закрыть","Dismiss"))}})
    }
    state.linkConfirmation?.let { confirmation ->
        AlertDialog(onDismissRequest={send("dismiss-link:${confirmation.id}")},
            title={Text(text("Подтвердите явную связь","Confirm explicit link"))},text={Column {
                Text("Local: ${confirmation.localJobId} / ${confirmation.localCandidateId}")
                Text("Server: ${confirmation.serverJobId} / ${confirmation.serverEntryId}")
                Text("Predecessor: ${confirmation.predecessor}; rank ${confirmation.rank}")
                Text(text("Связь не изменяет аудиофайлы и не подтверждает решение о Recording.","This link changes no audio file and confirms no Recording decision."))
            }},confirmButton={TextButton(onClick={send("confirm-link:${confirmation.id}")},enabled=!state.busy) {Text(text("Связать","Link"))}},
            dismissButton={TextButton(onClick={send("dismiss-link:${confirmation.id}")},enabled=!state.busy) {Text(text("Закрыть","Dismiss"))}})
    }
}

@Composable
private fun Candidate(candidate: DesktopMatchingCandidate,language: DesktopLanguage): Unit {
    Column(Modifier.fillMaxWidth(),verticalArrangement=Arrangement.spacedBy(2.dp)) {
        Text("#${candidate.rank}: ${candidate.title} - ${candidate.artist}")
        Text("Recording: ${candidate.recordingId}"); candidate.duration?.let {Text(it)}
        Text("${candidate.confidence ?: "?"} / ${candidate.tier} / schema ${candidate.schema}")
        Text("Evidence SHA-256: ${candidate.evidenceSha256}",style=MaterialTheme.typography.bodySmall)
        if(candidate.hardConflicts) Text(if(language==DesktopLanguage.Russian) "Есть существенные конфликты" else "Hard conflicts are present",color=MaterialTheme.colorScheme.error)
        if(!candidate.available) Text(if(language==DesktopLanguage.Russian) "Evidence сохранено, действия недоступны" else "Evidence retained; actions unavailable")
    }
}
