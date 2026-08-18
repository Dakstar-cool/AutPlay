# AutPlay Track Identity and Fingerprint Specification v1

**Статус:** Draft for benchmark and implementation review  
**Версия:** 1.0  
**Основание:** `ТЗ AutPlay Draft 0.3`, `AutPlay System Architecture v1`, `AutPlay ER Model v1`  
**ADR:** ADR-004 - Recording identity is distinct from release position and encoded audio  
**Связанные сущности:** `Recording`, `ReleaseTrack`, `AudioVariant`, `VaultObject`, `UserTrackRef`  
**Persistence amendment:** ADR-015 Accepted, proposal revision `c108c109d8eb1ab71631ea79e831a20e6cc6811bff5264cb6d1bb38f7433ac71`
**Deterministic-byte amendment:** ADR-019 Accepted; P00-D004 Variant A permits only the separate strict technical reuse described below

---

# 1. Назначение

Документ определяет, как AutPlay:

- создает кандидатов на совпадение музыкальных записей;
- отделяет совпадение bytes, audio encoding и Recording;
- объединяет metadata, external IDs, duration и audio fingerprint;
- ранжирует кандидатов и объясняет решение;
- выбирает одно из `AUTO_MATCH`, `REVIEW_REQUIRED`, `NO_MATCH`, `INTEGRITY_CONFLICT`, `DEFERRED_EVIDENCE`;
- защищается от ошибочного объединения studio, live, remix, edit и remaster;
- версионирует fingerprint и matcher;
- проверяет качество matching до включения applied auto-match.

Главный принцип:

> Ложное объединение разных Recording опаснее, чем временно нераспознанный Track.

Поэтому score сам по себе никогда не дает права на необратимый merge. Auto-match требует достаточной доказательной базы, отсутствия блокирующих противоречий и прохождения benchmark gate.

---

# 2. Граница сущностей

| Вопрос | Сущность | Правило идентичности |
| --- | --- | --- |
| Это те же bytes? | `VaultObject` | Равенство SHA-256 |
| Это то же кодирование? | `AudioVariant` | Один validated `VaultObject` и одна техническая интерпретация |
| Это та же слышимая запись? | `Recording` | Совокупность audio и metadata evidence |
| Это позиция на конкретном издании? | `ReleaseTrack` | `Medium + sequence_no` и связь с Recording |
| Это пользовательское намерение без надежного match? | `UserTrackRef` | Сохраняется отдельно до resolution |

Следствия:

1. Одинаковый SHA-256 означает один `VaultObject`, но не заменяет проверку целостности catalog link.
2. FLAC и MP3 одной Recording имеют разные `VaultObject` и обычно разные `AudioVariant`.
3. Одна Recording может иметь несколько `ReleaseTrack` в сингле, альбоме и сборнике.
4. Remix, live, radio edit, instrumental и karaoke по умолчанию являются отдельными Recording.
5. Unresolved export row не создает низкокачественную глобальную Recording.

---

# 3. Термины решения

| Термин | Значение |
| --- | --- |
| Query | Импортируемый Track, локальный файл или внешний объект, который требуется разрешить |
| Candidate | Существующая Recording, потенциально соответствующая Query |
| Evidence | Отдельный проверяемый сигнал совпадения или противоречия |
| Candidate generation | Поиск ограниченного множества возможных Recording |
| Scoring | Вычисление сравнительных feature scores |
| Calibration | Преобразование raw score в оценку вероятности на размеченном наборе |
| Hard conflict | Противоречие, запрещающее auto-match независимо от итогового score |
| Review margin | Разница между первым и вторым кандидатом |
| Resolution | Привязка `UserTrackRef` или импортируемого объекта к Recording |
| Merge | Объединение уже существующих global Recording через change set и redirect |
| Resolver state | Результат evaluator; не import status, UserTrackRef status или manual action |
| Execution mode | `SHADOW` не меняет owner projection; `APPLIED` может менять ее только через проверенную command transaction |
| Decision kind | `EVALUATION` или `REVIEW_ACTION` |
| Review action | `ACCEPT`, `REJECT`, `KEEP_UNRESOLVED` или `CREATE_RECORDING`; никогда не `AUTO_MATCH` |
| Owner projection | Валидированная current-decision ссылка ImportEntry/UserTrackRef; история остается в Identity Catalog |

