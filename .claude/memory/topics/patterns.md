# Patterns

> Reusable code patterns discovered in sentinel-ops.

---

## Job Pattern

All jobs follow: **detect → check → remediate → log**

```python
@register_job(name="job_name", description="...", tags=[...])
class MyJob(BaseJob):
    def run(self, **params) -> JobResult:
        result = JobResult.create(self.name, self.dry_run)
        ops = SomeOperations(self.odoo, self.ctx, self.log)

        # 1. Detect
        records = ops.find_something()

        # 2. Check + Remediate
        for record in records:
            if ops.should_skip(record):
                self.log.skip(record["id"], "reason")
                continue
            op_result = ops.fix_something(record)
            result.add_operation(op_result)

        # 3. Log (automatic via result)
        result.complete()
        return result
```

---

## Operation Pattern

Operations wrap Odoo calls with dry-run support:

```python
class MyOperations(BaseOperation):
    def do_something(self, record_id: int) -> OperationResult:
        return self._safe_write(
            model="model.name",
            ids=[record_id],
            values={"field": "value"},
            action="action_name",
        )
```

Use `_safe_write`, `_safe_message_post`, `_safe_add_tag` for automatic dry-run handling.

---

## KPI Pattern

Original format preserved for compatibility:

```python
result.kpis = {
    "lines_checked": result.records_checked,
    "lines_updated": result.records_updated,
    "exceptions": len(result.errors),
}
```

---

## Dispatcher Pattern (Legacy)

From original codebase - kept for reference:

```python
WORKFLOW_MAP = {"name": "module.path.function"}
# Dynamic import via __import__ + getattr
```

**Replaced by**: `@register_job` decorator with class-based jobs.

---

## Intervention Tracking Pattern

For jobs that detect issues requiring intervention:

```python
@register_job(name="my_job", ...)
@intervention_detector(
    issue_type="qty_mismatch",
    document_type="sale.order",
    enabled=True,  # Flip when ready
)
class MyJob(BaseJob):
    def run(self, **params) -> JobResult:
        # Detect issue (append-only to BQ)
        self.interventions.detect(
            document_id=order_id,
            title="Issue found",
            detection_data={...},
        )

        # Log resolution when AI fixes it
        self.interventions.resolve(
            document_id=order_id,
            title="Fixed",
            resolution_type="auto_adjusted",
        )
```

---

## Data Provider Pattern

For candidate discovery with source abstraction:

```python
from core.data import get_candidate_provider

class MyJob(BaseJob):
    def run(self, source: str = "odoo", **params) -> JobResult:
        # Get appropriate provider
        provider = get_candidate_provider(
            source=source,  # "odoo", "bq", or "hybrid"
            odoo=self.odoo,
            bq=self.bq,
        )

        # Use consistent interface regardless of source
        candidates, stats = provider.get_orders_with_qty_mismatch(...)

        # Always verify before mutation (hybrid does this automatically)
        for candidate in candidates:
            fresh = provider.verify_line(candidate["id"], [...])
            if fresh and should_process(fresh):
                # Safe to mutate
                ...
```

**When to use which source:**
- `odoo`: Small datasets, need real-time accuracy
- `bq`: Large datasets, complex joins, discovery only
- `hybrid`: Large datasets + need accuracy before mutations

---

## BQ Query Pattern

Store SQL in `core/data/queries/` as functions:

```python
# core/data/queries/orders.py
def orders_with_qty_mismatch_sql(
    project: str,
    dataset: str,
    ah_statuses: list[str],
    **filters,
) -> str:
    """Return SQL string for easy debugging."""
    return f"""
    SELECT ...
    FROM `{project}.{dataset}.sale_order_line` l
    WHERE ...
    """
```

Benefits:
- Print SQL for debugging
- Version controlled
- Testable
- Reusable

---

**Last updated**: 2025-01-24
