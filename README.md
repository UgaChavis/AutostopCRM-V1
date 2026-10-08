# AutoStop CRM

AutoStop CRM is the active workshop CRM on `autostopcrm-v1`: board, clients,
vehicles, repair orders, warehouse, cashboxes, payroll, files, and API/MCP
integrations. `minimal_kanban`, `%APPDATA%\Minimal Kanban`, and
`Start Kanban.exe` are compatibility names for this same product.

## Source Of Truth

Use current code and focused checks first:

- [AGENTS.md](AGENTS.md) — autonomy and durable safety boundaries;
- [operations runbook](docs/OPERATIONS_RUNBOOK.md) — local checks, production,
  deployment, rollback, and maintenance;
- [API guide](API_GUIDE.md) — HTTP contracts;
- [MCP guide](MCP_GUIDE.md) — Gateway v2, Store boundary, and action guards;
- [ChatGPT/Responses compatibility](CHATGPT_CONNECTOR_SETUP.md) — OAuth setup.

The single [CRM development skill](https://github.com/UgaChavis/AutostopCRM-V1/blob/autostopcrm-v1/tools/codex/skills/autostopcrm-maintain/SKILL.md)
is versioned here; the runbook describes checking or installing its local copy.
Role entrypoints and priority are in AGENTS.md. Current code-health budgets and
ownership are defined by `scripts/code_health_audit.py`; the compact owner
cards under `tech_debt/` identify its existing budget owners, without a roadmap.

Generated builds, release copies, screenshots, private bundles, and old plans
are not sources of truth. For production, use the runbook to compare local,
remote, and server revisions and verify live health.

## Architecture

```text
Browser UI / MCP / API clients -> HTTP API -> domain services -> JsonStore
Owner MCP client -> 24-tool Gateway v2 -> internal Store adapter -> Store API
```

- `src/minimal_kanban/services/` owns business behavior;
  `storage/json_store.py` owns persistence and normalization.
  `SnapshotService` owns journal reads, visibility filtering and response assembly;
  `services/card_log_projection.py` formats the permitted events into entries,
  groups, totals and Markdown.
  `services/board_read_projection.py` formats permitted board content and event
  sections; section-only reads skip preparation of the unused section.
- `api/server.py` and `api/route_registry.py` own HTTP transport,
  authentication, and mutation classification.
- `src/minimal_kanban/mcp/` owns the public Gateway v2, action guards, and the
  internal Store adapter. API, MCP, UI, and scripts use the same services.
- `src/minimal_kanban/web_app_assets/source/` plus `assembler.py` own browser
  assets; `module_assets.py` owns versioned on-demand panel bundles.
- `printing/web_module.py` assembles the print markup and scripts in one shared
  JavaScript scope. Its internal `web_styles.py`, `web_completion_act.py` and
  `web_template_editor.py` own styles, the completion-act editor and template
  editing; `web_async_context.py` and `web_workspace_loading.py` own asynchronous
  context and workspace loading. The board assembler consumes the combined assets.
  The built-in vehicle acceptance act prints the saved card's **«Краткая суть»**
  (`card.title`) under **«Какой ремонт необходимо выполнить?»** in preview,
  PDF, and print output. Documents without a CRM card keep the manually entered
  repair reason, including line breaks. Custom templates can use `{{card.title}}`
  for the card value or `{{{vehicle_acceptance_act.requested_repair_html}}}` for
  the same source selection as the built-in act.
- `main.py` and `main_mcp.py` are desktop and API/MCP entrypoints. The Qt/PySide6
  window hosts the browser UI, integration settings, and printing runtime.
- Detached service bundles prepare mutations before JsonStore commits them;
  derived repair-order text files are published after the authoritative save.

Agent workflows, context selection and action guards are documented in the
[MCP guide](MCP_GUIDE.md).

## Manager infrastructure map

The retained reference map at `/module-map` is separate from the new diagram.
The public HTML is a shell; `GET /api/get_module_map_infrastructure` still requires
an operator session and returns `autostopmanager.infrastructure-map.v1`.
The source dataset is `web_app_assets/source/manager_infrastructure.json`: 48
element IDs and 37 connection IDs, with retained coordinates, nesting, full
canonical instructions and protocols. The former
application/IT maps and their snapshots are retired. Removed IDs `N1`, `N2`, `C1`,
`L8`, and `L9` are not reused. The Codex-to-CRM route is `A2` → `L10` → `C2` →
`L11` → `C3`; the direct MCP/HTTPS route keeps OAuth 2.1.

The main instruction and note reading route is `A2` → `A3` → `D2` → `D3`:
Codex reads local files through CLI. `D1` → `D2` represents only specific
Manager functions that use saved technical experience, not general knowledge search.
`L6` connects the work account to the compact wake node without a visible label.
`E1` has fourteen children, `E2`–`E15`: vehicle identity, part identification,
catalogs, crosses, fitment, maintenance, fluids, new and used parts, market prices,
labor times/prices, diagnostics and public research. `E8` covers fluids and
`E15` covers public search. Provider instructions and readiness live in the
pinned tool cards; source definitions do not prove live provider availability.

Static node `indicator` values are manually maintained display states: `on`
(green), `off` (red), or `unknown` (yellow; also the fallback for missing or
invalid B4 values). They do not switch or poll a provider. G1 retains its live
automation status. `A1.links` lists
the main Manager instructions on GitHub for the read-only detail panel.

The map supports keyboard selection, pan/zoom, and a read-only detail panel.
The `G1` automation center is the explicit exception: while its drawer is open,
it polls `/api/automation_center/status` every five seconds and lets an authorized
administrator submit typed commands to `/api/automation_center/control`. Desired
switches and actual-state lamps stay separate; every mutation is followed by a
fresh server readback. Other map elements never invoke a business operation or
poll a provider. `#L10`, `#L7`, or `#G1` links focus a specific element after authentication;
obsolete hashes clear the selection and show the map normally.
Browser regression tests use a disposable local CRM with synthetic data:
`python -m unittest tests.test_module_map tests.test_module_map_browser -v`.

## Manager structure constructor

Board settings open **«Конструктор структуры менеджера»** at
`/manager-structure`. The configured owner can move and resize modules, edit
titles and full instructions, and connect or reconnect links with the mouse.
The corner button switches between a clean diagram and the editor. Geometry
is saved on mouse release; text is saved with the form button. The diagram
shows larger and nested module codes, status indicators, and only relation IDs.
Full relation labels remain in hover hints and the properties panel. G1 reads
automation status; other modules can use manual green, yellow, or red states.
E1–E15 use the canonical Manager Markdown exported in the pinned automotive
bundle. In clean view, click a module or press Enter/Space to open its central
instruction and operation dialog; editor mode retains geometry and text editing.
The dialog shows provider, implementation state, invocation and limitations for
each stable operation ID. The configured owner can save a red **«Ещё не введена»**,
yellow **«Временно не работает»**, or green **«Работает нормально»** commissioning
mark. Other operators only read the mark. These marks never call or enable a
provider, and never establish part fitment or stock.

Orthogonal routes may cross at a visible bridge. Shared runs and free parallel
lanes closer than 10 diagram units are rejected. Saved SVG paths remain
orthogonal; bridges are
drawn by the constructor.

The diagram is durable CRM data in `manager_structure.json` beside the CRM
state. If an existing CRM has a saved `telegram_agent_behavior` graph, the
constructor reads a compatible copy and writes its own file only on the first
edit. The original setting and the older API remain intact for rollback. The
owner is configured by `AUTOSTOP_MANAGER_STRUCTURE_OWNER_LOGIN`, falling back
to the existing `AUTOSTOP_TELEGRAM_BEHAVIOR_OWNER_LOGIN`. Other operators can
view modules and instructions. Browser and raw Gateway writes require the owner,
an exact version and an idempotency key; raw writes verify a second read.

The portable example is `templates/manager_structure.json`. It preserves the
retained 48-node/37-relation technical graph and canonical Manager instructions.
To run the persistent local demonstration:

```powershell
python scripts/run_manager_structure_demo.py
```

Open `http://127.0.0.1:42991/`, log in as `admin / admin` in this synthetic
stand, then open `http://127.0.0.1:42991/manager-structure`. Demo data lives in
ignored `output/manager-structure-demo`. Export and restore a template with
`scripts/manager_structure_template.py export|restore --url URL` and an owner
session in `AUTOSTOP_MANAGER_STRUCTURE_SESSION`. Restore refuses to overwrite a
nonempty diagram unless `--replace-existing` is explicit. The reference can be
rebuilt on an empty stand with `--build-reference`.

For focused edits, `scripts/manager_structure_edit.py` changes a module,
relation, or UTF-8 instruction through the versioned API and verifies readback.
`scripts/manager_structure_reroute.py` explicitly recalculates a template or
local diagram after writing a backup outside the repository. Existing saved
v1 diagrams retain their paths until that explicit reroute.

`scripts/check_automotive_tool_catalog.py` validates the bundled content hash and
exact Manager source pin offline. The self-contained
`web_app_assets/source/automotive_tool_catalog.json` travels inside the CRM image;
the API never reads a mutable Manager checkout when opening a dialog. Local CI
and the Docker runtime contract validate this artifact. Full release backups
include the `tool_statuses` map; portable graph exports omit it and graph restore
preserves the destination's existing commissioning marks. See the
[operations runbook](docs/OPERATIONS_RUNBOOK.md) for release-tuple verification.

`scripts/sync_manager_structure_instructions.py` prepares or checks these two
artifacts offline from a fresh full technical graph and canonical Manager docs.
Pass `--bundle` to require parity with the already exported immutable package.
`--refresh-templates` updates repository artifacts; `--check` only reads them.
`--check-saved` reads stored texts and exits nonzero with changed node IDs on drift.
An optional `--patch-output` outside the repository prepares instruction-only
preview/write request bodies for a later authorized release, never sends them.
Fresh full readback and current version guards are required before application.
The retained L30/L36 route conflict is preserved; instruction refresh does not
reroute the owner's graph.

## Local Development

```powershell
.\scripts\setup_dev.ps1 -InstallGitHooks
.\scripts\doctor.ps1
.\scripts\run_checks.ps1
.\scripts\run_checks.ps1 -Profile ci
.\scripts\run_dev.ps1
.\scripts\run_mcp_server.ps1
```

Local API/UI defaults to `http://127.0.0.1:41731`; MCP defaults to
`http://127.0.0.1:41831/mcp`. Production topology and release recovery are in
the [operations runbook](docs/OPERATIONS_RUNBOOK.md).

Dependency pins have one hierarchy: `requirements-common.txt` ->
`requirements-runtime.txt` (server/browser automation) -> `requirements.txt`
(Windows packaging) -> `requirements-dev.txt` (development and verification).

## Safety

Do not commit secrets or runtime/production data, or manually alter production
state, finance, or order history. Public anonymous API/MCP reads and writes are
blocked in production. Use the narrow guide or runbook for high-impact work.