Resolution и Merge являются разными командами. Привязка одного `UserTrackRef` не должна автоматически запускать глобальное объединение двух Recording.

---

# 4. Нормализация входных данных

Нормализация создает search features, но не изменяет raw/display metadata.

## 4.1. Общий pipeline

1. Unicode normalization: NFC для display, NFKC для search copy.
2. Locale-independent case folding.
3. Нормализация пробелов и типографских вариантов punctuation.
4. Разбор artist join phrases: `feat.`, `ft.`, `with`, `&`, `x` только как candidates, без разрушения исходной строки.
5. Выделение version markers из title/version fields.
6. Создание Latin/Cyrillic transliteration aliases как дополнительных поисковых форм.
7. Сохранение языка и script, если они известны.
8. Никакого удаления значимых маркеров `live`, `remix`, `edit`, `remaster`, `instrumental`, `karaoke`.

## 4.2. Version markers

Минимальный словарь категорий:

```text
LIVE
REMIX
EDIT
RADIO_EDIT
EXTENDED
REMASTER
DEMO
ACOUSTIC
INSTRUMENTAL
KARAOKE
COVER
REHEARSAL
MONO
STEREO
UNKNOWN_VERSION
```

В persistence и API version marker записывается в верхнем регистре, а исходное написание сохраняется в `raw_span`.

Каждый marker содержит:

```text
category
normalized_value
raw_span
source_field
extractor_version
confidence
```

Неизвестная версия не приравнивается к studio. Отсутствие marker означает `unknown`, а не доказательство равенства.

## 4.3. Artist credit

Artist comparison использует:

- ordered primary artists;
- featured artists;
- aliases;
- credited names;
- transliteration aliases;
- join phrases как слабый сигнал.

Remixer и performer не должны автоматически считаться primary artist.

---

# 5. Candidate generation

Candidate generation максимизирует recall, decision stage максимизирует precision.

## 5.1. Источники кандидатов

Кандидаты объединяются из независимых generators:

| Generator | Условие | Максимум кандидатов |
| --- | --- | ---: |
| Exact provider reference | `provider + entity_type + external_id + market` | 5 |
| Verified MBID | Recording MBID | 5 |
| ISRC | Exact normalized ISRC | 20 |
| SHA-256 | Уже известный `VaultObject` | 2 |
| Fingerprint | Versioned fingerprint lookup/similarity | 20 |
| Artist + title exact-normalized | Exact normalized pair | 20 |
| Artist + title + duration | Fuzzy names, bounded duration window | 50 |
| Release context | Album/release/track position evidence | 30 |
| Transliteration/trigram | Cross-script and typo-tolerant search | 50 |

После union выполняется dedup по canonical Recording ID с разрешением redirects.

## 5.2. Ограничение множества

- Максимум до scoring: 100 Recording.
- Каждый generator сохраняет origin и rank.
- Слабый fuzzy generator не вытесняет exact-ID candidate.
- Redirect source нормализуется до active target до scoring.
- `MERGED` и `DEPRECATED` rows не возвращаются как финальный target.

## 5.3. Отсутствие кандидатов

Если кандидатов нет:

- metadata-only Query -> `NO_MATCH` или `NOT_FOUND`;
- Query с audio -> новый provisional Recording создается только через отдельную ingest/resolution command;
- `UserTrackRef` сохраняется независимо от результата.

---

# 6. Evidence model

Каждый feature имеет значение, признак наличия и версию вычислителя.

```json
{
  "feature": "title_similarity",
  "value": 0.97,
  "present": true,
  "extractor_version": "title-sim/1",
  "evidence_refs": ["query:title", "recording:title"]
}
```

Отсутствующий feature не равен нулевому совпадению. При missing data его вес исключается и остальные веса перенормируются внутри разрешенной evidence group.

## 6.1. Positive features

