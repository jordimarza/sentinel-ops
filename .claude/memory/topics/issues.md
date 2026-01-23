# Known Issues

> Issues encountered and their workarounds.

---

## Odoo Domain Limitation: Field Comparison

**Issue**: Can't compare two fields directly in Odoo domain

```python
# This doesn't work:
domain = [("qty_delivered", "<", "product_uom_qty")]
```

**Workaround**: Fetch records, filter in Python

```python
lines = self.odoo.search_read(model, [("qty_delivered", ">", 0)], ...)
partial = [l for l in lines if l["qty_delivered"] < l["product_uom_qty"]]
```

**Location**: `core/operations/orders.py:find_partial_orders_older_than()`

---

## datetime.utcnow() Deprecation

**Issue**: Python 3.12+ warns about `datetime.utcnow()`

```
DeprecationWarning: datetime.datetime.utcnow() is deprecated
```

**Status**: Low priority, doesn't affect functionality

**Fix when ready**:
```python
# Old
from datetime import datetime
datetime.utcnow()

# New
from datetime import datetime, UTC
datetime.now(UTC)
```

**Locations**: `core/context.py`, `core/result.py`, `core/operations/orders.py`

---

## Flask Import in CLI Mode

**Issue**: Importing `adapters.http` fails without Flask installed

**Solution implemented**: Lazy imports in `adapters/__init__.py`

```python
def __getattr__(name):
    """Lazy import HTTP handlers to avoid Flask dependency in CLI mode."""
    if name in __all__:
        from adapters.http import ...
```

---

**Last updated**: 2025-01-22
