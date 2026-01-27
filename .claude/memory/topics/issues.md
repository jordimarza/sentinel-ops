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

## BigQuery: Adding New Tables

**Issue**: `scripts/recreate_bq_tables.py` drops ALL tables when run, losing existing data.

**Mistake made**: Ran full script to add `intervention_tasks` table, which unnecessarily deleted data in `audit_log`, `job_kpis`, `execution_plans`, `execution_feedback`.

**Correct approach for adding a single table**:

```python
# Option 1: Use the ensure method directly
from core.clients.bigquery import get_bigquery_client
bq = get_bigquery_client()
bq._ensure_tasks_table()  # Only creates if doesn't exist

# Option 2: Create table via BQ client
from google.cloud import bigquery
client = bigquery.Client()
# ... define schema and create single table
```

**Rule**: When adding new tables, never use the full recreate script unless you intend to wipe all data. Use targeted table creation instead.

---

## Odoo: Changing Sale Line Qty Creates Moves

**Issue**: When you change `product_uom_qty` on a sale.order.line, Odoo automatically creates stock.move records to fulfill the new demand.

**Problem encountered**: Job had logic `target_qty = delivered + open_moves`, which:
1. Increased line qty (e.g., 1 → 4)
2. Odoo created new moves for the difference
3. Next run: open_moves now higher → target increases
4. Feedback loop → qty kept growing (reached 20.0)

**Root cause**: Open moves on **closed orders** are orphaned/stale. They shouldn't be counted.

**Fix**: For closed orders, `target_qty = delivered_qty` (ignore open moves)

**Related picking issue**: The orphaned OUT picking (e.g., RMTS/OUT/01778) needs manual cancellation if `tec_date_export` is NULL.

**Prevention checklist for jobs that modify sale order lines:**
- [ ] Consider if Odoo will auto-create moves
- [ ] For closed orders, only match delivered qty
- [ ] If open moves matter (active orders), verify they're legitimate
- [ ] Test with dry-run AND verify with a second run

---

## Odoo: Picking Export Lock Field

**Field**: `tec_date_export` on stock.picking

**Meaning**:
- `NULL` → Not exported to warehouse, safe to modify/cancel
- Set → Already sent to warehouse, DO NOT TOUCH

**Use in jobs**: Always check this field before modifying/cancelling pickings.

```python
if picking.get("tec_date_export"):
    # Already exported - skip
    continue
# Safe to modify
```

---

**Last updated**: 2025-01-24
