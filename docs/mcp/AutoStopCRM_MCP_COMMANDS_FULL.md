# AutoStop CRM MCP Commands Full Reference

Дата: 27.04.2026

Полный справочник MCP-команд AutoStop CRM. Всего команд: 59.

## Как не перегружать коннектор

Не все команды одинаково тяжелые. Для обычной работы лучше держать приоритет таким:

1. `ping_connector`, `bootstrap_context`, `get_runtime_status`
2. `get_board_context`, `review_board`, `search_cards`, `search_clients`, `get_cards(compact=true)`
3. `get_card_context`, `suggest_clients_for_card`, `list_card_attachments`, `get_card_attachment`, `read_card_attachment`, `get_repair_order`, `get_card_log`, `get_repair_order_text`
4. `get_board_snapshot`, `get_board_content`, `get_board_events`, `get_gpt_wall`

Правило простое:

- сначала короткая диагностика;
- потом точечный read;
- потом тяжелый экспорт;
- если нужен большой обзор, лучше собирать его из `review_board` + `get_board_context` + `get_card_context`, а не сразу дергать `get_gpt_wall`;
- `get_gpt_wall` оставляй для случаев, когда нужен единый ответ с обеими hidden machine wall секциями, но в обычном агентском потоке предпочитай `get_board_content` и `get_board_events`.

## Контекст и диагностика

| Команда | Тип | Что дает |
| --- | --- | --- |
| `get_connector_identity` | read | Жесткая идентичность коннектора: имя, board scope, resource URL, правило одной доски. |
| `ping_connector` | read | Самая легкая проверка доступности MCP. Возвращает `pong`. |
| `bootstrap_context` | read | Стартовый пакет для GPT: identity, board context, preview стены, recommended write flow. |
| `get_runtime_status` | read | Диагностика runtime: API health, board counts, endpoint visibility, connector metadata. Это короткая безопасная проверка. |

## Доска, Markdown-стена и поиск

| Команда | Тип | Что дает |
| --- | --- | --- |
| `get_board_context` | read | Контекст доски: имя, scope, столбцы, счетчики, schema vehicle profile, правила autofill. |
| `get_board_snapshot` | read | Структурированный snapshot: столбцы, активные карточки, архивный хвост, стикеры, settings. Тяжелая команда, лучше использовать с `compact=true` и без архива, если нужен быстрый обзор. |
| `review_board` | read | Операционный обзор: summary, загрузка по столбцам, alerts, priority cards, recent events. Это одна из лучших команд для регулярной проверки. |
| `get_board_content` | read | Hidden machine wall в Markdown: карточки по столбцам, архивные карточки, стикеры, vehicle profile compact. Тяжелый экспорт стены без журнала событий. |
| `get_board_events` | read | Markdown-журнал событий, по умолчанию последние 100, порядок `newest_first`. Для обычной работы держи limit ниже. |
| `get_gpt_wall` | read | Совместимый агрегатор `board_content` + `event_log` в Markdown. В agent-режиме он урезается до компактных карточек и ограниченного журнала, поэтому для повседневной работы лучше сначала использовать `get_board_content` и `get_board_events`. |
| `search_cards` | read | Поиск карточек по query и фильтрам: column, tag, indicator, status, include_archived. Лучше работает с русско-латинскими вариантами названий и маркеров. |
| `update_board_settings` | write | Обновляет настройки доски; сейчас используется для `board_scale`. |

## Карточки