| Feature | Диапазон | Смысл |
| --- | ---: | --- |
| `sha256_exact` | 0/1 | Точные bytes уже принадлежат AudioVariant |
| `provider_target_exact` | 0/1 | ExternalReference уже надежно разрешена в Recording |
| `mbid_exact_verified` | 0/1 | Verified Recording MBID |
| `isrc_exact` | 0/1 | Совпадает ISRC; только candidate/evidence, не global key |
| `fingerprint_similarity` | 0..1 | Сходство в рамках конкретного algorithm/version |
| `fingerprint_coverage` | 0..1 | Доля пригодного overlap audio |
| `title_similarity` | 0..1 | Лучший результат по original/alias/transliteration forms |
| `artist_similarity` | 0..1 | Ordered credit-aware similarity |
| `duration_similarity` | 0..1 | Сходство длительности с учетом uncertainty |
| `version_compatibility` | 0..1 | Совместимость version markers |
| `release_similarity` | 0..1 | Album/release/track context |
| `position_similarity` | 0..1 | Disc/track position как слабое evidence |

## 6.2. Negative features

| Feature | Диапазон | Смысл |
| --- | ---: | --- |
| `version_conflict` | 0/1 | Несовместимые explicit version markers |
| `artist_conflict` | 0..1 | Сильное противоречие artist credit |
| `duration_outlier` | 0..1 | Разница не объясняется tolerance/known edit |
| `fingerprint_conflict` | 0..1 | Достаточный overlap, но audio не совпадает |
| `external_target_conflict` | 0/1 | Exact external reference уже указывает на другую active Recording |
| `release_context_conflict` | 0..1 | Сильное несовпадение при наличии надежного release context |

## 6.3. Duration feature

Для `delta = abs(query_ms - candidate_ms)` применяется плавная функция:

```text
duration_similarity = exp(-delta / tau)
```

Bootstrap `tau = 3000 ms` используется только до benchmark. Для коротких Track дополнительно оценивается относительная разница.

Длительность из container metadata имеет uncertainty. Fingerprint duration и decoded duration считаются более надежными, чем строка export.

---

# 7. Deterministic byte and external-ID paths

## 7.1. SHA-256 exact

Если SHA-256 совпадает:

1. проверить byte size;
2. найти существующий `VaultObject`;
3. проверить его `COMMITTED`/validation status;
4. найти связанный `AudioVariant`;
5. разрешить Recording redirect;
6. переиспользовать existing object/variant;
7. при нескольких конфликтующих Recording остановить auto path и создать integrity incident.

Новый duplicate blob не создается.

## 7.2. External reference exact

Exact provider ID дает direct resolution только если:

- provider namespace и entity type совпадают;
- reference уже имеет canonical Recording target;
- target active или разрешается через redirect;
- нет explicit version/fingerprint conflict;
- observation не помечена stale/revoked policy.

Provider ID из одного market не переносится в другой без declared provider policy.

## 7.3. ISRC

ISRC используется как сильный generator, но не как UNIQUE constraint и не как самостоятельное разрешение.

Причины:

- ошибочные metadata;
- повторно используемые коды;
- разные версии, ошибочно имеющие один код;
- provider mapping mistakes.

Auto-match по ISRC требует как минимум совместимых artist/title/version и отсутствия duration/fingerprint conflict.

---

# 8. Fingerprint strategy

## 8.1. Назначение

Fingerprint отвечает на вопрос о сходстве decoded audio, а не о равенстве файлов или релизов.

V1 baseline:

```text
algorithm = CHROMAPRINT
tool = fpcalc
algorithm_version = pinned build/version
decode_profile = mono PCM, tool-defined canonical processing
```

## 8.2. Versioning

Хранятся:

```text
algorithm
algorithm_version
tool_build_sha256
decoder_name
decoder_version
duration_ms
fingerprint_payload
candidate_representation
created_at
quality_flags
```

Fingerprint разных algorithm/version не сравниваются напрямую без version-specific compatibility adapter.

## 8.3. Когда fingerprint вычисляется

- локальный import на Android: SHOULD, если стоимость допустима;
- server ingest: MUST после decode validation;
- внешняя metadata-only migration: не вычисляется до появления audio;
- повторный ingest: переиспользуется только при совпадении bytes и версии fingerprint pipeline;
- смена algorithm version: новая row, без перезаписи старой.

## 8.4. Ограничения

- Exact fingerprint string не является primary key Recording.
- Разные codec/bitrate могут дать близкие, но не идентичные fingerprints.
- Короткие clips, silence, intros/outros, speed/pitch changes и повреждения снижают надежность.
- Remaster может быть очень близок к исходной записи и поэтому не должен auto-merge только по fingerprint.
- Fingerprint конфликт считается сильным только при достаточном overlap/coverage.

