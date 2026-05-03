# Руководство по MCP

Этот документ описывает отдельный MCP server, который добавлен поверх текущей kanban-доски.

## Зачем нужен MCP server

MCP server нужен для прямого tool-based доступа к доске из:

- ChatGPT app / Developer mode
- Responses API
- собственных клиентов, умеющих работать с MCP

MCP server не двигает мышь и не эмулирует UI.

Он работает правильно по архитектуре:

```text
MCP tool call
  ->
адаптер MCP
  ->
локальный API доски
  ->
CardService
  ->
JsonStore
```

## Что именно добавлено

Ключевые файлы:

- `src/minimal_kanban/mcp/client.py`
- `src/minimal_kanban/mcp/server.py`
- `src/minimal_kanban/mcp/runtime.py`
- `src/minimal_kanban/mcp/auth.py`
- `src/minimal_kanban/mcp/main.py`
- `scripts/run_mcp_server.ps1`
- `main_mcp.py`

## Опциональная память менеджера AutostopManager

Если рядом с CRM-репозиторием доступен проект `AutostopManager`, CRM MCP server автоматически добавляет headless memory-tools в тот же MCP endpoint:

- `remember`
- `recall`
- `add_manager_task`
- `today_context`
- `manager_journal`

Это не дублирует CRM. Карточки, клиенты, автомобили, заказ-наряды и кассы остаются в AutoStop CRM. AutostopManager хранит только долговременную память менеджера: факты, договоренности, личные дела, аренду, напоминания, правила и журнал решений.

Путь можно задать явно:

```text
AUTOSTOP_MANAGER_PATH=/opt/AutostopManager
```

## Доступные MCP tools

Базовый CRM runtime-tool inventory после добавления скрытой AI-краткой сути карточек: `70` tools. Если подключен `AutostopManager`, в том же endpoint дополнительно доступны `5` memory-tools. Production smoke после этого изменения должен видеть `75` tools именно из-за этой связки.

Полный статический справочник команд больше не ведётся отдельным файлом: он быстро устаревает. Источник правды — `src/minimal_kanban/mcp/server.py`, live `tools/list`, этот guide и MCP-тесты.

Для больших клиентских справочников не тянуть лишние данные:

- `list_clients(limit=50..100, include_stats=false)` для быстрого обзора
- `search_clients(query=...)` для точечного поиска
- `get_client(client_id)` для выбранного профиля
- `get_client_stats(client_id)` для легкой статистики
- `suggest_clients_for_card(card_id)` для подбора клиента из карточки
- `upsert_client_vehicle(client_id, vehicle=...)` для добавления или обновления машины клиента
- `delete_client_vehicle(client_id, client_vehicle_id, unlink_cards=true)` для удаления машины из профиля клиента без удаления карточек

В большом справочнике `search_clients` сначала проверяет ФИО, телефоны, реквизиты и сохраненные `vehicles[]` профиля. Связанная история карточек используется как fallback, чтобы не делать тяжелый обход по всем клиентам при каждом вводе. В `vehicles_preview[]` возвращается стабильный `id` автомобиля; если машина известна, передавайте его в `link_card_to_client(..., client_vehicle_id=...)`.

## Как выбирать команды без лишнего payload

Сейчас у коннектора уже достаточно инструментов, поэтому полезнее не добавлять новые, а вызывать существующие в правильном порядке.

### Легкие команды для старта и повседневной работы

- `ping_connector`
- `get_connector_identity`
- `bootstrap_context`
- `get_runtime_status`
- `get_board_context`
- `review_board`
- `list_columns`
- `search_cards`
- `get_cards(compact=true)`
- `search_clients`
- `list_clients(limit=50)`
- `list_shared_files`

### Команды для точечного чтения

- `get_card_context`
- `get_card`
- `list_card_attachments`
- `get_card_attachment`
- `read_card_attachment`
- `get_card_log`
- `get_repair_order`
- `list_repair_orders`
- `get_repair_order_text`
- `list_overdue_cards`
- `list_archived_cards`
- `list_clients`
- `search_clients`
- `get_client`
- `get_client_stats`
- `suggest_clients_for_card`
- `list_shared_files`
- `get_shared_file_info`
- `download_shared_file`

### Тяжелые команды, которые стоит вызывать редко

- `get_gpt_wall`
- `get_board_content`
- `get_board_snapshot`
- `get_board_events`
- `get_card` в полном режиме
- `get_card_context` с длинной историей

Правило по умолчанию:

1. сначала брать короткий диагностический инструмент;
2. потом узкий поиск или фокусный read;
3. только после этого открывать тяжелый экспорт.

Для тяжелых инструментов держите лимиты как можно ниже:

