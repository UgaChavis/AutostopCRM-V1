# 001. Зафиксировать числовой maintainability ratchet

Приоритет: P0
Этап: 1
Оценка: 1–2 дня
Риск реализации: низкий
Статус: completed; hosted CI confirmed 2026-08-26

## Реализация и доказательства (2026-08-23)

- Все исходные 39 size-exemptions преобразованы в `RatchetBudget` с reason,
  baseline, `max_allowed` без headroom и одним `owner_task`.
- После закрытия 004 три browser exemptions удалены; gate содержал 36/36
  size-ratchets и 3/3 complexity-ratchets. После закрытия 007 удалены size и
  complexity ratchets `_make_handler`, а после MCP-среза 003 — module ratchet
  `tests/test_mcp.py`; текущий gate содержит 34/34 и 2/2 соответственно.
- Owner IDs проверяются против единственного `tech_debt/<id>-*.md`; missing,
  duplicate и некорректные mappings валят audit.
- Text/JSON отчёты детерминированно показывают current/max/delta/present.
- Текущие exact complexity caps: Gateway executor 72 и `update_card` 29.
  `_make_handler` сокращён до 127 строк и больше не требует exemption.
- Synthetic growth/shrink/config/missing-target tests и полный
  `tests.test_code_health_audit`: 18/18 `OK`; include-untracked audit после
  payload, diagnostics и core board-read срезов 008: 368 файлов, 0 issues.

## Результат

Известные oversized hotspots больше не могут незаметно расти. Каждый
grandfathered module/class/function получает измеряемый потолок и понятный
путь удаления исключения.

## Доказательства

`scripts/code_health_audit.py` сейчас содержит:

- 15 `ALLOWED_LARGE_MODULES`;
- 10 `ALLOWED_LARGE_CLASSES`;
- 9 `ALLOWED_LARGE_FUNCTIONS`.

Значение словаря — только текстовая причина. После попадания в allowlist файл
может расти без лимита. Строгий Ruff-профиль на текущем HEAD показывает 433
сигнала, поэтому включить его целиком как blocking gate нельзя.

## Scope

1. Заменить безусловные exemptions на записи с:
   `reason`, `baseline`, `max_allowed` и `owner_task`.
2. Для line-based limits использовать текущий HEAD как baseline и нулевой либо
   минимальный документированный headroom.
3. Для трёх самых опасных функций добавить точечный complexity ratchet:
   `ApiServer._make_handler`, Gateway `_execute_workflow` и
   `CardService.update_card`.
4. JSON output должен показывать текущую величину, потолок и delta.
5. Текстовый output при успехе должен кратко сообщать количество активных
   exemptions; при росте — имя hotspot и превышение.
6. Удаление/снижение exemption становится acceptance criterion последующих
   extraction-задач.

## Карта владельцев исходных exemptions

