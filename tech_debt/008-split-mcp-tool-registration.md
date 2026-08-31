# 008. Разрезать MCP registration по доменам

Приоритет: P1. Статус: in progress; выбирается ровно один следующий seam.

## Цель и защищённый контракт

`create_mcp_server` должен стать orchestration небольших registrars. Mechanical
move не меняет payloads, schemas, auth, relay, idempotency или backend behavior.

- builtin/raw registry: ровно 98 unique registrations;
- characterization hash:
  `c7c68b2b73880c7a8d958b6596b7e2d61e37ebd11570ec782ee684355de2fa5d`;
- Gateway v2: ровно 24 public tools; hidden Store tools не попадают в raw escape;
- source caps принадлежат 001: `mcp/server.py` 3514, `create_mcp_server` 3104,
  `register_agent_gateway_v2` 3086.

## Выполнено и следующее

В `payloads.py` вынесены 13 models/normalization с compatibility re-exports.
Вынесены diagnostics, core board reads, board column/sticky/timer/indicator
writes, card attachment reads и shared-file reads/writes. Каждый registrar
использует frozen/slots context, возвращает exact names и сохраняет registration
order.

Следующий один seam: read-only card/board review, client/vehicle или inventory;
после него — соответствующие bounded writes с exact readback. Repair-order,
finance/payroll и manager operations — отдельные high-risk slices. Transport,
OAuth bootstrap, shared relay/normalization и runtime lifecycle остаются в
`server.py`, пока не доказана более узкая общая зависимость.

## Механика и приёмка

1. RED: names, schemas, defaults, annotations, order и backend arguments.
2. Вынести один registrar с минимальным context; не создавать god-context.
3. Проверить 98 registrations, duplicates, hash, 24 public tools и hidden tools.
4. Запустить focused MCP/backend/transport tests, снизить cap без headroom,
   затем full local и hosted CI.

Registrar ≤800 строк (target ≤500), `create_mcp_server` target ≤500. Неизменны
inventories, schemas, annotations, errors, media, order, auth и idempotency;
проходят backend/transport/OAuth, capability parity и exhaustive Gateway smoke.

`python -m unittest tests.test_mcp_registration_contracts tests.test_mcp_payload_contracts tests.test_mcp_connector_diagnostics tests.test_mcp_board_reads -v`
`python -m unittest tests.test_mcp tests.test_mcp_main tests.test_mcp_server_hardening -v`
`python scripts/crm_capability_parity.py --require-complete`
`python scripts/check_agent_gateway_v2.py --mcp-url http://127.0.0.1:41831/mcp --exhaustive`
`python scripts/code_health_audit.py --format text`

Stop: если registrar копирует relay/auth/idempotency или меняет schema, сначала
выделить отдельный helper/functional slice.
