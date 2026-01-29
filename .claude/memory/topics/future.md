# Future Developments

> Planned features and improvements.

---

## Data Layer (IMPLEMENTED 2025-01-24)

**Status**: ✅ Foundation implemented

**Location**: `core/data/`

**What's done**:
- `providers.py`: CandidateProvider abstraction with Odoo, BQ, Hybrid implementations
- `queries/orders.py`: BQ SQL queries for order discovery

**What's next**:
- [ ] Migrate `adjust_closed_order_quantities` to use provider pattern
- [ ] Add more query functions to `queries/`
- [ ] Add `queries/transfers.py` for stock move queries
- [ ] Test hybrid provider with real BQ data

---

## BQ-First Discovery (IMPLEMENTED 2025-01-25)

**Status**: ✅ Implemented for all date compliance jobs

All three date compliance jobs now auto-discover candidates from BigQuery when no explicit IDs are provided:

| Job | BQ Query | Discovery Method |
|-----|----------|------------------|
| `check_ar_hold_violations` | Finds blocked partners past cancel date | `_discover_from_bq()` returns order_ids |
| `sync_so_picking_dates` | Finds SO pickings with date mismatches | `_discover_from_bq()` returns picking_ids |
| `sync_po_picking_dates` | Finds PO pickings with date mismatches | `_discover_from_bq()` returns candidates |

**Pattern**: BQ data can be ~1h stale, so always verify with Odoo before processing.
**Error handling**: BQ errors are added to `result.errors` for visibility.

---

## MCP Server Implementation

**Status**: Placeholder in place, not functional
**Priority**: Medium - when AI agent integration needed
**Location**: `adapters/mcp.py`

**Current state**:
- `get_mcp_tools()` generates tool schemas
- `RequestContext.for_mcp()` exists
- Core is transport-agnostic (ready for MCP)

**Implementation needed**:
```python
from mcp.server import Server
from mcp.types import Tool, TextContent

app = Server("sentinel-ops")

@app.list_tools()
async def list_tools():
    return [
        Tool(name="sentinel_execute_job", ...),
        Tool(name="sentinel_list_jobs", ...),
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict):
    ctx = RequestContext.for_mcp(...)
    job = get_job(arguments["job"])
    result = job(ctx).execute(**arguments.get("params", {}))
    return TextContent(text=json.dumps(result.to_dict()))
```

**Dependencies to add**: `mcp>=0.1.0`

**Use cases**:
- Claude Desktop integration for COO operations
- AI agent automation of ERP tasks
- Natural language job execution

---

## Additional Jobs to Implement

| Job | Description | Priority |
|-----|-------------|----------|
| `remediate_orders_to_invoice` | Handle stuck "to invoice" orders | **High** |
| `cap_pending_move_quantities` | Ensure delivered + pending <= ordered | **High** |
| `cancel_orphaned_pickings` | Cancel pickings on closed orders | **High** |
| `monitor_inventory` | Alert on low stock levels | Medium |
| `cleanup_stalled_pickings` | Handle stuck transfers | Medium |
| `reconcile_invoices` | Match payments to invoices | Medium |
| `archive_old_quotations` | Clean up expired quotes | Low |

---

## Remediate Orders to Invoice Job

**Status**: Planned
**Priority**: High

**Problem**: Orders stuck in "to invoice" status that need remediation based on different scenarios.

**Scenarios to investigate**:

| Scenario | Condition | Potential Action |
|----------|-----------|------------------|
| Return already processed | Return picking done, original order still "to invoice" | Send quantities to virtual location? |
| B2B older orders | B2B order, older than X days | Raise intervention task |
| Partial delivery | Some items delivered, some pending | TBD - may need manual review |
| Zero-value order | Order total = 0 (discounts/credits) | Auto-invoice? |
| Other | TBD | TBD |

**Discovery needed**:
- What makes an order "to invoice" in Odoo? (`invoice_status = 'to invoice'`?)
- Common root causes from historical data
- BQ query to find candidates by scenario type
- What fields indicate a return was processed?

**Questions**:
1. What does "send quantities to virtual location" mean exactly?
2. Which B2B partners/order types need intervention vs auto-remediation?
3. Are there orders that should stay "to invoice" indefinitely?
4. How to detect if a return was fully processed?

**Implementation approach**:
- Likely needs scenario-based routing (detect scenario → apply specific action)
- Integration with intervention system for cases needing human review
- BQ-first discovery with Odoo verification

---

## Cap Pending Move Quantities Job

**Status**: Planned
**Priority**: High

**Problem**: Stock moves can have quantities that would cause over-delivery:
- `qty_delivered + pending_move_qty > product_uom_qty`

**Solution**: Reduce move quantities to cap at ordered qty.

**Key constraint**: Only modify pickings NOT exported to warehouse
- Field: `tec_date_export`
- If NULL → safe to modify
- If set → already sent to warehouse, DO NOT TOUCH

**Logic**:
```
For each sale.order.line:
  excess = (qty_delivered + sum(pending_moves)) - product_uom_qty
  if excess > 0:
    reduce moves (LIFO - last created first) by excess amount
    only if picking.tec_date_export is NULL
```

**Potential conflicts**:
- Intentional over-orders (wholesale?) - may need whitelist
- Returns that affect the math
- Multiple moves per line - which to reduce?

