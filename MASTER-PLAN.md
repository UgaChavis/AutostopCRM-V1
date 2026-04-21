# MASTER PLAN: AutoStop CRM

Главный архитектурный и производственный план ветки `autostopcrm-v1`.

Назначение этого файла:

1. Быстро вводить нового разработчика в реальное состояние проекта.
2. Показывать дерево модулей и их внутренние версии.
3. Фиксировать, что уже готово, что находится в активной доработке и куда идти дальше.
4. Давать основу для параллельной разработки без рассыпания контекста.

Этот файл должен обновляться вместе с заметными изменениями в GitHub-ветке и после значимых production-проверок.

## 1. Правила чтения и использования

- Ветка активной разработки: `autostopcrm-v1`
- `autostopCRM` оставлена как устаревшая историческая линия
- Историческое имя пакета и app-data сохраняется: `minimal_kanban`
- Этот файл должен быть repo-safe: сюда не кладутся живые секреты, приватные ключи, session tokens и полные access credentials
- Источник оперативного контекста: этот файл -> `00_START_HERE_AUTOSTOP_CRM.md` -> `PROJECT_HANDOFF.md` -> код нужного модуля

## 2. Текущее состояние на момент обновления

- Локальный HEAD и GitHub HEAD нужно перепроверять перед работой через `git rev-parse --short HEAD`
- Production URL:
  - CRM: `https://crm.autostopcrm.ru`
  - MCP: `https://crm.autostopcrm.ru/mcp`
- Production host: `46.8.254.243`
- Production repo path: `/opt/autostopcrm`
- Последнюю production-верификацию нужно оценивать отдельно перед каждым серьёзным pass
- Следствие: GitHub и локальная ветка актуальны, а production-паритет всегда надо перепроверять отдельно перед работой с боем

## 3. Что такое AutoStop CRM сейчас

AutoStop CRM — это CRM для автосервиса на базе kanban-доски, где поверх базовой доски уже выросли:

- vehicle profile и card autofill
- repair orders, works, materials, payments, printing
- operator/admin auth
- cashboxes, employees, payroll
- локальный HTTP API
- MCP transport layer
- отдельный server AI worker

Это уже не "minimal kanban", а многослойная operational CRM-система.

## 4. Глобальная схема системы

```text
Desktop UI / Browser UI
    ->
Local HTTP API
    ->
CardService + domain services
    ->
JsonStore / persistent state

External ChatGPT / MCP client
    ->
MCP server
    ->
BoardApiClient
    ->
Local HTTP API
    ->
Same business core

Server AI worker
    ->
AgentControlService / AgentStorage
    ->
AgentRunner orchestration core
    ->
Bounded automotive + board tools
    ->
Patch / verify / follow-up
```

## 5. Дерево модулей и внутренняя версияция

Версии ниже — это не public semver-релизы продукта. Это внутренняя модульная версия зрелости, чтобы можно было параллельно вести разные блоки.

Правило версий:

- `1.0` — модуль собран и работает в базовом сценарии
- `1.1` — модуль стабилизирован после первых production-pass
- `1.2+` — модуль получил заметные расширения или архитектурное усиление
- плюс `0.1` за существенную волну функциональных или структурных изменений

### 5.1 Дерево модулей

```text
0. AutoStop CRM Product Envelope v1.6
  1. Platform Runtime v1.2
    1.1 Desktop bootstrap v1.1
    1.2 Container runtime v1.2
    1.3 Settings and publication runtime v1.2
  2. Board Core v1.5
    2.1 Cards and columns v1.5
    2.2 Snapshots, search, wall v1.3
    2.3 Tags, deadlines, archive, attachments v1.4
  3. Workshop Operations v1.4
    3.1 Vehicle profile v1.3
    3.2 Repair orders v1.3
    3.3 Printing and exports v1.2
    3.4 Cashboxes v1.1
    3.5 Employees and payroll v1.2
  4. API and Access Control v1.4
    4.1 Local HTTP API v1.4
    4.2 Operator auth and admin users v1.2
  5. MCP Layer v1.4
    5.1 MCP runtime v1.3
    5.2 BoardApiClient transport v1.3
    5.3 Connector protocol surface v1.4
  6. Server AI Contour v1.6
    6.1 Agent control and scheduler v1.4
    6.2 Orchestration core v1.6
    6.3 Policy engine v1.3
    6.4 Automotive lookup tools v1.3
    6.5 Deterministic scenarios v1.2
    6.6 Follow-up and cache behavior v1.3
  7. Browser UI Surface v1.5
    7.1 Board workspace v1.5
    7.2 Column drag-and-drop v1.1
    7.3 Employees workspace v1.2
    7.4 Repair-order workspace v1.2
  8. Tests and Diagnostics v1.4
    8.1 API and service regression v1.4
    8.2 MCP and runtime tests v1.4
    8.3 Agent tests v1.5
  9. Docs and Handoff v1.2
    9.1 Start-here docs v1.1
    9.2 Project handoff docs v1.2
    9.3 Master plan v1.0
```