- `get_gpt_wall`: `include_archived=false`, `event_limit=10..20`
- `get_board_content`: `include_archived=false`, `view_mode=agent`
- `get_board_events`: `event_limit=20..50` для обычной работы
- `get_board_snapshot`: `compact=true`, `include_archive=false`, `archive_limit` минимально нужный
- `get_card_context`: `event_limit=5..20`, `include_repair_order_text=false`, если текст не нужен

### Служебные и диагностические

- `ping_connector`
- `get_connector_identity`
- `bootstrap_context`
- `get_runtime_status`

### Доска и карточки

- `list_columns`
- `create_column`
- `rename_column`
- `delete_column`
- `get_cards`
- `get_card`
- `get_card_context`
- `list_card_attachments`
- `get_card_attachment`
- `read_card_attachment`
- `get_board_snapshot`
- `get_board_context`
- `review_board`
- `get_board_content`
- `get_board_events`
- `get_gpt_wall`
- `get_card_log`
- `list_archived_cards`
- `search_cards`
- `list_overdue_cards`
- `create_card`
- `update_card`
- `set_card_board_summary`
- `list_clients`
- `search_clients`
- `get_client`
- `get_client_stats`
- `create_client`
- `update_client`
- `delete_client`
- `link_card_to_client`
- `upsert_client_vehicle`
- `delete_client_vehicle`
- `unlink_card_from_client`
- `suggest_clients_for_card`
- `set_card_deadline`
- `set_card_indicator`
- `move_card`
- `mark_card_ready`
- `bulk_move_cards`
- `archive_card`
- `restore_card`

### Краткая суть карточки на доске

- `set_card_board_summary(card_id, summary, actor_name=None)` обновляет скрытую AI-краткую суть карточки.
- Команда нужна, когда агент прочитал карточку через `get_card_context` / `get_card` и должен заменить техническое превью на доске понятной рабочей выжимкой.
- Это write-action: она идет через local API и `CardService`, пишет audit/journal event `board_summary_changed`, но не меняет `title` и `description`.
- Ограничение: максимум `5` непустых строк и `560` символов.
- Если карточку потом изменили обычным способом, в карточке появляется `board_summary_stale=true`; агент должен прочитать актуальный контекст и вызвать `set_card_board_summary` заново.

Рекомендуемый формат `summary`:

```text
Что сейчас: коротко, что происходит с машиной/задачей.
Стадия: диагностика / согласование / ожидание / ремонт / выдача.
Следующее действие: один понятный шаг для оператора.
Важно: только рабочий риск или блокер, без VIN и лишних техданных.
```

### Общие файлы

- `list_shared_files`
- `get_shared_file_info`
- `download_shared_file`
- `upload_shared_file`
- `delete_shared_file`
- `update_shared_file_position`

Правила:

- MCP работает с файлами только через backend/API слой CRM.
- `download_shared_file` возвращает base64 только если файл укладывается в заданный `max_base64_bytes`; для крупных файлов остаётся metadata/download path.
- `upload_shared_file` принимает `content_base64` и использует те же backend-проверки имени, запрещённых расширений и общего лимита 500 МБ.
- `delete_shared_file` является destructive write-action.

### Вложения карточек

- `list_card_attachments`
- `get_card_attachment`
- `read_card_attachment`

Правильный порядок чтения вложений:

1. `get_card_context(card_id)` или `get_card(card_id)`, чтобы понять, есть ли вложения.
2. `list_card_attachments(card_id)`, чтобы получить безопасный список файлов без байтов.
3. `get_card_attachment(card_id, attachment_id)`, если нужны размер, `sha256`, тип и download path.
4. `read_card_attachment(card_id, attachment_id, mode="preview"|"text"|"base64")`, только для выбранного файла.

Ограничения:

- `read_card_attachment` возвращает bounded text для TXT/DOCX/XLSX и best-effort text для простых PDF.
- Изображения не проходят OCR внутри CRM; инструмент возвращает размеры и может отдать `base64/data_url`, если включить `include_base64=true` или `mode="base64"`.
- Не включайте base64 по умолчанию. Для изображений используйте его только когда агент действительно будет анализировать файл vision-моделью.
- Держите `max_chars` и `max_base64_bytes` минимально достаточными.

### Клиенты

- `list_clients`
- `search_clients`
- `get_client`
- `get_client_stats`
- `create_client`
- `update_client`
- `delete_client`
- `link_card_to_client`
- `upsert_client_vehicle`
- `delete_client_vehicle`
- `unlink_card_from_client`
- `suggest_clients_for_card`

Правильный порядок работы с клиентами:

