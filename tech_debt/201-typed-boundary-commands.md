# 201. Ввести typed commands между transport и domain services

Приоритет: P2
Этап: 2 — только после обсуждения
Оценка: 3–6 недель по доменам
Риск: высокий из-за ширины diff
Статус: proposed

## Проблема

HTTP, MCP и CardService широко передают `dict[str, Any]`. Validation,
defaults, aliases и revisions повторяются. Это увеличивает риск, что transport
и domain по-разному понимают одно поле.

## Почему не сейчас

Route registry, service seams и tests сначала должны быть разделены задачами
003, 006, 008, 012. Иначе DTO migration затронет god-files одновременно и
создаст огромный diff без близкого ROI.

## Результат

Go/no-go ADR и, только при `go`, один bounded pilot без массовой DTO migration.

## Вариант минимальной реализации

- Typed dataclasses только для mutation commands critical domains.
- `from_payload` остаётся на transport boundary.
- Domain принимает command, возвращает existing response DTO.
- Начать с repair-order reopen и inventory write-off, не со всех 175 действий.
- Не менять public JSON schema.

## Решение go/no-go

Go, если после этапа 1 всё ещё регулярно возникают:

- разные defaults HTTP/MCP;
- validation duplicated bugs;
- Any-related runtime errors;
- дорогие schema parity updates.

No-go, если RouteSpec + focused validators уже дают достаточную безопасность.

## Обязательные tests

Public payload roundtrip, unknown fields policy, legacy aliases, exact errors,
serialization equality, revision/idempotency requirements.

## Acceptance

Один pilot domain уменьшает duplicate validation и не увеличивает boilerplate
сильнее, чем удаляет. После pilot — отдельное решение о масштабировании.

## Stop condition

Не продолжать после pilot, если public schema меняется, parity усложняется или
нового boilerplate не меньше удалённой validation duplication.