### 5.2 Матрица модулей

| ID | Модуль | Версия | Статус | Ключевые файлы |
|---|---|---:|---|---|
| `1` | Platform Runtime | `v1.2` | stable | `main.py`, `main_mcp.py`, `main_agent.py`, `src/minimal_kanban/app.py` |
| `2` | Board Core | `v1.5` | stable | `src/minimal_kanban/services/card_service.py`, `src/minimal_kanban/services/column_service.py` |
| `3` | Workshop Operations | `v1.4` | active | `src/minimal_kanban/vehicle_profile.py`, `src/minimal_kanban/repair_order.py`, `src/minimal_kanban/printing/service.py` |
| `4` | API and Access Control | `v1.4` | stable | `src/minimal_kanban/api/server.py`, `src/minimal_kanban/operator_auth.py` |
| `5` | MCP Layer | `v1.4` | hardening | `src/minimal_kanban/mcp/server.py`, `src/minimal_kanban/mcp/client.py`, `src/minimal_kanban/mcp/runtime.py` |
| `6` | Server AI Contour | `v1.6` | active | `src/minimal_kanban/agent/control.py`, `src/minimal_kanban/agent/runner.py`, `src/minimal_kanban/agent/policy.py` |
| `7` | Browser UI Surface | `v1.5` | active | `src/minimal_kanban/web_assets.py` |
| `8` | Tests and Diagnostics | `v1.4` | stable | `tests/test_api.py`, `tests/test_agent.py`, `tests/test_mcp.py`, `scripts/check_live_connector.py` |
| `9` | Docs and Handoff | `v1.2` | active | `00_START_HERE_AUTOSTOP_CRM.md`, `PROJECT_HANDOFF.md`, `MASTER-PLAN.md` |

## 6. Подробная карта модулей

### 6.1 Module 1: Platform Runtime v1.2

Что внутри:

- desktop bootstrap
- MCP-only runtime
- standalone AI worker runtime
- settings/publication bootstrapping
- containerized deployment via `docker-compose`

Ключевые файлы:

- `main.py`
- `main_mcp.py`
- `main_agent.py`
- `src/minimal_kanban/app.py`
- `docker-compose.yml`
- `deploy.sh`

Состояние:

- базовый runtime стабилен
- контейнерный контур уже production-oriented
- lingering risk: drift between local/GitHub/prod still must be checked manually

### 6.2 Module 2: Board Core v1.5

Что внутри:

- карточки
- столбцы
- reorder карточек и столбцов
- archive, sticky notes, tags, deadlines, attachments
- snapshots, review board, search, wall

Ключевые файлы:

- `src/minimal_kanban/services/card_service.py`
- `src/minimal_kanban/services/column_service.py`
- `src/minimal_kanban/services/snapshot_service.py`

Последние важные изменения:

- добавлен left-to-right reorder колонок
- расширена drag-capture зона колонки

### 6.3 Module 3: Workshop Operations v1.4

Что внутри:

- vehicle profile
- repair orders
- print/export
- cashboxes
- employees and payroll

Ключевые файлы:

- `src/minimal_kanban/vehicle_profile.py`
- `src/minimal_kanban/repair_order.py`
- `src/minimal_kanban/printing/service.py`
- `src/minimal_kanban/services/card_service.py`

Последние важные изменения:

- employees workspace переработан
- create path сотрудников больше не затирает старые записи stale ID
- лимит сотрудников поднят до `15`

