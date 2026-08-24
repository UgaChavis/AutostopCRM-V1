# 203. Решить судьбу desktop и embedded agent runtime

Приоритет: P2
Этап: 2 — продуктово-техническое решение
Оценка: 2–4 дня discovery
Риск: высокий при удалении
Статус: proposed

## Проблема

Проект одновременно поддерживает:

- production browser/API/MCP server;
- desktop PySide shell и portable `Start Kanban.exe`;
- embedded AgentControl/AgentRunner;
- Gateway v2.

Все paths активны в code/tests/build, но неизвестна фактическая usage доля.
Удаление не является обычным cleanup.

## Результат

Owner-approved ADR о поддерживаемых runtime entrypoints; задача 206 либо
разблокирована, либо закрыта и заменена bounded retirement plan.

## Evidence

- Desktop стартует через `main.py → minimal_kanban.app.run`.
- Release/build scripts и Qt tests активны.
- Embedded agent стартует в desktop и MCP main.
- Standalone AI chat UI retired, но enrichment/scheduled tasks остаются.

## Решение

Собрать без PII:

- фактические desktop releases/downloads/launch markers;
- production flags embedded agent;
- operator workflows, которых нет в browser/Gateway;
- rollback/support obligations.

Варианты:

1. Keep both, но formalize boundaries.
2. Server-first, desktop shell maintenance-only.
3. Retire embedded agent, keep Gateway.
4. Retire desktop после migration window.

## Acceptance

Короткий ADR с owner decision, поддерживаемыми entrypoints, sunset window,
telemetry/evidence и rollback. До ADR ничего не удалять.

## Stop condition

Без usage evidence и product owner решения не рефакторить и не удалять
AgentRunner/desktop compatibility paths.
