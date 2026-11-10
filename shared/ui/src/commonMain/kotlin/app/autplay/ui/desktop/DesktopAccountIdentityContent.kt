package app.autplay.ui.desktop

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp

@Composable
internal fun DesktopPortableAccountContent(owner: DesktopAccountUiOwner, language: DesktopLanguage): Unit {
    val state = owner.state.portable
    AccountCard(DesktopAccountText.Portable, language) {
        Text(DesktopAccountText.PortableHelp.text(language))
        AccountStatus(state.condition, DesktopAccountIssue.None, language)
        state.publicId?.let { AccountDecisionValue(DesktopAccountText.PublicId, it, language) }
        state.rootVersion?.let { AccountDecisionValue(DesktopAccountText.RootVersion, it.toString(), language) }
        if (owner.permit(DesktopAccountAction.CreatePortable, null) != null) PortableCreateForm(owner, language)
        if (owner.permit(DesktopAccountAction.UnlockPortable, null) != null)
            AccountSecretForm(owner, DesktopAccountAction.UnlockPortable, language, DesktopAccountText.Password)
        if (owner.permit(DesktopAccountAction.LockPortable, null) != null)
            AccountButton(owner, DesktopAccountAction.LockPortable, language)
        AccountButton(owner, DesktopAccountAction.Refresh, language)
    }
    AccountCard(DesktopAccountText.RecoveryCode, language) {
        Text(DesktopAccountText.RecoveryFileHelp.text(language))
        val fileText = when (state.recoveryFileStatus) {
            DesktopRecoveryFileStatus.None -> null
            DesktopRecoveryFileStatus.Choosing -> DesktopAccountText.FileChoosing
            DesktopRecoveryFileStatus.ReadbackRequired -> DesktopAccountText.FileReadback
            DesktopRecoveryFileStatus.Verified -> DesktopAccountText.FileVerified
            DesktopRecoveryFileStatus.TargetExists -> DesktopAccountText.FileExists
            DesktopRecoveryFileStatus.Failed -> DesktopAccountText.FileFailed
        }
        fileText?.let { Text(it.text(language)) }
        AccountButton(owner, DesktopAccountAction.ImportRecoveryFile, language)
        AccountButton(owner, DesktopAccountAction.ExportRecoveryFile, language)
        AccountSensitiveOutput(owner, DesktopAccountSensitiveOutputKind.PortableRecoveryCode, language)
        if (owner.permit(DesktopAccountAction.RestorePortable, null) != null)
            AccountSecretForm(owner, DesktopAccountAction.RestorePortable, language,
                DesktopAccountText.RecoveryCode, DesktopAccountText.NewPassword)
    }
    AccountCard(DesktopAccountText.RotationWarning, language) {
        AccountButton(owner, DesktopAccountAction.PrepareRotation, language)
        state.rotationPreviewId?.let { id -> key(id) {
            var checked by remember { mutableStateOf(false) }
            AccountCheck(checked, DesktopAccountText.Confirm.text(language)) { checked = it }
            if (checked) AccountSecretForm(owner, DesktopAccountAction.RotateRoot, language,
                DesktopAccountText.Password, DesktopAccountText.NewPassword, id)
        } }
        state.reauthenticationId?.let { id ->
            AccountSecretForm(owner, DesktopAccountAction.Reauthenticate, language, DesktopAccountText.Password, targetId = id)
        }
    }
}