### 6.4 Module 4: API and Access Control v1.4

Что внутри:

- локальный HTTP API
- operator session layer
- admin users
- proxy-aware write protection
- health, runtime, service routes

Ключевые файлы:

- `src/minimal_kanban/api/server.py`
- `src/minimal_kanban/operator_auth.py`

Последние важные изменения:

- `base_url` нормализует wildcard/IPv6 bind hosts корректнее
- `/api/health` переведен в quieter logging path

### 6.5 Module 5: MCP Layer v1.4

Что внутри:

- MCP runtime
- MCP server surface
- BoardApiClient transport
- canonical connector pathing
- external agent/tool access to CRM

Ключевые файлы:

- `src/minimal_kanban/mcp/server.py`
- `src/minimal_kanban/mcp/client.py`
- `src/minimal_kanban/mcp/runtime.py`

Последние важные изменения:

- MCP runtime больше не self-probe’ится через `0.0.0.0`
- mixed MCP test runs очищены от `ResourceWarning`-шума
- последний коммит `6eec3a0` дополнительно выключил лишний `asyncio debug noise` в MCP tests

### 6.6 Module 6: Server AI Contour v1.6

Это главный интеллектуальный контур проекта.

Подмодули:

- `6.1` Agent control and scheduler `v1.4`
- `6.2` Orchestration core `v1.6`
- `6.3` Policy engine `v1.3`
- `6.4` Automotive lookup tools `v1.3`
- `6.5` Deterministic scenarios `v1.2`
- `6.6` Follow-up and cache behavior `v1.3`

Главный контракт:

- `read -> evidence -> plan -> tools -> patch -> write -> verify`

Ключевые файлы:

- `src/minimal_kanban/agent/control.py`
- `src/minimal_kanban/agent/runner.py`
- `src/minimal_kanban/agent/contracts.py`
- `src/minimal_kanban/agent/policy.py`
- `src/minimal_kanban/agent/tools.py`
- `src/minimal_kanban/agent/automotive_tools.py`
- `src/minimal_kanban/agent/scenarios/*`

Что уже реализовано:

- единый orchestration core
- structured deterministic card autofill
- policy-gated required tools
- patch-only writing
- read-after-write verification
- scenario feedback and follow-up reasons in traces
- per-run cache для automotive lookups
- quieter backoff на repeated no-op follow-ups

Что ещё остаётся главным ограничением:

- слабый внешний VIN source всё ещё может приводить к partial VIN enrichment
- второй VIN fallback source остаётся сильным кандидатом на следующий AI-pass

### 6.7 Module 7: Browser UI Surface v1.5

Что внутри:

- web board UI
- card modal
- employees workspace
- repair-order workspace
- column drag-and-drop

Ключевой файл:

- `src/minimal_kanban/web_assets.py`

Последние важные изменения:

- employees workspace переделан в master-detail
- column DnD added and widened

### 6.8 Module 8: Tests and Diagnostics v1.4

Что внутри:

- full `unittest` suite
- targeted regression packs
- live connector smoke
- agent runtime smoke
- isolated test runner

Ключевые файлы:

- `tests/test_agent.py`
- `tests/test_policy.py`
- `tests/test_api.py`
- `tests/test_mcp.py`
- `scripts/check_live_connector.py`
- `scripts/check_agent_runtime.py`

Последние важные изменения:

- policy edge cases получили отдельный `tests/test_policy.py`
- MCP tests очищены от stream/debug noise

### 6.9 Module 9: Docs and Handoff v1.2

Что внутри:

- `00_START_HERE_AUTOSTOP_CRM.md`
- `PROJECT_HANDOFF.md`
- `MASTER-PLAN.md`
- `README.md`

Роль:

- держать архитектуру, рабочую точку входа и текущий operational context в актуальном виде

## 7. AI-контур: отдельная карта

### 7.1 AI-архитектура

```text
Trigger
  -> context read
  -> evidence build
  -> scenario selection
  -> tool execution
  -> patch build
  -> write
  -> verify
  -> follow-up or finish
```

### 7.2 Что сейчас реально улучшено

