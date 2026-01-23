---
name: add-job
description: Create a new sentinel-ops job. Use when user says "new job", "create job", or "add job".
allowed-tools: Bash, Read, Write
---

# Add Job

> Create a new job in sentinel-ops.

## Steps

1. **Create job file at `core/jobs/$ARGUMENTS.py`**:

```python
"""
<Job Description>
"""

import logging
from typing import Optional

from core.jobs.registry import register_job
from core.jobs.base import BaseJob
from core.result import JobResult

logger = logging.getLogger(__name__)


@register_job(
    name="$ARGUMENTS",
    description="Short description of what this job does",
    tags=["category", "type"],
)
class MyNewJob(BaseJob):
    """Longer description of the job."""

    def run(
        self,
        param1: str = "default",
        param2: int = 10,
        **params
    ) -> JobResult:
        """Execute the job."""
        result = JobResult.create(self.name, self.dry_run)

        # Your job logic here
        # Use self.odoo, self.log, self.ctx, etc.

        result.complete()
        return result
```

2. **Register in `core/jobs/__init__.py`**:
   ```python
   from core.jobs import $ARGUMENTS
   ```

3. **Test the job**:
   ```bash
   python main.py list
   python main.py run $ARGUMENTS --dry-run
   ```

## Arguments

- `$ARGUMENTS` - Job name in snake_case (e.g., "clean_old_orders")

## Checklist

- [ ] Job file created in `core/jobs/`
- [ ] `@register_job` decorator with name, description, tags
- [ ] Job imported in `core/jobs/__init__.py`
- [ ] Docstrings for class and `run` method
- [ ] Parameters have type hints and defaults
- [ ] Uses `JobResult.create()` and `result.complete()`
- [ ] Respects `self.dry_run` for mutations
- [ ] Uses `self.log` for structured logging
- [ ] Tested with `--dry-run`

## Best Practices

1. **Meaningful names**: `clean_old_orders` not `job1`
2. **Dry-run first**: Always test mutations in dry-run mode
3. **Log comprehensively**: Use `self.log.info/success/error/skip`
4. **Handle errors**: Catch exceptions, add to result.errors
5. **Return KPIs**: Add custom KPIs via `result.kpis`

---

**Version**: 1.1.0
**Updated**: 2025-01-22 - Converted to SKILL.md format
