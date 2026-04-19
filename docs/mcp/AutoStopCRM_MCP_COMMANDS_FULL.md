# AutoStop CRM MCP Commands Full Reference

Дата: 19.04.2026

Полный справочник MCP-команд AutoStop CRM. Всего команд: 49.

## Контекст и диагностика

| Команда | Тип | Что дает |
| --- | --- | --- |
| `get_connector_identity` | read | Жесткая идентичность коннектора: имя, board scope, resource URL, правило одной доски. |
| `ping_connector` | read | Самая легкая проверка доступности MCP. Возвращает `pong`. |
| `bootstrap_context` | read | Стартовый пакет для GPT: identity, board context, preview стены, recommended write flow. |
| `get_runtime_status` | read | Диагностика runtime: API health, board counts, endpoint visibility, connector metadata. |

## Доска, Markdown-стена и поиск

| Команда | Тип | Что дает |
| --- | --- | --- |
| `get_board_context` | read | Контекст доски: имя, scope, столбцы, счетчики, schema vehicle profile, правила autofill. |
| `get_board_snapshot` | read | Структурированный snapshot: столбцы, активные карточки, архивный хвост, стикеры, settings. |
| `review_board` | read | Операционный обзор: summary, загрузка по столбцам, alerts, priority cards, recent events. |
| `get_board_content` | read | Hidden machine wall в Markdown: карточки по столбцам, архивные карточки, стикеры, vehicle profile compact. |
| `get_board_events` | read | Markdown-журнал событий, по умолчанию последние 100, порядок `newest_first`. |
| `get_gpt_wall` | read | Совместимый агрегатор `board_content` + `event_log` в Markdown. |
| `search_cards` | read | Поиск карточек по query и фильтрам: column, tag, indicator, status, include_archived. |
| `update_board_settings` | write | Обновляет настройки доски; сейчас используется для `board_scale`. |

## Карточки

| Команда | Тип | Что дает |
| --- | --- | --- |
| `get_cards` | read | Список карточек. По умолчанию compact payload для GPT-safe сканов. |
| `get_card` | read | Одна карточка по `card_id`, включая полный `vehicle_profile` и `vehicle_profile_compact`. |
| `get_card_context` | read | Фокусный контекст карточки: card data, events, attachments, board context, repair-order text. |
| `get_card_log` | read | Audit log одной карточки с лимитом событий. |
| `create_card` | write | Создает карточку с vehicle, title, description, tags, deadline, column, vehicle_profile. |
| `update_card` | write | Обновляет vehicle, title, description, tags, deadline, vehicle_profile. |
| `cleanup_card_content` | write | Локальная нормализация карточки: описание, очевидные поля, verify patch. |
| `move_card` | write | Перемещает карточку в столбец, опционально перед `before_card_id`. |
| `bulk_move_cards` | write | Массово перемещает несколько карточек в один столбец. |
| `set_card_deadline` | write | Меняет только дедлайн карточки. |
| `set_card_indicator` | write | Меняет сигнал карточки через пересчет дедлайна: green/yellow/red. |
| `archive_card` | destructive | Архивирует карточку. |
| `restore_card` | write | Восстанавливает архивную карточку в выбранный или стандартный столбец. |
| `list_archived_cards` | read | Показывает архивные карточки с лимитом и compact/full режимом. |
| `list_overdue_cards` | read | Показывает просроченные карточки. Архив исключен по умолчанию. |

## Столбцы и стикеры

| Команда | Тип | Что дает |
| --- | --- | --- |
| `list_columns` | read | Все столбцы текущей доски. |
| `create_column` | write | Создает новый столбец. |
| `rename_column` | write | Переименовывает столбец без смены id. |
| `delete_column` | destructive | Удаляет пустой столбец. Нельзя удалить последний или непустой столбец. |
| `create_sticky` | write | Создает стикер с текстом, координатами и дедлайном. |
| `update_sticky` | write | Меняет текст или дедлайн стикера. |
| `move_sticky` | write | Меняет координаты стикера. |
| `delete_sticky` | destructive | Удаляет стикер. |

## Автомобиль и заказ-наряд

| Команда | Тип | Что дает |
| --- | --- | --- |
| `autofill_vehicle_data` | read | Строит draft нормализованного vehicle profile из текста карточки и/или raw text. |
| `autofill_repair_order` | write | Заполняет заказ-наряд из текста карточки и vehicle profile, по умолчанию только пустые поля. |
| `list_repair_orders` | read | Список заказ-нарядов с фильтром status, поиском, сортировкой, card links. |
| `get_repair_order` | read | Структурированный заказ-наряд одной карточки. |
| `get_repair_order_text` | read | Текстовая версия заказ-наряда и file metadata. |
| `update_repair_order` | write | Патчит header/client/payment/status-информацию заказ-наряда без удаления прочих полей. |
| `replace_repair_order_works` | write | Полностью заменяет таблицу работ. |
| `replace_repair_order_materials` | write | Полностью заменяет таблицу материалов. |
| `set_repair_order_status` | write | Ставит заказ-наряду `open` или `closed`; закрытие требует корректной оплаты. |

## Кассы

| Команда | Тип | Что дает |
| --- | --- | --- |
| `list_cashboxes` | read | Все кассы с компактной статистикой балансов. |
| `get_cashbox` | read | Одна касса, статистика и журнал транзакций. |
| `create_cashbox` | write | Создает кассу. |
| `create_cash_transaction` | write | Создает приход или расход. Принимает `amount_minor` или `amount`. |
| `delete_cashbox` | destructive | Удаляет пустую кассу. Кассу с движениями удалить нельзя. |

## Минимальные правила параметров

- `deadline`: объект с `days`, `hours`, `minutes`, `seconds`; для `create_card` пустой дедлайн заменяется на 1 день.
- `StickyDeadlinePayload` дополнительно принимает `total_seconds`.
- `vehicle`: только марка/модель, без длинного описания проблемы.
- `title`: краткая суть задачи или неисправности.
- `vehicle_profile`: разрешает дополнительные поля, но manual values нельзя перетирать без явного решения.
- `repair_order.rows`: строки работ/материалов передаются целиком при replace-командах.
- `cashbox_id`, `card_id`, `sticky_id`, `column_id`: брать из read-команд, не выдумывать.