- `AgentControlService` стал аккуратнее нормализовать limit-параметры и честнее показывать scheduler diagnostics
- `ToolPolicyEngine` стал устойчивее к mixed-case tool names, дублям и unknown scenario chain values
- `AgentToolExecutor` теперь нормализует имена tools в lower-case при dispatch
- `AutomotiveLookupService` использует per-run cache для повторных VIN/parts/price запросов

### 7.3 Куда двигать AI дальше

Приоритетные AI-задачи:

1. Второй VIN source или VIN fallback path для европейских VIN.
2. Уточнение evidence-model вокруг partial VIN / parts enrichment.
3. Улучшение качества card autofill answer synthesis без нарушения patch-only discipline.
4. Дополнительные real-world проверки quick card prompts и follow-up traces.

## 8. Параллельные дорожки разработки

Чтобы несколько разработчиков могли работать параллельно, проект сейчас разумно делить так:

### Lane A: Server AI

- Module `6`
- основные файлы: `src/minimal_kanban/agent/*`, `tests/test_agent.py`, `tests/test_policy.py`
- focus: VIN fallback, scenario quality, follow-up tuning, external lookup reliability

### Lane B: MCP and transport

- Module `5`
- основные файлы: `src/minimal_kanban/mcp/*`, `tests/test_mcp.py`, `tests/test_mcp_main.py`
- focus: runtime stability, connector behavior, transport diagnostics

### Lane C: Browser UI and board ergonomics

- Module `7`
- основные файлы: `src/minimal_kanban/web_assets.py`, `tests/test_web_assets.py`
- focus: DnD polish, employees UX, touch/browser compatibility

### Lane D: Core CRM business behavior

- Modules `2`, `3`, `4`
- основные файлы: `src/minimal_kanban/services/*`, `src/minimal_kanban/api/server.py`, `tests/test_service.py`, `tests/test_api.py`
- focus: repair orders, employees, board state integrity

### Lane E: Operations and documentation

- Modules `1`, `8`, `9`
- основные файлы: `deploy.sh`, `scripts/*`, root docs
- focus: production parity, smoke-check quality, handoff docs

## 9. Текущее качество и уровень готовности

### Что выглядит зрелым

- базовый CRM runtime
- board core
- local API
- MCP surface
- production deployment flow
- server AI orchestration foundation

### Что находится в активной доработке

- AI quality and source reliability
- browser workspace ergonomics
- employees/payroll UX polish
- documentation freshness versus moving branch head

### Что остаётся риском

- production credential hygiene требует отдельного прохода
- часть handoff-доков отстаёт от текущего `HEAD`, если их не поддерживать после каждого рабочего дня
- AI quality всё ещё ограничена качеством внешних automotive sources

## 10. Фактическая рабочая карта репозитория

```text
minimal-kanban/
  00_START_HERE_AUTOSTOP_CRM.md
  PROJECT_HANDOFF.md
  MASTER-PLAN.md
  README.md
  API_GUIDE.md
  MCP_GUIDE.md
  deploy.sh
  docker-compose.yml
  main.py
  main_mcp.py
  main_agent.py
  scripts/
    run_dev.ps1
    run_mcp_server.ps1
    check_live_connector.py
    check_agent_runtime.py
  src/minimal_kanban/
    api/
    agent/
    mcp/
    printing/
    services/
    storage/
    ui/
    web_assets.py
  tests/
  docs/
```

## 11. Что обновлять при каждом заметном изменении

После заметного development-pass нужно обновлять:

1. текущий branch HEAD и verified state
2. список последних значимых changes по модулям
3. внутренние версии модулей, если был реальный шаг зрелости
4. active lanes и next steps
5. PDF-копию этого master-plan

## 12. Короткий practical summary

- Центральный производственный документ проекта теперь — `MASTER-PLAN.md`
- Главный интеллектуальный модуль — `Module 6: Server AI Contour v1.6`
- Главный UI-файл — `src/minimal_kanban/web_assets.py`
- Главный бизнес-центр — `src/minimal_kanban/services/card_service.py`
- Главный транспорт наружу — `Module 5: MCP Layer v1.4`
- При следующем AI-проходе наиболее ценный шаг — `VIN fallback / second source`
- При следующем UI-проходе — `column DnD / browser ergonomics recheck`
- При следующем ops-проходе — `credential hygiene + production parity verification`