1. Для существующей карточки сначала вызвать `suggest_clients_for_card(card_id)`.
2. Если подсказка не дала уверенного совпадения, использовать `search_clients(query=...)`.
3. Если клиента нет, создавать его через `create_client`.
4. Если выбрана существующая машина, привязать карточку через `link_card_to_client(card_id, client_id, client_vehicle_id=..., sync_fields=true, sync_vehicle_fields=true)`.
5. Если это новый автомобиль существующего клиента, использовать `link_card_to_client(card_id, client_id, create_vehicle_from_card=true)` или сначала `upsert_client_vehicle`, затем привязку.
6. Если оператор просит исправить машину клиента, использовать `upsert_client_vehicle` с `client_vehicle_id`; VIN/госномер/модель синхронизируются в связанных карточках.
7. Если оператор просит удалить машину из профиля клиента, использовать `delete_client_vehicle(..., unlink_cards=true)`: карточки и заказ-наряды не удаляются.
8. Проверить профиль через `get_client(client_id)` или `get_client_stats(client_id)`.

Для больших массивов клиентов:

- не запрашивать весь справочник одним тяжелым списком без необходимости;
- сначала использовать `search_clients` или `suggest_clients_for_card`;
- `list_clients` держать компактным и без статистики, если нужен только обзор;
- для тысяч записей работать батчами, а не одной большой выборкой.

Правила:

- Привязка клиента к карточке необязательна: карточка может жить с ручным именем клиента без записи в справочнике.
- Не создавать дубль клиента, пока не выполнен `search_clients` или `suggest_clients_for_card`.
- `search_clients(query=...)` ищет по ФИО/части ФИО, телефону, реквизитам, автомобилю, госномеру и VIN из связанных карточек и сохраненного профиля клиента.
- Российские телефоны с префиксом `+7` и `8` сопоставляются как один номер для поиска, подсказок и истории.
- `link_card_to_client` по умолчанию дозаполняет только пустые клиентские поля карточки и заказ-наряда.
- `overwrite_card_fields=true` использовать только после явного подтверждения пользователя.
- `delete_client` по умолчанию блокирует удаление связанного клиента; `allow_linked=true` использовать только после явного подтверждения.
- `delete_client_vehicle` удаляет только автомобиль клиента; карточки остаются, а связь с конкретной машиной очищается.
- Для организаций использовать `client_type="ooo"|"ip"|"company"` и поля реквизитов: `inn`, `kpp`, `ogrn`, счета, банк, адреса и контактное лицо.
- Если клиент импортируется со своим автопарком, `create_client`/`update_client` могут принимать `vehicles[]` с полями `id`, `vehicle`, `brand`, `model`, `vin`, `license_plate`, `year`, `mileage`, `engine_model`, `gearbox_model`, `drivetrain`.
- `client_id` связывает карточку с клиентом, `client_vehicle_id` связывает карточку с конкретной машиной клиента.
- При команде оператора “это тот же клиент, но новая машина” используйте `create_vehicle_from_card=true` или `upsert_client_vehicle`; не создавайте дубль клиента.

### Sticky notes

- `create_sticky`
- `update_sticky`
- `move_sticky`
- `delete_sticky`

### Repair orders

- `list_repair_orders`
- `get_repair_order`
- `get_repair_order_text`
- `update_repair_order`
- `set_repair_order_status`
- `mark_card_ready`
- `replace_repair_order_works`
- `replace_repair_order_materials`

### Cashboxes

- `list_cashboxes`
- `get_cash_journal` - structured cashbox journal: `entries`, `days`, `weeks`, `months`, `totals`, plus Markdown for human review/download.
- `get_cashbox`
- `create_cashbox`
- `delete_cashbox`
- `create_cash_transaction`

### Board settings

- `update_board_settings`

### Что не входит в MCP runtime

- `autofill_vehicle_data`
- `autofill_repair_order`

Эти автозаполнения остаются API/UI-only surface и не регистрируются в MCP runtime.

## Как MCP server выбирает backend

Порядок работы такой:

1. Проверяет переменную `MINIMAL_KANBAN_BOARD_API_URL`.
2. Если она задана, работает с этим локальным API.
3. Если переменная не задана, пытается найти уже работающий API доски на стандартных портах.
4. Если API не найден, поднимает скрытый backend сам и использует его.

Это сделано для двух сценариев:

- приложение доски уже открыто, и MCP должен менять живую доску
- приложение не открыто, но нужно управлять той же доской через то же хранилище

## Как запустить MCP server

Самый простой способ на Windows:

```powershell
.\scripts\run_mcp_server.ps1
```

Прямой Python-запуск:

```powershell
python .\main_mcp.py
```