## 8.5. Candidate index

До benchmark AutPlay не фиксирует LSH/hash-prefix как окончательный индекс.

Baseline evaluation сравнивает:

1. exact AcoustID/known fingerprint lookup, если разрешено;
2. application-side shortlist по versioned candidate representation;
3. metadata candidates с последующей fingerprint verification;
4. опциональный LSH/prefix index на реальной коллекции.

Выбранный индекс должен измеряться по recall, latency, storage и false candidate rate.

---

# 9. Hard conflicts

При любом hard conflict `AUTO_MATCH` запрещен.

## 9.1. Обязательные blockers

1. `LIVE` против studio/unknown при явном live evidence и отсутствии подтвержденного external target.
2. `REMIX` с различным remixer/version text.
3. `INSTRUMENTAL` против vocal версии.
4. `KARAOKE`/cover против original performer.
5. `RADIO_EDIT`/`EDIT` против full version при существенной разнице длительности.
6. ExternalReference уже надежно указывает на другую active Recording.
7. Сильный fingerprint mismatch при достаточном coverage.
8. Один SHA-256 оказался связан с несколькими несовместимыми Recording.
9. Кандидат находится в незавершенном merge/split change set.
10. Top-2 candidates не разделены минимальным review margin. Это отдельный auto-match gate, а не feature conflict.

## 9.2. Review-only conflicts

По умолчанию manual review требуют:

- remaster против original;
- mono против stereo;
- explicit metadata отличается;
- artist alias не подтвержден;
- ISRC совпал, но duration/version расходятся;
- fingerprint похож, но coverage низкий;
- title/artist совпадают, но несколько Recording одинаково правдоподобны.

---

# 10. Raw score v1

Raw score используется для ранжирования и построения calibration model.

## 10.1. Evidence groups

```text
I = identifier evidence
A = audio evidence
M = metadata evidence
R = release evidence
P = contradiction penalty
```

Bootstrap formula:

```text
I = max(
  1.00 * provider_target_exact,
  0.98 * mbid_exact_verified,
  0.78 * isrc_exact
)

A = 0.82 * fingerprint_similarity
  + 0.18 * fingerprint_coverage

M = 0.46 * title_similarity
  + 0.34 * artist_similarity
  + 0.12 * duration_similarity
  + 0.08 * version_compatibility

R = 0.70 * release_similarity
  + 0.30 * position_similarity

P = 0.45 * artist_conflict
  + 0.50 * duration_outlier
  + 0.80 * fingerprint_conflict
  + 1.00 * external_target_conflict
  + 0.70 * version_conflict

raw_score = clamp(
  0.24 * I +
  0.36 * A +
  0.34 * M +
  0.06 * R - P,
  0,
  1
)
```

Missing groups не дают искусственный ноль. Для metadata-only и audio-available режимов выпускаются разные calibrated models/threshold sets.

Числа являются стартовой гипотезой для benchmark, а не production truth.

## 10.2. Calibration

После расчета raw features применяется versioned calibrator:

```text
confidence = calibrator(raw features, evidence-presence mask)
```

Сравниваются:

- logistic regression;
- isotonic regression;
- gradient boosting только если дает измеримое улучшение и сохраняет explainability.

Модель не использует поля, которых нет в stored decision evidence.

## 10.3. Top-two margin

```text
margin = confidence(top1) - confidence(top2)
```

Auto-match требует одновременно:

- `top1 >= auto_threshold`;
- `margin >= margin_threshold`;
- отсутствие hard conflict;
- достаточный evidence tier;
- latest append-only activation event for the exact `(evidence_mode, evidence_tier)` scope points to the immutable matcher/calibrator/threshold set and is not a deactivation.

---

# 11. Evidence tiers

| Tier | Минимальное evidence | Разрешенное действие |
| --- | --- | --- |
| T0 | Только fuzzy metadata | Rank/review, auto запрещен |
| T1 | Strong title + artist + duration/version | Auto только после отдельного high-precision benchmark |
| T2 | T1 + verified provider/MBID/ISRC context | Auto при отсутствии conflict и достаточном margin |
| T3 | Good fingerprint coverage + compatible metadata | Auto при calibrated gate |
| T4 | Exact server-verified SHA/size linked to one valid AudioVariant/Recording | ADR-019 permits strict technical Vault re-reference outside probabilistic `AUTO_MATCH`; owner projection remains P10-owned, catalog conflict -> incident |

