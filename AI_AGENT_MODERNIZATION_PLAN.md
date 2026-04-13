# AI Agent Modernization Plan

Date: `2026-04-14`
Branch: `autostopCRM`

This file is the working implementation plan for turning the current server AI into a more multilayered and reliable agent.

## 10-Stage Plan

1. Create a scenario framework and registry so scenario behavior can leave `runner.py` safely.
2. Move deterministic card scenarios into dedicated scenario modules without changing runtime behavior.
3. Expand evidence from simple booleans into graded facts with source and confidence.
4. Split planning into deterministic eligibility gates and a richer strategy planner.
5. Introduce explicit outcome states such as `completed_partial` and `blocked_missing_source_data`.
6. Add a goal verifier on top of the current write verifier.
7. Promote related-card context into a first-class evidence source.
8. Add fallback behavior for weak VIN decoding while keeping writes safe.
9. Reduce prompt burden by moving more policy and write scope decisions into code.
10. Clean up `runner.py`, update docs, and run full regression plus production smoke.

## Current Execution Status

- Stages 1-9: completed
- Stage 10: in progress

## Stage 1 Scope

Stage 1 is intentionally low-risk.

It does not change production orchestration behavior yet.
It introduces:

- a scenario executor interface;
- scenario context and scenario result shapes;
- a registry that maps scenario ids to executors;
- a default deterministic scenario registration set.

The purpose is to create a stable spine for the next stages.