| Команда | Тип | Что дает |
| --- | --- | --- |
| `get_cards` | read | Список карточек. Для быстрых сканов держи `compact=true`. |
| `get_card` | read | Одна карточка по `card_id`, включая полный `vehicle_profile` и `vehicle_profile_compact`. Тяжелее, чем `get_card_context`, если нужен только контекст. |
| `get_card_context` | read | Фокусный контекст карточки: card data, events, attachments, board context, repair-order text. Это обычно лучший выбор для одной карточки. |
| `list_card_attachments` | read | Безопасный список вложений одной карточки без байтов файла: id, имя, MIME, размер, content_kind, readable_as_text, download_path. |
| `get_card_attachment` | read | Метаданные одного вложения: content_kind, размер, `sha256`, download_path, наличие файла на диске. Файл не возвращается. |
| `read_card_attachment` | read | Читает выбранное вложение bounded-режимом. TXT/DOCX/XLSX возвращают текст, PDF - best-effort text, изображения - размеры и опциональный `base64/data_url`. |
| `get_card_log` | read | Audit log одной карточки с лимитом событий. Для частного расследования обычно легче, чем `get_board_events`. |
| `create_card` | write | Создает карточку с vehicle, title, description, tags, deadline, column, vehicle_profile. |
| `update_card` | write | Обновляет vehicle, title, description, tags, deadline, vehicle_profile. |
| `cleanup_card_content` | write | Локальная нормализация карточки: описание, очевидные поля, verify patch. |
| `move_card` | write | Перемещает карточку в столбец, опционально перед `before_card_id`. |
| `bulk_move_cards` | write | Массово перемещает несколько карточек в один столбец. |
| `set_card_deadline` | write | Меняет только дедлайн карточки. Принимает `days/hours/minutes/seconds` или `total_seconds`. |
| `set_card_indicator` | write | Меняет сигнал карточки через пересчет дедлайна: green/yellow/red. |
| `archive_card` | destructive | Архивирует карточку. |
| `restore_card` | write | Восстанавливает архивную карточку в выбранный или стандартный столбец. |
| `list_archived_cards` | read | Показывает архивные карточки с лимитом и compact/full режимом. |
| `list_overdue_cards` | read | Показывает просроченные карточки. Архив исключен по умолчанию. |

## Клиенты

| Команда | Тип | Что дает |
| --- | --- | --- |
| `list_clients` | read | Список клиентов с ФИО/названием, типом, телефонами и опциональной короткой статистикой. Для обычного обзора держи `limit=50..100`. |
| `search_clients` | read | Ищет клиента по ФИО, названию организации, телефону, email, ИНН, реквизитам или контактному лицу. |
| `get_client` | read | Полный профиль клиента: контакты, реквизиты, связанные автомобили и последние заказ-наряды. |
| `get_client_stats` | read | Легкая статистика клиента без полного профиля: количество карточек, активных/архивных ремонтов, заказ-нарядов и автомобилей. |
| `suggest_clients_for_card` | read | Подбирает возможных клиентов для карточки по ручным полям клиента/телефона в паспорте автомобиля и заказ-наряде. |
| `create_client` | write | Создает запись клиента. Поддерживает физлицо, ИП, ООО и другую организацию. |
| `update_client` | write | Обновляет профиль клиента и реквизиты. Передавать только изменяемые поля. |
| `link_card_to_client` | write | Привязывает карточку к клиенту. По умолчанию дозаполняет только пустые клиентские поля карточки и заказ-наряда. |
| `unlink_card_from_client` | write | Снимает связь карточки с клиентом, но не удаляет ручные текстовые поля в карточке. |

Типы клиентов:

- `person`: физическое лицо.
- `ip`: индивидуальный предприниматель.
- `ooo`: ООО.
- `company`: другая организация.

Правильный flow для агента:

1. Если работа идет из карточки, сначала вызвать `suggest_clients_for_card(card_id)`.
2. Если уверенного совпадения нет, вызвать `search_clients(query=...)`.
3. Если клиент не найден, предложить создать через `create_client`.
4. После выбора клиента вызвать `link_card_to_client(card_id, client_id, sync_fields=true)`.
5. Проверить через `get_card_context(card_id)` и `get_client(client_id)`.

Ограничения:

- Создание клиента не является обязательным для каждой карточки.
- Не создавать дубль без поиска по телефону/ФИО/ИНН.
- Телефоны в формате `+7...` и `8...` считаются одним номером при поиске, подсказках и сборе истории клиента.
- `overwrite_card_fields=true` в `link_card_to_client` использовать только после явного подтверждения пользователя.
- Для реквизитов организаций использовать `legal_name`, `short_name`, `inn`, `kpp`, `ogrn`, `checking_account`, `bank_name`, `bik`, `correspondent_account`, `legal_address`, `actual_address`, `contact_person`, `contact_position`.

## Столбцы и стикеры

| Команда | Тип | Что дает |
| --- | --- | --- |
| `list_columns` | read | Все столбцы текущей доски. |
| `create_column` | write | Создает новый столбец. Принимает `label` или алиас `name`. |
| `rename_column` | write | Переименовывает столбец без смены id. |
| `delete_column` | destructive | Удаляет пустой столбец. Нельзя удалить последний или непустой столбец. |
| `create_sticky` | write | Создает стикер с текстом, координатами и дедлайном. |
| `update_sticky` | write | Меняет текст или дедлайн стикера. |
| `move_sticky` | write | Меняет координаты стикера. |
| `delete_sticky` | destructive | Удаляет стикер. |

