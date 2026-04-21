# AutoStop CRM Codex Workflow

This file is the practical operating guide for working on `autostopcrm-v1`.

Goal:

- make the project easier to understand on first read
- reduce guesswork during debugging and deployment
- keep local, GitHub, and production aligned
- preserve a repeatable path for tests and smoke checks

## What Makes Codex Better Here

1. A short, reliable first-read path.
2. A single place for deployment and server verification steps.
3. A stable module map so work lands in the right files.
4. A small, explicit set of checks before and after change.
5. A documented history of decisions instead of chat-only memory.

## Step-By-Step Improvement Plan

### Step 1. Build the operator runbook

Create a compact doc with:

- repo layout
- server location
- branch and HEAD rules
- deployment and smoke-check commands
- live URLs
- the safest way to verify sync across local, GitHub, and server

Status:

- implemented in `docs/OPERATIONS_RUNBOOK.md`

### Step 2. Make the first-read path explicit

Keep the root docs pointed at the same workflow so a new session can recover context fast.

Status:

- root docs now point to this workflow and the runbook

### Step 3. Add a project memory layer

Keep a small Markdown memory for:

- recurring bugs
- server changes
- module quirks
- stable commands
- decisions that should not be relearned every session

Recommended location:

- `docs/PROJECT_MEMORY.md`
- `docs/EMPLOYEES_MODULE.md` for the payroll workspace specifically

### Step 4. Add a project-specific skill guide

Codex works better when a repeatable workflow is encoded once instead of re-explained every turn.

Recommended content:

- when to inspect docs first
- which files define the product
- which checks are mandatory before changes land
- how to treat production and secrets

Recommended location:

- `docs/AGENT_SKILL_AUTOSTOPCRM.md`

### Step 5. Strengthen verification

Keep checks small and repeatable:

- `doctor`
- focused tests
- live smoke check
- branch synchronization check

### Step 6. Harden production operations

Keep production safer with:

- a staging environment if available
- separate deploy credentials
- reduced reliance on default admin credentials

### Step 7. Keep the docs current

Update the workflow when:

- the branch head changes
- production is redeployed
- a major module changes shape
- a new recurring bug appears
- a new safe check becomes part of the standard pass

## How To Use This Repo

1. Read `00_START_HERE_AUTOSTOP_CRM.md`.
2. Read `MASTER-PLAN.md`.
3. Read `PROJECT_HANDOFF.md`.
4. Read `docs/OPERATIONS_RUNBOOK.md`.
5. Read the module file you are changing.
6. Run the smallest useful checks first.
7. Sync local, GitHub, and server after meaningful changes.

## Good Default Habits

- prefer focused checks over repo-wide noise
- keep changes small enough to validate
- do not trust stale notes for HEAD or server state
- treat production secrets as managed, not casual
- document one-off discoveries in Markdown right away
