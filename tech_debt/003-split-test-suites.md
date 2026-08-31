# 003. Разрезать монолитные test modules и fixtures

Приоритет: P1. Статус: in progress; следующий test slice создаётся только
вместе с production seam, которому он нужен.

## Цель и защищённый контракт

Локализовать test domains без изменения поведения, fixtures или production API;
после переноса исходный module/class cap уменьшается либо exemption удаляется.

Опубликованы MCP test slices для registration, payloads, connector diagnostics,
board reads/writes, attachment reads и shared-file reads/writes. Characterization
фиксирует 98 уникальных registrations, schemas/annotations и hash
`c7c68b2b73880c7a8d958b6596b7e2d61e37ebd11570ec782ee684355de2fa5d`; внешний
Gateway сохраняет 24 инструмента. `test_mcp_tools_reach_backend` остаётся
protocol-to-real-backend test до отдельного backend/transport slice. Exact caps
принадлежат 001 и не копируются/не повышаются здесь.

## Независимые deliverables

1. Service: board/cards, repair orders, attachments, manager compatibility,
   archive/timers.
2. API: transport/static/downloads, operator auth, authorization, dispatch,
   maintenance/errors.
3. Web assets: отдельный module для каждого extraction 005.
4. Gateway: public surface, workflows, raw escape, Store, OAuth/audit actor.
5. MCP backend/transport/runtime — только вместе с registrar/transport slice 008.

## Правила и приёмка

- Снять executed/skipped count; перенести один coherent class/group без
  переименования методов, test data, expected payloads, mocks или API.
- Сохранить setup/cleanup, temp-state ownership и async loop; fixtures узкие,
  wildcard imports запрещены. Проверить duplicate names и unittest discovery.
- Удалить перенесённый блок и снизить ratchet в том же commit. Linux CI обязателен.
- Новый test module ≤3000, class ≤2500; full unittest и hosted CI проходят.
- Для MCP неизменны 98/24 inventories, schemas, annotations и order.

`python -m unittest <old-and-new-domain-modules> -v`
`python -m unittest discover -s tests -v`
`python -m ruff format --check tests`
`python -m ruff check tests`
`python scripts/code_health_audit.py --format text`

Stop: если перенос требует production API или изменения assertions, вынести
функциональный дефект в отдельный срез.
