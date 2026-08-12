# P00 - Repository Intake and Engineering Contract

Выполни только phase P00. Следуй `docs/build-pack/PROMPT_PROTOCOL.md`.

## Цель

Превратить переданный design/build pack в понятный repository baseline без реализации product features. После P00 новый Codex session должен восстановить состояние проекта только по repository files.

## Прочитать полностью

- `START_HERE.md`
- все файлы `docs/design/`
- `docs/build-pack/BUILD_STRATEGY.md`
- `docs/build-pack/DECISION_REGISTER.md`
- `docs/build-pack/VERSION_POLICY.md`
- `docs/build-pack/MVP_ACCEPTANCE_MATRIX.md`
- существующие `AGENTS.md`, README, manifests and Git status

## Scope

1. Определи empty workspace или existing repository.
2. Проведи read-only inventory текущего code/toolchains/docs.
3. Проверь design documents на missing files, broken references и критические contradictions.
4. Создай root `AGENTS.md`, адаптировав template к реальному repository.
5. Создай:
   - `docs/implementation/PLAN.md`;
   - `docs/implementation/PROGRESS.md`;
   - `docs/implementation/TRACEABILITY.md`;
   - `docs/implementation/RISK_REGISTER.md`;
   - `docs/implementation/VERSIONS.md`;
   - `docs/implementation/HANDOFF_P00.md`.
6. В PLAN разложи P01-P14 на repository deliverables, не переписывая phase prompts.
7. Зафиксируй detected discrepancies в proposed ADR list. Не меняй frozen decisions.
8. Если Git отсутствует в empty workspace, initialize repository and create appropriate `.gitignore` only.

## Не делать

- не создавать feature/domain/API/UI code;
- не выбирать external providers;
- не добавлять Redis, broker, MinIO или cloud resources;
- не создавать database migrations;
- не настраивать deployment outside local files;
- не начинать P01.

## Acceptance

- Все source documents доступны по стабильным repository paths.
- Root instructions не противоречат design precedence.
- PLAN связывает каждую phase с outputs и acceptance.
- Risk register содержит минимум data loss, false merge, sync, Vault, Android URI, GPU, backup, security.
- Version file разделяет pinned baseline и unresolved versions.
- Existing user changes сохранены.
- Handoff содержит exact next command/prompt.

## Checks

- проверить все relative links внутри build pack;
- проверить четность Markdown code fences;
- проверить отсутствие duplicate phase numbers/missing prompts;
- вывести compact file inventory;
- проверить `git status`.

После green checks создай один local commit `chore: establish AutPlay engineering contract`, если commit разрешен текущим запуском. Не push. Остановись после P00.
