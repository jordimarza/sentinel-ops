---
name: test-local
description: Test sentinel-ops locally. Use when user says "test", "run tests", "local testing", or "verify".
allowed-tools: Bash(python *), Bash(pytest *), Bash(curl *), Bash(functions-framework *)
---

# Test Local

> Test sentinel-ops locally before deploying.

## Setup (First Time)

1. **Create virtual environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**
   ```bash
   cp .env.local.template .env.local
   # Edit .env.local with credentials
   ```

## Test Methods

### CLI Testing

```bash
# List jobs
python main.py list

# Run job dry-run
python main.py run $ARGUMENTS --dry-run

# Run with parameters
python main.py run $ARGUMENTS --dry-run --param=value
```

### HTTP Testing

```bash
# Start local server
functions-framework --target=sentinel --debug --port=8080

# Test endpoints
curl http://localhost:8080/health
curl http://localhost:8080/jobs
curl -X POST http://localhost:8080/execute \
  -H "Content-Type: application/json" \
  -d '{"job": "$ARGUMENTS", "dry_run": true}'
```

### Unit Tests

```bash
# All tests
pytest tests/ -v

# Specific file
pytest tests/test_jobs.py -v

# With coverage
pytest tests/ --cov=core --cov-report=html
```

## Arguments

- `$ARGUMENTS` - Optional job name to test

## Debugging

### Check Odoo Connection
```python
from core.clients.odoo import get_odoo_client
client = get_odoo_client()
print(client.version())
```

### Check Job Registration
```python
from core.jobs import list_jobs
for job in list_jobs():
    print(f"{job['name']}: {job['description']}")
```

## Common Issues

| Issue | Solution |
|-------|----------|
| ImportError: No module 'core' | `cd sentinel-ops` or add to PYTHONPATH |
| Connection refused (Odoo) | Check ODOO_URL in .env.local |
| BigQuery permission denied | Run `gcloud auth application-default login` |

---

**Version**: 1.1.0
**Updated**: 2025-01-22 - Converted to SKILL.md format
