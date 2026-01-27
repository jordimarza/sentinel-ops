# CLAUDE.md - Sentinel-Ops

This file provides guidance to Claude Code when working with the sentinel-ops project.

## Project Purpose

Sentinel-Ops is the **ERP operations, monitoring, and automated remediation framework** for ALOHAS. It handles:

- ERP monitoring and health checks
- Automated remediation workflows
- Scheduled operational tasks
- Data quality monitoring
- Audit trail (BigQuery) and alerting (Slack)

## Architecture Overview

### Core Pattern: Jobs + Operations

**Jobs** orchestrate business logic. **Operations** are reusable Odoo interactions.

```
Request → Context → Job → Operations → Odoo
                      ↓
                   Result → Audit (BQ) + Alert (Slack)
```

### Directory Structure

```
sentinel-ops/
├── main.py                     # Cloud Function entry point + CLI
├── requirements.txt
├── .claude/                    # Agent structure
│   ├── AGENT.md               # Agent identity
│   ├── skills/                # How-to guides
│   └── memory/                # Learnings
├── core/
│   ├── context.py             # RequestContext for audit trail
│   ├── config.py              # Settings (Secret Manager + .env.local)
│   ├── result.py              # OperationResult, JobResult
│   ├── clients/
│   │   ├── odoo.py            # Real XML-RPC client
│   │   └── bigquery.py        # Audit-aware BQ client
│   ├── logging/
│   │   └── sentinel_logger.py # Structured BQ logger
│   ├── alerts/
│   │   └── slack.py           # Slack webhook alerts
│   ├── operations/
│   │   ├── base.py            # BaseOperation (dry-run aware)
│   │   ├── orders.py          # Sale order operations
│   │   └── transfers.py       # Stock transfer operations
│   └── jobs/
│       ├── registry.py        # @register_job decorator
│       ├── base.py            # BaseJob class
│       └── clean_old_orders.py # Example job
├── adapters/
│   ├── http.py                # HTTP adapter for Cloud Functions
│   └── mcp.py                 # MCP adapter (placeholder)
├── tests/
│   ├── conftest.py            # Test fixtures
│   ├── test_operations.py
│   └── test_jobs.py
└── scripts/
    ├── local_run.py           # Local testing script
    └── deploy.sh              # GCP deployment
```

## Quick Commands

### List Jobs

```bash
python main.py list
```

### Run a Job (Dry Run)

```bash
python main.py run clean_old_orders --dry-run
```

### Run a Job (Live)

```bash
python main.py run clean_old_orders
```

### Run with Parameters

```bash
python main.py run clean_old_orders --dry-run --days=60 --limit=100
```

### Local HTTP Testing

```bash
# Terminal 1
functions-framework --target=sentinel --debug

# Terminal 2
curl http://localhost:8080/health
curl http://localhost:8080/jobs
curl -X POST http://localhost:8080/execute \
  -H "Content-Type: application/json" \
  -d '{"job": "clean_old_orders", "dry_run": true}'
```

### Run Tests

```bash
pytest tests/ -v
```

## Creating New Jobs

1. Create `core/jobs/my_new_job.py`:

```python
from core.jobs.registry import register_job
from core.jobs.base import BaseJob
from core.result import JobResult

@register_job(name="my_new_job", description="Does something", tags=["example"])
class MyNewJob(BaseJob):
    def run(self, param1: int = 10, **params) -> JobResult:
        result = JobResult.create(self.name, self.dry_run)

        # Use self.odoo, self.log, self.ctx, etc.
        # Use operations: ops = OrderOperations(self.odoo, self.ctx, self.log)

        result.complete()
        return result
```

2. Register in `core/jobs/__init__.py`:

```python
from core.jobs import my_new_job  # Add this import
```

3. Test:

```bash
python main.py run my_new_job --dry-run
```

## Creating New Operations

Add to existing domain file or create new one in `core/operations/`:

```python
from core.operations.base import BaseOperation
from core.result import OperationResult

class MyOperations(BaseOperation):
    def do_something(self, record_id: int) -> OperationResult:
        return self._safe_write(
            model="my.model",
            ids=[record_id],
            values={"field": "value"},
            action="my_action",
        )
```

## Key Patterns

### 1. Dry-Run First

Always test with `--dry-run` before live execution. All mutations respect `self.ctx.dry_run`.

### 2. Audit Everything

`RequestContext` flows through all operations, enabling full audit trail in BigQuery.

### 3. Use Operations

Don't call `self.odoo.write()` directly in jobs. Use Operation classes for:
- Dry-run support
- Structured logging
- Error handling
- Consistent results

### 4. Return JobResult

Jobs must return `JobResult` with:
- Status (success/partial/failure/dry_run)
- Records checked/updated/skipped
- Errors list
- Optional KPIs

## Configuration

### Environment Variables

```bash
# Required for Odoo
ODOO_URL=https://your-odoo.com
ODOO_DB=database
ODOO_USERNAME=user
ODOO_PASSWORD=secret

# Optional
GCP_PROJECT=project-id
BQ_DATASET=sentinel_ops
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
ENVIRONMENT=development
LOG_LEVEL=INFO
```

### Local Development

```bash
cp .env.local.template .env.local
# Edit .env.local with your values
```

### Production (Secret Manager)

Secrets are loaded from Google Secret Manager with naming convention:
`sentinel-ops-{setting-name}` (e.g., `sentinel-ops-odoo-password`)

## Deployment

```bash
# Deploy to Cloud Functions
./scripts/deploy.sh --project YOUR_PROJECT

# Or manually
gcloud functions deploy sentinel \
  --gen2 \
  --runtime=python312 \
  --trigger-http \
  --region=us-central1
```

## Troubleshooting

### Job not found

```bash
python main.py list  # Check registration
python -c "from core.jobs import list_jobs; print(list_jobs())"
```

### Odoo connection issues

```bash
python -c "from core.clients.odoo import get_odoo_client; print(get_odoo_client().version())"
```

### Import errors

Ensure you're in the `sentinel-ops` directory or add to PYTHONPATH:

```bash
export PYTHONPATH=$PYTHONPATH:$(pwd)
```

## Session Startup

On session start, read `.claude/memory/topics/future.md` to load pending tasks and planned work.

---

**Last Updated**: 2025-01-27
**Architecture**: Jobs + Operations pattern with Cloud Function adapter
**Agent**: See `.claude/AGENT.md` for full agent context
