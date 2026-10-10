# Manager structure constructor

## Open and edit

Open `/manager-structure` from the board button **Конструктор структуры
менеджера**. Any operator with a session can open a module and read its
instruction. Only the configured owner can save, create or remove modules and
relations. In clean view, clicking a module (or pressing Enter/Space) opens its
instruction. E1–E15 also show their central automotive instruction and operation
cards from the pinned catalog; other modules show the instruction saved in CRM.
Escape closes
the dialog and returns focus to the original module without moving the viewport.
The corner button switches to the editor; there the same click selects properties
and keeps drag, resize and connection editing.

The source reference and Markdown links open in a separate tab. Relative links
resolve from the corresponding Manager document in GitHub at the catalog's exact
`source_revision`, including one percent-decoding pass and `file:line` links to
`#Lline`. A line suffix takes precedence over a heading fragment; ordinary heading
links keep their fragment. The instruction parser uses CommonMark with tables: inline, full reference,
collapsed and shortcut links, balanced labels/destinations and multiline
definitions follow the same visible-link rules as the Manager documentation
audit. A closed leading YAML block is metadata and creates no navigation. Code
literals and image alt text remain literal; raw HTML tokens and their attributes
are displayed through text nodes, so they cannot create elements or extra links.
Only allowed semantic tags are built with `createElement` and `textContent`.
Unsafe URL schemes stay text. Explicit HTTP/HTTPS links retain their external
destination. A custom module without a canonical source keeps relative links
as text because their document base is unknown.
The canonical source link for a saved CRM instruction does not assert that an
owner's custom instruction has the same text as that source document.

