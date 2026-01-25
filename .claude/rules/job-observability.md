# Job Observability Standards

> Standards for tracking and reporting job execution metrics.

---

## Skip Reason Tracking

When a job skips records, track **why** they were skipped for debugging and monitoring stale data.

### Pattern

```python
# Track skip reasons in job
skip_reasons: dict[str, int] = {}

# When skipping, increment the reason
if not verified:
    skip_reason = "not_found"  # or "not_draft", "has_moves", etc.
    skip_reasons[skip_reason] = skip_reasons.get(skip_reason, 0) + 1
    result.records_skipped += 1

# Include in KPIs
result.kpis = {
    "records_processed": processed_count,
    "records_skipped": sum(skip_reasons.values()),
    "skip_reasons": skip_reasons,  # {"not_found": 5, "not_draft": 12, "has_moves": 48}
    "exceptions": len(result.errors),
}
```

### Common Skip Reasons

| Reason | Description |
|--------|-------------|
| `not_found` | Record doesn't exist in Odoo |
| `not_draft` | State changed (BQ data stale) |
| `has_moves` | Picking now has moves (BQ data stale) |
| `no_block_tag` | Partner doesn't have required tag |
| `no_commitment_date` | Missing required date field |
| `dates_match` | No update needed (already correct) |
| `already_processed` | Previously handled |
| `error` | Exception during verification |

### Benefits

1. **Stale Data Detection**: High `not_draft` or `has_moves` counts indicate BQ sync lag
2. **Data Quality Issues**: High `not_found` counts indicate data integrity problems
3. **Job Tuning**: Understand what records are being filtered out
4. **Debugging**: Quickly identify why expected records weren't processed

---

## KPI Standards

All jobs should include these base KPIs:

```python
{
    "records_checked": int,      # Total records evaluated
    "records_updated": int,      # Successfully modified
    "records_skipped": int,      # Skipped (with reasons if applicable)
    "exceptions": int,           # Errors encountered
}
```

Domain-specific KPIs can be added (e.g., `pickings_cancelled`, `moves_updated`).

---

## Verification Before Processing

When using BQ-first discovery:

1. **BQ data can be ~1h stale**
2. Always verify with Odoo before modifying
3. Track verification failures as skip reasons

```python
# BQ says it's a draft empty picking
for picking in bq_discovered_pickings:
    # But verify with Odoo first
    verified, skip_reason = self._verify_empty_draft(picking["id"])
    if not verified:
        skip_reasons[skip_reason] = skip_reasons.get(skip_reason, 0) + 1
        continue
    # Now safe to process
```

---

**Last updated**: 2025-01-25
