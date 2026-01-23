---
name: run-job
description: Execute a sentinel-ops job. Use when user says "run job", "execute job", "run clean", or mentions a job name.
allowed-tools: Bash(python main.py *), Bash(curl *), Bash(functions-framework *)
---

# Run Job

> Execute a sentinel-ops job locally or via HTTP.

## Context

- Available jobs: !`python main.py list 2>/dev/null || echo "Run from sentinel-ops directory"`

## Steps

### Local Execution (CLI)

1. **List available jobs**
   ```bash
   python main.py list
   ```

2. **Run job (dry-run first)**
   ```bash
   python main.py run $ARGUMENTS --dry-run
   ```

3. **Run job (live)**
   ```bash
   python main.py run $ARGUMENTS
   ```

4. **Run with parameters**
   ```bash
   python main.py run $ARGUMENTS --dry-run --param=value
   ```

### HTTP Execution (Cloud Function)

1. **Start local server**
   ```bash
   functions-framework --target=sentinel --debug
   ```

2. **Test via HTTP**
   ```bash
   curl -X POST http://localhost:8080/execute \
     -H "Content-Type: application/json" \
     -d '{"job": "$ARGUMENTS", "dry_run": true}'
   ```

## Arguments

- `$ARGUMENTS` - Job name (e.g., "clean_old_orders")

## Response Format

```json
{
  "success": true,
  "job": "clean_old_orders",
  "result": {
    "status": "success",
    "records_checked": 150,
    "records_updated": 42,
    "dry_run": false
  }
}
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Job not found | Run `python main.py list` |
| Connection error | Check Odoo credentials in .env.local |
| Dry-run not working | Ensure job uses `self.ctx.dry_run` |

---

**Version**: 1.1.0
**Updated**: 2025-01-22 - Converted to SKILL.md format
