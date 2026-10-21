# 多市场批量商品视频任务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Codex-native job entry point for Mexico, Brazil, Indonesia, and the Philippines with an honest, inspectable batch creative plan.

**Architecture:** Keep the existing local API, CLI, and persistence model. Add market profiles and a batch registry in the existing JSON payload. Creation allocates pending items; real concepts submitted by Codex are validated and retained as revisions. Counts distinguish concepts and rendered work.

**Tech Stack:** Python, FastAPI/Pydantic, SQLAlchemy JSON payload, pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-multimarket-batch-design.md`

## Global Constraints

- Supported market codes are exactly `MX`, `BR`, `ID`, and `PH`.
- Default locales are `es-MX`, `pt-BR`, and `id-ID`; PH records a pending language choice unless `fil-PH`, `en-PH`, or `taglish-PH` is supplied.
- A batch plan is not a rendered video and must report `rendered_count=0` at creation.
- No paid API, TTS, rendering, publishing, or automatic platform search is added.

### Task 1: Market profiles and plan generator

**Files:**
- Create: `workflow/markets.py`
- Create: `workflow/batch.py`
- Test: `tests/test_batch.py`

- [x] Write tests for all four profiles, cross-market locale rejection, pending PH language, count bounds, duplicate concepts, revision preservation, representative count, and zero planned/rendered count at creation.
- [x] Run `pytest tests/test_batch.py -q` and confirm missing market/batch fields, rejected ID/PH markets and missing concept routes.
- [x] Implement profiles, pending items and concept registration with no external calls.
- [x] Run the focused tests and confirm they pass.

### Task 2: API and CLI task entry point

**Files:**
- Modify: `workflow/api.py`
- Modify: `workflow/cli.py`
- Test: `tests/test_batch.py`
- Modify: `tests/test_cli.py`

- [x] Extend `JobInput` and CLI choices to all four markets.
- [x] Persist `market_profile` and `batch` at creation; expose validated concept registration with cancelled-job rejection and source-asset membership checks.
- [x] Add tests proving BR and ID/PH creation returns locale and counts while preserving existing defaults; resolve pending PH language without recreating the batch.
- [x] Run focused API/CLI tests.

### Task 3: MCP and documentation

**Files:**
- Modify: `workflow/mcp_server.py`
- Modify: `skills/team-video-workflow/SKILL.md`
- Create: `docs/modules/batch.md`

- [x] Expose market choices and batch fields through the existing MCP create tool, with real stdio create/plan/list verification.
- [x] Document the employee-facing natural-language examples and the planned/rendered distinction; run the isolated BR24 example with three actual proposed concepts.
- [x] Run the complete project check: 75 passed, one upstream AnyIO deprecation warning. Creation-market refinements in the development chat remain in progress and need subsequent verification.