The browser parser is the locally packaged [markdown-it 15.0.2](https://github.com/markdown-it/markdown-it/tree/15.0.2)
UMD distribution, loaded through the same guarded source include assembler as
other CRM assets; no CDN or runtime parser download is used. Its immutable
[provenance manifest](../../../src/minimal_kanban/web_app_assets/source/markdown_it.provenance.json)
records the official tarball integrity, byte count and SHA-256
`635972b985228e8af9f0143647c68616b7a3bb09f6946e7e4a52e43dcf5e7be5`.
The [license notices](../../../src/minimal_kanban/web_app_assets/source/markdown_it.LICENSE)
retain the parser's MIT license, the licenses of all compiled dependencies and
the original Node URL-parser notice. Vendor bytes are unchanged; development
source maps are omitted. Future vendor updates must verify the checksum/notices,
CommonMark oracle cases and browser interactions before publication.

Set `AUTOSTOP_MANAGER_STRUCTURE_OWNER_LOGIN` to the owner CRM login. During
transition, the older `AUTOSTOP_TELEGRAM_BEHAVIOR_OWNER_LOGIN` setting is a
fallback. In the disposable local demonstration, the owner is `admin`.

The indicator is an editable display state: off, green, yellow or red. It does
not query an integration. Codex reads module instructions as working context
through the Gateway; the diagram does not execute instructions automatically.

Automotive operation cards use a separate manual commissioning mark: red
`not_commissioned` (**Ещё не введена**), yellow `temporarily_unavailable`
(**Временно не работает**) or green `working` (**Работает нормально**). Only the
configured owner can choose and save it, including in clean view. Other operators
see the saved state. It does not probe or enable the provider, and does not prove
part fitment, stock or procurement. A missing record displays red without writing.

## Data and compatibility

The new schema is `autostopcrm.manager-structure.v1`, saved in
`manager_structure.json` beside the CRM state. When this file is absent and
the older `telegram_agent_behavior` setting contains a saved graph, the
constructor reads a compatible copy. The first save writes the new file. It
does not change or delete the older setting. An invalid older graph causes an
error rather than an empty replacement.

Coordinated release backup includes `manager_structure.json`; rollback restores
the saved file or removes one first created by the failed candidate.

The root `tool_statuses` map stores stable tool IDs with server-created
`state`, `updated_at` and `updated_by`. Graph operations preserve it, complete
backup/restore returns it, and portable templates omit commissioning state.
Readonly catalog metadata and permissions are computed, not saved graph fields.
The bundled catalog pins the exact published Manager revision and content hash;
all canonical instruction text and tool cards come from that package. The browser
compares schema, content hash and source revision when refreshing its catalog.
Delayed graph responses cannot replace a newer version or a different session.
An unconfirmed status write keeps its original body and idempotency key until
exact receipt/readback reconciliation or a definite rejection; reopening the
modal in the same page retains that attempt and the unsaved choice.

An empty CRM has an empty constructor. The sample is a portable template in
`templates/manager_structure.json`, generated from CRM operations. The local
demo script restores that template into its own ignored data directory.

## Programmatic operations

Raw CRM Gateway capabilities:

- `api:/api/manager_structure`: read version, canvas, elements, relations and
  editor permission.
- `api:/api/manager_structure/tool_catalog`: authenticated readonly bundle,
  canonical Markdown, stable module/tool IDs, provider and invocation bindings.
- `api:/api/manager_structure/apply`: `upsert_element`,
  `upsert_relation`, `layout_element`, `layout_relation`, `remove_element`,
  `remove_relation`, `set_canvas`, `reroute`, `replace`, `set_tool_status` or
  `clear_tool_status`. Status operations pass
  `tool_status: {"operation_id":"partsapi.getArticles","state":"working"}`;
  clear omits state to restore the absence of a record. Send `expected_version`
  and a new `idempotency_key` for each new intended change. After an uncertain
  outcome, reconcile or replay the original body and key before creating another
  attempt. An upsert merges supplied fields with the existing element, so an
  instruction can be changed without replacing coordinates and style. For an
  existing node, an exact `element: {"id": "E2", "instruction": "..."}` payload
  preserves all geometry and manual paths without reanchoring or rerouting.
  Structural validation, owner authorization, version and idempotency guards
  still apply; adding any other field uses the normal route checks. Raw
  writes perform a second read and compare the changed fields and version.

Status readback checks both the exact saved record and the digest of every
unrelated persisted field. Preview uses the same durable projection to prove
nonmutation; computed permission/catalog metadata does not affect this digest.
`replace` preserves exact supplied manual paths and never reroutes unrelated
relations. Supply already-valid anchors and endpoints for a code migration.

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

`scripts/sync_manager_structure_instructions.py` is an offline preparation and
checking route. It copies the retained technical graph (48 nodes, 37 relations,
fourteen E1 children E2–E15), replaces instruction text from canonical Manager
docs and keeps geometry/styles exactly. A2 reads `AGENTS.md`; A5 uses a stable
canonical pointer if its full catalog exceeds the instruction limit. Portable
artifacts exclude owner receipts and commissioning records. Pass `--bundle`
to require exact module-text parity with the already exported immutable bundle.
`--check` performs read-only artifact comparison; `--check-saved` checks stored
instruction drift and returns changed IDs with a nonzero exit status.
`--refresh-templates` writes
only the selected repository template and blueprint. `--patch-output` prepares
private preview/write bodies outside the repository and never invokes an API.
Before a later authorized application, read the complete live graph again and
regenerate the patch against its current version. The existing L30/L36 conflict
is retained; instruction refresh is not a layout repair.

### Coordinated documentation publication

Commit and publish the Manager documentation first. From that clean producer,
run `scripts/export-automotive-tools.py --revision EXACT_MANAGER_SHA --output PATH`
with its Manager Python. It exports a Git snapshot of that exact commit; it does
not export dirty files. Copy the output only into the selected CRM source
`src/minimal_kanban/web_app_assets/source/automotive_tool_catalog.json`.
Validate it with `scripts/check_automotive_tool_catalog.py
--expected-source-revision EXACT_MANAGER_SHA`; a sealed snapshot carrying
`REVISION` additionally permits `--manager-root PATH --manager-python PYTHON` to
check every referenced instruction and the complete registry/schema producer.
Use the offline instruction-refresh helper for the portable template/blueprint.

A GitHub publication leaves the installed catalog and live graph on their
existing revision. Follow the [operations runbook](../../OPERATIONS_RUNBOOK.md)
for a later explicitly authorized coordinated release. After that release,
reread the full live graph, prepare fresh instruction-only preview/apply requests
against its current version, and verify instructions and catalog pin together.
Keep geometry, manual paths and commissioning records intact.

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
