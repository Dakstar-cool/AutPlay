package app.autplay.ui.desktop

internal enum class DesktopAccountText(val ru: String, val en: String) {
    Workspace("Аккаунт и ваша музыка", "Account and your music"),
    Portable("Переносимый аккаунт", "Portable account"),
    Login("Вход по ID и паролю", "Log in with ID and password"),
    LoginHelp("Введите ID и пароль аккаунта, сохранённого на выбранном сервере. Файл восстановления для входа не нужен.", "Enter the ID and password of an account saved on the selected server. No recovery file is required to log in."),
    RecoveryUnavailable("На этом Mac нет файла восстановления: аккаунт загружен с сервера по паролю. Сохранённый ранее файл продолжает действовать.", "This Mac has no recovery file: the account was downloaded using your password. Your previously saved recovery file remains valid."),
    Connection("Личный сервер", "Personal server"),
    Self("Другое ваше устройство", "Another device of yours"),
    Devices("Устройства и сеансы", "Devices and sessions"),
    Privacy("Приватность", "Privacy"),
    Catalog("Библиотека аккаунта", "Account library"),
    Downloads("Загрузки", "Downloads"),
    Statistics("Статистика", "Statistics"),
    ImportMatching("Сопоставление импорта", "Import matching"),
    ServerRecovery("Восстановление доступа к серверу", "Server access recovery"),
    ServerRecoveryHelp("Код восстановления сервера восстанавливает доступ к этому серверу. Файл переносимого аккаунта используется отдельно.", "A server recovery code restores access to this server. The portable account recovery file is a separate flow."),
    PortableHelp("Уже есть аккаунт? Выберите личный сервер и войдите по ID и паролю. Здесь можно создать новый аккаунт или открыть сохранённый на этом Mac.", "Already have an account? Select your personal server and log in with ID and password. Here you can create an account or unlock one saved on this Mac."),
    Password("Пароль", "Password"),
    NewPassword("Новый пароль", "New password"),
    RepeatPassword("Повторите пароль", "Repeat password"),
    RecoveryCode("Код восстановления", "Recovery code"),
    PublicId("Публичный ID", "Public ID"),
    Account("Аккаунт", "Account"),
    ConfirmAccount("Я подтверждаю показанный аккаунт", "I confirm the displayed account"),
    ServerAccountId("ID аккаунта на выбранном сервере", "Account ID on the selected server"),
    RootVersion("Версия ключа личности", "Identity key version"),
    RecoveryFileHelp("Файл восстановления содержит секретные данные. Выберите отдельное место сохранения; существующий файл не перезаписывается. Сохранение подтверждается после повторного чтения.", "The recovery file contains secret material. Choose a separate save destination; existing files are not overwritten. Saving is confirmed only after readback."),
    FileChoosing("Выберите файл в системном окне.", "Choose a file in the system dialog."),
    FileReadback("Файл ещё не подтверждён: требуется повторное чтение.", "The file is not confirmed yet: readback is required."),
    FileVerified("Файл сохранён и повторно прочитан.", "The file was saved and read back."),
    FileExists("Файл уже существует. Выберите другое имя или место.", "The file already exists. Choose another name or destination."),
    FileFailed("Не удалось подтвердить файл. Выберите файл повторно.", "The file could not be confirmed. Choose the file again."),
    RotationWarning("Смена ключа личности требует явного подтверждения. Убедитесь, что сохранён актуальный файл восстановления.", "Changing the identity key requires explicit confirmation. Make sure you have the current recovery file."),
    Origin("Адрес личного сервера", "Personal server address"),
    ConnectionHelp("Локальная музыка остаётся доступна без сервера. Обнаружение адреса ещё не подключает аккаунт.", "Local music remains available without a server. Discovering an address does not connect an account."),
    QrInput("Код QR2 или вставленные данные", "QR2 code or pasted data"),
    QrHelp("Вставьте код/JSON или выберите PNG/JPEG с QR (до 4 МиБ, 4 Мп и 4096 пикселей на сторону). Текстовый файл: до 16 КиБ. Для QR2 и приглашения сначала отдельно подтвердите сервер; код не выбирает сервер. Камера не используется.", "Enter code/JSON or choose a PNG/JPEG QR image (up to 4 MiB, 4 MP and 4096 pixels per side). Text file: up to 16 KiB. Confirm the server separately before using QR2 or an invitation; the code does not select a server. The camera is not used."),
    Invitation("Приглашение для устройства", "Device invitation"),
    TrustTitle("Проверьте сервер перед подключением", "Review the server before connecting"),
    ApiOrigin("Точный адрес API", "Exact API origin"),
    StreamOrigin("Точный адрес медиасервера", "Exact streaming origin"),
    ServerIdentity("Идентичность сервера", "Server identity"),
    ApiFingerprint("Отпечаток TLS API", "API TLS fingerprint"),
    StreamFingerprint("Отпечаток TLS медиасервера", "Streaming TLS fingerprint"),
    SystemTrust("Системная проверка сертификата", "System certificate verification"),
    VerifyApi("Я проверил адрес API и его TLS", "I checked the API origin and its TLS"),
    VerifyStream("Я проверил адрес медиасервера и его TLS", "I checked the streaming origin and its TLS"),
    VerifyIdentity("Я проверил идентичность сервера", "I checked the server identity"),
    LocalReview("Локальные данные перед подключением", "Local data before connecting"),
    LocalReviewHelp("Выберите только те изменения, которые хотите связать с показанным аккаунтом. Остальные остаются локальными. Названия не меняют владельца данных.", "Select only the changes to connect to the displayed account. Other data stays local. Labels do not change data ownership."),
    MoreLocal("Показана одна страница. Для остальных данных откройте следующую страницу проверки через владельца подключения.", "This is one page. Request the next review page from the connection owner for remaining data."),
    Selected("Выбрано", "Selected"),
    Clear("Снять выбор", "Clear selection"),
    SelfHelp("Сравните код на обоих устройствах. Подтверждайте только ваше устройство; ожидание и отмена не означают успешное подключение.", "Compare the code on both devices. Approve only your device; waiting or cancellation does not mean a successful connection."),
    Source("Исходное устройство", "Source device"),
    Recipient("Принимающее устройство", "Recipient device"),
    Sas("Код сравнения", "Comparison code"),
    SasConfirmed("Коды на обоих устройствах совпадают", "The codes on both devices match"),
    Expires("Действует до", "Expires"),
    SourceApproved("Исходное устройство подтвердило запрос", "The source device approved the request"),
    SourceWaiting("Ожидается подтверждение исходного устройства", "Waiting for source device approval"),
    Current("Это устройство / текущий сеанс", "This device / current session"),
    LastSeen("Последняя активность", "Last activity"),
    DeviceList("Устройства", "Devices"),
    SessionList("Сеансы", "Sessions"),
    InvitationList("Приглашения", "Invitations"),
    EmptyList("Нет записей в подтверждённом списке.", "No entries in the confirmed list."),
    DisconnectHelp("Отключение на этом устройстве сохраняет локальную музыку и ожидающие изменения. Удалённый выход подтверждается отдельно.", "Disconnecting on this device retains local music and pending changes. Remote logout is confirmed separately."),
    Training("Участие в общем обучении", "Shared training participation"),
    TrainingHelp("Участие выключено без явного согласия. Отзыв останавливает новое использование; уже завершённые веса моделей остаются до обычной замены. Личная статистика и рекомендации управляются отдельно.", "Participation stays off without explicit consent. Withdrawal stops new use; completed model weights remain until normal replacement. Personal statistics and recommendations are separate."),
    TrainingUnknown("Решение ещё не принято; участие выключено", "No decision yet; participation is off"),
    TrainingGranted("Согласие подтверждено сервером", "Consent confirmed by the server"),
    TrainingDenied("Отказ подтверждён сервером", "Refusal confirmed by the server"),
    TrainingWithdrawn("Отзыв подтверждён сервером", "Withdrawal confirmed by the server"),
    Allow("Разрешить", "Allow"),
    Refuse("Отказать", "Refuse"),
    Withdraw("Отозвать согласие", "Withdraw consent"),
    Visibility("Видимость статистики", "Statistics visibility"),
    Visible("Статистика видна другим", "Statistics is visible to others"),
    Hidden("Статистика скрыта от других", "Statistics is hidden from others"),
    ShowStatistics("Показывать статистику", "Show statistics"),
    HideStatistics("Скрыть статистику", "Hide statistics"),
    Unconfirmed("Подтверждённых данных пока нет", "No confirmed data yet"),
    DeleteTitle("Удаление аккаунта на сервере", "Delete the server account"),
    DeleteWarning("Запрос приостанавливает доступ и запускает удаление указанного аккаунта. Проверьте аккаунт, срок отмены и доступ к восстановлению. Скрытие экрана не отменяет запрос и не доказывает удаление файлов.", "A request suspends access and starts deletion of the displayed account. Check the account, cancellation deadline and recovery access. Closing this screen does not cancel the request or prove file erasure."),
    DeleteConfirm("Я проверил аккаунт и последствия удаления", "I checked the account and deletion consequences"),
    DeleteDeadline("Отменить можно до", "Cancellation deadline"),
    DeletionPending("Удаление запрошено", "Deletion requested"),
    DeletionSuspended("Доступ приостановлен", "Access suspended"),
    DeletionCancelling("Отмена ожидает подтверждения", "Cancellation awaits confirmation"),
    DeletionCancelled("Отмена подтверждена сервером", "Cancellation confirmed by the server"),
    DeletionPurging("Удаление выполняется, отмена недоступна", "Deletion is in progress; cancellation is unavailable"),
    DeletionPurged("Сервер подтвердил завершение удаления", "The server confirmed deletion completion"),
    RecoveryWarning("Восстановление может отозвать существующие устройства и сеансы. Подтвердите только показанный предварительный результат.", "Recovery may revoke existing devices and sessions. Confirm only the displayed preview."),
    RecoveryConfirm("Я проверил аккаунт и последствия восстановления", "I checked the account and recovery consequences"),
    RevokedDevices("Устройства для отзыва", "Devices to revoke"),
    RevokedSessions("Сеансы для отзыва", "Sessions to revoke"),
    ConfirmTitle("Подтвердите действие", "Confirm action"),
    Confirm("Подтвердить", "Confirm"),
    Cancel("Отмена", "Cancel"),
    LogoutWarning("Выход и отзыв доступа действуют на указанный сеанс или устройство. Локальные данные сохраняются; результат появится после подтверждения владельцем операции.", "Logout and revocation affect the displayed session or device. Local data is retained; the result appears after the operation owner confirms it."),
    CanonicalArtists("Исполнители каталога", "Catalog artists"),
    MetadataAlbum("Альбом по метаданным; идентичность релиза не установлена", "Metadata album; release identity is not established"),
    Unresolved("Идентичность записи ещё не установлена", "Recording identity is unresolved"),
    ProofMissing("Подтверждение владения ещё не получено", "Owner proof has not been confirmed"),
    PendingChanges("Есть неподтверждённые изменения", "Changes await confirmation"),
    Previous("Предыдущая страница", "Previous page"),
    Next("Следующая страница", "Next page"),
    Detail("Сведения", "Details"),
    Untitled("Без названия", "Untitled"),
    Cache("Кэш: может быть освобождён", "Cache: may be reclaimed"),
    Pinned("Закреплено для офлайн-доступа", "Pinned for offline access"),
    UserDownload("Ваша загрузка", "Your download"),
    UnknownStorage("Тип хранения пока не подтверждён", "Storage class is not confirmed yet"),
    RetainedUnavailable("Копия сохранена; воспроизведение требует доступного текущего владельца и проверки копии.", "The copy is retained; playback requires the current owner to be available and the copy to pass verification."),
    Metered("Разрешить загрузки в сети с оплатой трафика", "Allow downloads on metered networks"),
    MeteredUnknown("Настройка сети ещё не прочитана", "Network preference has not been read yet"),
    DownloadHelp("Прогресс показывает подтверждённые байты. Готовность офлайн появляется только после проверки копии. Отмена может ожидать завершения записи.", "Progress shows confirmed bytes. Offline availability appears only after copy verification. Cancellation may wait for writing to settle."),
    MoreDownloads("Есть другие загрузки; обновите или загрузите следующую страницу.", "More downloads exist; refresh or load the next page."),
    AccountStatistics("Статистика аккаунта", "Account statistics"),
    PortableStatistics("Переносимая статистика", "Portable statistics"),
    StatisticsHelp("Это отдельные представления истории. Итоги разных серверов не складываются; алиасы учитываются владельцем данных.", "These are separate history views. Totals from different servers are not added; aliases are reconciled by the data owner."),
    Listens("Прослушивания", "Listens"),
    PlayedTime("Время прослушивания", "Listening time"),
    Through("Данные по", "Data through"),
    Partial("Данные неполные", "Data is incomplete"),
    PendingOperations("Текущие операции", "Current operations"),
    NoPermission("Действие сейчас недоступно. Обновите состояние или устраните указанную причину.", "This action is currently unavailable. Refresh the state or resolve the displayed issue."),
    BasicStatus("Состояние подключения", "Connection status"),
    BasicStatusHelp("Здесь показано только безопасное краткое состояние. Секреты, адреса и технические журналы сюда не включаются.", "Only a safe status summary appears here. Secrets, origins and technical journals are excluded."),
    RevealSecret("Показать одноразовый код", "Reveal one-time code"),
    HideSecret("Скрыть и очистить код", "Hide and clear code"),
    SecretOutputHelp("Код даёт доступ. Показывайте его только вашему устройству или адресату. Он не копируется в буфер автоматически.", "This code grants access. Show it only to your device or intended recipient. It is not copied to the clipboard automatically."),
}

