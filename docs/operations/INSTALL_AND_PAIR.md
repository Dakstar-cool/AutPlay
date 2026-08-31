# Установка AutPlay v0.3.0 и подключение личного сервера

Это руководство относится к development pre-release `v0.3.0`. Android полностью работает без
сервера. Поставляемый установщик сервера предназначен для одного оператора, CPU `linux/amd64` и
доверенной домашней сети. Он не настраивает публичный домен, TLS, резервное копирование или
production-хранилище и не должен быть доступен из Интернета.

## 1. Что скачать

На странице GitHub Release нужны:

- `autplay-0.3.0-dev-signed.apk` — hardened APK для локального режима или сервера с отдельно
  настроенным HTTPS;
- `autplay-0.3.0-trusted-lan.apk` — отдельный debuggable APK для связи с поставляемым HTTP-сервером
  в доверенной RFC1918-сети;
- `autplay-server-v0.3.0-installer.zip` — CPU image, Compose-конфигурация и установщики Windows/Linux;
- `SHA256SUMS` — контрольные суммы всех опубликованных файлов.

Оба APK используют сохранённый development signer, а не production/app-store key. Не публикуйте
их в магазине приложений. `trusted-lan` намеренно debuggable и разрешает HTTP только для локальной
отладки. На чужом Wi-Fi, в гостевой сети или через проброшенные наружу порты его использовать
нельзя.

Проверьте SHA-256 до установки:

```powershell
Get-FileHash .\autplay-0.3.0-trusted-lan.apk -Algorithm SHA256
Get-FileHash .\autplay-server-v0.3.0-installer.zip -Algorithm SHA256
```

```bash
sha256sum autplay-0.3.0-trusted-lan.apk autplay-server-v0.3.0-installer.zip
```

Сравните значения с `SHA256SUMS`. При несовпадении ничего не запускайте.

## 2. Android без сервера

1. На телефоне разрешите установку неизвестных приложений только для браузера или файлового
   менеджера, которым открываете APK.
2. Откройте `autplay-0.3.0-dev-signed.apk` и подтвердите установку.
3. Запустите AutPlay. Для локальной музыки учётная запись и сервер не нужны.
4. Откройте медиатеку/импорт, выберите каталог через системный Android file picker и разрешите
   доступ только к нужной папке.
5. После установки выключите разрешение «Установка неизвестных приложений» у браузера или
   файлового менеджера.

Для связи с сервером из раздела 3 установите `autplay-0.3.0-trusted-lan.apk`. Он имеет отдельные
application id `app.autplay.lan` и имя `AutPlay LAN`, поэтому может стоять рядом с hardened AutPlay
и не получает доступ к его локальной базе. Данные между двумя вариантами автоматически не
переносятся. При сообщении о несовместимой подписи не удаляйте существующее приложение, если
локальные данные важны: удаление пакета сотрёт его локальную базу и настройки.

## 3. Подготовка компьютера-сервера

Требуется Docker Engine с Linux containers и Docker Compose `2.24.4+`. Архив сервера собран для
`linux/amd64`; ARM64 в этом выпуске не поставляется. На Windows запустите Docker Desktop в режиме
Linux containers. Убедитесь, что команды выполняются без ошибки:

Installer содержит образ AutPlay, но не дублирует digest-pinned образ PostgreSQL/pgvector. При
первом запуске Docker потребуется доступ к registry для его загрузки, если этот образ ещё не
находится в локальном cache. После загрузки дальнейший trusted-LAN запуск не требует Internet.

```text
docker version
docker compose version
```

Телефон и сервер должны находиться в одной доверенной домашней сети без client isolation. Найдите
конкретный RFC1918 IPv4 компьютера, например `192.168.1.25`:

```powershell
Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
    $_.IPAddress -like '10.*' -or $_.IPAddress -like '172.*' -or $_.IPAddress -like '192.168.*'
} | Select-Object InterfaceAlias,IPAddress
```

```bash
ip -4 address
```

Не используйте `0.0.0.0`, публичный IP, VPN-адрес или адрес чужой сети.

## 4. Установка сервера на Windows

1. Распакуйте `autplay-server-v0.3.0-installer.zip` в постоянную папку.
2. Откройте обычный PowerShell в этой папке. Администраторские права установщику не нужны.
3. Подставьте найденный IP:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install-server.ps1 `
  -BindHost 192.168.1.25
```

Установщик проверяет SHA-256 вложенного Docker image, загружает его, один раз создаёт секреты и
постоянный P-256 identity key в `%LOCALAPPDATA%\AutPlayServer`, проверяет Compose и ждёт healthy
состояния. Повторный запуск сохраняет существующие секреты и identity.

Разрешите входящие TCP `18787` и `18788` только для профиля Private и `LocalSubnet`. Выполните в
PowerShell от администратора:

```powershell
New-NetFirewallRule -DisplayName "AutPlay trusted LAN" -Direction Inbound -Action Allow `
  -Protocol TCP -LocalPort 18787,18788 -RemoteAddress LocalSubnet -Profile Private
