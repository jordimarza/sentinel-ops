---
name: add-operation
description: Create a new reusable Odoo operation. Use when user says "new operation", "add operation", or "create operation".
allowed-tools: Bash, Read, Write
---

# Add Operation

> Create a new reusable operation in sentinel-ops.

## When to Create an Operation

- Reusable piece of Odoo logic
- Something multiple jobs might need
- Complex query or mutation that should be encapsulated

## Steps

1. **Create or add to `core/operations/$ARGUMENTS.py`**:

```python
"""
<Domain> Operations
"""

import logging
from typing import Optional, List

from core.operations.base import BaseOperation
from core.result import OperationResult

logger = logging.getLogger(__name__)


class MyDomainOperations(BaseOperation):
    """Operations for <domain>."""

    MODEL = "odoo.model.name"

    def find_records(
        self,
        some_filter: str,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """Find records matching criteria."""
        domain = [("field", "=", some_filter)]
        return self.odoo.search_read(
            self.MODEL,
            domain,
            fields=["id", "name"],
            limit=limit,
        )

    def update_record(
        self,
        record_id: int,
        new_value: str,
    ) -> OperationResult:
        """Update a record."""
        return self._safe_write(
            model=self.MODEL,
            ids=[record_id],
            values={"field_name": new_value},
            action="update_field",
        )
```

2. **Export in `core/operations/__init__.py`**:
   ```python
   from core.operations.$ARGUMENTS import MyDomainOperations
   ```

3. **Use in a job**:
   ```python
   ops = MyDomainOperations(self.odoo, self.ctx, self.log)
   records = ops.find_records("filter_value")
   ```

## Arguments

- `$ARGUMENTS` - Domain name in snake_case (e.g., "orders", "transfers")

## BaseOperation Methods

| Method | Purpose |
|--------|---------|
| `self.odoo` | Odoo client |
| `self.ctx` | Request context |
| `self.log` | Structured logger |
| `self.dry_run` | Check if dry-run mode |
| `self._safe_write()` | Write with dry-run support |
| `self._safe_message_post()` | Post message with dry-run |
| `self._safe_add_tag()` | Add tag with dry-run |

## Checklist

- [ ] Operation class extends `BaseOperation`
- [ ] Operations grouped by domain
- [ ] Methods have docstrings
- [ ] Uses `self._safe_write()` for mutations
- [ ] Returns `OperationResult` for mutations
- [ ] Exported in `__init__.py`

---

**Version**: 1.1.0
**Updated**: 2025-01-22 - Converted to SKILL.md format
