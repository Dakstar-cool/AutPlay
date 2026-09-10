# Local music acquisition

Переносимый локальный модуль для ограниченной загрузки пользовательского TXT-плейлиста.
Он не входит в серверный runtime AutPlay и не меняет продуктовую границу провайдеров.

Порядок контуров фиксирован:

1. **Jamendo** — официальный API и повторная проверка `audiodownload_allowed`.
2. **Hitmo** — первые пять результатов, точное совпадение, локальный Edge по CDP.
3. **yt-dlp** — `ytsearch5`, только точное совпадение и только встроенный YouTube extractor.
4. **Yandex Music** — опциональный контур на закреплённом commit
   `llistochek/yandex-music-downloader`, точное совпадение и OAuth-токен только из файла.

По умолчанию сохранён строгий режим: следующий контур открывается только после
`exact_match_not_found`, а терминальная ошибка останавливает цепочку строки. Для пакетного
восстановления `--continue-on-provider-failure` разрешает переход после терминальной ошибки;
`--provider-failure-threshold 2` размыкает дважды подряд сломавшийся контур. Jamendo делает не
более одного повтора транспортного сбоя в ограниченном временном окне. `--workers 1..4`
обрабатывает разные строки параллельно, но каждый провайдер остаётся последовательной полосой:
два контура никогда не соревнуются за один трек.

Модуль предназначен только для материалов, на загрузку которых у пользователя есть права. Он не
обходит CAPTCHA и не использует cookies, логины, netrc, произвольные URL, динамические плагины
yt-dlp либо удалённые JS-компоненты. Yandex-контур работает только с явным OAuth-токеном аккаунта
и доступностью трека, которую сообщает API; ответственность за соблюдение условий сервиса остаётся
у пользователя.

## Установка

Нужны Python 3.12–3.14, `uv 0.12.3`, ffmpeg/ffprobe и Node.js. Сначала перейдите
в каталог переносимого модуля — это также избегает ограничений Windows launcher в Unicode-путях:

```powershell
Push-Location .\tools\local_music_acquisition
uv sync --frozen
```

Зависимости изолированы в каталоге модуля. Версия `yt-dlp[default,pin]` и точный commit
`yandex-music-downloader` зафиксированы в `uv.lock`; самообновление во время работы не выполняется.

Для Hitmo запустите отдельный видимый профиль Edge:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\start_hitmo_cdp_edge.ps1
```

## Формат плейлиста

Поддерживаются UTF-8 и CP1251, не более 500 валидных треков за запуск:

```text
Artist One<TAB>Track One<TAB>Optional Album
Artist Two - Track Two
```

`--normalize-numbered` удаляет префиксы `N.`/`N)` и строки `=== section ===`. Ошибочная строка
изолируется без обращения к провайдерам.

## Запуск четырёх контуров

Yandex выключен, пока не указан файл токена. На Linux создайте его вне репозитория с правами
`0600`; не передавайте токен в командной строке, логах или чате.

```powershell
uv run --frozen local-music-acquire `
  "D:\Playlists\tracks.txt" `
  --output-dir "D:\Music" `
  --jamendo-client-id-file "D:\Secrets\jamendo-client-id.txt" `
  --hitmo-rights-confirmed `
  --yt-dlp-rights-confirmed `
  --yandex-token-file "D:\Secrets\yandex-oauth-token.txt" `
  --yandex-rights-confirmed `
  --continue-on-provider-failure `
  --provider-failure-threshold 2 `
  --workers 4 `
  --normalize-numbered
```

После работы вернитесь в корень репозитория командой `Pop-Location`.

Флаги подтверждают права на каждый трек очереди до первого сетевого запроса. Первые три контура
можно убрать флагами `--disable-jamendo`, `--disable-hitmo` и `--disable-yt-dlp`; Yandex включается
только присутствием `--yandex-token-file`.

JSON-результат содержит номера строк, статусы, имена провайдеров и единообразные сокращённые
SHA-256 отпечатки загруженных байтов, но не абсолютные пути и не названия треков. Сокращённый
отпечаток нужен только для privacy-redacted correlation и не является доказательством целостности
или ключом дедупликации. Код `0` означает полную загрузку, `1` — завершённый прогон с
пропусками/ошибками, `2` — ошибку входных данных или конфигурации.

## Отдельный Hitmo-контур

Старая команда сохранена как совместимый launcher:

```powershell
uv run python .\final_script.py `
  --artist "Рубеж веков" --title "О боли" `
  --browser cdp --download-dir "D:\Music" `
  --download --rights-confirmed
```

Hitmo сохраняет privacy-redacted evidence во внешнем writable-каталоге
`$AUTPLAY_HITMO_EVIDENCE_ROOT/run_<n>` (по умолчанию — системный temp); скриншоты создаются только
с `--evidence-screenshots`. Это позволяет запускать пакет в read-only контейнере.

## Перенос

Скопируйте целиком `tools/local_music_acquisition` в другой каталог. Для работы не нужны
`server/src`, Android-проект или корневой Python-пакет AutPlay:

```powershell
Set-Location D:\Portable\local_music_acquisition
uv sync --frozen
uv run local-music-acquire --help
uv run python -m pytest -q
```

Корневые `scripts/jamendo_download.py` и `scripts/txt_track_import.py` — только совместимые
обёртки; канонические реализации находятся в `src/local_music_acquisition`.
