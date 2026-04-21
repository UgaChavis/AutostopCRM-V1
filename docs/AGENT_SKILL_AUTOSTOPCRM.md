# AutoStop CRM Agent Skill Guide

This is a project-specific workflow guide for Codex-style work on `autostopcrm-v1`.

Use this guide as the default operating contract for future sessions.

## Purpose

- reduce repeated context gathering
- make server and repo work repeatable
- keep changes small and verifiable
- protect production from casual edits

## Mandatory First Read

1. `00_START_HERE_AUTOSTOP_CRM.md`
2. `docs/OPERATIONS_RUNBOOK.md`
3. `docs/CODEX_WORKFLOW.md`
4. the module-specific source files

## Default Working Rules

- verify branch and HEAD before making assumptions
- read the smallest relevant files first
- prefer focused tests over broad noisy passes
- confirm production after meaningful deploys
- do not leave browser artifacts in the worktree

## Recommended Change Loop

1. inspect the current code path
2. identify the smallest risky edge
3. fix the edge
4. add regression coverage
5. run focused verification
6. sync local, GitHub, and production if the change is meant to ship

## When To Pause and Re-check

- if branch state does not match the documentation
- if server verification disagrees with local state
- if a browser artifact or temporary file appears
- if the UI and API behavior diverge

## Files To Read Often

- `src/minimal_kanban/services/card_service.py`
- `src/minimal_kanban/api/server.py`
- `src/minimal_kanban/web_assets.py`
- `src/minimal_kanban/mcp/server.py`
- `scripts/doctor.ps1`
- `scripts/run_checks.ps1`
- `docs/EMPLOYEES_MODULE.md`

## Good Output Shape

When reporting work:

- start with the result
- list the files changed
- mention verification
- mention any remaining risks
