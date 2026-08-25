# 007. Разделить HTTP request handler

Приоритет: P1
Этап: 1
Оценка: 4–6 дней
Риск реализации: средний/высокий
Статус: completed 2026-08-25

## Реализация и доказательства (2026-08-25)

- `_make_handler` сокращён с 1 352 до 127 строк; вложенный handler удалён.
- Выделены `RequestContextFactory` (93 строки), `HttpResponseWriter` (176),
  `StaticAndDownloadResponder` (449), `OperatorLoginLimiter` (62),
  `AuthenticationPolicy` (289), `JsonRouteDispatcher` (159) и тонкий
  `_ApiRequestHandler` (351).
- Самый длинный adapter method — `do_POST`, 109 строк. На каждый запуск
  `ApiServer` по-прежнему создаётся уникальный subclass и отдельный limiter;
  process-global runtime state не появился.
- Порядок POST-проверок и намеренная GET-асимметрия сохранены. Route policy
  выводится из immutable `RouteSpec`, а mutable `ROUTES` оставлен совместимым
  с runtime/test injection.
- Добавлены 10 transport/auth regression-тестов: malformed/truncated/empty
  body, bearer/maintenance order, registry-vs-protected GET order,
  ServiceError/generic envelopes, log secrecy, trusted `X-Real-IP` и release
  login reservation после non-auth failures, а также fail-closed запрет route
  без соответствующего `RouteSpec`.
- Исправлены два найденных TDD-дефекта: ненулевое усечённое тело больше не
  dispatch-ится, unexpected exception не пишет message/payload в лог. Legacy
  `Content-Length: 0` по-прежнему означает `{}`.
- Оба hardening-дефекта найдены TDD в процессе extraction и оставлены в одном
  полностью проверенном локальном P1 code-change. Искусственно возвращать старую
  реализацию внутри новых helper-классов только ради истории commits означало
  бы повторно менять validated tree и повышать риск для auth/body/log
  семантики. В следующих срезах поведенческие исправления отделять до широкого
  прогона, когда это не требует обратной хирургии по готовому change-set.
- `_make_handler` удалён из size/complexity ratchets: gate теперь содержит
  35/35 size и 2/2 complexity exemptions.
- Локальный полный covered suite: 1 965 тестов `OK` (`skipped=34`); 13/13
  coverage floors прошли, global branch coverage 79,36%, `api/server.py`
  88,83%, release backup/restore 78,40%.
- Capability parity: 175 actions, 170 covered, 0 gaps, 5 human-session
  exemptions; change-feed producer parity 100/100. Browser core 11/11 без
  console/page/request errors; production-sized stage-1 performance gate без
  нарушений.
- Независимый итоговый review: 9,4/10, runtime/security blockers не найдено.

## Результат

`ApiServer._make_handler` перестал быть 1 352-строчной фабрикой с 35 methods.
Transport parsing, auth, static/files и service dispatch тестируются отдельно,
при этом используется тот же stdlib HTTP server и публичный протокол.

## Доказательства

- `api/server.py` — 2 125 строк.
- Nested `RequestHandler` — около 1 219 строк.
- `_make_handler` — 1 352 строки, measured branch complexity 171; оба значения
  защищены ratchet-ами без headroom.
- Функция одновременно знает bearer, operator session, trusted service
  identity, rate limit, static routes, downloads, errors, audit actor и
  dispatch.

## Минимальные seams

1. `RequestContextFactory`: method/path/query/headers/request id/body limit.
2. `AuthenticationPolicy`: bearer, operator, admin, service identity.
3. `StaticAndDownloadResponder`: board assets/dashboard/attachments/files.
4. `JsonRouteDispatcher`: registry lookup, maintenance, handler call, envelope.
5. Тонкий `BaseHTTPRequestHandler` adapter.

Не вводить новый web framework.

## Порядок

1. Characterize full auth × route × maintenance matrix.
2. Вынести pure header/path/body helpers.
3. Вынести response writers.
4. Вынести static/download responder.
5. Вынести auth decision object.
6. Переключить service dispatch на `RouteSpec` из 006.
7. Удалить nested-function exemption.

## TDD-план

- malformed content length/body/JSON;
- HEAD/OPTIONS/GET/POST matrix;
- bearer absent/wrong/right;
- operator stale/disabled/admin/non-admin;
- trusted service identity cannot be claimed publicly;
- login rate-limit reservation and per-client isolation;
- maintenance read/write behavior;
- file traversal/symlink/content disposition;
- ServiceError vs unexpected exception envelope and no secret leakage.

## Подводные камни

- `BaseHTTPRequestHandler` invokes methods by name; static dead-code analysis
  не видит `do_GET/do_POST` references.
- Не менять timing/order auth checks: это может открыть existence oracle.
- Не читать request body дважды.
- Streaming/file responses нельзя оборачивать JSON.
- `X-Real-IP` доверяется только ограниченным peers; preserve exact rule.
- Logging не должен печатать tokens, passwords или business payloads.

## Acceptance criteria

- Adapter method ≤ 150 строк; policy components ≤ 500 строк каждый.
- `_make_handler` exemption удалён.
- Все существующие API/auth tests и browser core smoke проходят.
- HTTP status, headers, error codes и response bodies contract-equivalent.
- Login rate-limit concurrency tests проходят многократно.
- Нет нового runtime dependency.

## Проверки

`python -m unittest tests.test_api tests.test_api_transport_contracts tests.test_contracts -v`
`python scripts/check_live_connector.py --strict --skip-public-site --skip-public-write-protection --local-api-url http://127.0.0.1:41731 --skip-mcp --operator-username $env:AUTOSTOP_SMOKE_OPERATOR_USERNAME --operator-password $env:AUTOSTOP_SMOKE_OPERATOR_PASSWORD --expect-admin`
`python scripts/crm_change_feed_producer_parity.py --require-complete`

Для smoke credentials использовать только env vars по runbook; не добавлять
значения в task или shell history.

## Stop condition

Если extraction требует поменять protocol semantics, вернуть adapter на
предыдущий seam и оформить отдельный API change. Не делать transport rewrite.
