# 205. Оценить декомпозицию deploy.sh

Приоритет: P2
Этап: 2 — сначала design review
Оценка: discovery 3–5 дней
Риск: очень высокий
Статус: proposed, no rewrite authorized

## Проблема

`deploy.sh` около 947 строк и координирует preflight, image build, Manager
releases, OAuth snapshot/rotation, maintenance, backups, Store network,
candidate smoke, rollback и retention. Это большой release blast radius.

## Результат

ADR и максимум один read-only helper pilot с доказанной parity; deploy rewrite
не входит в scope.

## Почему не рефакторить сразу

Script имеет extensive tests и fail-closed semantics. Декомпозиция shell ради
размера может изменить traps, variable scope и cleanup ownership, ухудшив
надёжный текущий rollback.

## Discovery

1. Построить state machine release phases и owned artifacts.
2. Зафиксировать failure injection matrix.
3. Найти pure/read-only helpers, которые безопасно вынести первыми:
   env validation, manifest validation, retention planning.
4. Сравнить:
   - оставить shell orchestration;
   - вынести pure helpers в Python;
   - отдельный release orchestrator.
5. Проверить POSIX/Linux-only behavior.

## Go criteria

Есть повторяющиеся реальные defects/невозможность тестировать конкретную фазу,
а proposed seam сохраняет trap ownership и rollback proof.

## Acceptance

ADR и pilot только на read-only helper. Production deploy behavior не
изменяется без отдельного owner-approved release task.

## Stop condition

Любое отличие traps, maintenance cleanup, rollback ownership или release
artifact lifecycle останавливает pilot; production deploy не запускать.
