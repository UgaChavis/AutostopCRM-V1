# AutoStop CRM: backlog технического долга

Актуально на 2026-08-31. Ветка: `autostopcrm-v1`.

Этот каталог хранит только активные задачи и документы-владельцы рачетов.
Завершённые журналы удаляются после переноса устойчивого результата сюда;
текущие метрики берутся из кода и audit-скриптов, а не из старых снимков.

## Текущее состояние

- `scripts/code_health_audit.py`: все tracked-файлы классифицированы; 34/34
  size-ratchets и 2/2 complexity-ratchets проходят.
- Общие пределы: production module 2500, test module 3000, class 2500,
  function 450 строк. Exact caps принадлежат задаче 001.
- MCP characterization: 98 builtin/raw registrations без duplicates; внешний
  Gateway v2 публикует ровно 24 инструмента, attestation — 46 CRM operations.
- Capability и change-feed matrices обязаны иметь `gaps=0`.
- Compatibility-названия `minimal_kanban`, `%APPDATA%\\Minimal Kanban` и
  `Start Kanban.exe` сохраняются до отдельной доказанной миграции.
- One-off migrations из 017 — только read-only inventory, не разрешение на
  удаление. Generated/ignored outputs очищаются отдельно и recoverably;
  `release/`, `.venv/`, production data и rollback assets не удаляются.

## Консолидированная база

Задачи 000, 002, 004, 006 и 007 заменены устойчивыми gates: browser/PDF
preflight, branch coverage, core browser smoke, immutable `RouteSpec` и
компактный `_make_handler`.

Результаты удалённых журналов 015, 016, 022, 023 и 024 закреплены текущими
gates: Manager/browser/perf probes, collection guard и local CI mirror,
hosted Docker contract, docs audit и scoped audit локальных skills.

Задача 001 функционально завершена, но остаётся owner-документом двух data-only
caps. Задачи 003 и 008 остаются активными до исчерпания своих source caps.

## Активная очередь

| ID | Приоритет | Следующий результат |
|---|---|---|
| 001 | P0 owner | Exact maintainability caps и owner mappings |
| 003 | P1 | Test seam только вместе с нужным production extraction |
| 005 | P1 | Один небольшой JS-domain extraction |
| 008 | P1 | Один MCP registrar с exact registry characterization |
| 009 | P1 high | Executor/verifier slices после progress 008 |
| 010, 011 | P1 | Attachment/file I/O, затем manager compatibility |
| 012–014 | P1 high | Repair orders, payroll, printing — отдельными commits |
| 017 | P1 evidence | Read-only migration/compatibility inventory |
| 018 | P1 | Snapshot read models |
| 019 | P1 high | Finance audit/safe-fix planner |
| 020 | P1 high | Release backup/verify/restore boundaries |
| 021 | P1 | Embedded print web-module chunks |

Ratchet owners, которые должны существовать ровно по одному: 001, 003, 008,
009, 012, 013, 014, 018, 019, 021, 206 и 207.

Этап 2 не входит в текущую очередь: новая архитектурная инициатива требует
отдельного owner-approved ADR; 206 — только после такого решения о runtime,
207 — только при доказанной боли после 009.

## Последовательность и правила

1. Один малый registrar/test slice 008+003 с уменьшением исходного cap.
2. Один UI slice 005, затем read-only inventory 017.
3. Низкорисковые 010, 011 и 018; высокорисковые 009, 012–014 и 019–021 —
   только после characterization.
4. Один logical defect или mechanical transfer — один commit. Test предшествует
   переносу/удалению; source cap уменьшается в том же commit.
5. Cleanup не меняет HTTP/MCP DTO, schemas, error codes, audit/feed ordering,
   idempotency, revisions или compensating semantics. Business rules остаются
   в services.
6. Test LOC растёт только ради необходимой regression-защиты; active runtime и
   docs не растут. Finance, payroll, payments и production mutations требуют
   отдельного owner-approved контура. GitHub publish не разрешает deploy.

## Gates и stop-line

Для каждого среза: focused unittest и Ruff, code-health/docs audit, contract
parity при API/MCP изменении, JS syntax/browser smoke для UI, полный local CI
для shared changes и hosted CI на exact SHA. Перед deploy handoff дополнительно
нужны full browser, оба local-temp perf probes и toolchain doctor.

Stop: необъяснимый test/coverage/parity failure, contract drift, отсутствие
production evidence для migration cleanup или необходимость финансовой записи.
Сервер меняется только по отдельной команде владельца.
