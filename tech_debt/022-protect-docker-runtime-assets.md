# 022. Защитить runtime PNG в Docker build context

Приоритет: P0
Статус: готово к выполнению
Риск изменения продукта: низкий; release-safety fix

## Проблема

`.dockerignore` содержит `*.png`, но runtime использует два tracked PNG:

- `src/minimal_kanban/static/favicon.png` — HTTP `/favicon.png`;
- `src/minimal_kanban/printing/assets/autostop_brand_logo.png` — логотип
  печатных документов.

Docker удаляет совпавшие ignore patterns из build context. Текущий CI проверяет
только `docker compose config`, а не наличие файлов после `COPY . .`.

На момент аудита live `/favicon.png` отвечает `200`, `image/png`, 41 012 bytes,
поэтому favicon текущей production-инстанции не считается сломанным. Наличие
print-logo в текущем image этим не доказано. Дефект — небезопасный и
незафиксированный контракт следующей сборки.

## Итерационный срез

1. Добавить две точные negation rules после `*.png`; не разрешать все PNG.
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