Наличие T3 не означает merge remaster/edit без проверки version markers.

---

# 12. Decision policy

## 12.1. Состояния

```text
AUTO_MATCH
REVIEW_REQUIRED
NO_MATCH
INTEGRITY_CONFLICT
DEFERRED_EVIDENCE
```

Эти resolver states не являются candidate dispositions, review actions, ImportEntry workflow states или `UserTrackRef.resolution_status`.

## 12.1.1. Kind и execution mode

- `SHADOW` никогда не меняет ImportEntry, UserTrackRef или catalog.
- Неутвержденный matcher не может сохранять shadow `AUTO_MATCH` или `NO_MATCH`: ordinary ranked result использует `REVIEW_REQUIRED`; genuine hard integrity conflict и unavailable evidence сохраняют `INTEGRITY_CONFLICT`/`DEFERRED_EVIDENCE`, оставаясь без projection.
- `AUTO_MATCH` разрешен только как `APPLIED + EVALUATION + SYSTEM` при latest active policy exact scope, benchmark hash, calibrator, достаточном tier/score/margin и пустом hard-conflict set.
- Manual review всегда `APPLIED + REVIEW_ACTION`, сохраняет predecessor resolver state и никогда не кодируется как `AUTO_MATCH`.

## 12.2. Bootstrap thresholds

До benchmark, с учетом принятого ADR-019:

- `AUTO_MATCH` полностью отключен для T0/T1;
- T4 может дать только strict technical Vault re-reference по ADR-019; identity evaluation остается
  shadow `REVIEW_REQUIRED`, пока P10 не добавит отдельное auditable deterministic representation;
- T2/T3 допускаются максимум в shadow mode;
- пользователь видит кандидатов и explanation.

После benchmark thresholds публикуются как immutable `threshold_set_version`.

Начальные shadow values для сбора статистики:

```text
auto_threshold = 0.985
review_threshold = 0.750
margin_threshold = 0.080
```

Эти значения не являются критериями production release.

## 12.3. Pseudocode

```text
resolve(query):
    if exact_sha_reuse(query) is consistent:
        technical_result = DETERMINISTIC_VAULT_REUSE  # ADR-019, outside match projection
        record_shadow(REVIEW_REQUIRED, T4, counterfactual=technical_result)
        return technical_result

    candidates = generate_candidates(query)
    if candidates is empty:
        return NO_MATCH

    scored = score_and_calibrate(query, candidates)
    top1, top2 = best_two(scored)

    if has_integrity_conflict(top1):
        return INTEGRITY_CONFLICT

    if has_hard_conflict(top1):
        return REVIEW_REQUIRED

    if matcher_is_shadow_or_unapproved():
        return REVIEW_REQUIRED

    if evidence_tier(top1) >= minimum_auto_tier
       and top1.confidence >= auto_threshold
       and top1.confidence - top2.confidence >= margin_threshold:
        return AUTO_MATCH

    if top1.confidence >= review_threshold:
        return REVIEW_REQUIRED

    return NO_MATCH
```

---

# 13. Stored decision record

Каждое автоматическое или ручное решение хранит:

The logical record includes the typed query identity and snapshot, decision kind/mode,
candidate/result state, complete release/version snapshot, scores/explanation, actor,
idempotency identity, and the immutable backward `supersedes_decision_id`. The exact
physical fields and constraints are enumerated below and in ADR-015; there is no
mutable forward `superseded_by_decision_id` column.

Stored explanation не должна зависеть от повторного запроса к provider.

ADR-015 уточняет физический record без потери перечисленных полей:

- typed query key: `IMPORT_ENTRY`, `USER_TRACK_REF`, `LOCAL_AUDIO`, `EXTERNAL_REFERENCE`, `VAULT_OBJECT` или `AUDIO_VARIANT`; owner/device обязательны для owner-scoped query;
- sanitized `query_snapshot`, schema/canonicalization version и SHA-256;
- `decision_kind`, `execution_mode`, manual `review_action` и reviewed predecessor evidence;
- resolver state, selected/action Recording, exact candidate count, aggregate evidence SHA-256 и byte size;
- `evidence_mode`, candidate-generation/normalization/extractor/matcher/calibrator/threshold versions;
- scores, top-two, margin, tier, rank-one feature/conflict/origin explanation;
- actor, scoped idempotency key/request hash;
- immutable backward `supersedes_decision_id`, reason и time. Forward `superseded_by` получается inverse query и не требует UPDATE прошлого решения.

