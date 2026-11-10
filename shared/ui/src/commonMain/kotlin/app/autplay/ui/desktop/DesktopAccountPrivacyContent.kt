package app.autplay.ui.desktop

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue

@Composable
internal fun DesktopAccountPrivacyContent(owner: DesktopAccountUiOwner, language: DesktopLanguage): Unit {
    val state = owner.state.privacy
    AccountCard(DesktopAccountText.Privacy, language) {
        AccountStatus(state.condition, DesktopAccountIssue.None, language)
        AccountButton(owner, DesktopAccountAction.ReadPolicy, language)
    }
    AccountCard(DesktopAccountText.Training, language) {
        Text(DesktopAccountText.TrainingHelp.text(language))
        Text(when (state.training) {
            null -> DesktopAccountText.Unconfirmed
            DesktopTrainingPolicy.Unknown -> DesktopAccountText.TrainingUnknown
            DesktopTrainingPolicy.Granted -> DesktopAccountText.TrainingGranted
            DesktopTrainingPolicy.Denied -> DesktopAccountText.TrainingDenied
            DesktopTrainingPolicy.Withdrawn -> DesktopAccountText.TrainingWithdrawn
        }.text(language))
        val revision = state.trainingRevision
        if (revision != null && state.training != null) {
            AccountButton(owner, DesktopAccountAction.DecideTraining, language,
                label = DesktopAccountText.Allow.text(language), enabled = state.training != DesktopTrainingPolicy.Granted,
                command = { DesktopAccountCommand.Training(DesktopAccountCommand.TrainingDecision.Grant, revision) })
            AccountButton(owner, DesktopAccountAction.DecideTraining, language,
                label = (if (state.training == DesktopTrainingPolicy.Granted) DesktopAccountText.Withdraw else DesktopAccountText.Refuse).text(language),
                enabled = state.training != DesktopTrainingPolicy.Denied && state.training != DesktopTrainingPolicy.Withdrawn,
                command = { DesktopAccountCommand.Training(if (state.training == DesktopTrainingPolicy.Granted)
                    DesktopAccountCommand.TrainingDecision.Withdraw else DesktopAccountCommand.TrainingDecision.Deny, revision) })
        }
    }
    AccountCard(DesktopAccountText.Visibility, language) {
        Text(when (state.statisticsVisible) { true -> DesktopAccountText.Visible; false -> DesktopAccountText.Hidden
            null -> DesktopAccountText.Unconfirmed }.text(language))
        val revision = state.visibilityRevision
        if (revision != null && state.statisticsVisible != null) {
            AccountButton(owner, DesktopAccountAction.DecideVisibility, language,
                label = (if (state.statisticsVisible) DesktopAccountText.HideStatistics else DesktopAccountText.ShowStatistics).text(language),
                command = { DesktopAccountCommand.Visibility(!state.statisticsVisible, revision) })
        }
    }
    AccountCard(DesktopAccountText.DeleteTitle, language) {
        val deletion = state.deletion
        Text(DesktopAccountText.DeleteWarning.text(language))
        if (deletion == null) AccountButton(owner, DesktopAccountAction.PreviewDeletion, language)
        deletion?.let { current -> key(current.id, current.stage) {
            AccountDecisionValue(DesktopAccountText.PublicId, current.accountPublicId, language)
            Text((if (current.role == DesktopDeletionRole.Source) DesktopAccountText.Source else DesktopAccountText.Recipient).text(language))
            current.deadlineLabel?.let { AccountDecisionValue(DesktopAccountText.DeleteDeadline, it, language) }
            Text(when (current.stage) {
                DesktopDeletionStage.Preview -> DesktopAccountText.DeleteWarning
                DesktopDeletionStage.Pending -> DesktopAccountText.DeletionPending
                DesktopDeletionStage.Suspended -> DesktopAccountText.DeletionSuspended
                DesktopDeletionStage.Cancelling -> DesktopAccountText.DeletionCancelling
                DesktopDeletionStage.Cancelled -> DesktopAccountText.DeletionCancelled
                DesktopDeletionStage.Purging -> DesktopAccountText.DeletionPurging
                DesktopDeletionStage.Purged -> DesktopAccountText.DeletionPurged
                DesktopDeletionStage.Unknown -> DesktopAccountText.Unconfirmed
            }.text(language))
            if (current.stage == DesktopDeletionStage.Preview && current.role == DesktopDeletionRole.Source) {
                var checked by remember { mutableStateOf(false) }
                AccountCheck(checked, DesktopAccountText.DeleteConfirm.text(language)) { checked = it }
                if (owner.permit(DesktopAccountAction.RequestDeletion, current.id)?.requiresPassword == true) {
                    if (checked) AccountSecretForm(owner, DesktopAccountAction.RequestDeletion, language,
                        DesktopAccountText.RecoveryCode, targetId = current.id)
                } else AccountButton(owner, DesktopAccountAction.RequestDeletion, language, current.id, enabled = checked)
            }
            if (current.canCancel) {
                AccountButton(owner, DesktopAccountAction.ChooseServerRecoveryFile, language, current.id)
                AccountRecoveryPreviewForm(owner, language, cancelDeletion = true, targetId = current.id)
                current.recoveryPreviewId?.let { preview ->
                    owner.state.connection.localChoiceSummary?.let { Text(it) }
                    var checked by remember(preview) { mutableStateOf(false) }
                    AccountCheck(checked, DesktopAccountText.RecoveryConfirm.text(language)) { checked = it }
                    AccountButton(owner, DesktopAccountAction.CancelDeletion, language, preview, enabled = checked)
                }
            }
            AccountButton(owner, DesktopAccountAction.ResumeDeletion, language, current.id)
        } }
    }
}
