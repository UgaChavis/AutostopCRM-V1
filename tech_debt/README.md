# AutoStop CRM: backlog технического долга

Актуально на 2026-09-05. Ветка: `autostopcrm-v1`.

Этот каталог — краткая карта активных задач и owner-документов ratchet, а не
пошаговый сценарий. Опирайся на текущий код, тесты и audit-скрипты, выбирай
наименьший полезный срез и сохраняй пространство для инженерного суждения.
Завершённые результаты живут в gates, а не в исторических журналах.

## Текущие контракты

- `scripts/code_health_audit.py` классифицирует все tracked-файлы; пределы:
  module 2500, test module 3000, class 2500, function 450 строк.
- MCP surface, capability и change-feed matrices остаются audit-owned и без
  gaps; точные значения берутся из текущих проверок.
- Compatibility-названия `minimal_kanban`, `%APPDATA%\\Minimal Kanban` и
  `Start Kanban.exe` сохраняются до доказанной отдельной миграции.
- Задача 017 — только read-only inventory: generated/ignored outputs очищаются
  отдельно и recoverably; `release/`, `.venv/`, production data и rollback
  assets не являются целями cleanup.

## Активная очередь

| ID | Приоритет | Следующий результат |
|---|---|---|
| 001 | P0 owner | Exact maintainability caps и owner mappings |
| 003 | P1 | Test seam вместе с нужным production extraction |
| 005 | P1 | Небольшой JS-domain extraction |
| 008–009 | P1 | MCP registrar, затем executor/verifier slices |
| 010–011 | P1 | Attachment/file I/O, затем manager compatibility |
| 012–014 | P1 high | Repair orders, payroll, printing — отдельными commits |
| 017 | P1 evidence | Read-only migration/compatibility inventory |
| 018 | P1 | Snapshot read models |
| 019–021 | P1 high | Finance planner, release boundaries, print web chunks |

Ratchet owners существуют ровно по одному для 001, 003, 008, 009, 012, 013,
014, 018, 019, 021 и 206. Задача 206 ждёт отдельного runtime/ADR решения.

## Рабочая модель

- Выбирай следующий срез по текущей боли и доказательствам, а не по старому
  порядку списка; сохраняй один ясный и откатываемый результат на commit.
- До удаления или миграции проверь runtime, imports, tests, docs и rollback.
  Source cap уменьшается вместе с переносом; HTTP/MCP DTO, schemas, errors,
  audit/feed ordering, idempotency и revisions не меняются cleanup-правкой.
- Начинай с focused unittest и Ruff; добавляй code-health/docs audit, parity,
  browser или full local CI по затронутому контракту. После публикации жди
  hosted CI exact SHA перед следующим срезом.
- Production, finance/payroll/payments и data mutations остаются в границах
  `AGENTS.md` и runbook: публикация в GitHub не расширяет эти полномочия.
