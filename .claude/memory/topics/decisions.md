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

**Last updated**: 2025-01-22