## Автомобиль и заказ-наряд

| Команда | Тип | Что дает |
| --- | --- | --- |
| `list_repair_orders` | read | Список заказ-нарядов с фильтром status, поиском, сортировкой, card links. |
| `get_repair_order` | read | Структурированный заказ-наряд одной карточки. Если заказ-наряда еще нет, команда может создать его из карточки и сохранить текстовый файл. |
| `get_repair_order_text` | read | Текстовая версия заказ-наряда и file metadata. Не создает новый заказ-наряд, но может обновить текстовый файл при чтении. Используй, когда нужен именно полный текст. |
| `update_repair_order` | write | Патчит header/client/payment/status-информацию заказ-наряда. Используй короткий структурный patch. |
| `replace_repair_order_works` | write | Полностью заменяет таблицу работ. |
| `replace_repair_order_materials` | write | Полностью заменяет таблицу материалов. |
| `set_repair_order_status` | write | Ставит заказ-наряду `open` или `closed`; закрытие требует корректной оплаты. |

## Вложения карточек

Правильный flow для агента:

1. Получить карточку через `get_card_context(card_id)` или `get_card(card_id)`.
2. Если в карточке есть вложения, вызвать `list_card_attachments(card_id)`.
3. Выбрать конкретный `attachment_id` по имени, типу или размеру.
4. При необходимости проверить файл через `get_card_attachment(card_id, attachment_id)`.
5. Только потом читать содержимое через `read_card_attachment`.

Параметры `read_card_attachment`:

- `card_id`: id карточки.
- `attachment_id`: id вложения.
- `mode`: `preview`, `text`, `base64`, `auto`.
- `max_chars`: максимум символов текста, по умолчанию `12000`, максимум `50000`.
- `include_base64`: включить `base64/data_url`; по умолчанию `false`.
- `max_base64_bytes`: максимум размера файла для base64, по умолчанию `1048576`, максимум `4194304`.

Ограничения и правила:

- Не использовать `mode=base64` как первый шаг. Сначала читать метаданные.
- Для TXT/DOCX/XLSX инструмент возвращает нормальный текст.
- Для PDF извлечение best-effort: если PDF сканированный или со сложным сжатием, текст может быть пустым.
- Для PNG/JPG/GIF/WEBP CRM не делает OCR. Агент получает размеры и может запросить `data_url` для своей vision-модели.
- Старые DOC/XLS считаются `office_legacy`: лучше попросить пользователя заменить их на DOCX/XLSX или читать через base64 внешним инструментом.

## Что не входит в MCP runtime

- `autofill_vehicle_data` и `autofill_repair_order` остаются в HTTP API и UI, но не являются MCP tools.
- Для автозаполнения через ИИ агент должен читать контекст и писать обычными `update_*` командами.

## Кассы

| Команда | Тип | Что дает |
| --- | --- | --- |
| `list_cashboxes` | read | Все кассы с компактной статистикой балансов. |
| `get_cashbox` | read | Одна касса, статистика и журнал транзакций. |
| `create_cashbox` | write | Создает кассу. |
| `create_cash_transaction` | write | Создает приход или расход. Принимает `amount_minor` или `amount`. |
| `delete_cashbox` | destructive | Удаляет пустую кассу. Кассу с движениями удалить нельзя. |

## Минимальные правила параметров

- `deadline`: объект с `days`, `hours`, `minutes`, `seconds`; для короткой записи можно использовать `total_seconds`. Для `create_card` пустой дедлайн заменяется на 1 день.
- `StickyDeadlinePayload` также принимает `total_seconds`.
- `create_column`: основной параметр `label`, но `name` тоже принимается как алиас для совместимости.
- `set_card_deadline`: принимает тот же `deadline`-объект, что и `create_card`/`update_card`.
- `vehicle`: только марка/модель, без длинного описания проблемы.
- `title`: краткая суть задачи или неисправности.
- `vehicle_profile`: разрешает дополнительные поля, но manual values нельзя перетирать без явного решения.
- `client_id`: брать только из `list_clients`, `search_clients`, `suggest_clients_for_card` или `get_client`; не выдумывать.
- `client`: для `create_client` передавать объект профиля, минимум имя/название или телефон.
- `patch`: для `update_client` передавать только изменяемые поля, не весь профиль без необходимости.
- `repair_order.rows`: строки работ/материалов передаются целиком при replace-командах.
- `cashbox_id`, `card_id`, `sticky_id`, `column_id`: брать из read-команд, не выдумывать.
