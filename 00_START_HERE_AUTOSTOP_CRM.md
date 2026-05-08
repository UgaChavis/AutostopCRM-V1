# AutoStop CRM: первый файл

Этот файл нужен только для быстрого входа в текущую ветку `autostopcrm-v1`.
Не считайте его журналом релизов: актуальное состояние всегда проверяется командами.

## Рабочая истина

- локальная папка: `C:\Users\9860606\Desktop\AutostopCRM\autostopcrm`
- ветка: `autostopcrm-v1`
- GitHub remote: `origin`
- production repo: `/opt/autostopcrm`
- CRM: `https://crm.autostopcrm.ru`
- MCP: `https://crm.autostopcrm.ru/mcp`
- перед релизной работой сверяйте local / GitHub / production через `docs/OPERATIONS_RUNBOOK.md`

## Что это за продукт

AutoStop CRM - рабочая CRM автосервиса:

- kanban-доска, карточки, колонки, архив, дедлайны, теги, вложения и заметки;
- клиенты, автомобили клиента, привязка карточек к клиенту и конкретной машине;
- заказ-наряды, работы, материалы, оплаты, печатные PDF;
- кассы, сотрудники, payroll;
- общая файловая папка мастерской;
- локальный HTTP API и MCP endpoint для внешних инструментов;
- Telegram AI Board Manager как основной owner-facing AI-контур.

Исторические имена `minimal_kanban`, `%APPDATA%\Minimal Kanban` и `Start Kanban.exe` остаются частью совместимости.

## Что читать

Минимальный порядок для агента:

1. `00_START_HERE_AUTOSTOP_CRM.md`
2. `PROJECT_HANDOFF.md`
3. `README.md`
4. `docs/OPERATIONS_RUNBOOK.md`
5. `MCP_GUIDE.md` или `API_GUIDE.md` только если задача про интеграции

Дополнительные документы открывайте только по задаче:

- `CHATGPT_CONNECTOR_SETUP.md` - подключение ChatGPT/MCP;
- `docs/TELEGRAM_AI_BOARD_MANAGER.md` - техническая карта Telegram AI;

## Основная архитектура

```text
Desktop/browser UI
  -> local HTTP API
  -> CardService + domain services
  -> JsonStore

ChatGPT / MCP client
  -> MCP server
  -> local HTTP API
  -> same business core

Telegram owner
  -> Telegram AI worker
  -> OpenAI + CRM tool registry
  -> local HTTP API
  -> read-back verification and audit
```

Ключевые файлы:

- `main.py`, `main_mcp.py`, `main_telegram_ai.py`
- `src/minimal_kanban/api/server.py`
- `src/minimal_kanban/services/card_service.py`
- `src/minimal_kanban/mcp/server.py`
- `src/minimal_kanban/web_assets.py`
- `src/minimal_kanban/telegram_ai/`

## AI-правила для агентов

- Основной новый AI-контур - Telegram AI Board Manager.
- Старый card-indicator/enrichment путь оставлен только для совместимости.
- Команда владельца `Приберись` - это процедура над существующими CRM tools, а не отдельный backend-tool.
- Порядок уборки: прочитать live-контекст, patch-only обновить подтверждённые поля, отдельно обновить `board_summary`, перечитать и проверить результат.
- Не двигайте и не архивируйте карточки во время уборки без отдельной явной команды.
- `description` хранит полные сведения; `board_summary` - короткое превью доски на 4-5 строк.
- В `board_summary` не переносить телефон, VIN, полное имя клиента, длинные жалобы или сырые диагностические дампы.

## Проверки

Быстрая ориентация:

```powershell
git status --short --branch
git rev-parse --short HEAD
git fetch origin autostopcrm-v1 --prune
git rev-parse --short origin/autostopcrm-v1
```

Локальная проверка после doc-only правок:

```powershell
python scripts\audit_localization.py
```

После изменений кода или UI используйте команды из `docs/OPERATIONS_RUNBOOK.md`.

## Политика документации

- Не добавляйте новые root-документы без причины.
- Не фиксируйте в docs точные commit IDs, количество tools и старые smoke-цифры как текущую истину.
- Если появился новый workflow, обновляйте один канонический документ, а не создавайте отдельную памятку.
- Старые планы, memory dumps и agent scratch docs удаляются после переноса полезной части в README, handoff, runbook, API или MCP guide.