internal fun DesktopAccountText.text(language: DesktopLanguage): String = if (language == DesktopLanguage.Russian) ru else en
internal fun desktopAccountActionText(action: DesktopAccountAction, language: DesktopLanguage): String {
    val pair = when (action) {
        DesktopAccountAction.Refresh -> "Обновить состояние" to "Refresh state"
        DesktopAccountAction.CreatePortable -> "Создать переносимый аккаунт" to "Create portable account"
        DesktopAccountAction.UnlockPortable -> "Открыть аккаунт" to "Unlock account"
        DesktopAccountAction.LockPortable -> "Заблокировать" to "Lock"
        DesktopAccountAction.ImportRecoveryFile -> "Выбрать файл восстановления" to "Choose recovery file"
        DesktopAccountAction.ExportRecoveryFile -> "Сохранить файл восстановления" to "Save recovery file"
        DesktopAccountAction.RestorePortable -> "Восстановить переносимый аккаунт" to "Restore portable account"
        DesktopAccountAction.PrepareRotation -> "Подготовить смену ключа личности" to "Prepare identity key rotation"
        DesktopAccountAction.RotateRoot -> "Подтвердить смену ключа" to "Confirm key rotation"
        DesktopAccountAction.RegisterPortable -> "Зарегистрировать этот аккаунт на сервере" to "Register this account on the server"
        DesktopAccountAction.LoginPortable -> "Войти в существующий аккаунт сервера" to "Log in to the existing server account"
        DesktopAccountAction.Reauthenticate -> "Подтвердить личность заново" to "Reauthenticate"
        DesktopAccountAction.Discover -> "Найти сервер" to "Discover server"
        DesktopAccountAction.ChooseQrFile -> "Выбрать файл или QR-изображение" to "Choose file or QR image"
        DesktopAccountAction.SubmitQr -> "Прочитать код" to "Read code"
        DesktopAccountAction.ConfirmTransport -> "Подтвердить этот сервер" to "Confirm this server"
        DesktopAccountAction.PreparePinnedTransport -> "Проверить TLS-политику" to "Review TLS policy"
        DesktopAccountAction.ConfirmPinnedTransport -> "Подтвердить TLS и прочитать discovery" to "Confirm TLS and read discovery"
        DesktopAccountAction.SubmitInvitation -> "Проверить приглашение" to "Review invitation"
        DesktopAccountAction.ConfirmInvitation -> "Подключиться к показанному аккаунту" to "Connect to displayed account"
        DesktopAccountAction.RequestAdmission -> "Запросить доступ" to "Request admission"
        DesktopAccountAction.ResumeAdmission -> "Продолжить запрос доступа" to "Resume admission"
        DesktopAccountAction.ConfirmAdmission -> "Подключиться к показанному аккаунту" to "Connect to displayed account"
        DesktopAccountAction.EnrollTrusted -> "Подключить доверенное устройство заново" to "Reenroll trusted device"
        DesktopAccountAction.ConfirmTrusted -> "Подтвердить повторное подключение" to "Confirm reenrollment"
        DesktopAccountAction.Bind -> "Подключить этот аккаунт" to "Bind this account"
        DesktopAccountAction.KeepLocal -> "Оставить только на этом устройстве" to "Keep only on this device"
        DesktopAccountAction.ReviewLocal -> "Проверить локальные изменения" to "Review local changes"
        DesktopAccountAction.ConnectSelected -> "Связать выбранные изменения" to "Connect selected changes"
        DesktopAccountAction.CancelConnection -> "Отменить подключение" to "Cancel connection"
        DesktopAccountAction.StartSelf -> "Подключить другое своё устройство" to "Connect another device of yours"
        DesktopAccountAction.ClaimSelf -> "Продолжить на принимающем устройстве" to "Continue on recipient device"
        DesktopAccountAction.ApproveSelf -> "Подтвердить своё устройство" to "Approve your device"
        DesktopAccountAction.ExchangeSelf -> "Завершить подключение к этому аккаунту" to "Complete connection to this account"
        DesktopAccountAction.CancelSelf -> "Отменить перенос" to "Cancel transfer"
        DesktopAccountAction.ResumeSelf -> "Продолжить перенос" to "Resume transfer"
        DesktopAccountAction.ChooseServerRecoveryFile -> "Выбрать файл восстановления сервера" to "Choose server recovery file"
        DesktopAccountAction.ConfigureServerRecovery -> "Заменить код восстановления сервера" to "Replace server recovery code"
        DesktopAccountAction.ExportServerRecovery -> "Сохранить файл восстановления сервера" to "Save server recovery file"
        DesktopAccountAction.PreviewRecovery -> "Проверить восстановление" to "Preview recovery"
        DesktopAccountAction.CommitRecovery -> "Подтвердить восстановление" to "Confirm recovery"
        DesktopAccountAction.ResumeRecovery -> "Узнать результат восстановления" to "Resolve recovery outcome"
        DesktopAccountAction.RefreshDevices -> "Обновить устройства и сеансы" to "Refresh devices and sessions"
        DesktopAccountAction.LogoutCurrent -> "Выйти из текущего сеанса" to "Log out current session"
        DesktopAccountAction.LogoutAll -> "Выйти из всех сеансов" to "Log out all sessions"
        DesktopAccountAction.RevokeDevice -> "Отозвать доступ устройства" to "Revoke device access"
        DesktopAccountAction.Disconnect -> "Отключить на этом устройстве" to "Disconnect on this device"
        DesktopAccountAction.CreateInvitation -> "Создать приглашение" to "Create invitation"
        DesktopAccountAction.CancelInvitation -> "Отменить приглашение" to "Cancel invitation"
        DesktopAccountAction.ReadPolicy -> "Прочитать настройки приватности" to "Read privacy settings"
        DesktopAccountAction.DecideTraining -> "Изменить согласие" to "Change consent"
        DesktopAccountAction.DecideVisibility -> "Изменить видимость" to "Change visibility"
        DesktopAccountAction.PreviewDeletion -> "Проверить удаление или отмену" to "Preview deletion or cancellation"
        DesktopAccountAction.RequestDeletion -> "Запросить удаление аккаунта" to "Request account deletion"
        DesktopAccountAction.CancelDeletion -> "Подтвердить отмену удаления" to "Confirm deletion cancellation"
        DesktopAccountAction.ResumeDeletion -> "Узнать состояние удаления" to "Resolve deletion status"
        DesktopAccountAction.OpenArtist -> "Открыть исполнителя" to "Open artist"
        DesktopAccountAction.OpenAlbum -> "Открыть альбом" to "Open album"
        DesktopAccountAction.OpenRecording -> "Открыть запись" to "Open recording"
        DesktopAccountAction.OpenRelease -> "Открыть релиз каталога" to "Open catalog release"
        DesktopAccountAction.OpenTrack -> "Открыть трек" to "Open track"
        DesktopAccountAction.OpenPlaylist -> "Открыть плейлист" to "Open playlist"
        DesktopAccountAction.CatalogPage -> "Загрузить страницу" to "Load page"
        DesktopAccountAction.PlayTrack -> "Воспроизвести" to "Play"
        DesktopAccountAction.QueueTrack -> "Добавить в очередь" to "Add to queue"
        DesktopAccountAction.Download -> "Скачать для офлайн-доступа" to "Download for offline access"
        DesktopAccountAction.CancelDownload -> "Отменить загрузку" to "Cancel download"
        DesktopAccountAction.RetryDownload -> "Повторить загрузку" to "Retry download"
        DesktopAccountAction.PlayOffline -> "Воспроизвести офлайн" to "Play offline"
        DesktopAccountAction.SetMetered -> "Изменить настройку сети" to "Change network preference"
        DesktopAccountAction.RefreshDownloads -> "Обновить загрузки" to "Refresh downloads"
        DesktopAccountAction.DownloadPage -> "Открыть страницу загрузок" to "Open downloads page"
        DesktopAccountAction.RefreshStatistics -> "Обновить статистику" to "Refresh statistics"
        DesktopAccountAction.ImportMatching -> "Проверить сопоставление импорта" to "Review import matching"
        DesktopAccountAction.ResumeOperation -> "Выяснить результат сохранённой операции" to "Resolve retained operation"
        DesktopAccountAction.CancelOperation -> "Отменить операцию" to "Cancel operation"
        DesktopAccountAction.DomainMutation -> "Изменить данные аккаунта" to "Change account data"
    }
    return if (language == DesktopLanguage.Russian) pair.first else pair.second
}

