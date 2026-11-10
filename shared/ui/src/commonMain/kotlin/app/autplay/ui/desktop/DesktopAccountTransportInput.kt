package app.autplay.ui.desktop

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier

/** Origin and SPKI text is process-only and disappears on submission or surface disposal. */
@Composable
internal fun DesktopAccountPinnedTransportContent(owner: DesktopAccountUiOwner, language: DesktopLanguage): Unit {
    val ru = language == DesktopLanguage.Russian
    var expanded by remember(owner.state.scope.lifetime) { mutableStateOf(false) }
    TextButton(onClick = { expanded = !expanded }) {
        Text(if (ru) "Дополнительная проверка TLS" else "Additional TLS verification")
    }
    if (expanded && owner.permit(DesktopAccountAction.PreparePinnedTransport, null) != null) {
        PinnedTransportForm(owner, language)
    }
    owner.state.connection.transportBootstrap?.let { review -> key(review.id) {
        AccountCard(DesktopAccountText.TrustTitle, language) {
            AccountDecisionValue(DesktopAccountText.ApiOrigin, review.apiOrigin, language)
            AccountDecisionValue(DesktopAccountText.StreamOrigin, review.streamOrigin, language)
            AccountDecisionValue(DesktopAccountText.ApiFingerprint, review.apiFingerprint
                ?: DesktopAccountText.SystemTrust.text(language), language)
            AccountDecisionValue(DesktopAccountText.StreamFingerprint, review.streamFingerprint
                ?: DesktopAccountText.SystemTrust.text(language), language)
            Text(if (ru) "Сверьте отпечатки по отдельному доверенному каналу. Затем приложение прочитает identity сервера для отдельного подтверждения."
                else "Compare the fingerprints through a separate trusted channel. The app then reads the server identity for separate confirmation.")
            var api by remember { mutableStateOf(false) }
            var stream by remember { mutableStateOf(false) }
            AccountCheck(api, DesktopAccountText.VerifyApi.text(language)) { api = it }
            AccountCheck(stream, DesktopAccountText.VerifyStream.text(language)) { stream = it }
            AccountButton(owner, DesktopAccountAction.ConfirmPinnedTransport, language, review.id,
                enabled = api && stream, command = { DesktopAccountCommand.ConfirmPinnedTransport(review.id, api, stream) })
        }
    } }
}

@Composable
private fun PinnedTransportForm(owner: DesktopAccountUiOwner, language: DesktopLanguage): Unit {
    val ru = language == DesktopLanguage.Russian
    var api by remember { mutableStateOf("") }
    var stream by remember { mutableStateOf("") }
    var apiSpki by remember { mutableStateOf("") }
    var streamSpki by remember { mutableStateOf("") }
    DisposableEffect(Unit) { onDispose { api = ""; stream = ""; apiSpki = ""; streamSpki = "" } }
    val enabled = owner.available(DesktopAccountAction.PreparePinnedTransport)
    Column {
        Text(if (ru) "Введите точные HTTPS origins и DER SPKI в стандартном base64. Пустое поле SPKI использует системную проверку сертификата."
            else "Enter the exact HTTPS origins and DER SPKI in standard base64. An empty SPKI field uses system certificate validation.")
        OutlinedTextField(api, { if (it.length <= 2048) api = it }, enabled = enabled, singleLine = true,
            label = { Text(DesktopAccountText.ApiOrigin.text(language)) }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(stream, { if (it.length <= 2048) stream = it }, enabled = enabled, singleLine = true,
            label = { Text(DesktopAccountText.StreamOrigin.text(language)) }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(apiSpki, { if (it.length <= 8192) apiSpki = it }, enabled = enabled, maxLines = 3,
            label = { Text(if (ru) "SPKI сертификата API" else "API certificate SPKI") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(streamSpki, { if (it.length <= 8192) streamSpki = it }, enabled = enabled, maxLines = 3,
            label = { Text(if (ru) "SPKI сертификата потока" else "Stream certificate SPKI") }, modifier = Modifier.fillMaxWidth())
        AccountButton(owner, DesktopAccountAction.PreparePinnedTransport, language,
            enabled = api.isNotEmpty() && stream.isNotEmpty(), command = {
                val command = DesktopAccountCommand.PinnedTransport(DesktopAccountSecret(api), DesktopAccountSecret(stream),
                    DesktopAccountSecret(apiSpki), DesktopAccountSecret(streamSpki))
                api = ""; stream = ""; apiSpki = ""; streamSpki = ""
                command
            })
    }
}
