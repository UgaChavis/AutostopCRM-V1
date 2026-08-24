# 206. Разделить AgentRunner, только если embedded runtime сохраняется

Приоритет: P2
Этап: 2
Оценка: 6–10 дней
Риск реализации: средний/высокий
Статус: blocked by go/no-go 203

## Результат

После подтверждённого решения сохранить runtime task loop, scenario planning,
tool execution, autofill analysis и result serialization разделены.
Network/provider errors имеют явную classification.

## Доказательства

- `agent/runner.py` — 5 093 строки.
- `AgentRunner` — 4 865 строк / 146 methods.
- 18 strict complexity signals.
- В runner найдено 11 broad `except Exception` — часть оправдана runtime
  isolation, но classification размазана.
- Код подключён через desktop и MCP startup, но это ещё не доказывает живое
  использование. До решения 203 дорогой refactor запрещён.

## Seams

- `AgentTaskOrchestrator`;
- `ScenarioPlanner`;
- `ToolExecutionEngine`;
- `CardAutofillAnalyzer`;
- существующий `AgentRunnerOutputMixin` развить в serializer;
- `AgentFailureClassifier`.

## Порядок

1. Characterize task state transitions and emitted logs.
2. Вынести pure prompt/context builders.
3. Вынести output serialization.
4. Вынести tool execution.
5. Вынести autofill.
6. Оставить runner loop thin.
7. Сузить broad exceptions на новых boundaries.

## TDD-план

- provider timeout/invalid JSON/refusal;
- tool validation/authorization/network failures;
- exact expected revision and readback;
- cancellation/shutdown between stages;
- task serialization/restart;
- payload size/redaction;
- partial autofill and no overwrite of operator facts;
- repeated task/idempotency behavior.

## Подводные камни

- Background thread shutdown и scheduler ownership.
- Logging не должен содержать prompt/private attachment bodies.
- Не смешивать old embedded agent с Gateway v2 surface.
- Model nondeterminism: tests используют fakes/golden structured payloads.
- Broad exception на top loop допустим только после classification/log-safe
  result и продолжения scheduler.

## Acceptance criteria

- Runner class ≤ 1 000 строк; components ≤ 800.
- No behavior/schema/log-level drift in characterization tests.
- Broad exceptions сокращены либо имеют explicit boundary rationale.
- Agent runtime, scenarios, payload hardening и startup tests проходят.
- Runner exemptions удалены/снижены.

## Проверки

`python -m unittest tests.test_agent_scenarios tests.test_agent_payload_hardening tests.test_agent_runner_output tests.test_agent_runner_serialization tests.test_agent_runtime_check -v`
`python scripts/check_agent_runtime.py --help`

## Stop condition

Если решение 203 — retire, эту задачу закрыть как `not planned` и выполнять
малый compatibility-aware retirement plan вместо декомпозиции.
