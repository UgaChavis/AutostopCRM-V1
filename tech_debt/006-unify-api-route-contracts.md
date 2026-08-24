# 006. Свести HTTP route metadata в один registry

Приоритет: P0
Этап: 1
Оценка: 3–5 дней
Риск реализации: средний
Статус: completed locally 2026-08-23

## Реализация и доказательства (2026-08-23)

- Добавлен immutable `RouteSpec`: path/handler/registry, methods, mutation,
  auth, maintenance, response, feed expectation и readback class.
- Service, operator и isolated change-feed registries собираются с проверкой
  overlap; server auth/maintenance/GET policy выводится из concrete specs.
- Historical `PROXIED_WRITE_ROUTES`, `OPERATOR_SESSION_ROUTES`,
  `ADMIN_ONLY_ROUTES` и `READONLY_GET_ROUTES` сохранены как exact derived
  compatibility views.
- Capability parity теперь публикует RouteSpec metadata и определяет writes
  той же policy-функцией; capability gaps остаются 0, producer parity 100/100.
- Полный disjoint policy catalog содержит 169 routes: 152 service,
  14 operator и 3 change-feed. Любой неизвестный route — независимо от имени
  и глагольного prefix — fail closed; incomplete metadata и cross-registry
  duplicate также запрещены. Публичные paths/methods/envelopes не менялись.
- Route/API/parity characterization и regression tests проходят; финальный
  общий covered suite после интеграции всех P0-срезов: 1 946 тестов, 34 skip,
  `OK`.

## Результат

Handler, auth policy, maintenance policy, capability parity и change-feed
inventory получают route classification из одного исполнимого источника.
Добавление route не требует синхронно править несколько независимых sets и
manifests.

## Доказательства

- `api/route_registry.py` отдельно содержит `PROXIED_WRITE_ROUTES`,
  `OPERATOR_SESSION_ROUTES`, `ADMIN_ONLY_ROUTES` и mapping handlers.
- По `src/scripts/tests` найдено около 1 837 route literals `/api/...`.
- Capability parity и producer parity уже ловят многие drift cases, но делают
  это постфактум через отдельные manifests/inspection.
- Ошибка классификации route затрагивает auth, maintenance, audit actor,
  change feed и Gateway risk — это P0 correctness, не косметика.

## Минимальная модель

Добавить immutable `RouteSpec` для registry-owned routes:

- path;
- handler resolver/name;
- methods;
- mutation kind: read/write/checkpoint/render;
- auth kind: bearer/operator/admin/service;
- maintenance behavior;
- response kind: json/file/html/stream;
- feed expectation/readback class только там, где это runtime contract.

Derived sets сохранить как compatibility exports на первом этапе.

## Порядок

1. Characterization: сравнить текущие sets и discovered handler mapping.
2. Ввести `RouteSpec` без переключения server.
3. Генерировать прежние sets из specs и доказать exact equality.
4. Переключить parity scripts на specs.
5. Переключить handler auth/maintenance checks.
6. Удалить дублированные declarations только после equality tests.

## TDD-план

- Каждый handler имеет ровно один spec.
- Duplicate path и contradictory auth fail at import/build time.
- Admin route автоматически operator-authenticated.
- Write route не может быть marked readonly maintenance-safe без explicit
  reviewed exception.
- File/HTML route не попадает в JSON envelope path.
- Exact snapshots текущих route classifications до/после совпадают.
- Change-feed parity остаётся 100/100.

## Подводные камни

- `/api/get_repair_order` исторически classified как proxied write из-за
  compatibility create behavior; нельзя «исправить название» без контракта.
- `preview_*` может быть compute-heavy, но не state write.
- Operator personal preferences доступны во время maintenance, хотя это POST.
- Change-feed bootstrap/ack — checkpoint writes с особыми Gateway semantics.
- Static/download routes частично живут вне service registry; не тянуть их
  насильно в один тип в первом commit.
- Manifest JSON может быть release evidence. Генерировать deterministic,
  проверять committed diff, не удалять вслепую.

## Acceptance criteria

- Один source of truth для всех service-owned routes.
- Compatibility sets exact-equal старым значениям до их удаления.
- Новый write route без auth/feed/readback metadata валит test/audit.
- API, capability parity и producer parity проходят без exemptions роста.
- Публичные paths, methods, codes и envelopes не изменились.

## Проверки

`python -m unittest tests.test_api tests.test_crm_capability_parity tests.test_crm_change_feed_producer_parity -v`
`python scripts/crm_capability_parity.py --require-complete`
`python scripts/crm_change_feed_producer_parity.py --require-complete`
`python -m ruff check src/minimal_kanban/api scripts tests`

## Stop condition

Если один spec начинает включать MCP schema, UI labels и docs text, остановить:
это новый god-registry. HTTP runtime policy и cross-surface evidence должны
быть связаны стабильными IDs, а не одним огромным объектом.
