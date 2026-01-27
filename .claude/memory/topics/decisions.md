# Decisions

> Architecture decisions and their rationale.

---

## 2025-01-22: Jobs + Operations over Triple Dispatcher

**Decision**: Replace workflows/tasks/actions with Jobs + Operations pattern

**Options considered**:
1. Keep triple dispatcher, add real clients
2. Jobs + Operations (chosen)
3. Single monolithic handler

**Rationale**:
- Jobs are self-contained orchestrators
- Operations are reusable across jobs
- Cleaner than 3-layer separation
- Easier to test and maintain

---

## 2025-01-22: Transport-Agnostic Core

**Decision**: Core knows nothing about HTTP/MCP/CLI

**Rationale**:
- Same job runs from CLI, Cloud Function, or MCP
- RequestContext carries transport info for audit
- Adapters translate transport → core calls

---

## 2025-01-22: Real XML-RPC from Start

**Decision**: Implement real Odoo client immediately, no stubs

**Rationale**:
- Stubs were blocking all functionality
- Test credentials available
- Mock in tests, real in production

---

## 2025-01-22: Secret Manager + .env.local

**Decision**: Use Secret Manager in production, .env.local for development

**Rationale**:
- Secret Manager is GCP best practice
- .env.local allows local development without GCP
- Config module handles fallback automatically

---

## 2025-01-22: Delete Old Code Immediately

**Decision**: Remove old workflows/tasks/actions, don't deprecate

**Options considered**:
1. Keep old code alongside new (migration period)
2. Delete immediately (chosen)

**Rationale**:
- Patterns captured in memory/learnings
- Clean slate prevents confusion
- No production dependencies on old code

---

## 2025-01-24: Interventions Module (Refactor)

**Decision**: Extract intervention tracking from scattered locations into `core/interventions/`

**What changed**:
- `@task_detector` → `@intervention_detector`
- `self.log_detection()` → `self.interventions.detect()`
- Moved ~1000 lines from bigquery.py to interventions/store.py

**Rationale**:
- Clear ownership (one module owns intervention concept)
- Better naming (not confused with logging)
- Cleaner job interface

---

## 2025-01-24: Data Layer Abstraction

**Decision**: Add `core/data/` layer for candidate discovery with source abstraction

**Pattern**:
```
Job → CandidateProvider (interface)
         ├── OdooCandidateProvider (real-time)
         ├── BigQueryCandidateProvider (fast)
         └── HybridCandidateProvider (BQ + Odoo verify)
```

**Rationale**:
- Odoo XML-RPC can't handle large datasets efficiently
- BQ is great for discovery but may be stale
- Hybrid gives speed + accuracy
- Abstraction allows easy switching per job

---

## 2025-01-24: BQ Queries as Module

**Decision**: Store BQ SQL in `core/data/queries/` as functions

**Rationale**:
- Easy to debug (print SQL, run in console)
- Version controlled (git tracks changes)
- Testable (can validate syntax)
- Reusable across jobs
- Clear separation from Python logic

---

## 2025-01-24: Fix target_qty Logic for Closed Orders

**Decision**: For closed orders, `target_qty = delivered_qty` (not `delivered + open_moves`)

**Problem**: Adding open move qty caused feedback loop (Odoo creates more moves when line qty increases)

**Lesson**: Open moves on closed orders are orphaned/stale. Don't count them.

---

**Last updated**: 2025-01-24
