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
- `remaining_seconds` — оставшееся время
- `remaining_display` — готовая строка для UI
- `status` — `ok`, `warning`, `expired`
- `indicator` — `green`, `yellow`, `red`

Важно:

- `indicator` не хранится отдельно, а вычисляется из дедлайна
- endpoint `set_card_indicator` меняет дедлайн так, чтобы карточка получила нужный цвет лампочки

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
Компактный ответ содержит `vehicles_preview` — 1-2 связанных автомобиля для UI-подсказок.

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

Назначение: привязать карточку к клиенту.

Запрос:

```json
{
  "card_id": "CARD_ID",
  "client_id": "CLIENT_ID",
  "sync_fields": true,
  "overwrite_card_fields": false
}
```

Важно:

- `sync_fields=true` дозаполняет пустые клиентские поля карточки и заказ-наряда.
- `overwrite_card_fields=true` может заменить ручные поля клиента, поэтому использовать только после подтверждения пользователя.

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

## Типовые ошибки

### `validation_error`

Когда возникает:

- пустой `title`
- некорректный `deadline`
- несуществующий `column`
- некорректный `indicator`
- неверный тип `include_archived`

### `not_found`

Когда возникает:

- карточка не найдена
- маршрут API не найден

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