| Exemption | Owner | Ожидаемый исход |
|---|---:|---|
| module `scripts/attest_agent_gateway_v2.py` | 207 | conditional split, exemption удалить |
| module `scripts/browser_smoke.py` | 004 | split, удалить либо оставить малый CLI cap |
| module `mcp/agent_gateway_v2.py` | 009 | registry 008 — contributor; после executor split удалить |
| module `mcp/raw_gateway.py` | 009 | readback split, удалить |
| module `services/card_service.py` | 012 | срезы 010/011 — contributors; остаточный facade закрывает 012 |
| module `services/card_service_finance.py` | 019 | planner split, удалить/снизить до facade cap |
| module `services/card_service_payroll.py` | 013 | calculators split, удалить/снизить до facade cap |
| module `services/snapshot_service.py` | 018 | read models split, удалить |
| module `agent/runner.py` | 206 | 203 — обязательный go/no-go; затем удалить либо снизить |
| module `mcp/server.py` | 008 | registry split, удалить |
| module `printing/service.py` | 014 | components split, удалить |
| module `printing/web_module.py` | 021 | embedded asset split, удалить |
| modules `tests/test_service.py`, `test_api.py`, `test_agent_gateway_v2.py`, `test_web_assets.py` | 003 | четыре независимых test-slices, удалить |
| module `tests/test_mcp.py` | 003 | **удалено 2026-08-25** после MCP registration/payload slice |
| classes `PrintModuleService` | 014 | thin facade cap |
| `CardService` | 012 | снижать cap в 010/011; остаток закрывает 012 |
| `CardServicePayrollMixin` | 013 | удалить/снизить до facade cap |
| `CardServiceFinanceMixin` | 019 | удалить/снизить до facade cap |
| `SnapshotService` | 018 | удалить |
| `AgentRunner` | 206 | только после keep decision 203 |
| test classes `ApiServerTests`, `AgentGatewayV2Tests`, `CardServiceTests`, `WebAssetsTests` | 003 | соответствующий test-slice, удалить |
| function `scripts/attest_agent_gateway_v2.py:_finance_apply_audit_safe_fixes_case` | 207 | case split, удалить |
| functions `browser_smoke:_desktop_scenarios`, `_exercise_completion_act_editor` | 004 | scenario split, удалить |
| function `api/server:_make_handler` | 007 | **удалено 2026-08-25** |
| function `demo_seed:_demo_specs` | 001 | оставить bounded data-only cap; запретить рост |
| function Gateway `register_agent_gateway_v2` | 008 | registry split, удалить |
| functions Gateway `_execute_workflow`, `call_raw_capability` | 009 | executor split, удалить |
| function `raw_gateway:verify_virtual_api_write_readback` | 009 | verifier split, удалить |
| function `mcp/server:create_mcp_server` | 008 | registration split, удалить |
| function `printing/defaults:builtin_template_records` | 001 | оставить bounded data-only cap; запретить рост |
| function `test_mcp:test_mcp_tools_reach_backend` | 003 | MCP test-slice, удалить |

Составные строки сохраняют исходную карту владельцев; итоговая машинная
проверка сейчас считает ровно 36 активных owner mappings. Две
data-only фабрики не дробятся без доказанной боли: для них результат задачи —
жёсткий текущий cap, а не новый abstraction layer.

## Не входит

- Массовое исправление 433 диагностических сигналов.
- Снижение текущих baseline без предварительного refactor.
- Новый внешний quality SaaS.

## TDD-план

1. Добавить tests для grandfathered file ровно на потолке.
2. Добавить failing test для роста на одну строку.
3. Проверить class/function bounds отдельно от module bounds.
4. Проверить, что сокращение проходит и отражается отрицательной delta.
5. Проверить invalid/duplicate owner task и missing reason.
6. Проверить JSON schema и стабильный deterministic ordering.

## Подводные камни

- Не считать blank/comment-only diff бизнес-улучшением; line metric остаётся
  простым guard, а не оценкой качества.
- Не давать «+10% на всякий случай»: это легализует дальнейший рост.
- Не привязывать gate к абсолютным line numbers функций — после вставки выше
  они меняются. Ключ: `relative_path:symbol_name`.
- Nested functions Gateway должны находиться по qualified name, иначе
  `_execute_workflow` внутри registration-функции потеряется.
- Если AST не может разобрать файл, audit должен fail closed.

## Acceptance criteria

- Рост любого текущего allowed hotspot сверх потолка валит audit.
- Сокращение не требует обновлять baseline вверх.
- Все 36 текущих exemption имеют owner task из `tech_debt/`.
- `code_health_audit.py --format text/json` проходит.
- Docs audit и focused tests проходят.

## Проверки

`python -m unittest tests.test_code_health_audit -v`
`python scripts/code_health_audit.py --format text`
`python scripts/code_health_audit.py --format json`
`python -m ruff check scripts/code_health_audit.py tests/test_code_health_audit.py`

## Stop condition

Если qualified nested functions нельзя стабильно измерить штатным AST без
сложного анализатора, ограничить первый commit module/class/function length
ratchet и завести отдельный маленький follow-up только для complexity.
