# Local music acquisition

Переносимый локальный модуль для последовательной загрузки пользовательского TXT-плейлиста.
Он не входит в серверный runtime AutPlay и не меняет продуктовую границу провайдеров.

Порядок контуров фиксирован:

1. **Jamendo** — официальный API и повторная проверка `audiodownload_allowed`.
2. **Hitmo** — первые пять результатов, точное совпадение, локальный Edge по CDP.
3. **yt-dlp** — `ytsearch5`, только точное совпадение и только встроенный YouTube extractor.

Следующий контур открывается только после `exact_match_not_found`. Сетевая ошибка, отказ
провайдера, ошибка прав, CDP, yt-dlp, ffmpeg, проверки файла или публикации останавливает цепочку
для этой строки. Следующая строка плейлиста начинается только после полного завершения предыдущей.

Модуль предназначен только для материалов, на загрузку которых у пользователя есть права. Он не
обходит DRM, CAPTCHA или контроль доступа и не использует cookies, логины, netrc, произвольные URL,
динамические плагины yt-dlp либо удалённые JS-компоненты.

## Установка

Нужны Python 3.12–3.14, `uv 0.12.3`, ffmpeg/ffprobe и Node.js. Сначала перейдите
в каталог переносимого модуля — это также избегает ограничений Windows launcher в Unicode-путях:

```powershell
Push-Location .\tools\local_music_acquisition
uv sync --frozen
```

Зависимости изолированы в каталоге модуля. Версия `yt-dlp[default,pin]` зафиксирована в
`uv.lock`; самообновление во время работы не выполняется.

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

## Запуск всех трёх контуров

```powershell
uv run --frozen local-music-acquire `
  "D:\Playlists\tracks.txt" `
  --output-dir "D:\Music" `
  --jamendo-client-id-file "D:\Secrets\jamendo-client-id.txt" `
  --hitmo-rights-confirmed `
  --yt-dlp-rights-confirmed `
  --normalize-numbered
```

После работы вернитесь в корень репозитория командой `Pop-Location`.

Оба флага подтверждают права на каждый трек очереди до первого сетевого запроса. Любой контур
можно явно убрать флагом `--disable-jamendo`, `--disable-hitmo` или `--disable-yt-dlp`.

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

Hitmo сохраняет privacy-redacted evidence в `final_runs/run_<n>/`; скриншоты создаются только с
`--evidence-screenshots`.

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
