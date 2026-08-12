# Common Prompt Protocol

Этот контракт применяется ко всем phase prompts P00-P14. Phase prompt не повторяет его целиком.

## 1. До изменения файлов

1. Найти repository root.
2. Полностью прочитать применимые `AGENTS.md`.
3. Прочитать current phase prompt, последний handoff, `DECISION_REGISTER.md` и указанные design documents.
4. Проверить `git status` и сохранить unrelated user changes.
5. Проверить prerequisites/exit evidence предыдущей phase.
6. Составить короткий executable plan.

Если предыдущая phase не green, сначала исправить только блокирующий regression в границах current phase либо остановиться с точным blocker.

## 2. Source-of-truth precedence

1. Security/privacy/destructive-data constraints из product specification.
2. Более узкая specification для своей области.
3. Physical PostgreSQL/Room schema для persistence details.
4. System Architecture для boundaries и dependency direction.
5. ER model для conceptual meaning.
6. Current phase prompt для delivery scope.

Нельзя молча исправлять документы под удобную implementation. Противоречие фиксируется exact references и ADR/change proposal.

## 3. Разрешенная автономность

Codex самостоятельно:

- читает repository;
- редактирует in-scope local files;
- добавляет dependencies, необходимые current phase;
- запускает formatters, linters, builds и tests;
- создает disposable local test data/containers;
- исправляет найденные in-scope defects;
- обновляет implementation docs и handoff.

Codex останавливается перед:

- destructive operation над реальными/user data;
- push, PR, publication или deployment;
- использованием real credentials;
- покупкой/paid resource;
- существенным расширением phase scope;
- изменением frozen decision;
- выбором неоднозначного external provider/legal policy.

## 4. Engineering constraints

- Modular monolith before microservices.
- Pure domain не импортирует framework/storage/network/GPU code.
- Application layer владеет transactions/use cases.
- Ports описывают boundaries; adapters реализуют technology.
- CPU-only server path является обязательным.
- GPU imports не попадают в API/CPU worker process.
- PostgreSQL является server metadata/job source of truth.
- Filesystem/NAS является initial Vault backend.
- Android operation сначала commits local state and Offline Journal.
- Media3 владеет playback/download execution.
- WorkManager владеет guaranteed deferred metadata/sync work.
- Unknown persisted/API values сохраняются безопасно.
- No destructive migration fallback.

## 5. Dependency policy

- Python workflow через `uv` и committed `uv.lock`.
- Android versions через Gradle version catalog and wrapper.
- Container images pinned by version; production by digest when release candidate is built.
- Добавлять минимальную dependency surface.
- Не добавлять Redis, RabbitMQ, NATS, Kafka, MinIO/S3 или external vector DB без accepted ADR and benchmark.
- Не использовать floating `latest`.
- Перед новым dependency проверить active maintenance, license, platform support и necessity.

## 6. Code quality

- Code identifiers, scripts, configs and comments in English/ASCII.
- Public/non-obvious APIs typed and documented.
- No blanket exception swallowing.
- Stable machine-readable errors.
- Bounded retries, payloads, batch sizes and timeouts.
- Structured logs redacted from tokens, private URLs, raw paths and personal payloads.
- Test fixtures deterministic.
- No critical `TODO`, `pass`, disabled test or placeholder success response at phase exit.

## 7. Testing rule

Для каждого behavior определить cheapest test, который реально доказывает его.

- Constraint требует real database test.
- Migration требует previous-version fixture.
- Filesystem atomicity требует failure injection.
- Sync idempotency требует duplicate/reorder/retry vectors.
- Playback требует Media3/instrumentation smoke where JVM unit test insufficient.
- UI state требует behavior test, не screenshot only.
- Performance claim требует recorded benchmark on named environment.

Не подменять integration test большим количеством mocks.

## 8. Commands

Использовать canonical repository commands. Если command отсутствует, current phase должна добавить документированный wrapper/task.

Preferred server checks:

```text
uv sync --frozen
uv run ruff check .
uv run mypy server/src
uv run pytest
```

Preferred Android checks:

```text
./gradlew lint
./gradlew test
./gradlew connectedCheck
```

Конкретные module tasks уточняются после P01 и записываются в root README/AGENTS.md.

## 9. Documentation updates

Каждая phase обновляет при необходимости:

- `docs/implementation/PROGRESS.md`;
- `docs/implementation/TRACEABILITY.md`;
- `docs/implementation/RISK_REGISTER.md`;
- `docs/adr/ADR-xxxx-*.md`;
- exact setup/test commands;
- `HANDOFF_Pxx.md`.

Docs описывают проверенное состояние, а не намерение.

## 10. Handoff contract

В конце phase создать `docs/implementation/HANDOFF_Pxx.md` по template со следующими разделами:

1. Outcome.
2. Scope delivered/not delivered.
3. Changed files/modules.
4. Decisions/ADR.
5. Migrations/contracts.
6. Commands executed with results.
7. Test evidence.
8. Known risks/debt.
9. Exact prerequisite for next phase.
10. Git status/commit identifier.

Финальный ответ Codex краткий. Не вставлять полные файлы или длинные logs.

## 11. Git discipline

- Не использовать destructive reset/checkout.
- Не менять unrelated user work.
- Один successful phase может завершаться одним local commit, если это разрешено первым сообщением текущего запуска.
- Никогда не push автоматически.
- При dirty worktree сначала отделить свои changes логически, не удаляя чужие.

## 12. Stop condition

Phase завершена только когда:

- весь declared scope реализован;
- acceptance criteria имеют evidence;
- required checks green;
- docs/handoff обновлены;
- blockers отсутствуют либо явно требуют user decision;
- future phase не начата.
