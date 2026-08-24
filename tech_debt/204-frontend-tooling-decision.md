# 204. Оценить frontend build tooling

Приоритет: P3
Этап: 2 — обсуждение после 005
Оценка: pilot 3–5 дней
Риск: средний/высокий
Статус: not recommended now

## Проблема

Source сейчас склеивается Python assembler'ом в один fingerprinted asset. После
005 chunks станут управляемыми, но останутся global scope, ручные dependencies
и отсутствие static type checking.

## Результат

Короткий измеримый pilot и ADR либо зафиксированное решение остаться на
assembler; внедрение bundler не является автоматической частью задачи.

## Почему не сейчас

Bundler/TypeScript/framework добавят Node toolchain, build artifacts,
source maps, CSP/cache/PyInstaller integration и новый release surface.
Текущая проблема размера решается дешевле source chunks + browser tests.

## Pilot только при доказанной боли

- Взять один изолированный pure UI domain.
- Сравнить esbuild-only bundle и текущий assembler:
  build time, artifact determinism, size, CSP, desktop/offline, tests.
- Не менять UI framework.
- Не коммитить generated bundle.

## Go criteria

После 005 остаются частые ordering/global collision bugs или невозможность
тестировать modules. Иначе закрыть как no-go.

## Acceptance

ADR с измерениями и полной стоимостью CI/build/deploy. «Современнее» не
является аргументом.

## Stop condition

Если 005 устраняет ordering/collision pain или pilot ухудшает determinism,
desktop/offline либо CI, закрыть no-go без framework migration.
