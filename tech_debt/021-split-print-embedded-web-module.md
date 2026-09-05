# 021. Embedded print interface

The print asset combines markup/styles, draft editing, preview, bridge calls and
binding. Reduce duplication or separate responsibilities when it helps; retain
the existing resource and Windows offline loading without a new toolchain.

Preserve draft versions/source fingerprints, preview/export ordering, bridge
messages, CSP, focus/Escape and cleanup. Bind listeners once and keep accounting
calculation in the backend. Python string escaping can alter generated JavaScript.

Validate syntax, draft load/edit/save/reset, conflicts, unavailable bridge and
full completion-act preview/PDF behavior. Toolchain failures are distinct from
UI regressions. Component count, extraction sequence and size targets are choices
informed by current evidence, not mandatory steps.
