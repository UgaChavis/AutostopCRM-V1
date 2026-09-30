# Manager structure constructor

## Open and edit

Open `/manager-structure` from the board button **Конструктор структуры
менеджера**. Any operator with a session can open a module and read its
instruction. Only the configured owner can save, create or remove modules and
relations. Click a nested module to edit its independent full instruction.
The square toolbar button shows only the diagram; press Esc to restore controls.

Set `AUTOSTOP_MANAGER_STRUCTURE_OWNER_LOGIN` to the owner CRM login. During
transition, the older `AUTOSTOP_TELEGRAM_BEHAVIOR_OWNER_LOGIN` setting is a
fallback. In the disposable local demonstration, the owner is `admin`.

The indicator is an editable display state: off, green, yellow or red. It does
not query an integration. Codex reads module instructions as working context
through the Gateway; the diagram does not execute instructions automatically.

## Data and compatibility

The new schema is `autostopcrm.manager-structure.v1`, saved in
`manager_structure.json` beside the CRM state. When this file is absent and
the older `telegram_agent_behavior` setting contains a saved graph, the
constructor reads a compatible copy. The first save writes the new file. It
does not change or delete the older setting. An invalid older graph causes an
error rather than an empty replacement.

Coordinated release backup v4 includes `manager_structure.json`; rollback restores
the saved file or removes one first created by the failed candidate.

An empty CRM has an empty constructor. The sample is a portable template in
`templates/manager_structure.json`, generated from CRM operations. The local
demo script restores that template into its own ignored data directory.

## Programmatic operations

Raw CRM Gateway capabilities:

- `api:/api/manager_structure`: read version, canvas, elements, relations and
  editor permission.
- `api:/api/manager_structure/apply`: `upsert_element`,
  `upsert_relation`, `layout_element`, `layout_relation`, `remove_element`,
  `remove_relation`, `set_canvas`, `reroute` or `replace`. Send `expected_version` and a new `idempotency_key` on every
  write. An upsert merges supplied fields with the existing element, so an
  instruction can be changed without replacing coordinates and style. Raw
  writes perform a second read and compare the changed fields and version.

Relations expose their route controls in both the constructor and this API:

- `route_mode: "auto" | "manual"` selects generated or owner-specified geometry.
- `path` accepts absolute SVG `M`, `L`, `H`, `V`, `Q` and `C` commands. A manual
  path starts at the `from` card and ends at `to`; the service derives missing
  anchors from its endpoints or snaps it to the supplied anchors.
- `from_anchor` and `to_anchor` are `{ "side": "left|right|top|bottom",
  "offset": 0..1 }`, measured along that card edge. Moving or resizing either
  endpoint card moves the path endpoint and its adjacent curve control point;
  internal waypoints stay fixed. A move that blocks the route is rejected.
- `label_mode: "manual"` preserves `label_x` and `label_y` as canvas coordinates;
  `auto` lets the router place the ID label again.
- `direction` is `forward`, `reverse`, `both` or `none` and controls arrowheads.

Use `layout_relation` to ask CRM to route an automatic relation, and
`upsert_relation` to save an exact manual path, anchors, label position and
direction. The write readback compares the saved values. `set_canvas` can grow
the workspace without moving existing cards; pan, zoom and **Вместить** navigate
the canvas and fit the populated schema. Automatic routes avoid cards and
parallel overlaps, account for curved paths and crossing costs, and reject
blocked manual routes.

The screen and API read the same saved `manager_structure.json`. A compact MCP
summary may shorten large arrays; pass `allow_large_output: true` when the full
module and relation lists are needed. A `truncated_items` marker means that
summary is incomplete and is not evidence that a relation is absent.

The owner browser session or signed OAuth owner identity is required for
writes. Version conflicts and reused idempotency keys for different payloads
return 409. Removing a module with children or relations is rejected.

### Codex dialogue sequence

1. Discover these two existing raw capabilities and their current schema hashes.
   Read the full graph with `allow_large_output: true`. Work by stable module and
   relation IDs, semantic fields, hierarchy and cardinal anchors; CRM owns geometry.
2. Send a narrow change with `preview: true`, current `expected_version` and a
   fresh request key. The Gateway verifies that the entire saved snapshot stayed
   unchanged. Preserve the returned draft separately from the saved graph.
3. Save the change with another fresh request key and the current version.
   Retry an uncertain write with its original key; on 409 read again before editing.
4. Read the complete snapshot again and compare the accepted fields, version,
   paths and label coordinates. The open screen polls every five seconds while
   retaining its viewport and unsaved property text.

`operation: "reroute"` recalculates the current saved graph atomically without
changing cards, instructions, IDs, hierarchy, directions or label visibility.
Its preview and write return `routes` and `labels` maps keyed by relation ID.
The write verifier compares both maps with a fresh read. Back up the full private
snapshot before a production reroute; use version guards for any later restore.

Automatic routes reserve 12 canvas units around cards, enter normally through
straight 16-unit terminal segments, and separate parallel runs by 16 units.
Short facing connections may be straight with a smaller terminal length.
Deterministic A* uses length plus 24 per bend and 96 per crossing. A narrow layout
has a two-second calculation budget; a full reroute has fifteen seconds. Failure
names the relation and saves no partial graph. Manual SVG geometry remains supported.
Visible automatic ID labels sit on a free straight section of their own wire;
other wires avoid their rectangles. An impossible visible label is an explicit
routing error. Full relation names appear beside the ID on hover, focus or selection.

`python scripts/manager_structure_template.py export --url URL` writes an
inspectable JSON template using a fresh API read. The `restore` command
restores it and performs an exact readback. Use
`AUTOSTOP_MANAGER_STRUCTURE_SESSION` for the owner session token. Restore
refuses to replace a nonempty diagram without `--replace-existing`.

## Local demonstration

```powershell
python scripts/run_manager_structure_demo.py
```

The script starts a synthetic CRM on port 42991 by default. Sign in through the
root page as `admin / admin` and open `/manager-structure`. Its data persists
under ignored `output/manager-structure-demo`. The `--build-reference`
option builds the current sample on an empty stand through one API operation
per module and relation, then exports the template. It refuses an occupied
diagram.
