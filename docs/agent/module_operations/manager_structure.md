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
not query an integration. Module instructions are stored text and are not passed
to the running agent.

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
  `upsert_relation`, `remove_element`, `remove_relation`, `set_canvas` or
  `replace`. Send `expected_version` and a new `idempotency_key` on every
  write. An upsert merges supplied fields with the existing element, so an
  instruction can be changed without replacing coordinates and style. Raw
  writes perform a second read and compare the changed fields and version.

The owner browser session or signed OAuth owner identity is required for
writes. Version conflicts and reused idempotency keys for different payloads
return 409. Removing a module with children or relations is rejected.

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
