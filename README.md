# AutoStop CRM

AutoStop CRM - рабочая CRM автосервиса на ветке `autostopcrm-v1`.

В коде остаются исторические имена `minimal_kanban`, `%APPDATA%\Minimal Kanban` и `Start Kanban.exe`. Это совместимость, а не признак старого продукта.

## Возможности

- доска с карточками, колонками, архивом, тегами, дедлайнами, вложениями и заметками;
- клиентский справочник: физлица, ИП, ООО, организации, телефоны, реквизиты и автомобили;
- связь карточки с клиентом и конкретным автомобилем клиента;
- заказ-наряды, работы, материалы, статусы, оплаты и печатные PDF;
- кассы с компактным журналом движения денег, сотрудники и payroll;
- общая файловая папка мастерской с API/UI/MCP-доступом;
- локальный HTTP API для UI и интеграций;
- MCP endpoint для ChatGPT, Responses API и совместимых клиентов;
- Telegram AI Board Manager для owner-controlled операций через текст, голос и фото.

## Архитектура

```text
Desktop/browser UI
  -> local HTTP API
  -> CardService + domain services
  -> JsonStore

MCP client / ChatGPT
  -> MCP server
  -> local HTTP API
  -> same CardService

Telegram owner
  -> Telegram AI worker
  -> OpenAI + explicit CRM tool registry
  -> local HTTP API
  -> verify + audit
```

Главное правило: UI, MCP и Telegram AI не дублируют бизнес-логику, а идут через общий backend.

## Ключевые файлы

- `main.py` - desktop runtime.
- `main_mcp.py` - отдельный MCP runtime.
- `main_telegram_ai.py` - Telegram AI worker.
- `src/minimal_kanban/api/server.py` - HTTP API.
- `src/minimal_kanban/services/card_service.py` - основной бизнес-сервис.
- `src/minimal_kanban/storage/json_store.py` - JSON-хранилище.
- `src/minimal_kanban/mcp/server.py` - MCP tools.
- `src/minimal_kanban/telegram_ai/` - Telegram AI.
- `src/minimal_kanban/web_assets.py` - browser UI facade/export.
- `src/minimal_kanban/web_app_assets/assembler.py` - browser UI assembly, modal stack and cash journal UI.
- `deploy.sh`, `docker-compose.yml`, `Dockerfile` - production deploy.

## Локальная разработка

Рекомендуемый путь:

```powershell
.\scripts\setup_dev.ps1 -InstallGitHooks
.\scripts\doctor.ps1
.\scripts\run_checks.ps1
```

Запуск desktop-приложения:

```powershell
.\scripts\run_dev.ps1
```

MCP отдельно:

```powershell
.\scripts\run_mcp_server.ps1
```

Основной regression перед релизом:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -v
```

При изменении `src/minimal_kanban/web_assets.py`:

```powershell
python scripts\check_web_assets_js.py
```

При изменении browser UI, модальных цепочек, карточек, клиентов, касс,
сотрудников, файлов или заказ-нарядов:

```powershell
python scripts\browser_smoke.py
```

Browser smoke поднимает временный local runtime с synthetic данными и проверяет
privacy gate до входа оператора, board/card roundtrip, кассы и журнал, клиентов,
сотрудников, файлы, архив, заказ-наряды и закрытие вложенных модальных окон по
одной ступени назад.

При изменении русских UI/docs-текстов:

```powershell
python scripts\docs_audit.py --format text
python scripts\audit_localization.py
```

## Интеграции

### API

Локальный API по умолчанию: `http://127.0.0.1:41731`.
Детальная карта endpoint-групп: `API_GUIDE.md`.

### MCP

MCP по умолчанию: `http://127.0.0.1:41831/mcp`.
Точный runtime-набор tools не фиксируется в документации: проверяйте live `tools/list`, `scripts/check_live_connector.py` и `src/minimal_kanban/mcp/server.py`.
Manager tools зависят от доступности optional `AutostopManager`; например
`estimate_repair_work_cost` может быть в production и отсутствовать локально.
При release-сверке сравнивайте имена tools и отдельно отмечайте optional
manager layer, а не только общий count.

`autofill_vehicle_data`, `autofill_repair_order` и `cleanup_card_content` остаются API/UI/compatibility путями и не должны считаться обычными MCP tools.

### ChatGPT connector

Пользовательский файл подключения должен оставаться под именем `CHATGPT_CONNECTOR_SETUP.md`: его копируют release/runtime-пути и проверяют тесты.

