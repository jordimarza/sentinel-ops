"""
Clean Empty Draft Transfers Job

Finds and cancels/deletes stock.picking records in 'draft' state
that have no stock.move lines (empty transfers).
"""

import logging
from typing import Optional

from core.jobs.registry import register_job
from core.jobs.base import BaseJob
from core.operations.transfers import TransferOperations
from core.result import JobResult, OperationResult

logger = logging.getLogger(__name__)


@register_job(
    name="clean_empty_draft_transfers",
    description="Cancel empty draft transfers (pickings with no moves)",
    tags=["transfers", "cleanup", "maintenance"],
)
class CleanEmptyDraftTransfersJob(BaseJob):
    """
    Cancel or delete empty draft transfers.

    Pattern: detect -> verify -> remediate -> log

    Condition:
    - stock.picking.state = 'draft'
    - No stock.move records linked to the picking

    Actions:
    - Cancel the picking (action_cancel) or unlink if cancel fails
    - Log to audit trail

    Data Source:
    - picking_ids: Explicit list from BQ query
    - Or: Discover directly from Odoo (if no IDs provided)
    """

    PICKING_MODEL = "stock.picking"
    MOVE_MODEL = "stock.move"

    # BQ query to find empty draft pickings
    BQ_QUERY = """
    SELECT
        sp.id AS picking_id,
        sp.name AS picking_name,
        sp.picking_type_id,
        sp.scheduled_date,
        sp.create_date,
        sp.sale_id,
        sp.origin
    FROM `alohas-analytics.prod_staging.stg_bq_odoo__stock_picking` sp
    LEFT JOIN `alohas-analytics.prod_staging.stg_odoo__stock_move` sm
        ON sm.picking_id = sp.id
    WHERE sp.state = 'draft'
    GROUP BY 1, 2, 3, 4, 5, 6, 7
    HAVING COUNT(sm.id) = 0
    ORDER BY sp.create_date ASC
    """

    def run(
        self,
        picking_ids: Optional[list[int]] = None,
        limit: Optional[int] = None,
        delete_instead_of_cancel: bool = False,
        discover_from_odoo: bool = False,
        **_params
    ) -> JobResult:
        """
        Execute the clean empty draft transfers job.

        Args:
            picking_ids: List of picking IDs to process (explicit)
            limit: Maximum number of pickings to process
            delete_instead_of_cancel: If True, delete instead of cancel
            discover_from_odoo: If True, discover from Odoo instead of BQ

        Returns:
            JobResult with execution details
        """
        result = JobResult.from_context(self.ctx, parameters={
            "picking_ids": picking_ids,
            "limit": limit,
            "delete_instead_of_cancel": delete_instead_of_cancel,
            "discover_from_odoo": discover_from_odoo,
        })

        # Initialize operations for chatter messages
        self.transfer_ops = TransferOperations(self.odoo, self.ctx, self.log)

        # Get pickings to process
        pickings_to_process = []

        if picking_ids:
            # Use provided IDs
            if limit and len(picking_ids) > limit:
                picking_ids = picking_ids[:limit]

            self.log.info(f"Processing {len(picking_ids)} picking IDs from input")

            # Verify they're actually empty drafts
            for picking_id in picking_ids:
                picking_data = self._verify_empty_draft(picking_id)
                if picking_data:
                    pickings_to_process.append(picking_data)
                else:
                    result.records_skipped += 1

        elif discover_from_odoo:
            # Discover from Odoo directly
            self.log.info("Discovering empty draft pickings from Odoo")
            pickings_to_process = self._discover_empty_drafts_odoo(limit)

        else:
            # Default: Discover from BQ
            self.log.info("Discovering empty draft pickings from BigQuery")
            pickings_to_process, bq_error = self._discover_empty_drafts_bq(limit)
            if bq_error:
                result.errors.append(bq_error)

        if not pickings_to_process:
            self.log.info("No empty draft pickings found")
            result.kpis = self._build_kpis(result, 0, 0, 0)
            result.complete()
            return result

        self.log.info(f"Found {len(pickings_to_process)} empty draft pickings to clean")

        # Track KPIs
        cancelled_count = 0
        deleted_count = 0

        # Process each picking
        for picking in pickings_to_process:
            result.records_checked += 1
            picking_id = picking["id"]
            picking_name = picking.get("name", f"picking-{picking_id}")

            # Always verify with Odoo before processing (BQ data can be stale)
            verified = self._verify_empty_draft(picking_id)
            if not verified:
                self.log.info(
                    f"Skipping {picking_name}: no longer empty draft (BQ data stale)",
                    record_id=picking_id,
                )
                result.records_skipped += 1
                continue

            try:
                if delete_instead_of_cancel:
                    op_result = self._delete_picking(picking_id, picking_name)
                    if op_result.success and op_result.action != "skipped":
                        deleted_count += 1
                else:
                    op_result = self._cancel_picking(picking_id, picking_name)
                    if op_result.success and op_result.action != "skipped":
                        cancelled_count += 1

                # add_operation handles records_updated/skipped increment
                result.add_operation(op_result)

            except Exception as e:
                self.log.error(
                    f"Exception processing picking {picking_name}",
                    record_id=picking_id,
                    error=str(e),
                )
                result.errors.append(f"Picking {picking_name}: {e}")

        result.kpis = self._build_kpis(result, cancelled_count, deleted_count, len(pickings_to_process))
        result.complete()
        return result

    def _verify_empty_draft(self, picking_id: int) -> Optional[dict]:
        """
        Verify a picking is in draft state with no moves.

        Returns picking dict if valid, None otherwise.
        """
        try:
            pickings = self.odoo.search_read(
                self.PICKING_MODEL,
                [("id", "=", picking_id)],
                fields=["id", "name", "state", "move_ids"],
            )

            if not pickings:
                self.log.warning(f"Picking {picking_id} not found")
                return None

            picking = pickings[0]

            if picking.get("state") != "draft":
                self.log.skip(
                    picking_id,
                    f"Picking {picking['name']} is not in draft state (state={picking.get('state')})",
                )
                return None

            move_ids = picking.get("move_ids", [])
            if move_ids:
                self.log.skip(
                    picking_id,
                    f"Picking {picking['name']} has {len(move_ids)} moves - not empty",
                )
                return None

            return picking

        except Exception as e:
            self.log.error(f"Error verifying picking {picking_id}", error=str(e))
            return None

    def _discover_empty_drafts_bq(self, limit: Optional[int]) -> tuple[list[dict], Optional[str]]:
        """
        Discover empty draft pickings from BigQuery.

        Returns:
            Tuple of (pickings list, error message or None)
        """
        query = self.BQ_QUERY
        if limit:
            query += f"\nLIMIT {limit}"

        try:
            rows = self.bq.query(query)
            pickings = []
            for row in rows:
                pickings.append({
                    "id": row.get("picking_id"),
                    "name": row.get("picking_name"),
                    "picking_type_id": row.get("picking_type_id"),
                    "scheduled_date": row.get("scheduled_date"),
                    "create_date": row.get("create_date"),
                    "sale_id": row.get("sale_id"),
                    "origin": row.get("origin"),
                })
            self.log.info(f"Found {len(pickings)} empty draft pickings from BQ")
            return pickings, None
        except Exception as e:
            error_msg = f"BQ query failed: {e}"
            self.log.error(error_msg, error=str(e))
            return [], error_msg

    def _discover_empty_drafts_odoo(self, limit: Optional[int]) -> list[dict]:
        """
        Discover empty draft pickings directly from Odoo.
        """
        # Find draft pickings
        domain = [("state", "=", "draft")]
        kwargs = {"order": "create_date asc"}
        if limit:
            kwargs["limit"] = limit * 2  # Get more to filter

        draft_pickings = self.odoo.search_read(
            self.PICKING_MODEL,
            domain,
            fields=["id", "name", "state", "move_ids"],
            **kwargs,
        )

        # Filter to only empty ones
        empty_pickings = [
            p for p in draft_pickings
            if not p.get("move_ids")
        ]

        if limit and len(empty_pickings) > limit:
            empty_pickings = empty_pickings[:limit]

        self.log.info(f"Found {len(empty_pickings)} empty draft pickings from Odoo")
        return empty_pickings

    def _cancel_picking(self, picking_id: int, picking_name: str) -> OperationResult:
        """Cancel a picking using action_cancel."""
        reason = "No stock moves linked to this transfer (empty draft)"

        if self.dry_run:
            self.log.skip(picking_id, f"Would cancel {picking_name}")
            return OperationResult.skipped(
                record_id=picking_id,
                model=self.PICKING_MODEL,
                reason="Dry run: would cancel",
                record_name=picking_name,
            )

        try:
            # Post chatter message before cancelling
            self.transfer_ops.post_picking_cancelled_message(
                picking_id=picking_id,
                picking_name=picking_name,
                reason=reason,
                job_name=self.name,
            )

            self.odoo.call(self.PICKING_MODEL, "action_cancel", [picking_id])
            self.log.success(picking_id, f"Cancelled {picking_name}")
            return OperationResult.ok(
                record_id=picking_id,
                model=self.PICKING_MODEL,
                action="cancel",
                message="Cancelled empty draft picking",
                record_name=picking_name,
            )
        except Exception as e:
            self.log.error(f"Failed to cancel {picking_name}", error=str(e))
            return OperationResult.fail(
                record_id=picking_id,
                model=self.PICKING_MODEL,
                action="cancel",
                error=str(e),
                record_name=picking_name,
            )

    def _delete_picking(self, picking_id: int, picking_name: str) -> OperationResult:
        """Delete a picking using unlink."""
        reason = "No stock moves linked to this transfer (empty draft)"

        if self.dry_run:
            self.log.skip(picking_id, f"Would delete {picking_name}")
            return OperationResult.skipped(
                record_id=picking_id,
                model=self.PICKING_MODEL,
                reason="Dry run: would delete",
                record_name=picking_name,
            )

        try:
            # Post chatter message BEFORE deleting (record won't exist after)
            self.transfer_ops.post_picking_deleted_message(
                picking_id=picking_id,
                picking_name=picking_name,
                reason=reason,
                job_name=self.name,
            )

            self.odoo.unlink(self.PICKING_MODEL, [picking_id])
            self.log.success(picking_id, f"Deleted {picking_name}")
            return OperationResult.ok(
                record_id=picking_id,
                model=self.PICKING_MODEL,
                action="delete",
                message="Deleted empty draft picking",
                record_name=picking_name,
            )
        except Exception as e:
            self.log.error(f"Failed to delete {picking_name}", error=str(e))
            return OperationResult.fail(
                record_id=picking_id,
                model=self.PICKING_MODEL,
                action="delete",
                error=str(e),
                record_name=picking_name,
            )

    def _build_kpis(
        self,
        result: JobResult,
        cancelled: int,
        deleted: int,
        total_found: int,
    ) -> dict:
        """Build KPIs dict for the job result."""
        return {
            "pickings_found": total_found,
            "pickings_cancelled": cancelled,
            "pickings_deleted": deleted,
            "pickings_cleaned": cancelled + deleted,
            "exceptions": len(result.errors),
        }


