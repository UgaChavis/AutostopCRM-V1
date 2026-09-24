# 021. Embedded print interface

`printing/web_module.py` assembles the print interface and retains preview,
bridge and event-binding code. The completion-act editor lives in
`web_completion_act.py`; visual template helpers and the template workflow live
in `web_template_editor.py`. These string fragments share the existing JavaScript
scope and execute in their original positions. Reduce remaining duplication or
responsibility while preserving resource assembly and Windows offline loading.

Browser request ownership and the cancellable print-frame lifecycle live in
`web_async_context.py`. Each asynchronous continuation retains its operator,
card/workspace and request context, including failures and server-print fallback.
Changing context must never submit a follow-up write using the new operator.

Preserve draft versions/source fingerprints, preview/export ordering, bridge
messages, CSP, focus/Escape and cleanup. Bind listeners once and keep accounting
calculation in the backend. Python string escaping can alter generated JavaScript.
For mechanical extraction, compare the assembled script and HTML byte-for-byte.
`tests/test_printing_template_editor_extraction.py` protects one-time inclusion
and fragment order; `tests/test_printing_async_context.py` protects request and
frame ownership. Generated syntax is checked by `scripts/check_web_assets_js.py`.

Validate syntax, draft load/edit/save/reset, conflicts, unavailable bridge and
full completion-act preview/PDF behavior. Toolchain failures are distinct from
UI regressions. Backend templates, calculations and persistence belong to 014.
Choose further boundaries from current evidence.