Каждая decision содержит sealed snapshot 0..100 ranked candidates. Candidate evidence сохраняет Recording, rank, nullable scores, tier, schema-versioned feature/conflict/origin/extractor JSON и canonical SHA-256. Ranks непрерывны; selected/rank-one и top-two/margin согласованы двусторонне. Candidate document имеет максимум 128 KiB, decision aggregate - максимум 4 MiB. Late INSERT, UPDATE или DELETE отклоняется.

`matcher_release`, `calibrator_release` и `threshold_set` immutable с момента INSERT. `match_policy_activation` хранит append-only `ACTIVATE`/`DEACTIVATE`/`ROLLBACK` chain по exact `(evidence_mode, evidence_tier)` scope. Initial schema содержит zero activation events.

---

# 14. Manual review contract

Review UI показывает:

- raw query metadata;
- top candidates и confidence;
- различающиеся title/artist/version/duration;
- release appearances;
- external IDs с provider provenance;
- fingerprint similarity и coverage;
- предупреждение о live/remix/edit/remaster conflict;
- действия `Accept`, `Reject`, `Keep unresolved`, `Create new Recording`;
- отдельное admin-действие `Merge global Recordings`.

Обычный пользовательский Accept разрешает `UserTrackRef`, но не обязан выполнять глобальный merge.

Manual review matrix:

- `ACCEPT` ссылается на candidate evidence непосредственного predecessor и атомарно создает resolved owner projection;
- `REJECT` ссылается на candidate predecessor и оставляет query reviewable, не отвергая автоматически все candidates;
- `KEEP_UNRESOLVED` не имеет target и создает явную unresolved/deferred projection;
- `CREATE_RECORDING` не ссылается на старый candidate, но атомарно сохраняет новую Recording как action target;
- `INTEGRITY_CONFLICT` до новой conflict-cleared evaluation допускает только `KEEP_UNRESOLVED`.

Review decision, audit и owner projection фиксируются одной transaction; partial commit отклоняется deferred invariant. Global `MERGE` остается отдельной admin command.

---

# 15. Merge and split interaction

## 15.1. Merge

Global merge выполняется отдельной command:

1. lock source/target Recordings;
2. re-resolve redirects;
3. собрать affected references;
4. проверить UserTrackRef coalesce;
5. создать `catalog_change_set`;
6. переназначить разрешенные children;
7. создать redirect source -> target;
8. записать audit и sync events;
9. commit atomically.

Matching decision может предложить merge, но не применяет его скрыто.

## 15.2. Split

Split всегда review/admin operation. Matcher после split помечает старые решения, затронутые moved evidence, как требующие reevaluation.

## 15.3. Undo

Undo использует сохраненный change set, а не вычисляет обратную операцию по текущему состоянию на глаз. Если после merge появились новые зависимости, undo может потребовать новый reviewed change set.

---

# 16. Benchmark dataset

## 16.1. Набор пар

Минимальные классы:

- identical bytes;
- same Recording, different codec/bitrate;
- same Recording, different container/tagging;
- album vs single appearance;
- compilation appearance;
- live vs studio;
- remix vs original;
- radio edit vs full version;
- remaster vs original;
- mono vs stereo;
- instrumental/karaoke/cover;
- Cyrillic/Latin transliteration;
- typos and reordered artist credits;
- common titles by different artists;
- same artist/title, different Recording;
- incorrect duplicate ISRC;
- provider ID conflict;
- truncated/corrupt audio;
- silence/short clips;
- unavailable metadata-only import.

## 16.2. Split strategy

- split по Recording/artist, а не случайным парам;
- один artist/release family не должен одновременно утекать в train и test;
- отдельный hard-negative test set;
- отдельный device/source-domain test set;
- temporal holdout для новых provider observations;
- private user collection не публикуется и не смешивается с public benchmark export.

## 16.3. Минимальный размер до production auto-match

Рекомендуемый нижний предел:

