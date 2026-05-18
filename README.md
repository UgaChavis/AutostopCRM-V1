# AutoStop CRM

AutoStop CRM - рабочая CRM автосервиса на ветке `autostopcrm-v1`.

В коде остаются исторические имена `minimal_kanban`, `%APPDATA%\Minimal Kanban` и `Start Kanban.exe`. Это совместимость, а не признак старого продукта.

## Возможности

- доска с карточками, колонками, архивом, тегами, дедлайнами, вложениями и заметками;
- клиентский справочник: физлица, ИП, ООО, организации, телефоны, реквизиты и автомобили;
- связь карточки с клиентом и конкретным автомобилем клиента;
- заказ-наряды, работы, материалы, статусы, оплаты и печатные PDF;
- кассы, кассовый журнал, сотрудники и payroll;
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
- `src/minimal_kanban/web_assets.py` - browser UI.
- `src/minimal_kanban/web_app_assets/assembler.py` - browser UI assembly and modal workflow logic.
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
board/card roundtrip, кассы, журнал, клиентов, сотрудников, файлы, заказ-наряды
и закрытие вложенных модальных окон по одной ступени назад.

При изменении русских UI/docs-текстов:

```powershell
python scripts\audit_localization.py
```

## Интеграции

### API

Локальный API по умолчанию: `http://127.0.0.1:41731`.
Детальная карта endpoint-групп: `API_GUIDE.md`.

### MCP

MCP по умолчанию: `http://127.0.0.1:41831/mcp`.
Точный runtime-набор tools не фиксируется в документации: проверяйте live `tools/list`, `scripts/check_live_connector.py` и `src/minimal_kanban/mcp/server.py`.

`autofill_vehicle_data`, `autofill_repair_order` и `cleanup_card_content` остаются API/UI/compatibility путями и не должны считаться обычными MCP tools.

### ChatGPT connector

Пользовательский файл подключения должен оставаться под именем `CHATGPT_CONNECTOR_SETUP.md`: его копируют release/runtime-пути и проверяют тесты.

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
- `%APPDATA%\Minimal Kanban\logs\minimal-kanban.log`

В Docker:

- host data: `./data`
- container data: `/root/.minimal-kanban`

Не коммитьте runtime state, snapshots, SQLite/JSON data, attachments, secrets или credentials.

## Документация

Канонический минимум:

- `00_START_HERE_AUTOSTOP_CRM.md` - быстрый вход.
- `PROJECT_HANDOFF.md` - текущее состояние и правила работы.
- `docs/OPERATIONS_RUNBOOK.md` - sync, deploy, verification.
- `MCP_GUIDE.md` - MCP workflows и safety.
- `API_GUIDE.md` - endpoint-группы и контракт API.

Открывать только по задаче:

- `CHATGPT_CONNECTOR_SETUP.md` - ChatGPT/MCP подключение.
- `docs/TELEGRAM_AI_BOARD_MANAGER.md` - техническая карта Telegram AI.
- `AUTOSTOPCRM_FULL_INSTRUCTION.txt` - короткая server-side памятка, которую копирует `deploy.sh`.

Политика: не плодить новые docs. Старые планы, исторические отчёты и agent scratch-файлы удаляются после переноса полезной части в один из документов выше.
