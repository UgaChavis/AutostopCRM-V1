# 020. Release backup and recovery

`scripts/agent_release_backup.py` owns manifest parsing, inventory, verification
and restore. These operations have distinct failure modes. Improve clarity
without changing supported schemas, CLI output/exit codes or deployment
orchestration.

Validate the complete plan's paths, schema and checksums before replacing data.
Keep atomic replacement of each artifact, reparse/symlink protections and
Windows/Linux path handling. This is not a cross-artifact transaction: earlier
successful replacements can need reconciliation after a later failure.

Exercise current/old manifests, missing/duplicate entries, truncation, changed
destinations, disk/write/replace failures and repeated restore on temporary data.
`tests/test_agent_release_backup.py` and the runbook's backup/restore coverage
gate protect these contracts. Preserve redacted diagnostics.