@Composable
private fun PortableCreateForm(owner: DesktopAccountUiOwner, language: DesktopLanguage): Unit {
    var publicId by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var repeat by remember { mutableStateOf("") }
    DisposableEffect(Unit) { onDispose { publicId = ""; password = ""; repeat = "" } }
    val available = owner.available(DesktopAccountAction.CreatePortable)
    OutlinedTextField(publicId, { if (it.length <= 120) publicId = it }, enabled = available,
        label = { Text(DesktopAccountText.PublicId.text(language)) }, singleLine = true, modifier = Modifier.fillMaxWidth())
    OutlinedTextField(password, { if (it.length <= 16_384) password = it }, enabled = available,
        label = { Text(DesktopAccountText.NewPassword.text(language)) }, singleLine = true,
        visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
    OutlinedTextField(repeat, { if (it.length <= 16_384) repeat = it }, enabled = available,
        label = { Text(DesktopAccountText.RepeatPassword.text(language)) }, singleLine = true,
        visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
    AccountButton(owner, DesktopAccountAction.CreatePortable, language,
        enabled = publicId.isNotEmpty() && password.isNotEmpty() && password == repeat,
        command = {
            val command = DesktopAccountCommand.CreatePortable(publicId, DesktopAccountSecret(password))
            publicId = ""; password = ""; repeat = ""
            command
        })
}

@Composable
internal fun DesktopAccountConnectionContent(owner: DesktopAccountUiOwner, language: DesktopLanguage): Unit {
    val state = owner.state.connection
    AccountCard(DesktopAccountText.Connection, language) {
        Text(DesktopAccountText.ConnectionHelp.text(language))
        AccountStatus(state.condition, state.issue, language)
        state.serverLabel?.let { Text(it, style = MaterialTheme.typography.titleMedium) }
        state.accountPublicId?.let { AccountDecisionValue(DesktopAccountText.PublicId, it, language) }
        state.pendingItems?.let { count -> Text("${DesktopAccountText.PendingChanges.text(language)}: $count") }
        if (owner.permit(DesktopAccountAction.Discover, null) != null) AccountOriginForm(owner, language)
        DesktopAccountPinnedTransportContent(owner, language)
        Text(DesktopAccountText.QrHelp.text(language))
        AccountSecretForm(owner, DesktopAccountAction.SubmitQr, language, DesktopAccountText.QrInput)
        AccountButton(owner, DesktopAccountAction.ChooseQrFile, language)
        AccountButton(owner, DesktopAccountAction.CancelConnection, language)
    }
    state.transportReview?.let { review -> key(review.id) {
        AccountCard(DesktopAccountText.TrustTitle, language) {
            AccountDecisionValue(DesktopAccountText.ApiOrigin, review.normalizedApiOrigin, language)
            AccountDecisionValue(DesktopAccountText.StreamOrigin, review.normalizedStreamOrigin, language)
            AccountDecisionValue(DesktopAccountText.ServerIdentity, review.serverIdentity, language)
            AccountDecisionValue(DesktopAccountText.ApiFingerprint,
                review.apiTlsFingerprint ?: if (review.apiUsesSystemTrust) DesktopAccountText.SystemTrust.text(language)
                else DesktopAccountText.Unconfirmed.text(language), language)
            AccountDecisionValue(DesktopAccountText.StreamFingerprint,
                review.streamTlsFingerprint ?: if (review.streamUsesSystemTrust) DesktopAccountText.SystemTrust.text(language)
                else DesktopAccountText.Unconfirmed.text(language), language)
            var api by remember { mutableStateOf(false) }
            var stream by remember { mutableStateOf(false) }
            var identity by remember { mutableStateOf(false) }
            AccountCheck(api, DesktopAccountText.VerifyApi.text(language)) { api = it }
            AccountCheck(stream, DesktopAccountText.VerifyStream.text(language)) { stream = it }
            AccountCheck(identity, DesktopAccountText.VerifyIdentity.text(language)) { identity = it }
            Text(if (language == DesktopLanguage.Russian)
                "Сначала выберите ниже: оставить данные локально или проверить выбранные записи. Выбор относится к этому серверу; аккаунт подтверждается отдельно при подключении."
                else "First choose below: keep data local or review selected records. The choice applies to this server; confirm the account separately during connection.")
            if (state.localChoiceSelected) Text(if (language == DesktopLanguage.Russian)
                "Выбор локальных данных сохранён для подтверждения сервера." else "Local data choice captured for server confirmation.")
            AccountButton(owner, DesktopAccountAction.ConfirmTransport, language, review.id,
                enabled = api && stream && identity && (review.apiUsesSystemTrust || review.apiTlsFingerprint != null) &&
                    (review.streamUsesSystemTrust || review.streamTlsFingerprint != null),
                command = { DesktopAccountCommand.ConfirmTransport(review.id, api, stream, identity) })
        }
    } }
    AccountCard(DesktopAccountText.Invitation, language) {
        state.localChoiceSummary?.let { Text(it) }
        AccountSecretForm(owner, DesktopAccountAction.SubmitInvitation, language, DesktopAccountText.Invitation)
        AccountButton(owner, DesktopAccountAction.RequestAdmission, language)
        state.admissionId?.let { AccountButton(owner, DesktopAccountAction.ResumeAdmission, language, it) }
        AccountButton(owner, DesktopAccountAction.EnrollTrusted, language)
        state.admissionId?.let { target -> key(target, state.admissionAccountLabel) {
            state.admissionAccountLabel?.let { label ->
                AccountDecisionValue(DesktopAccountText.Account, label, language)
                if (state.admissionConfirmationRequired || state.trustedConfirmationRequired) {
                    var confirmed by remember { mutableStateOf(false) }
                    AccountCheck(confirmed, DesktopAccountText.ConfirmAccount.text(language)) { confirmed = it }
                    AccountButton(owner, if (state.trustedConfirmationRequired) DesktopAccountAction.ConfirmTrusted
                        else DesktopAccountAction.ConfirmAdmission, language, target, enabled = confirmed)
                }
            }
        } }
        state.accountPublicId?.let { publicId -> key(publicId) {
            if (owner.permit(DesktopAccountAction.RegisterPortable, null) != null ||
                owner.permit(DesktopAccountAction.LoginPortable, null) != null) {
                AccountDecisionValue(DesktopAccountText.Account, publicId, language)
                var confirmed by remember { mutableStateOf(false) }
                AccountCheck(confirmed, DesktopAccountText.ConfirmAccount.text(language)) { confirmed = it }
                if (confirmed) {
                    AccountSecretForm(owner, DesktopAccountAction.RegisterPortable, language, DesktopAccountText.Password)
                    AccountSecretForm(owner, DesktopAccountAction.LoginPortable, language, DesktopAccountText.Password)
                }
            }
        } }
    }
    state.invitationReview?.let { review -> key(review.id) {
        DesktopInvitationInputContent(owner, review, language)
    } }
    AccountCard(DesktopAccountText.LocalReview, language) {
        Text(DesktopAccountText.LocalReviewHelp.text(language))
        val review = state.localDataReview
        AccountButton(owner, DesktopAccountAction.KeepLocal, language, review?.id ?: state.bindingId?.takeIf { state.transportReview == null })
        AccountButton(owner, DesktopAccountAction.ReviewLocal, language, state.bindingId?.takeIf { state.transportReview == null })
        review?.let { current -> key(current.id) {
            AccountDecisionValue(DesktopAccountText.PublicId, current.accountPublicId, language)
            var selected by remember { mutableStateOf(emptyList<String>()) }
            current.facts.forEach { fact -> key(fact.id) {
                AccountCheck(fact.id in selected, fact.label, fact.eligible) { checked ->
                    selected = if (checked && fact.id !in selected && selected.size < (if (state.transportReview != null) 32 else 100)) selected + fact.id
                        else if (!checked) selected - fact.id else selected
                }
            } }
            Text("${DesktopAccountText.Selected.text(language)}: ${selected.size}")
            AccountButton(owner, DesktopAccountAction.ConnectSelected, language, current.id,
                enabled = selected.isNotEmpty(), command = { DesktopAccountCommand.ConnectSelected(current.id, selected.toList()) })
            TextButton(onClick = { selected = emptyList() }, modifier = Modifier.heightIn(min = 48.dp)) {
                Text(DesktopAccountText.Clear.text(language))
            }
            if (current.hasMore) {
                Text(DesktopAccountText.MoreLocal.text(language))
                AccountButton(owner, DesktopAccountAction.ReviewLocal, language, current.id, label = DesktopAccountText.Next.text(language))
            }
        } }
        state.bindingId?.let { AccountButton(owner, DesktopAccountAction.Bind, language, it) }
    }
    AccountCard(DesktopAccountText.BasicStatus, language) {
        Text(DesktopAccountText.BasicStatusHelp.text(language))
        AccountStatus(state.condition, state.issue, language)
        AccountButton(owner, DesktopAccountAction.Refresh, language)
    }
}

@Composable
private fun AccountOriginForm(owner: DesktopAccountUiOwner, language: DesktopLanguage): Unit {
    var origin by remember { mutableStateOf("") }
    DisposableEffect(Unit) { onDispose { origin = "" } }
    OutlinedTextField(origin, { if (it.length <= 2048) origin = it },
        label = { Text(DesktopAccountText.Origin.text(language)) }, singleLine = true,
        modifier = Modifier.fillMaxWidth(), enabled = owner.available(DesktopAccountAction.Discover))
    AccountButton(owner, DesktopAccountAction.Discover, language, enabled = origin.isNotEmpty(), command = {
        val command = DesktopAccountCommand.Discover(DesktopAccountSecret(origin)); origin = ""; command
    })
}

@Composable
internal fun DesktopAccountSelfContent(owner: DesktopAccountUiOwner, language: DesktopLanguage): Unit {
    val state = owner.state.selfPairing
    AccountCard(DesktopAccountText.Self, language) {
        owner.state.connection.localChoiceSummary?.let { Text(it) }
        Text(DesktopAccountText.SelfHelp.text(language))
        Text((if (state.role == DesktopSelfRole.Source) DesktopAccountText.Source else DesktopAccountText.Recipient).text(language))
        AccountStatus(state.condition, DesktopAccountIssue.None, language)
        AccountButton(owner, DesktopAccountAction.StartSelf, language)
        AccountSensitiveOutput(owner, DesktopAccountSensitiveOutputKind.SelfQr, language)
        AccountSecretForm(owner, DesktopAccountAction.ClaimSelf, language, DesktopAccountText.QrInput)
        AccountButton(owner, DesktopAccountAction.ChooseQrFile, language)
        state.ceremonyId?.let { id -> key(id, state.sas) {
            state.recipientLabel?.let { AccountDecisionValue(DesktopAccountText.Recipient, it, language) }
            state.expiresLabel?.let { AccountDecisionValue(DesktopAccountText.Expires, it, language) }
            state.sas?.let { AccountDecisionValue(DesktopAccountText.Sas, it, language) }
            state.accountLabel?.let { AccountDecisionValue(DesktopAccountText.Account, it, language) }
            if (state.sourceApproved != null) Text((if (state.sourceApproved) DesktopAccountText.SourceApproved
                else DesktopAccountText.SourceWaiting).text(language))
            if (state.role == DesktopSelfRole.Source && state.sas != null && state.sourceApproved != true) {
                var codesMatch by remember { mutableStateOf(false) }
                AccountCheck(codesMatch, DesktopAccountText.SasConfirmed.text(language)) { codesMatch = it }
                AccountButton(owner, DesktopAccountAction.ApproveSelf, language, id, enabled = codesMatch)
            }
            if (state.role == DesktopSelfRole.Recipient && state.exchangeConfirmationRequired && state.accountLabel != null) {
                var accountConfirmed by remember(state.accountLabel) { mutableStateOf(false) }
                AccountCheck(accountConfirmed, DesktopAccountText.ConfirmAccount.text(language)) { accountConfirmed = it }
                AccountButton(owner, DesktopAccountAction.ExchangeSelf, language, id, enabled = accountConfirmed)
            }
            AccountButton(owner, DesktopAccountAction.CancelSelf, language, id,
                label = if (state.role == DesktopSelfRole.Recipient) {
                    if (language == DesktopLanguage.Russian) "Остановить на этом устройстве" else "Stop on this device"
                } else desktopAccountActionText(DesktopAccountAction.CancelSelf, language))
            AccountButton(owner, DesktopAccountAction.ResumeSelf, language, id)
        } }
    }
}

@Composable
internal fun DesktopAccountDevicesContent(owner: DesktopAccountUiOwner, language: DesktopLanguage): Unit {
    val state = owner.state.devices
    AccountCard(DesktopAccountText.Devices, language) {
        AccountStatus(state.condition, DesktopAccountIssue.None, language)
        Text(DesktopAccountText.DisconnectHelp.text(language))
        AccountButton(owner, DesktopAccountAction.RefreshDevices, language)
        AccountDestructiveButton(owner, DesktopAccountAction.LogoutCurrent, language)
        AccountDestructiveButton(owner, DesktopAccountAction.LogoutAll, language)
        AccountDestructiveButton(owner, DesktopAccountAction.Disconnect, language)
    }
    AccountCard(DesktopAccountText.DeviceList, language) {
        if (state.devices.isEmpty() && state.condition == DesktopAccountCondition.Ready) Text(DesktopAccountText.EmptyList.text(language))
        state.devices.forEach { device -> key(device.id) {
            Text(device.label, style = MaterialTheme.typography.titleMedium)
            if (device.current) Text(DesktopAccountText.Current.text(language))
            AccountStatus(device.condition, DesktopAccountIssue.None, language)
            device.lastSeenLabel?.let { AccountDecisionValue(DesktopAccountText.LastSeen, it, language) }
            AccountDestructiveButton(owner, DesktopAccountAction.RevokeDevice, language, device.id, device.label)
        } }
    }
    AccountCard(DesktopAccountText.SessionList, language) {
        if (state.sessions.isEmpty() && state.condition == DesktopAccountCondition.Ready) Text(DesktopAccountText.EmptyList.text(language))
        state.sessions.forEach { session -> key(session.id) {
            Text(session.deviceLabel, style = MaterialTheme.typography.titleMedium)
            if (session.current) Text(DesktopAccountText.Current.text(language))
            AccountStatus(session.condition, DesktopAccountIssue.None, language)
        } }
    }
    AccountCard(DesktopAccountText.InvitationList, language) {
        AccountButton(owner, DesktopAccountAction.CreateInvitation, language)
        AccountSensitiveOutput(owner, DesktopAccountSensitiveOutputKind.EnrollmentInvitation, language)
        state.invitations.forEach { invitation -> key(invitation.id) {
            AccountDecisionValue(DesktopAccountText.Expires, invitation.expiresLabel, language)
            AccountStatus(invitation.condition, DesktopAccountIssue.None, language)
            AccountDestructiveButton(owner, DesktopAccountAction.CancelInvitation, language, invitation.id)
        } }
    }
}

@Composable
internal fun DesktopAccountServerRecoveryContent(owner: DesktopAccountUiOwner, language: DesktopLanguage): Unit {
    val state = owner.state.serverRecovery
    AccountCard(DesktopAccountText.ServerRecovery, language) {
        Text(DesktopAccountText.ServerRecoveryHelp.text(language))
        AccountStatus(state.condition, DesktopAccountIssue.None, language)
        AccountSensitiveOutput(owner, DesktopAccountSensitiveOutputKind.ServerRecoveryCode, language)
        if (owner.available(DesktopAccountAction.ConfigureServerRecovery)) {
            var confirmed by remember(owner.state.scope) { mutableStateOf(false) }
            AccountCheck(confirmed, if (language == DesktopLanguage.Russian)
                "Заменить прежний код восстановления и сохранить новый файл"
                else "Replace the previous recovery code and save a new file") { confirmed = it }
            AccountButton(owner, DesktopAccountAction.ConfigureServerRecovery, language, enabled = confirmed)
        }
        if (state.exports.isNotEmpty()) Text(if (language == DesktopLanguage.Russian)
            "Сохранённые результаты выпуска кода. Другое устройство могло заменить этот код."
            else "Retained code issuance results. Another device may have replaced this code.")
        state.exports.forEach { export -> key(export.targetId) {
            Text(when (export.kind) {
                DesktopServerRecoveryExportKind.SourceConfiguration -> if (language == DesktopLanguage.Russian)
                    "Настройка кода на этом устройстве" else "Code configured on this device"
                DesktopServerRecoveryExportKind.RecipientRecovery -> if (language == DesktopLanguage.Russian)
                    "Код после восстановления доступа" else "Code after access recovery"
                DesktopServerRecoveryExportKind.DeletionCancellation -> if (language == DesktopLanguage.Russian)
                    "Код после отмены удаления" else "Code after deletion cancellation"
            })
            Text((if (language == DesktopLanguage.Russian) "Поколение кода: " else "Code generation: ") + export.codeGeneration)
            AccountButton(owner, DesktopAccountAction.ExportServerRecovery, language, export.targetId)
        } }
        AccountButton(owner, DesktopAccountAction.ChooseServerRecoveryFile, language)
        AccountRecoveryPreviewForm(owner, language, cancelDeletion = false)
        state.previewId?.let { id -> key(id) {
            state.accountLabel?.let { AccountDecisionValue(DesktopAccountText.Account, it, language) }
            Text(DesktopAccountText.RecoveryWarning.text(language))
            owner.state.connection.localChoiceSummary?.let { Text(it) }
            state.consequences?.let { consequences ->
                AccountDecisionValue(DesktopAccountText.RevokedDevices, consequences.devicesRevoked?.toString()
                    ?: DesktopAccountText.Unconfirmed.text(language), language)
                AccountDecisionValue(DesktopAccountText.RevokedSessions, consequences.sessionsRevoked?.toString()
                    ?: DesktopAccountText.Unconfirmed.text(language), language)
            }
            var checked by remember { mutableStateOf(false) }
            AccountCheck(checked, DesktopAccountText.RecoveryConfirm.text(language)) { checked = it }
            AccountButton(owner, DesktopAccountAction.CommitRecovery, language, id, enabled = checked)
            AccountButton(owner, DesktopAccountAction.ResumeRecovery, language, id)
        } }
    }
}

@Composable
internal fun AccountRecoveryPreviewForm(owner: DesktopAccountUiOwner, language: DesktopLanguage,
    cancelDeletion: Boolean, targetId: String? = null): Unit {
    var account by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    DisposableEffect(Unit) { onDispose { account = ""; code = "" } }
    val action = if (cancelDeletion) DesktopAccountAction.PreviewDeletion else DesktopAccountAction.PreviewRecovery
    val available = owner.available(action, targetId)
    OutlinedTextField(account, { if (it.length <= 128) account = it }, enabled = available,
        label = { Text(DesktopAccountText.ServerAccountId.text(language)) }, singleLine = true, modifier = Modifier.fillMaxWidth())
    OutlinedTextField(code, { if (it.length <= 4096) code = it }, enabled = available,
        label = { Text(DesktopAccountText.RecoveryCode.text(language)) }, singleLine = true,
        visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
    AccountButton(owner, action, language, targetId, enabled = account.isNotEmpty() && code.isNotEmpty(), command = {
        val command = DesktopAccountCommand.RecoveryPreview(DesktopAccountSecret(account), DesktopAccountSecret(code), cancelDeletion, targetId)
        account = ""; code = ""; command
    })
}

@Composable
internal fun AccountDecisionValue(label: DesktopAccountText, value: String, language: DesktopLanguage): Unit {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(label.text(language), style = MaterialTheme.typography.labelLarge)
        Text(value, style = MaterialTheme.typography.bodyLarge)
    }
}

@Composable
private fun AccountSensitiveOutput(owner: DesktopAccountUiOwner, kind: DesktopAccountSensitiveOutputKind,
    language: DesktopLanguage): Unit {
    val output = owner.sensitiveOutput?.takeIf { it.scope == owner.state.scope && it.kind == kind } ?: return
    var revealed by remember(output) { mutableStateOf<String?>(null) }
    DisposableEffect(output) { onDispose { revealed = null; output.clear() } }
    Text(DesktopAccountText.SecretOutputHelp.text(language))
    if (revealed == null) TextButton(onClick = { revealed = output.reveal() }, modifier = Modifier.heightIn(min = 48.dp)) {
        Text(DesktopAccountText.RevealSecret.text(language))
    } else {
        Text(revealed.orEmpty())
        TextButton(onClick = { revealed = null; output.clear() }, modifier = Modifier.heightIn(min = 48.dp)) {
            Text(DesktopAccountText.HideSecret.text(language))
        }
    }
}
