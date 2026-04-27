# AutoStop CRM MCP Workflows

Дата: 27.04.2026

Практические сценарии для GPT-чата, который работает с AutoStop CRM через MCP.

## 1. Быстрый старт работы с доской

1. `bootstrap_context`
2. `review_board`
3. `get_board_context`
4. `get_board_events(event_limit=20..50, include_archived=true)` только если нужен журнал изменений

Если нужен полный экспорт, уже потом использовать `get_board_content(include_archived=true)` и `get_board_events(event_limit=100, include_archived=true)`.
`get_gpt_wall` оставлять для случаев, когда нужен единый ответ с обеими секциями, но не как первый шаг.

Результат: GPT знает текущее состояние карточек, архив, столбцы и последние изменения.

## 2. Найти и отредактировать карточку

1. `search_cards(query="...")` или `get_board_snapshot(compact=true)` / `get_board_content`.
2. `get_card_context(card_id, event_limit=20, include_repair_order_text=true)`.
3. Сформировать точечный patch.
4. `update_card(card_id, ...)`.
5. Проверить результат через `get_card_context`.

Правило: не менять карточку по названию. Сначала получить точный `card_id`. Если `search_cards` не нашёл очевидное совпадение, переходить к `get_board_snapshot` и `get_board_content`, а не останавливаться.

## 3. Заполнить автомобиль

1. `get_card_context(card_id)`.
2. Сформировать карточку и `vehicle_profile` через обычные write-команды.
3. `update_card(card_id, vehicle_profile=...)`.
4. Проверить `vehicle_profile_compact` через `get_card_context` или `get_card`.

Правило: ручные поля клиента не перезаписывать без прямого запроса.

## 4. Заполнить заказ-наряд

1. `get_card_context(card_id, include_repair_order_text=true)`.
2. Сформировать короткий структурный patch для шапки заказ-наряда.
3. `update_repair_order(card_id, repair_order={...})` для клиента, телефона, госномера, формы оплаты и примечаний.
4. `replace_repair_order_works(card_id, rows=[...])`.
5. `replace_repair_order_materials(card_id, rows=[...])`.
6. `get_repair_order_text(card_id)` для проверки итогового текста.
7. `set_repair_order_status(card_id, status="closed")` только когда оплата закрыта.

Правило: replace-команды полностью заменяют таблицу, поэтому GPT должен передавать весь новый список строк.
Правило: `update_repair_order` должен оставаться коротким и структурным. Длинный текст лучше держать в `description`, `note` или отдельном комментарии карточки.
Правило: `get_repair_order` может материализовать заказ-наряд из карточки, если его еще не было. Если нужен только текст уже существующего заказа-наряда, использовать `get_repair_order_text`.

## 5. Работа с клиентом

1. Если есть карточка, вызвать `suggest_clients_for_card(card_id, limit=5)`.
2. Если совпадение не найдено, вызвать `search_clients(query="ФИО телефон ИНН")`.
3. Если клиент уже есть, открыть `get_client(client_id)` и сверить телефон/машину/историю заказов.
4. Если клиента нет и пользователь хочет справочник, создать через `create_client(client={...})`.
5. Привязать карточку через `link_card_to_client(card_id, client_id, sync_fields=true)`.
6. Проверить результат через `get_card_context(card_id)` и `get_client(client_id)`.

Правило: карточка может остаться без привязки к клиенту, если это разовая запись или пользователь не хочет заводить профиль.
Правило: перед `create_client` обязательно проверить дубликаты по телефону, ФИО, названию организации или ИНН.
Правило: `overwrite_card_fields=true` использовать только после явного подтверждения, потому что это может заменить ручные поля клиента в карточке и заказ-наряде.
Правило: `delete_client` использовать только для реально лишних профилей. Если клиент связан с карточками, команда без `allow_linked=true` должна отказать; `allow_linked=true` разрешен только после явного подтверждения.
Правило: для организаций заполнять `client_type`, реквизиты и контактное лицо, а историю ремонтов получать через `get_client`.

## 6. Прочитать вложение карточки

1. `get_card_context(card_id, event_limit=10, include_repair_order_text=false)`.
2. Проверить `attachment_count` и attachment summaries.
3. `list_card_attachments(card_id)`.
4. Выбрать конкретный `attachment_id`.
5. `get_card_attachment(card_id, attachment_id)`, если нужно подтвердить тип, размер, `sha256` или download path.
6. `read_card_attachment(card_id, attachment_id, mode="preview", max_chars=12000)`.
7. Если файл изображение и агент должен его визуально разобрать: повторить `read_card_attachment(..., mode="base64", max_base64_bytes=1048576)`.

Правило: не читать все вложения подряд. Сначала список, потом один выбранный файл.
Правило: base64 включать только для конкретного изображения или бинарного файла, когда это действительно нужно.
Правило: PDF читается best-effort. Если текст пустой, возможно это скан, тогда агент должен запросить изображение/base64 или попросить пользователя загрузить текстовый вариант.

## 7. Работа с кассами

1. `list_cashboxes(limit=200)` для общей картины.
2. `get_cashbox(cashbox_id, transaction_limit=300)` для журнала.
3. `create_cash_transaction(cashbox_id, direction="income|expense", amount=..., note=...)`.
4. Повторить `get_cashbox` и сверить баланс/транзакцию.

Правило: `delete_cashbox` использовать только для пустой кассы без движений.

## 8. Движение карточек по доске

1. `list_columns` или `get_board_context` для допустимых столбцов.
2. `move_card(card_id, column, before_card_id=...)` для одной карточки.
3. `bulk_move_cards(card_ids=[...], column=...)` для массового переноса.
4. Проверить через `get_board_snapshot` или `get_board_content`.

Правило: для массовых операций использовать `bulk_move_cards`, чтобы не делать длинные цепочки вызовов.

## 9. Архив и просрочка

1. `list_overdue_cards` для просроченных активных карточек.
2. `set_card_deadline` для точной смены срока.
3. `set_card_indicator` для служебного перевода лампочки через дедлайн.
4. `archive_card` для архивации.
5. `list_archived_cards` для проверки архива.
6. `restore_card` для возврата карточки на доску.

Правило: archive/destructive действия требуют явного подтверждения пользователя.

## 10. Стикеры

1. `create_sticky(text, deadline, x, y)`.
2. `update_sticky(sticky_id, text=..., deadline=...)`.
3. `move_sticky(sticky_id, x, y)`.
4. `delete_sticky(sticky_id)`.

Стикеры входят в `get_board_content` и `get_board_snapshot`.

## 11. Как GPT должен отвечать пользователю

- Сначала кратко сообщать, какие данные прочитаны.
- Перед write/destructive действием назвать точный объект: `card_id`, `column`, `cashbox_id`.
- После write-команды читать результат и подтверждать конкретное изменение.
- Если команда вернула `ok=false`, показывать `error.code` и понятное объяснение, не повторять вызов вслепую.
