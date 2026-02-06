# Project Boundaries

> Sentinel-ops is an API. It doesn't know about callers.

---

## What Belongs Here

- Jobs (business logic)
- Operations (Odoo interactions)
- API adapters (HTTP, MCP)
- Configuration (secrets, settings)

## What Does NOT Belong Here

- n8n workflows or presets
- Partner mappings (that's n8n-hub)
- UI code
- Caller-specific logic

## Collaboration Pattern

```
Caller (n8n, MCP, scheduler)
    │
    ▼ HTTP POST /execute
Sentinel-ops
    │
    ▼ XML-RPC
Odoo
```

Sentinel-ops receives a JSON payload with all values resolved. It doesn't:
- Look up partner names → caller does this
- Apply preset defaults → caller does this
- Know about Google Drive folders → caller handles this

## See Also

Governance: `claude-agents-alohas-automation/shared/patterns/project-separation.md`

---

**Last updated**: 2025-01-30
