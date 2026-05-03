# Печатные документы AutoStop CRM

## Где лежит модуль

- Типы документов и встроенные HTML-шаблоны: `src/minimal_kanban/printing/defaults.py`
- Профиль компании и модель настроек: `src/minimal_kanban/printing/models.py`
- Подготовка данных для шаблонов, предпросмотр, PDF и печать: `src/minimal_kanban/printing/service.py`
- Модальное окно печати и редактор шаблонов в CRM: `src/minimal_kanban/printing/web_module.py`

Пользовательские шаблоны и настройки сохраняются не в коде, а в данных приложения:

```text
%APPDATA%\Minimal Kanban\printing\settings.json
%APPDATA%\Minimal Kanban\printing\templates.json
```

## Реквизиты компании

Единый дефолтный профиль Auto Stop задан в `PrintServiceProfile`.
Через окно печати в CRM его можно изменить и сохранить в `settings.json`.

Актуальные дефолтные данные:

- ИП Гришкявичус Константин Владиславович
- ИНН 246413435608
- ОГРНИП 319246800097453
- Адрес: 660012, г. Красноярск, ул. Семафорная, 80, стр. 4
- Ресепшн: 288-14-15
- Запчасти: +7 (963) 184-76-76
- Банк: ФИЛИАЛ «НОВОСИБИРСКИЙ» АО «АЛЬФА-БАНК»
- Р/с 40802810523260001815
- БИК 045004774
- К/с 30101810600000000774

## Документы

Сейчас встроены:

- `repair_order` - заказ-наряд
- `vehicle_acceptance_act` - акт приема автомобиля в работу
- `invoice` - счет на оплату
- `invoice_factura` - счет-фактура
- `inspection_sheet` - дефектовочная ведомость
- `completion_act` - акт выполненных работ
- `parts_sale` - продажа запчастей без привязки к автомобилю

Все документы получают нормализованный контекст из `PrintModuleService._build_document_context`.
Пустые поля выводятся аккуратным прочерком, а не `undefined`, `null` или `NaN`.

## Как проверить в CRM

1. Открыть карточку с заказ-нарядом.
2. Нажать `РАСПЕЧАТАТЬ`.
3. Слева выбрать нужный документ.
4. Проверить предпросмотр.
5. Нажать `PDF` для сохранения или `ПЕЧАТЬ` для печати.

Для дефектовочной ведомости доступна кнопка `ЗАПОЛНИТЬ ВЕДОМОСТЬ`.
Для изменения шаблонов используется кнопка `ШАБЛОНЫ`.

## PDF для агента и MCP

Агенты не должны собирать счета и заказ-наряды своим отдельным PDF-генератором. Для отправки клиенту используется общий CRM-экспорт:

- API: `POST /api/export_repair_order_print_pdf`
- MCP: `download_repair_order_print_pdf`
- Agent/Telegram tool: `download_repair_order_print_pdf`

Минимальный payload:

```json
{
  "card_id": "CARD_ID",
  "selected_document_ids": ["invoice"]
}
```

Ответ содержит `mime_type="application/pdf"` и `content_base64`; эти байты можно прикладывать к письму или сообщению. Поддерживаются все встроенные документы: `repair_order`, `vehicle_acceptance_act`, `invoice`, `invoice_factura`, `inspection_sheet`, `completion_act`, `parts_sale`.

## Проверка документов

- Акт приема: проверить клиента, автомобиль, VIN/госномер, причину обращения, фотофиксацию и юридический блок.
- Заказ-наряд: проверить работы, материалы, итоги, гарантийные условия и подписи.
- Счет: проверить банковский блок, поставщика, покупателя, позиции и итоги.
- Акт выполненных работ: проверить выполненные работы, материалы, итог и подписи.
- Продажа запчастей: проверить, что документ работает даже без автомобиля и использует строки материалов.

## Команды

```powershell
$env:PYTHONPATH='src'
python -m unittest tests.test_printing_service -v
python -m unittest tests.test_api.ApiServerTests.test_repair_order_print_module_routes_preview_export_and_template_crud tests.test_api.ApiServerTests.test_inspection_sheet_form_routes_save_preview_and_autofill tests.test_api.ApiServerTests.test_repair_order_print_pdf_export_works_from_http_thread -v
```
