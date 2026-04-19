# AutoStop CRM MCP Workflows

Дата: 19.04.2026

Практические сценарии для GPT-чата, который работает с AutoStop CRM через MCP.

## 1. Быстрый старт работы с доской

1. `bootstrap_context`
2. `get_board_content(include_archived=true)`
3. `get_board_events(event_limit=100, include_archived=true)`
4. Если нужен краткий операционный взгляд: `review_board`

Результат: GPT знает текущее состояние карточек, архив, столбцы и последние изменения.

## 2. Найти и отредактировать карточку

1. `search_cards(query="...")` или `get_board_content`.
2. `get_card_context(card_id, event_limit=20, include_repair_order_text=true)`.
3. Сформировать точечный patch.
4. `update_card(card_id, ...)`.
5. Проверить результат через `get_card` или `get_card_context`.

Правило: не менять карточку по названию. Сначала получить точный `card_id`.

## 3. Заполнить автомобиль

1. `get_card_context(card_id)`.
2. `autofill_vehicle_data(raw_text=..., vehicle=..., title=..., description=..., vehicle_profile=...)`.
3. Проверить draft: `make_display`, `model_display`, `production_year`, `vin`, `engine_model`, `gearbox_model`, `drivetrain`, `oem_notes`.
4. `update_card(card_id, vehicle_profile=draft)`.
5. Проверить `vehicle_profile_compact` через `get_card`.

Правило: ручные поля клиента не перезаписывать без прямого запроса.

## 4. Заполнить заказ-наряд

1. `get_card_context(card_id, include_repair_order_text=true)`.
2. `autofill_repair_order(card_id, overwrite=false)`.
3. `update_repair_order(card_id, repair_order={...})` для клиента, телефона, госномера, формы оплаты и примечаний.
4. `replace_repair_order_works(card_id, rows=[...])`.
5. `replace_repair_order_materials(card_id, rows=[...])`.
6. `get_repair_order_text(card_id)` для проверки итогового текста.
7. `set_repair_order_status(card_id, status="closed")` только когда оплата закрыта.

Правило: replace-команды полностью заменяют таблицу, поэтому GPT должен передавать весь новый список строк.

## 5. Работа с кассами

1. `list_cashboxes(limit=200)` для общей картины.
2. `get_cashbox(cashbox_id, transaction_limit=300)` для журнала.
3. `create_cash_transaction(cashbox_id, direction="income|expense", amount=..., note=...)`.
4. Повторить `get_cashbox` и сверить баланс/транзакцию.

Правило: `delete_cashbox` использовать только для пустой кассы без движений.

## 6. Движение карточек по доске

1. `list_columns` или `get_board_context` для допустимых столбцов.
2. `move_card(card_id, column, before_card_id=...)` для одной карточки.
3. `bulk_move_cards(card_ids=[...], column=...)` для массового переноса.
4. Проверить через `get_board_snapshot` или `get_board_content`.

Правило: для массовых операций использовать `bulk_move_cards`, чтобы не делать длинные цепочки вызовов.

## 7. Архив и просрочка

1. `list_overdue_cards` для просроченных активных карточек.
2. `set_card_deadline` для точной смены срока.
3. `set_card_indicator` для служебного перевода лампочки через дедлайн.
4. `archive_card` для архивации.
5. `list_archived_cards` для проверки архива.
6. `restore_card` для возврата карточки на доску.

Правило: archive/destructive действия требуют явного подтверждения пользователя.

## 8. Стикеры

1. `create_sticky(text, deadline, x, y)`.
2. `update_sticky(sticky_id, text=..., deadline=...)`.
3. `move_sticky(sticky_id, x, y)`.
4. `delete_sticky(sticky_id)`.

Стикеры входят в `get_board_content` и `get_board_snapshot`.

## 9. Как GPT должен отвечать пользователю

- Сначала кратко сообщать, какие данные прочитаны.
- Перед write/destructive действием назвать точный объект: `card_id`, `column_id`, `cashbox_id`.
- После write-команды читать результат и подтверждать конкретное изменение.
- Если команда вернула `ok=false`, показывать `error.code` и понятное объяснение, не повторять вызов вслепую.
