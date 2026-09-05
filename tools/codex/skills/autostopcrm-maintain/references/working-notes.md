# Useful CRM Boundaries

Choose only the notes relevant to the task.

- Cleanup: `scripts/code_health_audit.py` maps file roles and active debt
  owners; `scripts/docs_audit.py` checks links and current interfaces.
  Inspect imports, dynamic registration, packaging, tests, and rollback before
  deleting a candidate. Generated output and deployed data have different owners.
- Performance: `scripts/perf_workflows.py` measures user flows; compare the
  same fixture, iterations, environment, and latency distribution. Investigate
  serialization, JsonStore persistence, change-feed preparation, and browser
  refresh only when measurements point there. Preserve durable saves, audit
  events, revision conflicts, and freshness. Synthetic timings are not live proof.
- UI: browser sources and their assembler live in
  `src/minimal_kanban/web_app_assets/`; Windows UI lives in
  `src/minimal_kanban/ui/`. Verify the rendered changed flow and its adjacent
  keyboard, mouse, loading, and refresh behavior; avoid changing business rules
  merely to simplify rendering.
- Embedded agent: a nonempty `agent/system_prompt.md` in the effective app-data
  directory overrides the code default; persisted context and tool descriptions
  are added afterward. Inspect the effective prompt before changing routing.
  Preserve custom facts and settings, JSON transport, and native action guards.
