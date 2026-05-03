# Руководство по API

Локальный API предназначен для UI-приложения, MCP-адаптера и внешних локальных интеграций.

## Базовая информация

- протокол: HTTP
- формат: JSON request / JSON response
- адрес по умолчанию: `http://127.0.0.1:41731`
- путь health-check: `GET /api/health`

Если занят стартовый порт, API автоматически переходит на следующий свободный порт в диапазоне fallback.

## Авторизация

По умолчанию локальный API работает без bearer token.

Если задана переменная окружения:

```text
MINIMAL_KANBAN_API_BEARER_TOKEN=ваш_секрет
```

тогда все endpoint-ы, кроме `GET /api/health`, требуют заголовок:

```http
Authorization: Bearer ваш_секрет
```

## Общий формат ответа

Успех:

```json
{
  "ok": true,
  "data": {
    "card": {
      "id": "..."
    }
  },
  "error": null,
  "meta": {
    "request_id": "uuid",
    "timestamp": "2026-03-24T10:00:00+00:00"
  }
}
```

Ошибка:

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "validation_error",
    "message": "Поле deadline.hours должно быть в диапазоне от 0 до 23.",
    "details": {
      "field": "deadline.hours"
    }
  },
  "meta": {
    "request_id": "uuid",
    "timestamp": "2026-03-24T10:00:00+00:00"
  }
}
```

## Модель карточки

Объект карточки содержит:

```json
{
  "id": "uuid",
  "title": "Позвонить клиенту",
  "description": "Подтвердить встречу",
  "column": "inbox",
  "archived": false,
  "created_at": "2026-03-24T10:00:00+00:00",
  "updated_at": "2026-03-24T10:00:00+00:00",
  "deadline_timestamp": "2026-03-25T10:00:00+00:00",
  "client_id": "",
  "client_vehicle_id": "",
  "board_summary": "Что сейчас: согласовать диагностику.\nСледующее действие: позвонить клиенту.",
  "board_summary_updated_at": "2026-03-24T10:05:00+00:00",
  "board_summary_source": "mcp",
  "board_summary_stale": false,
  "remaining_seconds": 86395,
  "remaining_display": "0д 23:59:55",
  "status": "ok",
  "indicator": "green"
}
```

### Поля карточки

- `id` — идентификатор карточки
- `title` — заголовок
- `description` — описание
- `column` — id столбца
- `archived` — архивирована ли карточка
- `deadline_timestamp` — абсолютный UTC deadline
- `client_id` — необязательная связь карточки с записью клиента
- `client_vehicle_id` — необязательная связь карточки с конкретным автомобилем внутри профиля клиента
- `board_summary` — скрытая краткая суть для превью карточки на доске, заполняется агентом/API/MCP, а не ручным UI-полем
- `board_summary_updated_at` — когда агент обновил краткую суть
- `board_summary_source` — нормализованный источник обновления (`mcp`, `api`, `ui` или `system`)
- `board_summary_stale` — `true`, если содержимое карточки изменилось после последнего обновления краткой сути
- `remaining_seconds` — оставшееся время
- `remaining_display` — готовая строка для UI
- `status` — `ok`, `warning`, `expired`
- `indicator` — `green`, `yellow`, `red`

Важно:

- `indicator` не хранится отдельно, а вычисляется из дедлайна
- endpoint `set_card_indicator` меняет дедлайн так, чтобы карточка получила нужный цвет лампочки
- `board_summary` не заменяет `description`: полное описание остается источником восстановления, журнал карточки фиксирует обновления краткой сути отдельно

## Модель deadline

Формат входного `deadline`:

```json
{
  "days": 1,
  "hours": 2,
  "minutes": 0,
  "seconds": 0
}
```

Ограничения:

- `days`: `0..365`
- `hours`: `0..23`
- `minutes`: `0..59`
- `seconds`: `0..59`
- итоговое время должно быть больше `0`

## Endpoint-ы

### `GET /api/health`

Назначение: проверить, что API поднялся.

Пример ответа:

```json
{
  "ok": true,
  "data": {
    "status": "ok",
    "base_url": "http://127.0.0.1:41731",
    "auth_required": false
  },
  "error": null,
  "meta": {
    "request_id": "uuid",
    "timestamp": "2026-03-24T10:00:00+00:00"
  }
}
```

### `GET /api/list_columns`

Назначение: получить все столбцы доски.

Ответ:

```json
{
  "ok": true,
  "data": {
    "columns": [
      {"id": "inbox", "label": "Входящие"},
      {"id": "in_progress", "label": "В работе"},
      {"id": "done", "label": "Готово"}
    ]
  }
}
```

### `POST /api/create_column`

Назначение: создать новый столбец.

Запрос:

```json
{
  "label": "Блокеры"
}
```

### `POST /api/get_cards`

Назначение: получить карточки.

Запрос:

```json
{
  "include_archived": false
}
```

### `POST /api/list_overdue_cards`

Назначение: получить только просроченные карточки.

Запрос:

```json
{
  "include_archived": false
}
```

### `POST /api/get_card`

Назначение: получить одну карточку.

Запрос:

```json
{
  "card_id": "a4d4d10a-0a5a-4d7f-99e1-4d7ddbc6b0a4"
}
```

### `POST /api/create_card`

Назначение: создать карточку.

Запрос:

```json
{
  "title": "Подготовить созвон",
  "description": "Собрать вопросы",
  "column": "inbox",
  "deadline": {
    "days": 0,
    "hours": 6
  }
}
```

### `POST /api/update_card`

Назначение: обновить title, description или deadline.

Запрос:

```json
{
  "card_id": "a4d4d10a-0a5a-4d7f-99e1-4d7ddbc6b0a4",
  "title": "Подготовить созвон с клиентом",
  "description": "Добавить повестку",
  "deadline": {
    "days": 0,
    "hours": 8
  }
}
```

### `POST /api/set_card_board_summary`

Назначение: обновить скрытую AI-краткую суть карточки для отображения на доске.

Это служебный endpoint для агента/MCP/Telegram. Он не меняет `title` и `description`, чтобы операторские данные и восстановление через журнал не терялись. UI карточки на доске берет `board_summary`, если поле заполнено, и только потом падает обратно на `description_preview`.

Ограничения:

- максимум `5` непустых строк
- максимум `560` символов
- архивные карточки не изменяются
- после обычного изменения карточки `board_summary_stale` становится `true`, пока агент не обновит краткую суть заново

Запрос:

```json
{
  "card_id": "a4d4d10a-0a5a-4d7f-99e1-4d7ddbc6b0a4",
  "summary": "Что сейчас: проверить жалобу по тормозам.\nСтадия: диагностика.\nСледующее действие: согласовать работы."
}
```

Ответ содержит обновленную карточку и метаданные:

```json
{
  "ok": true,
  "data": {
    "card": {
      "id": "a4d4d10a-0a5a-4d7f-99e1-4d7ddbc6b0a4",
      "board_summary": "Что сейчас: проверить жалобу по тормозам.\nСтадия: диагностика.\nСледующее действие: согласовать работы.",
      "board_summary_source": "mcp",
      "board_summary_stale": false
    },
    "meta": {
      "changed": true,
      "summary_lines": 3,
      "board_summary_stale": false
    }
  }
}
```

### `POST /api/set_card_deadline`

Назначение: поменять только дедлайн карточки.

Запрос:

```json
{
  "card_id": "a4d4d10a-0a5a-4d7f-99e1-4d7ddbc6b0a4",
  "deadline": {
    "minutes": 30
  }
}
```

### `POST /api/set_card_indicator`

Назначение: выставить лампочку карточки в `green`, `yellow` или `red`.

Важно:

- endpoint не хранит отдельное поле `indicator`
- endpoint меняет deadline так, чтобы вычисляемая лампочка стала нужного цвета

Запрос:

```json
{
  "card_id": "a4d4d10a-0a5a-4d7f-99e1-4d7ddbc6b0a4",
  "indicator": "yellow"
}
```

### `POST /api/move_card`

Назначение: переместить карточку в другой столбец.

Запрос:

```json
{
  "card_id": "a4d4d10a-0a5a-4d7f-99e1-4d7ddbc6b0a4",
  "column": "done"
}
```

### `POST /api/archive_card`

Назначение: архивировать карточку.

Запрос:

```json
{
  "card_id": "a4d4d10a-0a5a-4d7f-99e1-4d7ddbc6b0a4"
}
```

## Клиенты

Клиентский модуль хранит справочник физических лиц, ИП, ООО и организаций. Связь с карточкой необязательна: карточка может содержать ручные поля клиента без создания профиля.

Поиск и привязка клиентов считают российские телефоны с префиксом `+7` и `8` одним номером, чтобы история не терялась из-за разного формата записи.

### Модель клиента

```json
{
  "id": "uuid",
  "client_type": "person",
  "last_name": "Иванов",
  "first_name": "Иван",
  "middle_name": "Иванович",
  "display_name": "",
  "phone": "+79130000000",
  "phones": ["+79130000000"],
  "email": "",
  "comment": "",
  "legal_name": "",
  "short_name": "",
  "inn": "",
  "kpp": "",
  "ogrn": "",
  "checking_account": "",
  "bank_name": "",
  "bik": "",
  "correspondent_account": "",
  "legal_address": "",
  "actual_address": "",
  "contact_person": "",
  "contact_position": "",
  "vehicles": [
    {
      "vehicle": "Toyota Camry",
      "brand": "Toyota",
      "model": "Camry",
      "vin": "JTDBE32K620654321",
      "license_plate": "А123ВС124",
      "year": "2017"
    }
  ]
}
```

Типы:

- `person` — физическое лицо.
- `ip` — индивидуальный предприниматель.
- `ooo` — ООО.
- `company` — другая организация.

### `POST /api/list_clients`

Назначение: получить список клиентов.

Запрос:

```json
{
  "limit": 100,
  "include_stats": true
}
```

### `POST /api/search_clients`

Назначение: найти клиента по имени, части ФИО, телефону, email, ИНН, названию организации,
контактному лицу, а также по автомобилю, госномеру или VIN из связанных карточек и сохраненного профиля клиента.
Телефон ищется в разных форматах: `+7`, `8`, без пробелов, со скобками и дефисами.
Для больших справочников поиск сначала проверяет собственные поля клиента и сохраненные `vehicles[]`, а связанную историю карточек использует как fallback.
Компактный ответ содержит `vehicles_preview` — 1-2 связанных автомобиля для UI-подсказок. У каждого автомобиля есть стабильный `id`; если оператор выбирает конкретную машину, этот `id` нужно передать как `client_vehicle_id` в `/api/link_card_to_client`.

Запрос:

```json
{
  "query": "Иванов 913",
  "limit": 10
}
```

### `POST /api/get_client`

Назначение: открыть профиль клиента, связанные автомобили и последние заказ-наряды.

Запрос:

```json
{
  "client_id": "CLIENT_ID",
  "order_limit": 30
}
```

### `POST /api/get_client_stats`

Назначение: получить компактную статистику клиента.

Запрос:

```json
{
  "client_id": "CLIENT_ID"
}
```

### `POST /api/create_client`

Назначение: создать клиента.

Запрос:

```json
{
  "client": {
    "client_type": "person",
    "last_name": "Иванов",
    "first_name": "Иван",
    "middle_name": "Иванович",
    "phone": "+79130000000",
    "vehicles": [
      {
        "id": "optional-existing-or-generated-id",
        "brand": "Toyota",
        "model": "Camry",
        "vin": "JTDBE32K620654321",
        "license_plate": "А123ВС124",
        "year": "2017"
      }
    ]
  },
  "actor_name": "operator"
}
```

### `POST /api/update_client`

Назначение: обновить профиль клиента. Передавать только изменяемые поля.

Запрос:

```json
{
  "client_id": "CLIENT_ID",
  "patch": {
    "email": "client@example.com",
    "comment": "Постоянный клиент",
    "vehicles": [
      {
        "brand": "Toyota",
        "model": "Camry",
        "vin": "JTDBE32K620654321",
        "license_plate": "А123ВС124"
      }
    ]
  },
  "actor_name": "operator"
}
```

### `POST /api/delete_client`

Назначение: удалить профиль клиента. Карточки не удаляются. Если к клиенту привязаны карточки, команда по умолчанию вернет ошибку `client_has_linked_cards`.

Запрос:

```json
{
  "client_id": "CLIENT_ID",
  "allow_linked": false,
  "actor_name": "operator"
}
```

Важно:

- `allow_linked=false` безопасный режим по умолчанию.
- `allow_linked=true` сначала снимет `client_id` со связанных карточек, затем удалит профиль клиента.
- Использовать `allow_linked=true` только после явного подтверждения пользователя.

### `POST /api/link_card_to_client`

Назначение: привязать карточку к клиенту и, если известно, к конкретному автомобилю клиента.

Запрос:

```json
{
  "card_id": "CARD_ID",
  "client_id": "CLIENT_ID",
  "client_vehicle_id": "CLIENT_VEHICLE_ID",
  "create_vehicle_from_card": false,
  "sync_vehicle_fields": true,
  "sync_fields": true,
  "overwrite_card_fields": false
}
```

Важно:

- `sync_fields=true` дозаполняет пустые клиентские поля карточки и заказ-наряда.
- `client_vehicle_id` заполняет паспорт автомобиля из выбранной машины клиента и сохраняется в карточке.
- `create_vehicle_from_card=true` создает новый автомобиль в профиле существующего клиента из паспорта текущей карточки.
- `sync_vehicle_fields=true` синхронизирует поля автомобиля; последующие изменения паспорта связанной карточки обновляют выбранный автомобиль клиента.
- `overwrite_card_fields=true` может заменить ручные поля клиента, поэтому использовать только после подтверждения пользователя.

### `POST /api/upsert_client_vehicle`

Назначение: создать или обновить автомобиль внутри профиля клиента.

Запрос:

```json
{
  "client_id": "CLIENT_ID",
  "client_vehicle_id": "CLIENT_VEHICLE_ID",
  "vehicle": {
    "vehicle": "Toyota Camry 2017",
    "brand": "Toyota",
    "model": "Camry",
    "vin": "JTDBE32K620654321",
    "license_plate": "А123ВС124",
    "year": "2017",
    "mileage": "120000",
    "engine_model": "2AR-FE",
    "gearbox_model": "U760E",
    "drivetrain": "FWD"
  },
  "sync_linked_cards": true,
  "actor_name": "operator"
}
```

Если `client_vehicle_id` не передан, создается новая машина. Если передан `card_id` и не передан `vehicle`, данные автомобиля берутся из паспорта карточки. По умолчанию `sync_linked_cards=true`: при изменении VIN/госномера/модели выбранного автомобиля связанные карточки с этим `client_vehicle_id` получают обновленный паспорт автомобиля.

### `POST /api/delete_client_vehicle`

Назначение: удалить автомобиль из профиля клиента без удаления карточек и заказ-нарядов.

Запрос:

```json
{
  "client_id": "CLIENT_ID",
  "client_vehicle_id": "CLIENT_VEHICLE_ID",
  "unlink_cards": true,
  "actor_name": "operator"
}
```

Если `unlink_cards=true`, связанные карточки остаются привязанными к клиенту, но у них очищается только `client_vehicle_id`. Удаленный автомобиль скрывается из списка машин клиента, чтобы он не появлялся обратно из старой истории карточек.

### `POST /api/unlink_card_from_client`

Назначение: снять связь карточки с клиентом без удаления ручных текстовых полей.

Запрос:

```json
{
  "card_id": "CARD_ID"
}
```

### `POST /api/suggest_clients_for_card`

Назначение: подобрать клиентов для карточки по ручному ФИО, телефону и данным заказ-наряда.

Запрос:

```json
{
  "card_id": "CARD_ID",
  "limit": 5
}
```

## Печатные PDF для заказ-нарядов и счетов

### `POST /api/export_repair_order_print_pdf`

Назначение: сгенерировать CRM-штатный PDF из окна печати заказ-наряда. Endpoint используется UI, MCP и агентами, чтобы не создавать отдельные PDF вне CRM.

Запрос:

```json
{
  "card_id": "CARD_ID",
  "selected_document_ids": ["invoice"],
  "selected_template_ids": {
    "invoice": "custom:invoice:..."
  },
  "print_settings": {
    "paper_size": "A4",
    "orientation": "portrait"
  }
}
```

Минимально нужен только `card_id`; если `selected_document_ids` не передан, печатный модуль использует свой дефолтный набор. Поддерживаемые документы: `repair_order`, `vehicle_acceptance_act`, `invoice`, `invoice_factura`, `inspection_sheet`, `completion_act`, `parts_sale`.

Ответ:

```json
{
  "ok": true,
  "data": {
    "file_name": "invoice-card.pdf",
    "mime_type": "application/pdf",
    "content_base64": "JVBERi0xLjQK...",
    "size_bytes": 12345,
    "meta": {
      "documents": [
        {"id": "invoice", "label": "Счет на оплату"}
      ],
      "paper_size": "A4",
      "orientation": "portrait"
    }
  }
}
```

## Модуль «Файлы»

Назначение: общая серверная папка автосервиса для счетов, PDF, Word/Excel, изображений и похожих рабочих файлов.

Хранилище:

- папка файлов: `%APPDATA%\Minimal Kanban\shared-files`
- индекс: `%APPDATA%\Minimal Kanban\shared_files_index.json`
- общий лимит: `500` МБ
- запрещённые расширения: `exe`, `bat`, `cmd`, `ps1`, `msi`, `scr`, `vbs`

Endpoint-ы:

- `GET /api/list_shared_files`
- `POST /api/get_shared_file_info`
- `POST /api/fetch_shared_file`
- `POST /api/upload_shared_file`
- `POST /api/rename_shared_file`
- `POST /api/delete_shared_file`
- `POST /api/copy_shared_file`
- `POST /api/paste_shared_file`
- `POST /api/update_shared_file_position`
- `GET /api/shared_file?file_id=FILE_ID`

Загрузка файла:

```json
{
  "file_name": "invoice.pdf",
  "mime_type": "application/pdf",
  "content_base64": "JVBERi0xLjQK...",
  "x": 24,
  "y": 48
}
```

Скачивание:

```text
GET /api/shared_file?file_id=FILE_ID
```

Открытие в браузере, если тип файла поддерживается:

```text
GET /api/shared_file?file_id=FILE_ID&disposition=inline
```

`fetch_shared_file` возвращает base64 только если файл укладывается в `max_base64_bytes`; для крупных файлов используйте `download_path`.

## Типовые ошибки

### `validation_error`

Когда возникает:

- пустой `title`
- некорректный `deadline`
- несуществующий `column`
- некорректный `indicator`
- неверный тип `include_archived`
- запрещённое или пустое имя файла

### `not_found`

Когда возникает:

- карточка не найдена
- общий файл не найден
- маршрут API не найден

### `storage_limit_exceeded`

Когда возникает:

- upload или copy/paste превысит общий лимит файлового хранилища

### `archived_card`

Когда возникает:

- попытка изменить уже архивную карточку

### `unauthorized`

Когда возникает:

- включён bearer token
- запрос пришёл без правильного `Authorization` заголовка

### `internal_error`

Когда возникает:

- непредвиденный сбой на стороне сервера

## Примеры запросов

Создать карточку:

```powershell
$body = @{
  title = "Подготовить демо"
  description = "Сделать короткий список задач"
  deadline = @{
    hours = 4
  }
} | ConvertTo-Json -Depth 5