if __name__ == "__main__":
    import sys
    print("\n" + "=" * 70)
    print("Clean Empty Draft Transfers Job")
    print("=" * 70)
    print("\nUsage:")
    print("-" * 70)
    print("\n# Default: Discover from BQ (no params needed)")
    print("python main.py run clean_empty_draft_transfers --dry-run")
    print("\n# With limit")
    print("python main.py run clean_empty_draft_transfers --dry-run limit=10")
    print("\n# Explicit picking IDs (skip BQ query)")
    print("python main.py run clean_empty_draft_transfers --dry-run picking_ids=1001,1002")
    print("\n# Discover from Odoo instead of BQ")
    print("python main.py run clean_empty_draft_transfers --dry-run discover_from_odoo=True")
    print("\n# Delete instead of cancel")
    print("python main.py run clean_empty_draft_transfers --dry-run picking_ids=1001 delete_instead_of_cancel=True")
    print("\n# Live execution (cancels pickings)")
    print("python main.py run clean_empty_draft_transfers dlimit=10")
    print("\n" + "-" * 70)
    print("\nBQ Query to find candidates:")
    print("-" * 70)
    print("""
SELECT
    sp.id AS picking_id,
    sp.name AS picking_name,
    sp.picking_type_id,
    sp.scheduled_date,
    sp.create_date,
    sp.sale_id,
    sp.origin,
    CONCAT('https://odoo.alohas.com/web#id=', CAST(sp.id AS STRING),
           '&model=stock.picking&view_type=form') AS picking_url
FROM `alohas-analytics.prod_staging.stg_bq_odoo__stock_picking` sp
LEFT JOIN `alohas-analytics.prod_staging.stg_odoo__stock_move` sm
    ON sm.picking_id = sp.id
WHERE sp.state = 'draft'
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
HAVING COUNT(sm.id) = 0
ORDER BY sp.create_date ASC
""")
    print("=" * 70 + "\n")
    sys.exit(0)
