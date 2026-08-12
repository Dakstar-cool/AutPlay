# Первое сообщение для Codex

Отправьте Codex содержимое блока ниже вместе с ZIP.

```text
Я прикрепил AutPlay_Codex_Build_Pack_v1.zip. Работай непосредственно с файлами текущего workspace.

Сначала найди или распакуй архив и прочитай полностью:
1. START_HERE.md
2. docs/build-pack/PROMPT_PROTOCOL.md
3. docs/build-pack/DECISION_REGISTER.md
4. docs/build-pack/PHASE_INDEX.md
5. docs/build-pack/prompts/P00_repository_intake.md
6. все документы из docs/design/, на которые ссылается P00
7. существующий AGENTS.md, если repository уже существует

Выполни только phase P00. Не начинай P01 и не реализуй product features.

Если workspace пустой, используй корень распакованного пакета как основу нового AutPlay repository. Если code уже существует, сначала проведи read-only audit, сохрани пользовательские изменения и интегрируй документы без перезаписи несвязанного code.

Разрешены локальные edits, создание repository structure, установка project dependencies, недеструктивные проверки и один локальный commit после green P00. Запрещены push, публикация, deployment, внешние записи, работа с реальными secrets и destructive commands.

Не выводи полные измененные файлы. В конце дай:
- краткий результат;
- список измененных файлов;
- выполненные проверки и их статус;
- принятые ADR/assumptions;
- blockers;
- ссылку/путь на docs/implementation/HANDOFF_P00.md.

Остановись после P00 и дождись следующего phase prompt.
```

## Опциональный Goal Mode

Если `/goal` доступен, первую строку можно заменить на:

```text
/goal Выполни только AutPlay phase P00 из прикрепленного build pack и остановись, когда все критерии P00 подтверждены командами проверки и создан HANDOFF_P00.md.
```
