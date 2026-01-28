"""
Sync SO Picking Dates Job

Synchronizes stock.picking dates (scheduled_date, date_deadline) to match
the parent sale.order commitment_date.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from core.jobs.registry import register_job
from core.jobs.base import BaseJob
from core.operations.dates import DateComplianceOperations
from core.result import JobResult

logger = logging.getLogger(__name__)


@register_job(
    name="sync_so_picking_dates",
    description="Sync SO picking dates to match commitment_date",
    tags=["sales", "compliance", "dates", "sync"],
)
class SyncSOPickingDatesJob(BaseJob):
    """
    Sync SO picking dates to match order commitment_date.

    Pattern: detect -> verify -> remediate -> log

    Condition:
    - picking.scheduled_date != order.commitment_date
    - OR picking.date_deadline != order.commitment_date
    - Picking state not in (done, cancel)

    Actions:
    1. Update stock.picking.scheduled_date = commitment_date
    2. Update stock.picking.date_deadline = commitment_date
    3. Update stock.move.date = commitment_date
    4. Post chatter message

    Data Source:
    - BQ query (auto-discovery) or explicit order_ids/picking_ids parameter
    """

    # BQ query to find SO picking date mismatches
    BQ_QUERY = """
    SELECT
        so.id AS order_id,
        so.name AS order_name,
        so.commitment_date,
        sp.id AS picking_id,
        sp.name AS picking_name,
        sp.scheduled_date
    FROM `alohas-analytics.prod_staging.stg_odoo__sales` so
    JOIN `alohas-analytics.prod_staging.stg_bq_odoo__stock_picking` sp ON sp.sale_id = so.id
    WHERE sp.state NOT IN ('done', 'cancel')
      AND so.commitment_date IS NOT NULL
      AND (DATE(sp.scheduled_date) != DATE(so.commitment_date))
    """

    def run(
        self,
        order_ids: Optional[list[int]] = None,
        picking_ids: Optional[list[int]] = None,
        limit: Optional[int] = None,
        include_bq_query: bool = False,
        **_params
    ) -> JobResult:
        """
        Execute the SO picking date sync job.

        Args:
            order_ids: List of order IDs to process (explicit from Job 1)
            picking_ids: List of picking IDs to process directly
            limit: Maximum number of records to process
            include_bq_query: If True, also query for additional mismatches (future: BQ)

        Returns:
            JobResult with execution details
        """
        # Create result with full context for audit trail
        result = JobResult.from_context(self.ctx, parameters={
            "order_ids": order_ids,
            "picking_ids": picking_ids,
            "limit": limit,
            "include_bq_query": include_bq_query,
        })

        # Initialize operations
        date_ops = DateComplianceOperations(self.odoo, self.ctx, self.log)

        # Track KPIs
        pickings_checked = 0
        pickings_updated = 0
        moves_updated = 0
        skip_reasons: dict[str, int] = {}

        # Discover from BQ if no explicit IDs provided
        if not order_ids and not picking_ids:
            self.log.info("No explicit IDs provided - discovering from BigQuery")
            picking_ids, bq_error = self._discover_from_bq(limit)
            if bq_error:
                result.errors.append(bq_error)
            if not picking_ids:
                self.log.info("No SO picking date mismatches found")
                result.kpis = self._build_kpis(result, pickings_checked, pickings_updated, moves_updated, {})
                result.complete()
                return result

        # Collect pickings to process
        pickings_to_process = []

        # Process from explicit order_ids
        if order_ids:
            if limit and len(order_ids) > limit:
                order_ids = order_ids[:limit]

            self.log.info(
                f"Finding open pickings for {len(order_ids)} orders",
                data={"order_ids": order_ids},
            )

            for order_id in order_ids:
                try:
                    # Get order commitment_date and ah_cancel_date
                    orders = self.odoo.search_read(
                        "sale.order",
                        [("id", "=", order_id)],
                        fields=["id", "name", "commitment_date", "ah_cancel_date"],
                    )

                    if not orders:
                        continue

                    order = orders[0]
                    commitment_date_str = order.get("commitment_date")

                    if not commitment_date_str:
                        continue

                    commitment_date = self._parse_date(commitment_date_str)
                    cancel_date = self._parse_date(order.get("ah_cancel_date"))

                    # Get open pickings for this order
                    pickings = date_ops.get_open_pickings_for_order(order_id)

                    for picking in pickings:
                        origin = picking.get("origin") or ""
                        is_return = self._is_return_picking(origin)
                        pickings_to_process.append({
                            "picking_id": picking["id"],
                            "picking_name": picking.get("name"),
                            "old_scheduled": picking.get("scheduled_date"),
                            "old_deadline": picking.get("date_deadline"),
                            "target_date": commitment_date,
                            "cancel_date": cancel_date,
                            "reference_field": "commitment_date",
                            "parent_model": "sale.order",
                            "parent_id": order_id,
                            "parent_name": order["name"],
                            "is_return": is_return,
                        })

                except Exception as e:
                    self.log.error(
                        f"Error getting pickings for order {order_id}",
                        record_id=order_id,
                        error=str(e),
                    )

        # Process from explicit picking_ids
        if picking_ids:
            self.log.info(
                f"Processing {len(picking_ids)} explicit picking IDs",
                data={"picking_ids": picking_ids},
            )

            for picking_id in picking_ids:
                try:
                    # Get picking with related order info
                    pickings = self.odoo.search_read(
                        "stock.picking",
                        [("id", "=", picking_id)],
                        fields=["id", "name", "sale_id", "scheduled_date", "date_deadline", "origin"],
                    )

                    if not pickings:
                        continue

                    picking = pickings[0]
                    sale_id = picking.get("sale_id")

                    if not sale_id:
                        continue

                    order_id = sale_id[0]
                    order_name = sale_id[1] if len(sale_id) > 1 else f"order-{order_id}"

                    # Get order commitment_date and ah_cancel_date
                    orders = self.odoo.search_read(
                        "sale.order",
                        [("id", "=", order_id)],
                        fields=["commitment_date", "ah_cancel_date"],
                    )

                    if not orders or not orders[0].get("commitment_date"):
                        continue

                    commitment_date = self._parse_date(orders[0]["commitment_date"])
                    cancel_date = self._parse_date(orders[0].get("ah_cancel_date"))

                    origin = picking.get("origin") or ""
                    is_return = self._is_return_picking(origin)
                    pickings_to_process.append({
                        "picking_id": picking_id,
                        "picking_name": picking.get("name"),
                        "old_scheduled": picking.get("scheduled_date"),
                        "old_deadline": picking.get("date_deadline"),
                        "target_date": commitment_date,
                        "cancel_date": cancel_date,
                        "reference_field": "commitment_date",
                        "parent_model": "sale.order",
                        "parent_id": order_id,
                        "parent_name": order_name,
                        "is_return": is_return,
                    })

                except Exception as e:
                    self.log.error(
                        f"Error processing picking {picking_id}",
                        record_id=picking_id,
                        error=str(e),
                    )

        # TODO: Add BQ query support when include_bq_query=True
        # This would query for additional pickings with date mismatches

        if not pickings_to_process:
            self.log.info("No pickings to process")
            result.kpis = self._build_kpis(result, 0, 0, 0, {})
            result.complete()
            return result

        self.log.info(f"Processing {len(pickings_to_process)} pickings")

        # Apply limit
        if limit and len(pickings_to_process) > limit:
            pickings_to_process = pickings_to_process[:limit]

        # Process each picking
        for pick_data in pickings_to_process:
            pickings_checked += 1
            result.records_checked += 1

            picking_id = pick_data["picking_id"]
            picking_name = pick_data["picking_name"] or f"picking-{picking_id}"
            target_date = pick_data["target_date"]

            try:
                # Parse old dates for comparison/logging
                old_scheduled = pick_data.get("old_scheduled")
                old_deadline = pick_data.get("old_deadline")

                if isinstance(old_scheduled, str):
                    old_scheduled = datetime.strptime(old_scheduled, "%Y-%m-%d %H:%M:%S")
                if isinstance(old_deadline, str):
                    old_deadline = datetime.strptime(old_deadline, "%Y-%m-%d %H:%M:%S")

                # Handle return pickings differently
                if pick_data.get("is_return"):
                    cancel_date = pick_data.get("cancel_date")
                    if not cancel_date:
                        # No ah_cancel_date on order, skip return
                        result.records_skipped += 1
                        skip_reasons["return_no_cancel_date"] = skip_reasons.get("return_no_cancel_date", 0) + 1
                        continue

                    # Safeguard: if date_deadline already matches ah_cancel_date, already processed
                    cancel_date_date = cancel_date.date() if hasattr(cancel_date, 'date') else cancel_date
                    old_deadline_date = old_deadline.date() if old_deadline and hasattr(old_deadline, 'date') else None
                    if old_deadline_date == cancel_date_date:
                        result.records_skipped += 1
                        skip_reasons["return_already_processed"] = skip_reasons.get("return_already_processed", 0) + 1
                        continue

                    # Return: date_deadline = ah_cancel_date, scheduled_date = today + 15
                    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                    return_scheduled = today + timedelta(days=15)
                    pick_data["reference_field"] = "return_default"
                    self.log.info(
                        f"Return picking {picking_name}: deadline={cancel_date_date}, scheduled={return_scheduled.strftime('%Y-%m-%d')}",
                        data={"picking_id": picking_id},
                    )

                    # Use custom sync for returns (different dates for each field)
                    pick_result = date_ops.sync_picking_dates_split(
                        picking_id=picking_id,
                        scheduled_date=return_scheduled,
                        deadline_date=cancel_date,
                        picking_name=picking_name,
                    )
                    result.add_operation(pick_result)

                    if pick_result.success:
                        pickings_updated += 1
                        result.records_updated += 1

                        move_results = date_ops.sync_move_dates(
                            picking_id=picking_id,
                            new_date=return_scheduled,
                        )
                        picking_moves_updated = 0
                        for mr in move_results:
                            result.add_operation(mr)
                            if mr.success:
                                picking_moves_updated += 1
                                moves_updated += 1

                        msg_result = date_ops.post_date_sync_message(
                            model="stock.picking",
                            record_id=picking_id,
                            record_name=picking_name,
                            old_scheduled=old_scheduled,
                            old_deadline=old_deadline,
                            new_date=return_scheduled,
                            reference_field="return (ah_cancel_date → deadline, today+15 → scheduled)",
                            reference_value=cancel_date,
                            moves_updated=picking_moves_updated,
                            job_name="sync_so_picking_dates",
                            new_deadline=cancel_date,
                        )
                        result.add_operation(msg_result)

                        self.log.success(
                            picking_id,
                            f"Return {picking_name}: deadline={cancel_date_date}, scheduled={return_scheduled.strftime('%Y-%m-%d')}, {picking_moves_updated} moves",
                        )
                    continue

                # Normal pickings: scheduled_date=commitment_date, date_deadline=ah_cancel_date
                cancel_date = pick_data.get("cancel_date")
                # Fall back to commitment_date if no ah_cancel_date
                deadline_date = cancel_date if cancel_date else target_date

                target_date_date = target_date.date() if hasattr(target_date, 'date') else target_date
                deadline_date_date = deadline_date.date() if hasattr(deadline_date, 'date') else deadline_date
                old_scheduled_date = old_scheduled.date() if old_scheduled and hasattr(old_scheduled, 'date') else None
                old_deadline_date = old_deadline.date() if old_deadline and hasattr(old_deadline, 'date') else None

                needs_update = (
                    old_scheduled_date != target_date_date or
                    old_deadline_date != deadline_date_date
                )

                if not needs_update:
                    result.records_skipped += 1
                    skip_reasons["dates_match"] = skip_reasons.get("dates_match", 0) + 1
                    continue

                # Sync picking dates (split: scheduled=commitment, deadline=cancel)
                pick_result = date_ops.sync_picking_dates_split(
                    picking_id=picking_id,
                    scheduled_date=target_date,
                    deadline_date=deadline_date,
                    picking_name=picking_name,
                )
                result.add_operation(pick_result)

                picking_moves_updated = 0

                if pick_result.success:
                    pickings_updated += 1
                    result.records_updated += 1

                    # Sync move dates to commitment_date
                    move_results = date_ops.sync_move_dates(
                        picking_id=picking_id,
                        new_date=target_date,
                    )
                    for mr in move_results:
                        result.add_operation(mr)
                        if mr.success:
                            picking_moves_updated += 1
                            moves_updated += 1

                    # Post chatter message on picking
                    msg_result = date_ops.post_date_sync_message(
                        model="stock.picking",
                        record_id=picking_id,
                        record_name=picking_name,
                        old_scheduled=old_scheduled,
                        old_deadline=old_deadline,
                        new_date=target_date,
                        reference_field="commitment_date → scheduled, ah_cancel_date → deadline",
                        reference_value=target_date,
                        moves_updated=picking_moves_updated,
                        job_name="sync_so_picking_dates",
                        new_deadline=deadline_date,
                    )
                    result.add_operation(msg_result)

                    self.log.success(
                        picking_id,
                        f"Synced {picking_name}: scheduled={target_date_date}, deadline={deadline_date_date}, {picking_moves_updated} moves",
                    )

            except Exception as e:
                self.log.error(
                    f"Exception processing picking {picking_name}",
                    record_id=picking_id,
                    error=str(e),
                )
                result.errors.append(f"Picking {picking_name}: {e}")

        # Set KPIs
        result.kpis = self._build_kpis(result, pickings_checked, pickings_updated, moves_updated, skip_reasons)

        result.complete()
        return result

    @staticmethod
    def _is_return_picking(origin: str) -> bool:
        """Check if a picking is a return based on its origin field."""
        return origin.lower().startswith("return of")

    @staticmethod
    def _parse_date(value) -> Optional[datetime]:
        """Parse a date string from Odoo into a datetime, or return None."""
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                return datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                return None

    def _discover_from_bq(self, limit: Optional[int]) -> tuple[list[int], Optional[str]]:
        """
        Discover SO picking date mismatches from BigQuery.

        Returns:
            Tuple of (picking_ids list, error message or None)
        """
        query = self.BQ_QUERY
        if limit:
            query += f"\nLIMIT {limit}"

        try:
            rows = self.bq.query(query)
            picking_ids = list({row.get("picking_id") for row in rows if row.get("picking_id")})
            self.log.info(f"Found {len(picking_ids)} SO picking date mismatches from BQ")
            return picking_ids, None
        except Exception as e:
            error_msg = f"BQ query failed: {e}"
            self.log.error(error_msg, error=str(e))
            return [], error_msg

    def _build_kpis(
        self,
        result: JobResult,
        pickings_checked: int,
        pickings_updated: int,
        moves_updated: int,
        skip_reasons: dict[str, int],
    ) -> dict:
        """Build KPIs dict for the job result."""
        kpis = {
            "pickings_checked": pickings_checked,
            "pickings_updated": pickings_updated,
            "pickings_skipped": sum(skip_reasons.values()),
            "moves_updated": moves_updated,
            "exceptions": len(result.errors),
        }
        if skip_reasons:
            kpis["skip_reasons"] = skip_reasons
        return kpis


if __name__ == "__main__":
    import sys
    print("\n" + "=" * 70)
    print("Sync SO Picking Dates Job")
    print("=" * 70)
    print("\nUsage:")
    print("-" * 70)
    print("\n# By order IDs (from BQ query or check_ar_hold_violations)")
    print("python main.py run sync_so_picking_dates --dry-run order_ids=123,456")
    print("\n# By picking IDs directly")
    print("python main.py run sync_so_picking_dates --dry-run picking_ids=1001,1002")
    print("\n# With limit")
    print("python main.py run sync_so_picking_dates --dry-run order_ids=123,456 limit=10")
    print("\n# Live execution")
    print("python main.py run sync_so_picking_dates order_ids=123,456")
    print("\n" + "-" * 70)
    print("\nBQ Query to find candidates:")
    print("-" * 70)
    print("""
SELECT
    so.id AS order_id,
    so.name AS order_name,
    so.commitment_date,
    sp.id AS picking_id,
    sp.name AS picking_name,
    sp.scheduled_date,
    DATE_ADD(sp.scheduled_date, INTERVAL 15 DAY) AS date_deadline,
    CONCAT('https://odoo.alohas.com/web#id=', CAST(so.id AS STRING),
           '&model=sale.order&view_type=form') AS order_url,
    CONCAT('https://odoo.alohas.com/web#id=', CAST(sp.id AS STRING),
           '&model=stock.picking&view_type=form') AS picking_url
FROM `alohas-analytics.prod_staging.stg_odoo__sales` so
JOIN `alohas-analytics.prod_staging.stg_bq_odoo__stock_picking` sp ON sp.sale_id = so.id
WHERE sp.state NOT IN ('done', 'cancel')
  AND so.commitment_date IS NOT NULL
  AND (DATE(sp.scheduled_date) != DATE(so.commitment_date))
""")
    print("=" * 70 + "\n")
    sys.exit(0)
