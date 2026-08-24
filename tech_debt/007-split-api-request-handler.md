# 007. Разделить HTTP request handler

Приоритет: P1
Этап: 1
Оценка: 4–6 дней
Риск реализации: средний/высокий
Статус: ready for P1 after completed 006

## Результат

`ApiServer._make_handler` перестаёт быть 1 352-строчной фабрикой с 35 methods.
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

`python -m unittest tests.test_api tests.test_api_login_rate_limit tests.test_api_proxy_auth -v`
`python scripts/check_live_connector.py --strict --skip-public-site --skip-public-write-protection --local-api-url http://127.0.0.1:41731 --skip-mcp --operator-username $env:AUTOSTOP_SMOKE_OPERATOR_USERNAME --operator-password $env:AUTOSTOP_SMOKE_OPERATOR_PASSWORD --expect-admin`
`python scripts/crm_change_feed_producer_parity.py --require-complete`

Для smoke credentials использовать только env vars по runbook; не добавлять
значения в task или shell history.

## Stop condition

Если extraction требует поменять protocol semantics, вернуть adapter на
предыдущий seam и оформить отдельный API change. Не делать transport rewrite.