```text
>= 5 000 positive pairs
>= 10 000 hard-negative pairs
>= 500 examples на каждую критическую version-conflict группу
```

Если данных недостаточно для статистически надежной оценки, auto-match остается выключенным для соответствующего evidence tier.

---

# 17. Метрики и release gate

## 17.1. Основные метрики

- precision в `AUTO_MATCH`;
- false merge rate;
- auto-match coverage;
- recall кандидатов до scoring;
- review acceptance rate;
- top-1 accuracy;
- top-k recall;
- Brier score;
- Expected Calibration Error;
- latency p50/p95/p99;
- candidates per query;
- доля решений по evidence tier;
- показатели отдельно по hard-negative классу.

## 17.2. Production gate

Auto-match tier может стать `ACTIVE`, если:

1. candidate recall@50 >= 99.5% на applicable positive test set;
2. auto-match precision >= 99.9%;
3. нижняя граница 95% Wilson CI для precision >= 99.5%;
4. ни одного auto-match при explicit hard version conflict;
5. ECE <= 0.03 для calibrated confidence;
6. p95 metadata scoring <= 200 ms на reference server без network provider calls;
7. p95 fingerprint verification <= 2 s для уже вычисленных fingerprints;
8. решение воспроизводимо по stored versions/evidence;
9. shadow comparison не выявляет regression относительно предыдущей active version;
10. rollback на предыдущий matcher/threshold set проверен.

Coverage не повышается ценой снижения precision gate.

---

# 18. Shadow deployment

Новая matcher version проходит:

1. offline benchmark;
2. replay сохраненных обезличенных feature rows;
3. shadow scoring без изменения catalog;
4. comparison report с active matcher;
5. canary для добровольно выбранного test profile;
6. activation threshold set;
7. rollback window.

Shadow record не хранит лишние raw private provider payloads.

---

# 19. Observability

Метрики:

```text
match_queries_total{mode,evidence_tier}
match_candidates_count
match_decisions_total{state,matcher_version}
match_hard_conflicts_total{type}
match_review_accept_total
match_review_reject_total
match_shadow_disagreement_total
fingerprint_compute_seconds
fingerprint_failures_total{code}
identity_integrity_incidents_total{type}
```

Logs содержат IDs и reason codes, но не полные private URLs, tokens или raw imported payload.

---

# 20. Security and abuse controls

- Fingerprint/metadata payload size ограничен.
- Decoder запускается с time/memory limits и argument arrays.
- Provider observation не может напрямую переписать canonical Recording.
- External ID namespace валидируется adapter manifest.
- User input не становится SQL/regex без safe binding/limits.
- Global merge/split требует authorization и audit.
- Повторная decision command использует idempotency key: application command возвращает stored row только при совпадающем request hash; другой hash — stable conflict, direct duplicate INSERT — named unique violation.
- Malicious file не попадает в serving до ingest validation.

---

# 21. Compatibility rules

- Matcher version immutable.
- Matcher/calibrator/threshold releases, activation events, decisions и candidate evidence append-only; threshold set immutable уже с INSERT.
- Feature extractor version сохраняется для каждого evidence record.
- Unknown feature игнорируется старым reader без удаления record.
- Старое решение остается объяснимым после смены модели.
- Re-score не меняет прошлое решение без новой decision row.
- Shadow re-score не supersede текущую applied projection lineage; он начинает отдельную shadow lineage.
- Android может показывать simplified explanation, не реализуя server matcher.

---

# 22. Acceptance scenarios

| Сценарий | Ожидаемое решение |
| --- | --- |
| Повторный ingest exact bytes после ADR-019 | Strict technical object/variant/Recording re-reference; no `AUTO_MATCH`, merge or owner projection |
| FLAC и MP3 одной studio Recording | T3 candidate; auto только после benchmark |
| Один Track в album и single | Одна Recording, разные ReleaseTrack |
| Live и studio с одинаковым title | Hard conflict, не auto-merge |
| Remix с близким fingerprint fragment | Hard conflict/review |
| Radio edit короче full version | Отдельная Recording или review |
| Remaster очень близок по fingerprint | Review-only по умолчанию |
| Один ISRC у конфликтующих версий | Review, ISRC не решает конфликт |
| Export row без audio | UserTrackRef остается unresolved |
| Top-2 отличаются на 0.01 | Review независимо от top1 score |
| Provider ID уже target другой Recording | Integrity/review conflict |
| Corrupt file | Нет Recording auto-match и нет serving AudioVariant |

