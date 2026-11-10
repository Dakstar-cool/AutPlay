package app.autplay.ui.desktop

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue

/** Decoding a file never substitutes for this account-specific enrollment confirmation. */
@Composable
internal fun DesktopInvitationInputContent(owner: DesktopAccountUiOwner,
    review: DesktopInvitationInputReview, language: DesktopLanguage): Unit {
    AccountCard(DesktopAccountText.Invitation, language) {
        Text(if (language == DesktopLanguage.Russian)
            "Проверьте аккаунт и отпечаток уже выбранного сервера перед подключением этого устройства."
            else "Review the account and the already selected server fingerprint before connecting this device.")
        AccountDecisionValue(DesktopAccountText.Account, review.accountLabel, language)
        AccountDecisionValue(DesktopAccountText.ServerIdentity, review.serverFingerprint, language)
        AccountDecisionValue(DesktopAccountText.Expires, review.expiresLabel, language)
        var confirmed by remember(owner.state.scope, review.id) { mutableStateOf(false) }
        AccountCheck(confirmed, DesktopAccountText.ConfirmAccount.text(language)) { confirmed = it }
        AccountButton(owner, DesktopAccountAction.ConfirmInvitation, language, review.id, enabled = confirmed)
    }
}