internal fun desktopAccountConditionText(condition: DesktopAccountCondition, language: DesktopLanguage): String {
    val pair = when (condition) {
        DesktopAccountCondition.Ready -> "Подтверждено" to "Confirmed"
        DesktopAccountCondition.Loading -> "Загрузка" to "Loading"
        DesktopAccountCondition.Empty -> "Пока нет данных" to "No data yet"
        DesktopAccountCondition.Offline -> "Нет сети; локальные данные доступны" to "Offline; local data remains available"
        DesktopAccountCondition.Locked -> "Аккаунт заблокирован" to "Account locked"
        DesktopAccountCondition.RecoveryNeeded -> "Требуется восстановление" to "Recovery required"
        DesktopAccountCondition.Unsupported -> "Сервер или устройство не поддерживает это действие" to "The server or device does not support this action"
        DesktopAccountCondition.Attention -> "Требуется ваше внимание" to "Your attention is needed"
        DesktopAccountCondition.Pending -> "Ожидается подтверждение" to "Awaiting confirmation"
        DesktopAccountCondition.Unavailable -> "Сейчас недоступно" to "Currently unavailable"
    }
    return if (language == DesktopLanguage.Russian) pair.first else pair.second
}

internal fun desktopAccountIssueText(issue: DesktopAccountIssue, language: DesktopLanguage): String {
    val pair = when (issue) {
        DesktopAccountIssue.None -> "" to ""
        DesktopAccountIssue.Retry -> "Повторите действие после обновления состояния." to "Refresh the state, then retry."
        DesktopAccountIssue.Reconnect -> "Подключитесь заново; локальные данные сохранены." to "Reconnect; local data is retained."
        DesktopAccountIssue.Unlock -> "Откройте аккаунт для продолжения." to "Unlock the account to continue."
        DesktopAccountIssue.Authentication -> "Не удалось войти. Проверьте ID и пароль аккаунта." to "Could not log in. Check the account ID and password."
        DesktopAccountIssue.Recover -> "Откройте восстановление доступа." to "Open access recovery."
        DesktopAccountIssue.Capacity -> "Недостаточно места или достигнут предел хранения. Новые действия приостановлены." to "Storage is full or a storage limit was reached. New actions are paused."
        DesktopAccountIssue.IdentityChanged -> "Идентичность сервера или сертификат изменились. Проверьте их перед новым подключением." to "Server identity or certificate changed. Review them before reconnecting."
        DesktopAccountIssue.Expired -> "Срок действия истёк. Начните новый запрос." to "This request expired. Start a new request."
        DesktopAccountIssue.Conflict -> "Данные изменились. Обновите и подтвердите новый результат." to "Data changed. Refresh and confirm the new result."
        DesktopAccountIssue.ExportExists -> "Файл существует. Выберите другое место." to "The file exists. Choose another destination."
        DesktopAccountIssue.ExportReadback -> "Сохранение ещё не подтверждено повторным чтением." to "Saving has not been confirmed by readback yet."
        DesktopAccountIssue.Unsupported -> "Требуется поддерживаемое устройство или совместимый сервер." to "A supported device or compatible server is required."
        DesktopAccountIssue.Storage -> "Данные недоступны. Сохранённое состояние не удалено; устраните проблему хранения." to "Data is unavailable. Saved state has not been deleted; resolve the storage problem."
        DesktopAccountIssue.CancelPending -> "Отмена запрошена; ожидается завершение уже начатой операции." to "Cancellation requested; waiting for an operation already in progress to settle."
    }
    return if (language == DesktopLanguage.Russian) pair.first else pair.second
}
