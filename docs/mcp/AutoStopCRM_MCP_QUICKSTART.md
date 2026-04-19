# AutoStop CRM MCP Quickstart

Дата: 19.04.2026

Этот файл можно прикреплять к GPT-чату как короткую инструкцию по работе с MCP-коннектором AutoStop CRM.

## Главный принцип

Коннектор управляет только текущей доской AutoStop CRM, доступной через этот MCP endpoint. Перед любыми изменениями нужно подтвердить контекст доски и работать только через реальные `card_id`, `column_id`, `sticky_id`, `cashbox_id`.

## Как начинать разговор

1. Вызвать `ping_connector`, если нужно проверить, что MCP вообще доступен.
2. Вызвать `bootstrap_context`, чтобы получить identity, scope, краткий preview и рекомендуемый flow.
3. Для полного состояния доски вызвать `get_board_content`.
4. Для последних изменений вызвать `get_board_events` с `event_limit=100`.
5. Для конкретной карточки вызвать `get_card_context`, затем делать точечные изменения.

## Основные команды для GPT

| Задача | Команда |
| --- | --- |
| Проверить коннектор | `ping_connector` |
| Начать работу с доской | `bootstrap_context` |
| Получить полное Markdown-состояние карточек | `get_board_content` |
| Получить последние события | `get_board_events` |
| Найти карточку | `search_cards` |
| Прочитать карточку | `get_card_context` |
| Создать карточку | `create_card` |
| Обновить карточку | `update_card` |
| Заполнить данные автомобиля | `autofill_vehicle_data`, затем `update_card` |
| Заполнить заказ-наряд | `autofill_repair_order`, `update_repair_order`, `replace_repair_order_works`, `replace_repair_order_materials` |
| Двигать карточку | `move_card` или `bulk_move_cards` |
| Смотреть кассы | `list_cashboxes`, `get_cashbox` |
| Смотреть заказ-наряды | `list_repair_orders`, `get_repair_order`, `get_repair_order_text` |

## Базовый стартовый промпт для GPT

```text
Ты работаешь с AutoStop CRM через MCP-коннектор. Сначала вызови bootstrap_context.
Для полного состояния доски используй get_board_content.
Для последних изменений используй get_board_events(event_limit=100).
Не меняй данные, пока не определишь точные card_id/column_id/cashbox_id.
Основная задача: помогать редактировать карточки, нормализовать описание автомобиля,
заполнять vehicle_profile и заказ-наряд.
```

## Безопасность изменений

- Не угадывать `card_id`: сначала искать карточку через `search_cards`, `get_cards` или `get_board_content`.
- Для массового переноса карточек использовать `bulk_move_cards`, а не длинную цепочку `move_card`.
- Перед закрытием заказ-наряда проверять оплату через `get_repair_order`.
- Destructive-команды (`delete_column`, `delete_sticky`, `delete_cashbox`, `archive_card`) применять только после явного подтверждения пользователя.

## Что читать в первую очередь

- Полная доска: `get_board_content(include_archived=true)`.
- Журнал: `get_board_events(event_limit=100, include_archived=true)`.
- Одна карточка: `get_card_context(card_id, event_limit=20, include_repair_order_text=true)`.
- Кассы: `list_cashboxes`, затем `get_cashbox`.
- Заказ-наряды: `list_repair_orders`, затем `get_repair_order`.