```

Не открывайте `8787`: административный Web жёстко привязан к `127.0.0.1` и доступен только на
компьютере-сервере.

## 5. Установка сервера на Linux

Распакуйте ZIP в постоянную папку и выполните:

```bash
chmod +x install-server.sh server-control.sh
./install-server.sh --bind-host 192.168.1.25
```

Секреты и identity по умолчанию сохраняются в
`${XDG_STATE_HOME:-$HOME/.local/state}/autplay-server` с закрытыми правами. В firewall разрешите
TCP `18787` и `18788` только с локальной подсети и только на нужном LAN-интерфейсе. Конкретная
команда зависит от используемого firewall; установщик намеренно не меняет системные правила сам.

## 6. Получение доверенного fingerprint сервера

До первого сетевого подключения получите fingerprint непосредственно из сохранённого identity key
на серверном компьютере. Это независимый, контролируемый владельцем канал доверия; значение из
непроверенного network discovery само по себе не является доказательством.

```powershell
.\server-control.ps1 -Action fingerprint
```

```bash
./server-control.sh fingerprint
```

Запишите 64 строчные hex-цифры и сверяйте их с Android целиком. Не пересылайте fingerprint через
тот же непроверенный серверный endpoint, который собираетесь подтвердить.

## 7. Создание первого владельца

Эта команда выполняется ровно один раз на пустой базе. Она печатает чувствительный JSON с новым
сеансом; не перенаправляйте его в файл, не снимайте экран и не отправляйте в мессенджер.

Windows:

```powershell
.\server-control.ps1 -Action bootstrap-owner -DisplayName "Ваше имя"
```

Linux:

```bash
./server-control.sh bootstrap-owner "Ваше имя"
```

Сохраните `user_id` владельца в защищённом менеджере секретов. Повторный bootstrap намеренно
завершится ошибкой, если аккаунт уже существует.

## 8. Вход в локальный Web Admin

Создайте одноразовое приглашение для браузера. Оно действует пять минут и выводится только в
интерактивный терминал:

```powershell
.\server-control.ps1 -Action invite-browser -UserId <owner UUID>
```

```bash
./server-control.sh invite-browser <owner UUID>
```

На том же компьютере откройте `http://127.0.0.1:8787/admin/login`, вставьте invitation bearer в
маскированное поле и войдите. Cookie административного сеанса доступна только loopback, имеет
30-минутный idle timeout и 12-часовой абсолютный срок. Постоянного пароля или публичной формы
регистрации в этом выпуске нет.

## 9. Связывание Android и сервера

1. Установите `autplay-0.3.0-trusted-lan.apk`; включите Wi-Fi той же доверенной сети.
2. В AutPlay откройте `Профиль` → `Личный сервер`.
3. Введите `http://192.168.1.25:18787`, нажмите проверку сервера и внимательно проверьте
   показанные имя и origin. Сравните весь identity fingerprint с локальным значением из раздела 6;
   значение из discovery не подтверждайте само по себе.
4. Нажмите запрос одобрения. Телефон покажет review locator и 12-значный код сравнения.
5. На компьютере в Web Admin откройте `Connection requests`, вставьте locator и перейдите к
   проверке.
6. Сравните код в браузере с кодом на телефоне. При любом несовпадении отмените запрос.
7. В браузере выберите одноразовое одобрение или доверие этому exact device key. Не одобряйте
   неизвестное устройство.
8. На телефоне подтвердите сравнение, проверьте статус, подтвердите аккаунт и выберите, оставить ли
   локальные изменения только на телефоне или просмотреть их перед подключением.
9. Дождитесь состояния `Подключено`. Локальная медиатека и playback продолжают работать даже при
   недоступном сервере.

Если адрес сервера или identity key изменился, AutPlay завершает проверку fail-closed. Не обходите
это предупреждение: восстановите прежний state/identity либо явно удалите старое доверие и
проведите церемонию заново.

## 10. Управление сервером

Windows:

```powershell
.\server-control.ps1 -Action status
.\server-control.ps1 -Action logs
.\server-control.ps1 -Action stop
.\server-control.ps1 -Action start
```

Linux:

```bash
./server-control.sh status
./server-control.sh logs
./server-control.sh stop
./server-control.sh start
```

Обычный `stop` сохраняет PostgreSQL и Vault named volumes. Никогда не добавляйте `--volumes`, если
данные нужны: эта опция удалит их. Текущий development installer не является системой резервного
копирования. До использования с незаменимой медиатекой нужен отдельный согласованный off-host
backup/restore процесс.

## 11. Быстрая диагностика

- `Docker Engine is unavailable`: запустите Docker Desktop/Engine и повторите установщик.
- Телефон не видит сервер: проверьте одинаковую Wi-Fi сеть, отсутствие guest/client isolation,
  правильный RFC1918 IP и firewall на портах `18787/18788`.
- Hardened APK отклоняет `http://`: это ожидаемо; для поставляемой trusted-LAN topology нужен APK
  с суффиксом `trusted-lan`, а для hardened APK — отдельно настроенный HTTPS.
- Web Admin не открывается с телефона: это ожидаемо и является защитой; открывайте его только как
  `http://127.0.0.1:8787/admin` на серверном компьютере.
- Pairing показывает другой fingerprint или код: ничего не подтверждайте; проверьте адрес и
  сохранность `%LOCALAPPDATA%\AutPlayServer` либо Linux state directory.
- Контейнер unhealthy: выполните `server-control ... logs`; в отчёт об ошибке не включайте файлы
  из `secrets`, invitation bearer, полный owner bootstrap JSON или личные пути.

Production-публикация требует отдельно выбранных domain/TLS topology, secret delivery, backup,
signing key и rollout policy. Этот релиз их не подменяет.
