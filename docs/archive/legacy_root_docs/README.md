# Legacy Root Docs Archive

This directory stores older connector notes, screenshots, one-off operator instructions, and debugging documents that are no longer part of the primary root-level documentation set.

Keep active onboarding and operational docs in the repository root:

- `00_START_HERE_AUTOSTOP_CRM.md`
- `PROJECT_HANDOFF.md`
- `README.md`
- `AUTOSTOPCRM_FULL_INSTRUCTION.txt`
- `API_GUIDE.md`
- `MCP_GUIDE.md`
- `README_SETTINGS.md`
- `GPT_AGENT_01_VERIFIED_MCP_TOOLS.txt` through `GPT_AGENT_11_AGENT_AUTOFILL_ORCHESTRATION.md`

Keep `CHATGPT_CONNECTOR_SETUP.md` in the root as long as runtime code and tests resolve that exact path.

When archiving a root-level document here:

- prefer moving only files that are not referenced by runtime code or tests
- update `README.md` or `PROJECT_HANDOFF.md` if the active document set changes
- do not delete a file blindly if it may still be used in a manual production workflow
