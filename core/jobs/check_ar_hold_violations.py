"""
Check AR-HOLD Violations Job

Finds sales orders where picking scheduled_date > order ah_cancel_date
and the partner has a blocking tag. Extends commitment dates and tracks
violations with AR-HOLD:N tags.
"""

import logging
from datetime import datetime
from typing import Optional

from core.jobs.registry import register_job
from core.jobs.base import BaseJob
from core.operations.dates import DateComplianceOperations
from core.result import JobResult

logger = logging.getLogger(__name__)


@register_job(
    name="check_ar_hold_violations",
    description="Check for AR-HOLD date violations and extend commitment dates",
    tags=["sales", "ar", "compliance", "dates"],
)
class CheckArHoldViolationsJob(BaseJob):
    """
    Check for AR-HOLD violations and extend commitment dates.

    Pattern: detect -> verify -> remediate -> log

    Condition:
    - picking.scheduled_date > order.ah_cancel_date
    - AND partner has tag containing "block" (credit hold)

    Actions:
    1. Update sale.order.commitment_date += 15 days
    2. Increment AR-HOLD:N tag (or add AR-HOLD:1)
    3. Sync open picking dates to new commitment_date
    4. Sync stock.move.date on those pickings
    5. Post chatter message

    Data Source:
    - BQ query (auto-discovery) or explicit order_ids parameter
    """

    # BQ query to find AR-HOLD violation candidates
    BQ_QUERY = """
    WITH blocked_partners AS (
        SELECT DISTINCT rel.partner_id
        FROM `alohas-analytics.prod_staging.base_bq_odoo_res_partner_res_partner_category_rel` rel
        JOIN `alohas-analytics.prod_staging.base_bq_odoo_res_partner_category` cat
            ON cat.id = rel.category_id
        WHERE LOWER(cat.name.en_us) LIKE '%block%'
    )
    SELECT
        so.id AS order_id,
        so.name AS order_name,
        so.partner_id,
        rp.name AS partner_name,
        so.ah_status,
        so.x_studio_cancel_date AS ah_cancel_date,
        so.commitment_date
    FROM `alohas-analytics.prod_staging.stg_odoo__sales` so
    JOIN `alohas-analytics.prod_staging.stg_bq_odoo__stock_picking` sp ON sp.sale_id = so.id
    JOIN `alohas-analytics.prod_staging.stg_odoo__res_partner` rp ON rp.id = so.partner_id
    WHERE CURRENT_TIMESTAMP() > so.x_studio_cancel_date
      AND CURRENT_TIMESTAMP() > so.commitment_date
      AND sp.state NOT IN ('cancel')
      AND so.ah_status NOT IN ('shipped', 'delivered', 'customer-warehouse', 'cancelled', 'closed')
      AND (rp.id IN (SELECT partner_id FROM blocked_partners)
           OR rp.parent_id IN (SELECT partner_id FROM blocked_partners)
           OR rp.commercial_partner_id IN (SELECT partner_id FROM blocked_partners))
    GROUP BY 1, 2, 3, 4, 5, 6, 7
    """

    def run(
        self,
        order_ids: Optional[list[int]] = None,
        limit: Optional[int] = None,
        extension_days: int = 15,
        skip_partner_check: bool = False,
        **_params
    ) -> JobResult:
        """
        Execute the AR-HOLD violations check job.

        Args:
            order_ids: List of order IDs to process (from BQ query or direct)
            limit: Maximum number of orders to process
            extension_days: Days to extend commitment_date (default: 15)
            skip_partner_check: Skip partner block tag check (for testing)

        Returns:
            JobResult with execution details and processed_order_ids
        """
        # Create result with full context for audit trail
        result = JobResult.from_context(self.ctx, parameters={
            "order_ids": order_ids,
            "limit": limit,
            "extension_days": extension_days,
            "skip_partner_check": skip_partner_check,
        })

        # Initialize data for passing to next job
        result.data["processed_order_ids"] = []

        # Normalize order_ids to list (CLI may pass single int)
        if order_ids is not None and not isinstance(order_ids, list):
            order_ids = [order_ids]

        # Discover from BQ if no explicit order_ids
        if not order_ids:
            self.log.info("No order_ids provided - discovering from BigQuery")
            order_ids, bq_error = self._discover_from_bq(limit)
            if bq_error:
                result.errors.append(bq_error)
            if not order_ids:
                self.log.info("No AR-HOLD violation candidates found")
                result.kpis = self._build_kpis(result, 0, 0, 0, {})
                result.complete()
                return result

        # Apply limit if specified
        if limit and len(order_ids) > limit:
            order_ids = order_ids[:limit]

        self.log.info(
            f"Processing {len(order_ids)} orders for AR-HOLD violations",
            data={"order_ids": order_ids, "extension_days": extension_days},
        )

        # Initialize operations
        date_ops = DateComplianceOperations(self.odoo, self.ctx, self.log)

        # Track KPIs
        orders_processed = 0
        pickings_updated = 0
        moves_updated = 0
        skip_reasons: dict[str, int] = {}

        # Process each order
        for order_id in order_ids:
            result.records_checked += 1

            try:
                # Read order data
                orders = self.odoo.search_read(
                    "sale.order",
                    [("id", "=", order_id)],
                    fields=["id", "name", "partner_id", "commitment_date", "ah_cancel_date"],
                )

                if not orders:
                    self.log.warning(f"Order {order_id} not found")
                    result.records_skipped += 1
                    skip_reasons["not_found"] = skip_reasons.get("not_found", 0) + 1
                    continue

                order = orders[0]
                order_name = order["name"]
                partner_id = order["partner_id"][0] if order["partner_id"] else None

                # Check partner has block tag (unless skipped)
                if not skip_partner_check and partner_id:
                    has_block = date_ops.check_partner_has_block_tag(partner_id)
                    if not has_block:
                        self.log.skip(
                            order_id,
                            f"Partner does not have block tag - skipping {order_name}",
                        )
                        result.records_skipped += 1
                        skip_reasons["no_block_tag"] = skip_reasons.get("no_block_tag", 0) + 1
                        continue

                # Parse cancel_date (required for N calculation)
                cancel_date_str = order.get("ah_cancel_date")
                if not cancel_date_str:
                    self.log.warning(
                        f"Order {order_name} has no ah_cancel_date - skipping"
                    )
                    result.records_skipped += 1
                    skip_reasons["no_cancel_date"] = skip_reasons.get("no_cancel_date", 0) + 1
                    continue

                if isinstance(cancel_date_str, str):
                    try:
                        cancel_date = datetime.strptime(cancel_date_str, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        cancel_date = datetime.strptime(cancel_date_str, "%Y-%m-%d")
                else:
                    cancel_date = cancel_date_str

                # Parse current commitment_date (for logging)
                commitment_date_str = order.get("commitment_date")
                if commitment_date_str:
                    if isinstance(commitment_date_str, str):
                        current_commitment = datetime.strptime(
                            commitment_date_str, "%Y-%m-%d %H:%M:%S"
                        )
                    else:
                        current_commitment = commitment_date_str
                else:
                    current_commitment = cancel_date  # fallback

                # Calculate new commitment_date = cancel_date + (15 * N)
                # where N is the smallest integer that puts the date in the future
                new_commitment, hold_n = date_ops.calculate_next_commitment_date(
                    cancel_date=cancel_date,
                    interval_days=extension_days,
                )

                # Odoo safeguard: if commitment_date is already at or past
                # the calculated target, nothing to do (previous run handled it)
                if current_commitment >= new_commitment:
                    self.log.skip(
                        order_id,
                        f"Commitment date already at {current_commitment:%Y-%m-%d} "
                        f"(target: {new_commitment:%Y-%m-%d}) - skipping {order_name}",
                    )
                    result.records_skipped += 1
                    skip_reasons["already_extended"] = skip_reasons.get("already_extended", 0) + 1
                    continue

                # Get current AR-HOLD count for logging
                existing_tag = date_ops.find_ar_hold_tag_on_order(order_id)
                old_hold_count = existing_tag[1] if existing_tag else 0

                # Step 1: Set commitment_date = cancel_date + (15 * N)
                extend_result, new_commitment = date_ops.set_commitment_date(
                    order_id=order_id,
                    order_name=order_name,
                    new_date=new_commitment,
                )
                result.add_operation(extend_result)

                if not extend_result.success or not new_commitment:
                    self.log.error(
                        f"Failed to set commitment_date for {order_name}",
                        record_id=order_id,
                    )
                    result.errors.append(f"Order {order_name}: Failed to set date")
                    continue

                # Step 2: Set AR-HOLD:N tag (matches the N used for date)
                tag_result, new_hold_count = date_ops.set_ar_hold_tag(
                    order_id=order_id,
                    order_name=order_name,
                    target_n=hold_n,
                )
                result.add_operation(tag_result)

                # Step 3: Sync open pickings
                open_pickings = date_ops.get_open_pickings_for_order(order_id)
                order_pickings_updated = 0
                order_moves_updated = 0

                for picking in open_pickings:
                    picking_id = picking["id"]
                    picking_name = picking.get("name", f"picking-{picking_id}")

                    # Sync picking dates
                    pick_result = date_ops.sync_picking_dates(
                        picking_id=picking_id,
                        new_date=new_commitment,
                        picking_name=picking_name,
                    )
                    result.add_operation(pick_result)

                    if pick_result.success:
                        order_pickings_updated += 1
                        pickings_updated += 1

                    # Step 4: Sync move dates
                    picking_moves_updated = 0
                    move_results = date_ops.sync_move_dates(
                        picking_id=picking_id,
                        new_date=new_commitment,
                    )
                    for mr in move_results:
                        result.add_operation(mr)
                        if mr.success:
                            picking_moves_updated += 1
                            order_moves_updated += 1
                            moves_updated += 1

                    # Step 4b: Post chatter message on picking
                    if pick_result.success:
                        old_sched = picking.get("scheduled_date")
                        old_dead = picking.get("date_deadline")
                        if isinstance(old_sched, str):
                            old_sched = datetime.strptime(old_sched, "%Y-%m-%d %H:%M:%S")
                        if isinstance(old_dead, str):
                            old_dead = datetime.strptime(old_dead, "%Y-%m-%d %H:%M:%S")

                        picking_msg = date_ops.post_date_sync_message(
                            model="stock.picking",
                            record_id=picking_id,
                            record_name=picking_name,
                            old_scheduled=old_sched,
                            old_deadline=old_dead,
                            new_date=new_commitment,
                            reference_field="commitment_date (AR-HOLD extension)",
                            reference_value=new_commitment,
                            moves_updated=picking_moves_updated,
                            job_name="check_ar_hold_violations",
                        )
                        result.add_operation(picking_msg)

                # Step 5: Post chatter message
                msg_result = date_ops.post_ar_hold_message(
                    order_id=order_id,
                    order_name=order_name,
                    old_commitment=current_commitment,
                    new_commitment=new_commitment,
                    old_hold_count=old_hold_count,
                    new_hold_count=new_hold_count,
                    pickings_updated=order_pickings_updated,
                    moves_updated=order_moves_updated,
                )
                result.add_operation(msg_result)

                # Track success
                orders_processed += 1
                result.records_updated += 1
                result.data["processed_order_ids"].append(order_id)

                self.log.success(
                    order_id,
                    f"Processed AR-HOLD violation for {order_name}: "
                    f"AR-HOLD:{new_hold_count}, {order_pickings_updated} pickings",
                )

            except Exception as e:
                self.log.error(
                    f"Exception processing order {order_id}",
                    record_id=order_id,
                    error=str(e),
                )
                result.errors.append(f"Order {order_id}: {e}")

        # Set KPIs
        result.kpis = self._build_kpis(
            result, orders_processed, pickings_updated, moves_updated, skip_reasons
        )

        result.complete()
        return result

    def _discover_from_bq(self, limit: Optional[int]) -> tuple[list[int], Optional[str]]:
        """
        Discover AR-HOLD violation candidates from BigQuery.

        Returns:
            Tuple of (order_ids list, error message or None)
        """
        query = self.BQ_QUERY
        if limit:
            query += f"\nLIMIT {limit}"

        try:
            rows = self.bq.query(query)
            order_ids = list({row.get("order_id") for row in rows if row.get("order_id")})
            self.log.info(f"Found {len(order_ids)} AR-HOLD violation candidates from BQ")
            return order_ids, None
        except Exception as e:
            error_msg = f"BQ query failed: {e}"
            self.log.error(error_msg, error=str(e))
            return [], error_msg

    def _build_kpis(
        self,
        result: JobResult,
        orders_processed: int,
        pickings_updated: int,
        moves_updated: int,
        skip_reasons: dict[str, int],
    ) -> dict:
        """Build KPIs dict for the job result."""
        kpis = {
            "orders_checked": result.records_checked,
            "orders_processed": orders_processed,
            "orders_skipped": sum(skip_reasons.values()),
            "pickings_updated": pickings_updated,
            "moves_updated": moves_updated,
            "exceptions": len(result.errors),
        }
        if skip_reasons:
            kpis["skip_reasons"] = skip_reasons
        return kpis


if __name__ == "__main__":
    import sys

    print("\n" + "=" * 70)
    print("Check AR-HOLD Violations Job")
    print("=" * 70)
    print(r"""
Usage (CLI):
----------------------------------------------------------------------
# BQ auto-discovery (no IDs needed)
python main.py run check_ar_hold_violations --dry-run

# With limit
python main.py run check_ar_hold_violations --dry-run --limit 5

# Specific order IDs
python main.py run check_ar_hold_violations --dry-run order_ids=745296

# With custom extension days (default: 15)
python main.py run check_ar_hold_violations --dry-run order_ids=745296 extension_days=30

# Skip partner block check (for testing)
python main.py run check_ar_hold_violations --dry-run order_ids=745296 skip_partner_check=True

# Live execution
python main.py run check_ar_hold_violations order_ids=745296

Usage (curl):
----------------------------------------------------------------------
# NOTE: Add -H "X-API-Key: $SENTINEL_API_KEY" for remote calls

# BQ auto-discovery with limit
curl -X POST https://sentinel-ops-659945993606.europe-west1.run.app/execute \
    -H "Content-Type: application/json" \
    -H "X-API-Key: $SENTINEL_API_KEY" \
    -d '{"job": "check_ar_hold_violations", "dry_run": true, "params": {"limit": 5}}'

# Specific order IDs
curl -X POST https://sentinel-ops-659945993606.europe-west1.run.app/execute \
    -H "Content-Type: application/json" \
    -H "X-API-Key: $SENTINEL_API_KEY" \
    -d '{"job": "check_ar_hold_violations", "dry_run": true, "params": {"order_ids": [745296]}}'
""")
    print("-" * 70)
    print("\nBQ Query to find candidates:")
    print("-" * 70)
    print("""
WITH blocked_partners AS (
    SELECT DISTINCT rel.partner_id
    FROM `alohas-analytics.prod_staging.base_bq_odoo_res_partner_res_partner_category_rel` rel
    JOIN `alohas-analytics.prod_staging.base_bq_odoo_res_partner_category` cat
        ON cat.id = rel.category_id
    WHERE LOWER(cat.name.en_us) LIKE '%block%'
)
SELECT
    so.id AS order_id,
    so.name AS order_name,
    so.partner_id,
    rp.name AS partner_name,
    so.ah_status,
    so.x_studio_cancel_date AS ah_cancel_date,
    so.commitment_date,
    CONCAT('https://odoo.alohas.com/web#id=', CAST(so.id AS STRING),
           '&model=sale.order&view_type=form') AS order_url
FROM `alohas-analytics.prod_staging.stg_odoo__sales` so
JOIN `alohas-analytics.prod_staging.stg_bq_odoo__stock_picking` sp ON sp.sale_id = so.id
JOIN `alohas-analytics.prod_staging.stg_odoo__res_partner` rp ON rp.id = so.partner_id
WHERE CURRENT_TIMESTAMP() > so.x_studio_cancel_date
  AND CURRENT_TIMESTAMP() > so.commitment_date
  AND sp.state NOT IN ('cancel')
  AND so.ah_status NOT IN ('shipped', 'delivered', 'customer-warehouse', 'cancelled', 'closed')
  AND (rp.id IN (SELECT partner_id FROM blocked_partners)
       OR rp.parent_id IN (SELECT partner_id FROM blocked_partners)
       OR rp.commercial_partner_id IN (SELECT partner_id FROM blocked_partners))
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
""")
    print("=" * 70 + "\n")
    sys.exit(0)