### Кассы и журнал

Операторский UI касс сейчас строится вокруг журнала движения денег. Сверка не
показывается пользователю как отдельный раздел: `finance_audit` остаётся
backend/API/CLI диагностикой для read-only отчётов и owner-approved safe-fixes.

Журнал в UI:

- показывает операции как основную таблицу;
- держит остатки касс свернутыми по умолчанию;
- рендерит длинные журналы батчами;
- объединяет новые и legacy пары перемещений в одну строку `касса -> касса`;
- не показывает диагностические метки вроде `нет пары` в обычной рабочей
  строке.

### Manager knowledge and Obsidian

Для owner-facing AI/manager работы используйте AutostopManager и AutoStop
Obsidian vault как слой знаний поверх CRM:

- cloud vault: `C:\Users\User\Мой диск\Obsidian CRM\AutostopCRM`
- local mirror: `C:\Users\User\Desktop\Obsidian CRM\AutostopCRM`
- open first: `Home.md`, затем `80_Codex\Codex interaction.md`

CRM остаётся источником истины для live cards, clients, vehicles, repair
orders, files, payments и cashboxes. Obsidian хранит только рабочие заметки,
playbook-и, маршруты и безопасные выводы. Для менеджерской аналитики Obsidian
может хранить только агрегированные snapshots: загрузку доски, cashbox
overview, repair-order counts, client-quality signals и metadata общих файлов.
Полные клиентские базы, телефоны, VIN/госномера, кассовые журналы и полный
текст заказ-нарядов остаются в CRM, если владелец отдельно не подтвердил
конкретный cloud-export.

### Telegram AI

Основной AI-контур - `autostopcrm-telegram-ai`.
Он работает через long polling, не открывает публичный порт, пишет в CRM только через local API и проверяет write-actions read-back.

## Важные AI-контракты

- `Приберись` - агентская процедура над CRM tools, а не отдельная backend-команда.
- Routine cleanup не двигает и не архивирует карточки без отдельного явного запроса.
- `description` хранит подробности и восстановимость.
- `board_summary` хранит короткое превью доски на 4-5 строк.
- После изменения `title`, `description`, `tags` или `vehicle_profile` агент отдельно обновляет `board_summary`.
- VIN/chassis/frame enrichment заполняет `engine_model`, `gearbox_model`, `drivetrain` только по подтверждённым источникам и не перетирает ручные поля.

## Данные

Локально:

- `%APPDATA%\Minimal Kanban\state.json`
- `%APPDATA%\Minimal Kanban\settings.json`
- `%APPDATA%\Minimal Kanban\attachments`
- `%APPDATA%\Minimal Kanban\repair-orders`
- `%APPDATA%\Minimal Kanban\shared-files`
- `%APPDATA%\Minimal Kanban\audit-archive`
- `%APPDATA%\Minimal Kanban\logs\minimal-kanban.log`

В Docker:

- host data: `./data`
- container data: `/root/.minimal-kanban`

Не коммитьте runtime state, snapshots, SQLite/JSON data, attachments, secrets или credentials.

`audit-archive` хранит полные `before/after` для тяжёлых audit events, которые
в активном `state.json` остаются компактными. Перед обслуживанием размера state
используйте read-only `scripts/state_size_report.py`, затем
`scripts/compact_audit_events.py --dry-run`; live compaction выполняется только
с backup.

Для ручного QA на realistic data используйте dated sandbox вне repo, например
`%USERPROFILE%\Desktop\AutostopCRM-data-snapshots\prod-2026-05-19`, и запускайте
CRM с переопределённым `%APPDATA%`. Такая копия не является live-sync и не
должна попадать в Git, docs, Obsidian или отчёты без маскировки персональных и
финансовых данных.

## Документация

Канонический минимум:

- `README.md` - быстрый вход и карта продукта.
- `docs/OPERATIONS_RUNBOOK.md` - sync, deploy, verification.
- `MCP_GUIDE.md` - MCP workflows и safety.
- `API_GUIDE.md` - endpoint-группы и контракт API.

Открывать только по задаче:

- `CHATGPT_CONNECTOR_SETUP.md` - ChatGPT/MCP подключение.
- `AUTOSTOPCRM_FULL_INSTRUCTION.txt` - короткая server-side памятка, которую копирует `deploy.sh`.

Политика: не плодить новые docs. Старые планы, исторические отчёты и agent scratch-файлы удаляются после переноса полезной части в один из документов выше.