**Questions**:
- Should this run on all orders or just specific ah_status?
- What about backorders - are they separate lines or same?

---

## Cancel Orphaned Pickings Job

**Status**: Planned
**Priority**: High

**Problem**: Closed orders (ah_status=delivered/closed) may have orphaned OUT pickings
- Created by bugs or manual qty changes
- Will never be shipped
- Clutter the system

**Solution**: Cancel these pickings automatically.

**Key constraint**: Only cancel pickings NOT exported to warehouse
- Field: `tec_date_export`
- If NULL → safe to cancel
- If set → DO NOT CANCEL (warehouse already has it)

**Logic**:
```
Find pickings where:
  - origin is a closed order (ah_status in delivered/closed)
  - picking_type = outgoing
  - state not in (done, cancel)
  - tec_date_export IS NULL
Then: action_cancel()
```

**Safety**: Log all cancelled pickings to BigQuery for audit

---

## Virtual Product Line Jobs (TODO)

**Context**: Service-type products (discounts, shipping, gift cards, etc.) don't have physical delivery but appear on order lines. We need jobs to handle their `qty_delivered` status properly.

**Reference**: All virtual product IDs are defined in `core/operations/orders.py`

### Jobs to Create

| Job | Products | Action | Priority | Notes |
|-----|----------|--------|----------|-------|
| `complete_shipping_only_orders` | Shipping (73 IDs) | Set `qty_delivered = product_uom_qty` | **DONE** | Only when all physical products delivered |
| `complete_discount_lines` | Discounts (14 IDs) | Set `qty_delivered = product_uom_qty` | High | Similar pattern to shipping |
| `complete_gift_card_lines` | Gift Cards (12 IDs) | Set `qty_delivered = product_uom_qty` | Medium | May need different logic (codes sent?) |
| `complete_chargeback_lines` | Chargebacks (10 IDs) | TBD - review with finance | Medium | Need to understand chargeback workflow |
| `complete_tip_lines` | Tips (2 IDs) | Set `qty_delivered = product_uom_qty` | Low | Simple, low volume |
| `complete_duties_lines` | Duties/Customs (8 IDs) | TBD - review with logistics | Medium | Linked to shipment customs clearance? |
| `complete_commission_lines` | Commissions (4 IDs) | TBD - review with finance | Low | B2B orders, partner payouts |
| `complete_fee_lines` | Other fees (3 IDs) | Set `qty_delivered = product_uom_qty` | Low | Handling, carbon offset, down payment |

### Questions to Answer

1. **Discounts**: Should discount lines always be auto-completed when order is delivered? Or only when refund is processed?

2. **Gift Cards**: Are gift card lines completed when the code is sent? Or when the card is activated?

3. **Chargebacks**: What's the workflow?
   - When customer disputes → chargeback line created?
   - When resolved → should qty_delivered be set?

4. **Duties**: Are these linked to specific shipments?
   - Should they be completed when customs clearance is done?
   - How to detect customs clearance status?

5. **Commissions**: When are these "delivered"?
   - When partner invoice is paid?
   - When commission is calculated?

6. **Down Payment**: Should this ever be "delivered" or always stay at 0?

### Implementation Plan

1. **Phase 1** (High Priority):
   - [ ] Create `complete_discount_lines` job (same pattern as shipping)
   - [ ] Test with dry-run on S0% orders
   - [ ] Deploy to production

2. **Phase 2** (Medium Priority):
   - [ ] Review gift card workflow with team
   - [ ] Review chargeback workflow with finance
   - [ ] Review duties workflow with logistics
   - [ ] Create jobs based on findings

3. **Phase 3** (Low Priority):
   - [ ] Complete remaining virtual product jobs
   - [ ] Consider consolidating into single `complete_virtual_lines` job with config

### Product ID Summary

| Category | Count | IDs Location |
|----------|-------|--------------|
| Shipping | 73 | `DEFAULT_SHIPPING_PRODUCT_IDS` |
| Discounts | 14 | `DEFAULT_DISCOUNT_PRODUCT_IDS` |
| Gift Cards | 12 | `DEFAULT_GIFT_CARD_PRODUCT_IDS` |
| Chargebacks | 10 | `DEFAULT_CHARGEBACK_PRODUCT_IDS` |
| Tips | 2 | `DEFAULT_TIP_PRODUCT_IDS` |
| Duties | 8 | `DEFAULT_DUTIES_PRODUCT_IDS` |
| Commissions | 4 | `DEFAULT_COMMISSION_PRODUCT_IDS` |
| Other Fees | 3 | `DEFAULT_OTHER_FEE_PRODUCT_IDS` |
| **Total** | **126** | `DEFAULT_EXCLUDE_PRODUCT_IDS` |

---

## Query Endpoint

**Status**: Placeholder returns static message
**Location**: `adapters/http.py` → `handle_query()`

**Needed**: Actual BigQuery queries for:
- Job execution history
- KPI trends
- Error analysis

---

## Scheduled Execution

**Status**: Not implemented
**Options**:
1. Cloud Scheduler → HTTP trigger
2. Cloud Functions with Pub/Sub
3. Cloud Run Jobs

---

**Last updated**: 2025-01-29
