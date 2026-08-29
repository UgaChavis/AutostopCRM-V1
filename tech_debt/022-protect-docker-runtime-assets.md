# 022. Защитить runtime PNG в Docker build context

Приоритет: P0
Статус: completed 2026-08-29; hosted CI confirmed
Риск изменения продукта: низкий; release-safety fix

## Проблема

`.dockerignore` содержит `*.png`, но по используемой Docker семантике
`filepath.Match` этот pattern совпадает только с PNG в корне context. Runtime
использует два вложенных tracked PNG:

- `src/minimal_kanban/static/favicon.png` — HTTP `/favicon.png`;
- `src/minimal_kanban/printing/assets/autostop_brand_logo.png` — логотип
  печатных документов.

Оба runtime asset сейчас обходят корневое правило, но вместе с ними в context
попадают и вложенные generated PNG. Это не точный allowlist и не защищённый
контракт. Текущий CI проверяет только `docker compose config`, а не наличие
файлов после `COPY . .`.

На момент аудита live `/favicon.png` отвечает `200`, `image/png`, 41 012 bytes,
поэтому favicon текущей production-инстанции не считается сломанным. Наличие
print-logo в текущем image этим не доказано. Дефект — небезопасный и
незафиксированный контракт следующей сборки.

## Итерационный срез

1. Заменить `*.png` на рекурсивный `**/*.png` и добавить после него две точные
   negation rules; не разрешать все PNG.
2. После `COPY . .` заставить Docker build fail-closed проверять, что оба файла
   существуют и непусты.
3. Добавить regression в deploy/docs audit tests для ignore rules и Dockerfile
   assertions.
4. Добавить в hosted CI отдельный `docker build` candidate image без login,
   push, publication или deploy. После сборки fail-closed проверить внутри
   image, что оба файла существуют и непусты.
5. Локально повторить build-context check только если Docker доступен. Его
   отсутствие не заменять предположением: обязательным доказательством остаётся
   новый hosted build job.

## Результат

- `.dockerignore` рекурсивно исключает PNG и возвращает только два exact runtime
  asset; Dockerfile fail closed проверяет оба файла после `COPY`.
- Hosted CI собирает непубликуемый candidate image и проверяет файлы внутри.
  Commit `6221c83`, workflow `33255765251` зелёный; image push и production
  deploy не выполнялись.

## Приёмка

- hosted CI с нуля собирает непубликуемый candidate image и доказывает, что оба
  файла внутри image существуют и непусты;
- API/static и printing regression tests подтверждают favicon route и непустой
  logo data URI; live-проверка остаётся deploy-handoff gate;
- `.dockerignore` продолжает исключать screenshots и прочие generated PNG;
- deploy/rollback tests, post-build verification и relevant API/printing tests
  проходят.

## Не входит

- deploy production;
- push candidate image в registry;
- изменение брендинга или бинарного содержимого PNG;
- разрешение общего `*.png` build context.
