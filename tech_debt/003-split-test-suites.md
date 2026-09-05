# 003. Test maintainability

Service, HTTP, Gateway and web tests contain large suites and repeated fixtures.
Consolidate equivalent cases and use domain fixtures where that improves clarity.
Tests of retired behavior and checks of incidental source wording can go; keep
unique business, authorization, transport and recovery outcomes.

Preserve temporary-state ownership, cleanup, async-loop isolation and unittest
discovery. MCP registration/schema and public-surface expectations come from the
current contract tests, not a copied count or hash in this document.

Check the affected suites and discovery; the full local and hosted gates establish
release coverage. There is no requirement to keep obsolete assertions or to
split a module solely to reduce its line count.
