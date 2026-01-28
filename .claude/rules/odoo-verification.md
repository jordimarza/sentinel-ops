# Odoo Verification Before Write

> Mandatory: always verify live Odoo state before mutating records.

---

## Rule

When using BQ-first discovery, **always read the record from Odoo and verify it still needs updating** before calling `_safe_write`. BQ data can be ~1h stale.

## Why

- BQ tables sync periodically (typically hourly). Between syncs, records may have been updated by users, other jobs, or Odoo automations.
- Writing stale data causes unnecessary chatter messages, audit log noise, and potential data conflicts.
- Idempotent jobs should produce no side effects when run twice.

## Pattern

```python
# BAD: trust BQ blindly
if bq_says_needs_update:
    ops.sync_picking_dates(picking_id, new_date)

# GOOD: verify with Odoo first
if bq_says_needs_update:
    live = self.odoo.search_read("stock.picking", [("id", "=", picking_id)],
                                  fields=["scheduled_date", "date_deadline"])
    if live:
        live_sched = parse_date(live[0].get("scheduled_date"))
        if live_sched and live_sched.date() == target_date.date():
            skip_reasons["dates_match"] += 1
            continue
    ops.sync_picking_dates(picking_id, new_date)
```

## Applies To

- All jobs using BQ-first discovery (`_process_candidates`, `_discover_from_bq`)
- Any code path where the source of truth for "needs update" is not live Odoo data

## Does NOT Apply To

- `_process_simple` paths that already read live Odoo data and compare before writing
- Operations triggered by direct user input (explicit IDs with known state)

---

**Last updated**: 2025-01-28