Invoke-WebRequest `
  -Uri "http://127.0.0.1:41731/api/create_card" `
  -Method POST `
  -ContentType "application/json" `
  -Body $body
```

Выставить лампочку в красный:

```powershell
$body = @{
  card_id = "CARD_ID"
  indicator = "red"
} | ConvertTo-Json

Invoke-WebRequest `
  -Uri "http://127.0.0.1:41731/api/set_card_indicator" `
  -Method POST `
  -ContentType "application/json" `
  -Body $body
```

## Как этот API используется MCP-слоем

MCP server не реализует бизнес-логику доски повторно.

Он делает следующее:

1. принимает MCP tool call
2. формирует JSON payload
3. вызывает локальный endpoint доски
4. возвращает результат обратно в structured MCP output

Поэтому любые изменения правил карточек, дедлайнов, архивирования и столбцов происходят в одном месте — в backend доски.

## Как API связан с окном настроек

Для интеграции с ChatGPT / OpenAI / MCP в проект добавлен отдельный settings-layer:

- `src/minimal_kanban/settings_models.py`
- `src/minimal_kanban/settings_store.py`
- `src/minimal_kanban/settings_service.py`
- `src/minimal_kanban/ui/settings_window.py`

Что это меняет для API:

- host, port и bearer token локального API можно хранить отдельно в `%APPDATA%\Minimal Kanban\settings.json`
- при следующем запуске приложения локальный API поднимается с сохранёнными параметрами
- если файл настроек повреждён, приложение откатывается к значениям по умолчанию и не падает
- если заданы явные переменные окружения `MINIMAL_KANBAN_*`, они имеют приоритет над сохранёнными настройками

Проверка соединения из окна настроек использует этот API так:

- локальный API проверяется через `GET /api/health`
- bearer token локального API подставляется автоматически, если он сохранён в настройках
- результат проверки записывается в диагностическую секцию настроек

Ограничение текущего этапа:

- секреты пока хранятся в `settings.json` без системного шифрования, хотя в логах они редактируются и не выводятся открытым текстом
