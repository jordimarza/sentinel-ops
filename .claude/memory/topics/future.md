# Future Developments

> Planned features and improvements.

---

## MCP Server Implementation

**Status**: Placeholder in place, not functional
**Priority**: Medium - when AI agent integration needed
**Location**: `adapters/mcp.py`

**Current state**:
- `get_mcp_tools()` generates tool schemas
- `RequestContext.for_mcp()` exists
- Core is transport-agnostic (ready for MCP)

**Implementation needed**:
```python
from mcp.server import Server
from mcp.types import Tool, TextContent

app = Server("sentinel-ops")

@app.list_tools()
async def list_tools():
    return [
        Tool(name="sentinel_execute_job", ...),
        Tool(name="sentinel_list_jobs", ...),
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict):
    ctx = RequestContext.for_mcp(...)
    job = get_job(arguments["job"])
    result = job(ctx).execute(**arguments.get("params", {}))
    return TextContent(text=json.dumps(result.to_dict()))
```

**Dependencies to add**: `mcp>=0.1.0`

**Use cases**:
- Claude Desktop integration for COO operations
- AI agent automation of ERP tasks
- Natural language job execution

---

## Additional Jobs to Implement

| Job | Description | Priority |
|-----|-------------|----------|
| `monitor_inventory` | Alert on low stock levels | High |
| `cleanup_stalled_pickings` | Handle stuck transfers | Medium |
| `reconcile_invoices` | Match payments to invoices | Medium |
| `archive_old_quotations` | Clean up expired quotes | Low |

---

## Query Endpoint

**Status**: Placeholder returns static message
**Location**: `adapters/http.py` → `handle_query()`

**Needed**: Actual BigQuery queries for:
- Job execution history
- KPI trends
- Error analysis

---

## Scheduled Execution

**Status**: Not implemented
**Options**:
1. Cloud Scheduler → HTTP trigger
2. Cloud Functions with Pub/Sub
3. Cloud Run Jobs

---

**Last updated**: 2025-01-22
