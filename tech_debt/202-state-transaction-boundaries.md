# 202. Пересмотреть JSON state и cross-store transaction boundaries

Приоритет: P2
Этап: 2 — архитектурное обсуждение
Оценка: discovery 1 неделя; implementation неизвестна
Риск: очень высокий
Статус: proposed discovery only

## Проблема

CRM использует `JsonStore` плюс отдельные audit archive, operator activity,
completion-act drafts, change-feed SQLite и Manager SQLite. Код содержит
reconciliation/rollback для partial commits. Это работает и тестируется, но
увеличивает сложность attachment/printing/feed/release paths.

## Результат

Evidence-backed ADR: оставить текущую схему либо предложить ограниченный pilot
с migration, dual-write, rollback и измеримыми go/no-go критериями.

## Почему не мигрировать автоматически

- Current performance gates зелёные.
- JSON state — production source of truth с backup/rollback flow.
- «Перейти на PostgreSQL» без measured failure не минимальная правка.
- Cross-store migration затрагивает finance, deploy и rollback.

## Discovery

1. Каталог всех commit sinks и ownership.
2. Failure matrix до/после каждого fsync/rename/SQLite commit.
3. Production-sized timing и state size trends.
4. Частота реальных reconciliation events.
5. Варианты:
   - оставить архитектуру, усилить transaction coordinator;
   - append-only journal + projection;
   - SQLite для одного bounded subsystem;
   - полная DB migration.
6. Migration/dual-write/rollback proof-of-concept на synthetic copy.

## Go criteria

Нужны минимум два доказанных сигнала: performance ceiling, unrecoverable
partial failures, operational backup pain или feature block.

## No-go

Если текущий atomic bundle + reconciliation выдерживает production profile,
закрыть discovery рекомендацией «не менять».

## Acceptance criteria

- Inventory охватывает все durable sinks и partial-failure boundaries.
- Решение опирается на production-sized measurements без production mutation.
- Для `go` есть synthetic migration/rollback proof и оценка operational cost.

## Stop condition

Не начинать DB migration по одному аргументу «JSON устарел». Без двух go
signals задача завершается решением сохранить текущую архитектуру.
