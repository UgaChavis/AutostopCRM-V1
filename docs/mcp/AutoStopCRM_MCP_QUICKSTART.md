# AutoStop CRM MCP Quickstart

Дата: 27.04.2026

Этот файл можно прикреплять к GPT-чату как короткую инструкцию по работе с MCP-коннектором AutoStop CRM.

## Главный принцип

Коннектор управляет только текущей доской AutoStop CRM, доступной через этот MCP endpoint. Перед любыми изменениями нужно подтвердить контекст доски и работать только через реальные `card_id`, `column`, `sticky_id`, `cashbox_id`.

## Как не перегружать коннектор

Для обычной работы не начинай с тяжелых экспортов. Сначала используй короткие и узкие команды, а уже потом раскрывай широкий контекст.

### Базовый порядок

1. `ping_connector`
2. `bootstrap_context`
3. `get_runtime_status`, если нужно понять здоровье runtime
4. `get_board_context` или `review_board`
5. `search_cards`, `search_clients` или `get_cards(compact=true)`
6. `get_card_context` для одной карточки
7. `suggest_clients_for_card` перед привязкой карточки к клиенту
8. `list_card_attachments` -> `read_card_attachment` только если нужно прочитать конкретный файл карточки
9. `get_board_content` или `get_board_events` только когда действительно нужен полный обзор
10. `get_gpt_wall` только если нужен ответ с обеими wall-секциями сразу; в agent mode он компактный и ограничивает журнал

### Что считать тяжелым

- `get_gpt_wall` в full export mode
- `get_board_content`
- `get_board_snapshot`
- `get_board_events` с большим `event_limit`
- `get_card` в полном режиме

## Как начинать разговор

1. Вызвать `ping_connector`, если нужно проверить, что MCP вообще доступен.
2. Вызвать `bootstrap_context`, чтобы получить identity, scope, краткий preview и рекомендуемый flow.
3. Для полного состояния доски сначала попробовать `review_board`, а уже потом при необходимости вызвать `get_board_content`.
4. Для последних изменений по умолчанию держать `get_board_events` с меньшим `event_limit`; `100` использовать только для разбора истории.
5. Для конкретной карточки сначала вызвать `get_card_context`, затем делать точечные изменения.
6. Если `search_cards` не находит очевидное совпадение или `get_card_context` не добирается до цели, проверить `get_cards(compact=true)`, затем `get_board_snapshot(compact=true)`, и только потом `get_board_content`.

## Основные команды для GPT

| Задача | Команда |
| --- | --- |
| Проверить коннектор | `ping_connector` |
| Начать работу с доской | `bootstrap_context` |
| Получить полное Markdown-состояние карточек | `get_board_content` |
| Получить короткую сводку по доске | `review_board` |
| Получить последние события | `get_board_events` |
| Найти карточку | `search_cards` |
| Прочитать карточку | `get_card_context` |
| Найти клиента | `search_clients`, `list_clients` |
| Открыть профиль клиента | `get_client`, `get_client_stats` |
| Подобрать клиента для карточки | `suggest_clients_for_card` |
| Создать клиента | `create_client` |
| Изменить клиента | `update_client` |
| Удалить клиента | `delete_client` |
| Привязать карточку к клиенту | `link_card_to_client` |
| Снять связь карточки с клиентом | `unlink_card_from_client` |
| Посмотреть вложения карточки | `list_card_attachments`, затем `get_card_attachment` |
| Прочитать файл из карточки | `read_card_attachment` для конкретного `attachment_id` |
| Создать карточку | `create_card` |
| Обновить карточку | `update_card` |
| Заполнить данные автомобиля | `update_card` с `vehicle_profile` |
| Заполнить заказ-наряд | `update_repair_order` как короткий patch, затем `replace_repair_order_works`, `replace_repair_order_materials` |
| Двигать карточку | `move_card` или `bulk_move_cards` |
| Менять дедлайн | `set_card_deadline(card_id, deadline={total_seconds: 5400})` или объектом `days/hours/minutes/seconds` |
| Смотреть кассы | `list_cashboxes`, `get_cashbox` |
| Смотреть заказ-наряды | `list_repair_orders`, `get_repair_order`, `get_repair_order_text` |

## Базовый стартовый промпт для GPT

```text
Ты работаешь с AutoStop CRM через MCP-коннектор. Сначала вызови bootstrap_context.
Для полного состояния доски используй get_board_content.
Для последних изменений используй get_board_events(event_limit=20..50).
Не меняй данные, пока не определишь точные card_id/column/cashbox_id.
Если в карточке есть вложения, сначала используй list_card_attachments, затем read_card_attachment только для нужного attachment_id.
Если нужно работать с клиентом, сначала используй suggest_clients_for_card или search_clients. Не создавай дубль клиента без проверки.
delete_client является destructive-командой: сначала проверь связи клиента, а allow_linked=true используй только после явного подтверждения.
Основная задача: помогать редактировать карточки, нормализовать описание автомобиля,
заполнять vehicle_profile и заказ-наряд.
Работай через MCP tool names напрямую, а не через resource URL-пути.
```

## Безопасность изменений

- Не угадывать `card_id`: сначала искать карточку через `search_cards`, `get_cards`, `get_board_snapshot` или `get_board_content`.
- Для массового переноса карточек использовать `bulk_move_cards`, а не длинную цепочку `move_card`.
- Перед закрытием заказ-наряда проверять оплату через `get_repair_order`.
- Перед созданием клиента искать дубликаты через `search_clients` или `suggest_clients_for_card`.
- Привязка клиента к карточке не обязательна: если пользователь хочет разовую запись, можно оставить ручное имя без `create_client`.
- При поиске клиента можно использовать `+7...` или `8...`: CRM сопоставляет эти варианты как один российский номер.
- `link_card_to_client(..., overwrite_card_fields=true)` использовать только после прямого подтверждения пользователя.
- `delete_client` по умолчанию блокирует удаление связанного клиента; `allow_linked=true` снимает связи с карточек и требует прямого подтверждения.
- `update_repair_order` держать коротким и структурным: только шапка и минимально нужные поля.
- Destructive-команды (`delete_column`, `delete_sticky`, `delete_cashbox`, `archive_card`) применять только после явного подтверждения пользователя.

## Что читать в первую очередь

- Полная доска: `get_board_content(include_archived=false)` для обычной работы, `include_archived=true` только при необходимости полного экспорта.
- Журнал: `get_board_events(event_limit=20..50, include_archived=true)`, а `100` использовать только для расследований.
- Одна карточка: `get_card_context(card_id, event_limit=5..20, include_repair_order_text=false)`; `include_repair_order_text=true` включать только когда нужен полный текст.
- Клиенты: `suggest_clients_for_card(card_id)` из карточки, `search_clients(query)` по ФИО/телефону/ИНН/авто/госномеру/VIN, затем `get_client(client_id)` только для выбранного клиента.
- Вложения карточки: `list_card_attachments(card_id)` для списка, `get_card_attachment(card_id, attachment_id)` для метаданных, `read_card_attachment(card_id, attachment_id, mode="preview")` для bounded-чтения. Для изображений включать `mode="base64"` только если агент будет анализировать картинку.
- Кассы: `list_cashboxes`, затем `get_cashbox`.
- Заказ-наряды: `list_repair_orders`, затем `get_repair_order`.
