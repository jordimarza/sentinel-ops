# Sentinel-Ops Agent

> ERP operations, monitoring, and automated remediation for ALOHAS.

---

## Identity

| Field | Value |
|-------|-------|
| Name | sentinel-ops |
| Version | 0.1.0 |
| Domain | ERP Operations |
| Status | Active |
| Primary Language | Python 3.12 |
| Deployment | Google Cloud Functions |

---

## Purpose

Sentinel-Ops handles:
- **Monitoring**: Detect anomalies and issues in Odoo ERP
- **Remediation**: Automated fixes for common operational issues
- **Audit Trail**: Full logging of all operations to BigQuery
- **Alerts**: Slack notifications for failures and important events

---

## Architecture

```
sentinel-ops/
├── main.py                 # Cloud Function entry point
├── core/
│   ├── context.py          # RequestContext for audit threading
│   ├── config.py           # Settings with Secret Manager
│   ├── result.py           # OperationResult, JobResult
│   ├── clients/
│   │   ├── odoo.py         # Real XML-RPC client
│   │   └── bigquery.py     # Audit-aware BQ client
│   ├── logging/
│   │   └── sentinel_logger.py  # BQ audit logger
│   ├── alerts/
│   │   └── slack.py        # Slack webhook alerts
│   ├── operations/
│   │   ├── base.py         # BaseOperation with dry-run
│   │   ├── orders.py       # Order operations
│   │   └── transfers.py    # Transfer operations
│   └── jobs/
│       ├── registry.py     # @register_job decorator
│       ├── base.py         # BaseJob class
│       └── clean_old_orders.py  # Migrated job
└── adapters/
    ├── http.py             # HTTP routing for CF
    └── mcp.py              # MCP adapter (future)
```

---

## Key Patterns

### 1. Jobs + Operations

Jobs orchestrate operations. Operations are reusable building blocks.

```python
@register_job(name="my_job", description="Does something")
class MyJob(BaseJob):
    def run(self, **params) -> JobResult:
        ops = OrderOperations(self.odoo, self.ctx, self.log)
        # Use operations...
```

### 2. Dry-Run First

All mutations support dry-run mode. Always test with `--dry-run` first.

```bash
python main.py run clean_old_orders --dry-run
```

### 3. Audit Everything

RequestContext flows through all operations for complete audit trail.

```python
ctx = RequestContext.for_http(job_name="my_job", dry_run=False)
# ctx.request_id threads through all operations
```

---

## Skills

| Skill | Description |
|-------|-------------|
| [run-job](skills/run-job.md) | Execute a job locally or via HTTP |
| [add-job](skills/add-job.md) | Create a new job |
| [add-operation](skills/add-operation.md) | Create a new operation |
| [test-local](skills/test-local.md) | Test locally with functions-framework |
| [deploy](skills/deploy.md) | Deploy to Cloud Functions |

---

## Metrics

| Metric | Target | Current |
|--------|--------|---------|
| Jobs registered | 5+ | 1 |
| Test coverage | 80% | 0% |
| Audit completeness | 100% | 100% |
| Dry-run support | All jobs | All jobs |

---

## Configuration

### Environment Variables

```bash
# Odoo
ODOO_URL=https://your-odoo.com
ODOO_DB=your_database
ODOO_USERNAME=api_user
ODOO_PASSWORD=secret

# BigQuery
GCP_PROJECT=your-project
BQ_DATASET=sentinel_ops

# Slack
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
SLACK_CHANNEL=#sentinel-alerts

# Runtime
ENVIRONMENT=development  # or production
LOG_LEVEL=INFO
```

### Local Development

```bash
cp .env.local.template .env.local
# Fill in values
source .env.local
```

---

## Improvement Protocol

When I discover something while working:

1. **Pattern** → Add to `.claude/memory/patterns.md`
2. **Decision** → Add to `.claude/memory/decisions.md`
3. **Future idea** → Add to `.claude/memory/future.md`
4. **Issue/Bug** → Add to `.claude/memory/issues.md`
5. **Domain knowledge** → Add to `.claude/memory/odoo.md`
6. **Reusable workflow** → Create skill in `.claude/skills/`

See: `claude-agents-alohas-automation/shared/patterns/memory-structure.md`

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1.0 | 2025-01-22 | Initial refactored architecture |

---

**Registered in**: `claude-agents-alohas-automation/registry/AGENTS.md`