или:

```powershell
python -m minimal_kanban.mcp.main
```

## Какой адрес использует MCP server

По умолчанию:

- host: `127.0.0.1`
- port: `41831`
- path: `/mcp`

Полный локальный URL по умолчанию:

```text
http://127.0.0.1:41831/mcp
```

## Переменные окружения MCP

### Базовые

- `MINIMAL_KANBAN_MCP_HOST`
- `MINIMAL_KANBAN_MCP_PORT`
- `MINIMAL_KANBAN_MCP_PORT_FALLBACK_LIMIT`
- `MINIMAL_KANBAN_MCP_PATH`

### Подключение к backend

- `MINIMAL_KANBAN_BOARD_API_URL`
- `MINIMAL_KANBAN_API_BEARER_TOKEN`

### Защита MCP server

- `MINIMAL_KANBAN_MCP_BEARER_TOKEN`
- `MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL`

## Авторизация

### Что уже реализовано

В коде уже есть простой bearer token режим:

- локальный API можно защитить через `MINIMAL_KANBAN_API_BEARER_TOKEN`
- сам MCP server можно защитить через `MINIMAL_KANBAN_MCP_BEARER_TOKEN`

Если `MINIMAL_KANBAN_MCP_BEARER_TOKEN` задан, то к MCP server нужно обращаться с:

```http
Authorization: Bearer ваш_секрет
```

### Что важно понимать

Такой bearer token режим подходит для:

- локальной разработки
- тестов
- прямой интеграции через Responses API
- собственных клиентов

Для публичного подключения в ChatGPT workspace используется встроенный OAuth/dynamic client registration слой, если публичный MCP endpoint работает в bearer-token режиме. Legacy bearer token остаётся полезным для Responses API, локальной разработки и собственных клиентов.

## Как тестировать MCP локально

1. Запустите MCP server:

```powershell
.\scripts\run_mcp_server.ps1
```

2. Убедитесь, что локальный URL отвечает:

```text
http://127.0.0.1:41831/mcp
```

3. Прогоните автотесты:

```powershell
python -m unittest discover -s .\tests -v
```

Отдельный интеграционный тест MCP уже добавлен:

- `tests/test_mcp.py`

## Поведение инструмента `set_card_indicator`

В текущем MVP лампочка карточки вычисляется из дедлайна, а не хранится отдельно.

Поэтому `set_card_indicator` сделан как служебный инструмент:

- `green` — передвигает дедлайн так, чтобы времени было достаточно
- `yellow` — передвигает дедлайн так, чтобы карточка вошла в warning-зону
- `red` — делает карточку просроченной

Это сохранено намеренно, чтобы не ломать текущую минималистичную countdown-модель карточки.

## Что проверено

Реально прогнано:

- старт MCP server
- подключение MCP client к `streamable-http`
- `tools/list`
- `create_column`
- `create_card`
- `update_card`
- `set_card_board_summary`
- `move_card`
- `set_card_indicator`
- `archive_card`
- `list_overdue_cards`
- структурированные ошибки для невалидных случаев

Полная проверка current runtime tool surface должна опираться на `tools/list` в живом MCP runtime и на `tests/test_mcp.py`.

## Ограничения текущей реализации

- для прямого подключения в ChatGPT app production-grade вариантом всё ещё остаётся OAuth
- текущий bearer token режим не заменяет полноценный OAuth flow
- при интеграционных тестах MCP SDK оставляет шумные shutdown warning-и в stdout/stderr, но сами тесты проходят успешно

## Как MCP связан с окном настроек

В приложении добавлено отдельное окно `Настройки интеграции`, которое сохраняет параметры MCP в `%APPDATA%\Minimal Kanban\settings.json`.

Какие поля использует MCP server:

- включение MCP
- host MCP
- port MCP
- path MCP
- публичный HTTPS-адрес или адрес туннеля
- bearer token MCP
- bearer token локального API
- host и port локального API доски

Поведение:

- при запуске `main_mcp.py` или `scripts/run_mcp_server.ps1` MCP server сначала читает `settings.json`
- если нужные `MINIMAL_KANBAN_*` переменные окружения заданы явно, они имеют приоритет над сохранёнными настройками
- если локальный API по адресу из настроек не найден, MCP server пытается найти уже работающий backend или поднять встроенный локальный API сам

Кнопка `Проверить соединение` в окне настроек для MCP делает не простой ping, а полноценную проверку:

1. открывает streamable HTTP MCP-подключение
2. выполняет `initialize`
3. вызывает `tools/list`

Это позволяет сразу увидеть, что endpoint действительно отдаёт MCP tools, а не просто отвечает как обычный веб-сервер.
