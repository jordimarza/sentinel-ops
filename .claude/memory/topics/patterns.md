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

**Last updated**: 2025-01-22