---

# 23. Зафиксированные решения

1. SHA-256 определяет bytes, а не Recording.
2. Fingerprint является versioned evidence, а не primary key.
3. Candidate generation и decision разделены.
4. Missing feature не считается mismatch.
5. ISRC не имеет global UNIQUE и не дает auto-match в одиночку.
6. Explicit version conflict блокирует auto-match.
7. Auto-match включается отдельно для evidence tiers после benchmark.
8. Resolution UserTrackRef не равен global merge.
9. Все решения объяснимы и воспроизводимы по versioned evidence.
10. Precision имеет приоритет над coverage.
11. Полная identity history и release/policy registries append-only; owner projections не являются историей.
12. Initial schema не активирует ни один auto-match scope.

---

# 24. Открытые benchmark-решения

| Вопрос | Как закрывается |
| --- | --- |
| Точная fingerprint similarity | Benchmark Chromaprint representations на реальной коллекции |
| Duration `tau` | Grid search и calibration по типам источника |
| T1 metadata-only auto-match | Отдельный high-precision gate или оставить review-only |
| Logistic vs isotonic calibrator | Calibration metrics на holdout |
| Fingerprint candidate index | Recall/latency/storage benchmark |
| Remaster policy | Review fixtures и пользовательская политика отображения |
| Alias/transliteration weights | Per-script evaluation |

---

# 25. Implementation sequence

1. Реализовать normalization и version marker fixtures.
2. Реализовать candidate generators с origin tracing.
3. Реализовать feature extraction и stored evidence schema.
4. Подключить pinned Chromaprint/fpcalc pipeline.
5. Собрать benchmark dataset и hard negatives.
6. Запустить raw score как ranking-only.
7. Обучить/проверить calibrator.
8. Включить shadow decisions.
9. Активировать только прошедшие gate evidence tiers.
10. Реализовать merge/split proposal отдельно от resolver.
11. До runtime matcher проверить ADR-015 persistence invariants; ADR-019 применяется только как
    отдельный technical Vault path, а P10 должен отдельно определить immutable owner-resolution record.

---

# 26. Основания решений

- MusicBrainz разделяет Recording, Track и Release, что используется в domain model AutPlay.
- MusicBrainz Picard применяет AcoustID/fingerprint как lookup evidence, а не как замену всей release/recording модели.
- Chromaprint ориентирован на идентификацию near-identical audio и имеет ограничения для других задач similarity.
- beets использует составную weighted distance и настраиваемые веса полей, что подтверждает необходимость раздельных features и benchmark вместо одного идентификатора.

Ссылки:

- [MusicBrainz Recording](https://musicbrainz.org/doc/Recording)
- [MusicBrainz Track](https://musicbrainz.org/doc/Track)
- [MusicBrainz AcoustID guide](https://musicbrainz.org/doc/Guides/AcoustID)
- [MusicBrainz Picard: acoustic fingerprinting](https://picard-docs.musicbrainz.org/en/latest/tutorials/acoustid.html)
- [Chromaprint](https://github.com/acoustid/chromaprint)
- [beets distance weights](https://beets.readthedocs.io/en/stable/reference/config.html#distance-weights)

---

# 27. Definition of Done

Specification v1 считается готовой к реализации, когда:

- все identity fixtures имеют ожидаемые решения;
- feature/evidence schema согласована с PostgreSQL Schema v1;
- fingerprint pipeline pinning воспроизводимо;
- hard conflicts покрыты unit/property tests;
- benchmark runner формирует per-class report;
- auto-match остается выключенным до прохождения gate;
- manual review не запускает скрытый global merge;
- matcher rollback сохраняет прошлые decision records.
- все пять resolver states и оба execution modes имеют round-trip/negative fixtures;
- candidate sets 0/1/2/100 sealed, а late INSERT/UPDATE/DELETE rejected;
- manual review, supersession, activation/deactivation/rollback и owner projections покрыты transaction tests;
- initial policy activation history empty, поэтому F-016 остается enforced для probabilistic
  matching; ADR-019 не создает matcher activation и не меняет owner projection.
