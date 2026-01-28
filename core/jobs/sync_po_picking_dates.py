"""
Sync PO Picking Dates Job

Synchronizes stock.picking dates (scheduled_date, date_deadline) to match
the parent purchase.order date_planned. Supports granular control:
- Header-only updates (picking dates)
- Line-only updates (specific move dates)
- Both
"""

import logging
from datetime import datetime
from typing import Any, Optional

from core.jobs.registry import register_job
from core.jobs.base import BaseJob
from core.operations.purchases import PurchaseOperations
from core.result import JobResult

logger = logging.getLogger(__name__)


@register_job(
    name="sync_po_picking_dates",
    description="Sync PO picking dates to match date_planned",
    tags=["purchase", "compliance", "dates", "sync"],
)
class SyncPOPickingDatesJob(BaseJob):
    """
    Sync PO picking dates to match purchase order date_planned.

    Pattern: detect -> verify -> remediate -> log

    Supports 3 scenarios based on BQ query flags:
    1. Header only (needs_header_update=True, needs_line_update=False)
       - Update picking scheduled_date and date_deadline
    2. Line only (needs_header_update=False, needs_line_update=True)
       - Update specific stock.move.date
    3. Both (needs_header_update=True, needs_line_update=True)
       - Update both picking and move dates

    Data Source:
    - BQ query (auto-discovery) or explicit candidates/po_ids/picking_ids
    """

    # BQ query to find PO picking date mismatches
    BQ_QUERY = """
    WITH picking_base AS (
        SELECT po.id AS po_id, po.name AS po_name, po.date_planned AS po_date_planned,
               sp.id AS picking_id, sp.name AS picking_name, sp.scheduled_date,
               DATE(sp.scheduled_date) != DATE(po.date_planned) AS needs_header_update
        FROM `alohas-analytics.prod_staging.stg_odoo__purchase_order` po
        JOIN `alohas-analytics.prod_staging.stg_bq_odoo__stock_picking` sp
            ON sp.origin = po.name
        WHERE sp.state NOT IN ('done', 'cancel')
          AND po.date_planned IS NOT NULL
    ),
    move_details AS (
        SELECT sm.picking_id, pol.id AS pol_id, pol.product_id,
               pol.date_planned AS pol_date_planned, sm.id AS move_id,
               sm.date AS move_date,
               DATE(sm.date) != DATE(pol.date_planned) AS needs_line_update
        FROM `alohas-analytics.prod_staging.stg_odoo__purchase_order_line` pol
        JOIN `alohas-analytics.prod_staging.stg_odoo__stock_move` sm
            ON sm.purchase_line_id = pol.id
        WHERE sm.state NOT IN ('done', 'cancel')
    )
    SELECT pb.*, md.pol_id, md.product_id, md.pol_date_planned,
           md.move_id, md.move_date, md.needs_line_update
    FROM picking_base pb
    LEFT JOIN move_details md ON md.picking_id = pb.picking_id
    WHERE pb.needs_header_update = TRUE
       OR md.needs_line_update = TRUE
    """

    def run(
        self,
        candidates: Optional[list[dict[str, Any]]] = None,
        po_ids: Optional[list[int]] = None,
        picking_ids: Optional[list[int]] = None,
        limit: Optional[int] = None,
        sync_line_level: bool = False,
        **_params
    ) -> JobResult:
        """
        Execute the PO picking date sync job.

        Args:
            candidates: Full BQ result with granular update flags. Each dict should have:
                - po_id, po_name, po_date_planned (for header updates)
                - picking_id, picking_name, needs_header_update (bool)
                - move_id, pol_date_planned, needs_line_update (bool) (for line updates)
            po_ids: List of purchase order IDs (full sync - both header and line)
            picking_ids: List of picking IDs (header sync only)
            limit: Maximum number of records to process
            sync_line_level: Enable line-level sync when using po_ids (default: False)

        Returns:
            JobResult with execution details
        """
        # Create result with full context for audit trail
        result = JobResult.from_context(self.ctx, parameters={
            "candidates": f"{len(candidates)} candidates" if candidates else None,
            "po_ids": po_ids,
            "picking_ids": picking_ids,
            "limit": limit,
            "sync_line_level": sync_line_level,
        })

        # Discover from BQ if no explicit inputs provided
        if not candidates and not po_ids and not picking_ids:
            self.log.info("No explicit inputs provided - discovering from BigQuery")
            candidates, bq_error = self._discover_from_bq(limit)
            if bq_error:
                result.errors.append(bq_error)
            if not candidates:
                self.log.info("No PO picking date mismatches found")
                result.kpis = self._build_kpis(result, 0, 0, 0, 0, {})
                result.complete()
                return result

        # Route to appropriate handler based on input
        if candidates:
            return self._process_candidates(result, candidates, limit)
        else:
            return self._process_simple(result, po_ids, picking_ids, limit, sync_line_level)

    def _process_candidates(
        self,
        result: JobResult,
        candidates: list[dict[str, Any]],
        limit: Optional[int],
    ) -> JobResult:
        """
        Process candidates from BQ query with granular update flags.

        Handles 3 scenarios:
        - Header only: Update picking dates
        - Line only: Update specific move date
        - Both: Update both
        """
        po_ops = PurchaseOperations(self.odoo, self.ctx, self.log)

        # Track KPIs
        pickings_updated = 0
        moves_updated = 0
        header_only_count = 0
        line_only_count = 0
        both_count = 0

        # Apply limit by PO (not by raw row count)
        if limit:
            unique_po_ids: list[int] = []
            seen_pos: set[int] = set()
            for c in candidates:
                po_id = c.get("po_id")
                if po_id and po_id not in seen_pos:
                    seen_pos.add(po_id)
                    unique_po_ids.append(po_id)
            if len(unique_po_ids) > limit:
                allowed_pos = set(unique_po_ids[:limit])
                candidates = [c for c in candidates if c.get("po_id") in allowed_pos]

        # Count unique POs for logging
        po_count = len({c.get("po_id") for c in candidates if c.get("po_id")})
        self.log.info(f"Processing {len(candidates)} candidates from {po_count} POs")

        # Group by picking for efficient header updates
        pickings_processed = set()
        moves_processed = set()

        for candidate in candidates:
            result.records_checked += 1

            try:
                picking_id = candidate.get("picking_id")
                move_id = candidate.get("move_id")
                needs_header = candidate.get("needs_header_update", False)
                needs_line = candidate.get("needs_line_update", False)

                # Parse dates
                po_date_planned = self._parse_date(candidate.get("po_date_planned"))
                pol_date_planned = self._parse_date(candidate.get("pol_date_planned"))

                # Header update (picking dates)
                if needs_header and picking_id and picking_id not in pickings_processed:
                    picking_name = candidate.get("picking_name") or f"picking-{picking_id}"

                    if po_date_planned:
                        pick_result = po_ops.sync_picking_dates(
                            picking_id=picking_id,
                            new_date=po_date_planned,
                            picking_name=picking_name,
                        )
                        result.add_operation(pick_result)

                        if pick_result.success:
                            pickings_updated += 1
                            pickings_processed.add(picking_id)

                            if needs_line:
                                both_count += 1
                            else:
                                header_only_count += 1

                # Line update (specific move date)
                if needs_line and move_id and move_id not in moves_processed:
                    target_date = pol_date_planned or po_date_planned

                    if target_date:
                        move_result = po_ops.sync_single_move_date(
                            move_id=move_id,
                            new_date=target_date,
                            move_name=f"move-{move_id}",
                        )
                        result.add_operation(move_result)

                        if move_result.success:
                            moves_updated += 1
                            moves_processed.add(move_id)

                            if not needs_header:
                                line_only_count += 1

            except Exception as e:
                self.log.error(
                    f"Exception processing candidate",
                    data={"candidate": candidate},
                    error=str(e),
                )
                result.errors.append(f"Candidate error: {e}")

        # Post chatter messages on each updated picking
        self._post_picking_messages(result, candidates, po_ops, pickings_processed, moves_processed)

        # Post summary messages per PO
        self._post_summary_messages(result, candidates, po_ops, pickings_updated, moves_updated)

        # Set KPIs
        result.kpis = {
            "pos_checked": po_count,
            "candidates_processed": result.records_checked,
            "pickings_updated": pickings_updated,
            "moves_updated": moves_updated,
            "header_only_updates": header_only_count,
            "line_only_updates": line_only_count,
            "both_updates": both_count,
            "exceptions": len(result.errors),
        }

        result.complete()
        return result

    def _post_picking_messages(
        self,
        result: JobResult,
        candidates: list[dict[str, Any]],
        po_ops: PurchaseOperations,
        pickings_processed: set[int],
        moves_processed: set[int],
    ) -> None:
        """Post chatter messages on each updated picking."""
        if not pickings_processed:
            return

        # Group candidates by picking_id to gather context
        picking_info: dict[int, dict] = {}
        for c in candidates:
            pid = c.get("picking_id")
            if pid and pid in pickings_processed:
                if pid not in picking_info:
                    picking_info[pid] = {
                        "picking_name": c.get("picking_name") or f"picking-{pid}",
                        "po_name": c.get("po_name", ""),
                        "po_date_planned": self._parse_date(c.get("po_date_planned")),
                        "scheduled_date": self._parse_date(c.get("scheduled_date")),
                        "moves_updated": 0,
                    }
                # Count moves updated for this picking
                move_id = c.get("move_id")
                if move_id and move_id in moves_processed:
                    picking_info[pid]["moves_updated"] += 1

        # Post one message per picking
        for picking_id, info in picking_info.items():
            new_date = info["po_date_planned"] or datetime.now()
            picking_msg = po_ops.post_picking_date_sync_message(
                picking_id=picking_id,
                picking_name=info["picking_name"],
                new_date=new_date,
                po_name=info["po_name"],
                old_scheduled=info["scheduled_date"],
                old_deadline=None,  # BQ query doesn't include date_deadline
                moves_updated=info["moves_updated"],
            )
            result.add_operation(picking_msg)

    def _post_summary_messages(
        self,
        result: JobResult,
        candidates: list[dict[str, Any]],
        po_ops: PurchaseOperations,
        pickings_updated: int,
        moves_updated: int,
    ) -> None:
        """Post chatter messages summarizing updates per PO."""
        # Group by PO for summary messages
        po_summaries: dict[int, dict] = {}
        for c in candidates:
            po_id = c.get("po_id")
            if po_id:
                if po_id not in po_summaries:
                    po_summaries[po_id] = {
                        "po_name": c.get("po_name", f"PO-{po_id}"),
                        "date_planned": self._parse_date(c.get("po_date_planned")),
                        "pickings": 0,
                        "moves": 0,
                    }
                if c.get("needs_header_update"):
                    po_summaries[po_id]["pickings"] += 1
                if c.get("needs_line_update"):
                    po_summaries[po_id]["moves"] += 1

        # Post one message per PO
        for po_id, summary in po_summaries.items():
            if summary["pickings"] > 0 or summary["moves"] > 0:
                msg_result = po_ops.post_po_date_sync_message(
                    po_id=po_id,
                    po_name=summary["po_name"],
                    old_scheduled=None,
                    old_deadline=None,
                    new_date=summary["date_planned"] or datetime.now(),
                    pickings_updated=summary["pickings"],
                    moves_updated=summary["moves"],
                    line_level_sync=summary["moves"] > 0,
                )
                result.add_operation(msg_result)

    def _parse_date(self, date_val: Any) -> Optional[datetime]:
        """Parse date from various formats."""
        if date_val is None:
            return None
        if isinstance(date_val, datetime):
            return date_val
        if isinstance(date_val, str):
            try:
                return datetime.strptime(date_val, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    return datetime.strptime(date_val, "%Y-%m-%d")
                except ValueError:
                    return None
        return None

    def _discover_from_bq(self, limit: Optional[int]) -> tuple[list[dict], Optional[str]]:
        """
        Discover PO picking date mismatches from BigQuery.

        Returns:
            Tuple of (candidates list, error message or None)
        """
        query = self.BQ_QUERY
        if limit:
            query += f"\nLIMIT {limit}"

        try:
            rows = self.bq.query(query)
            candidates = []
            for row in rows:
                candidates.append({
                    "po_id": row.get("po_id"),
                    "po_name": row.get("po_name"),
                    "po_date_planned": row.get("po_date_planned"),
                    "picking_id": row.get("picking_id"),
                    "picking_name": row.get("picking_name"),
                    "scheduled_date": row.get("scheduled_date"),
                    "needs_header_update": row.get("needs_header_update", False),
                    "pol_id": row.get("pol_id"),
                    "product_id": row.get("product_id"),
                    "pol_date_planned": row.get("pol_date_planned"),
                    "move_id": row.get("move_id"),
                    "move_date": row.get("move_date"),
                    "needs_line_update": row.get("needs_line_update", False),
                })
            self.log.info(f"Found {len(candidates)} PO picking date mismatches from BQ")
            return candidates, None
        except Exception as e:
            error_msg = f"BQ query failed: {e}"
            self.log.error(error_msg, error=str(e))
            return [], error_msg

    def _process_simple(
        self,
        result: JobResult,
        po_ids: Optional[list[int]],
        picking_ids: Optional[list[int]],
        limit: Optional[int],
        sync_line_level: bool,
    ) -> JobResult:
        """Original simple processing for po_ids/picking_ids input."""

        # Initialize operations
        po_ops = PurchaseOperations(self.odoo, self.ctx, self.log)

        # Track KPIs
        pos_checked = 0
        pickings_updated = 0
        moves_updated = 0
        line_level_moves_updated = 0
        skip_reasons: dict[str, int] = {}

        # Collect POs to process
        pos_to_process = []

        # Process from explicit po_ids
        if po_ids:
            if limit and len(po_ids) > limit:
                po_ids = po_ids[:limit]

            self.log.info(
                f"Processing {len(po_ids)} purchase orders",
                data={"po_ids": po_ids},
            )

            for po_id in po_ids:
                try:
                    # Get PO details
                    pos = self.odoo.search_read(
                        "purchase.order",
                        [("id", "=", po_id)],
                        fields=["id", "name", "date_planned"],
                    )

                    if not pos:
                        continue

                    po = pos[0]
                    date_planned_str = po.get("date_planned")

                    if not date_planned_str:
                        continue

                    if isinstance(date_planned_str, str):
                        date_planned = datetime.strptime(
                            date_planned_str, "%Y-%m-%d %H:%M:%S"
                        )
                    else:
                        date_planned = date_planned_str

                    pos_to_process.append({
                        "po_id": po_id,
                        "po_name": po["name"],
                        "date_planned": date_planned,
                    })

                except Exception as e:
                    self.log.error(
                        f"Error getting PO {po_id}",
                        record_id=po_id,
                        error=str(e),
                    )

        # Process from explicit picking_ids
        if picking_ids:
            self.log.info(
                f"Processing {len(picking_ids)} explicit picking IDs",
                data={"picking_ids": picking_ids},
            )

            # Group pickings by PO
            for picking_id in picking_ids:
                try:
                    # Get picking with related PO info
                    pickings = self.odoo.search_read(
                        "stock.picking",
                        [("id", "=", picking_id)],
                        fields=["id", "name", "purchase_id"],
                    )

                    if not pickings:
                        continue

                    picking = pickings[0]
                    purchase_id = picking.get("purchase_id")

                    if not purchase_id:
                        continue

                    po_id = purchase_id[0]

                    # Check if already in list
                    if any(p["po_id"] == po_id for p in pos_to_process):
                        continue

                    # Get PO details
                    pos = self.odoo.search_read(
                        "purchase.order",
                        [("id", "=", po_id)],
                        fields=["id", "name", "date_planned"],
                    )

                    if not pos or not pos[0].get("date_planned"):
                        continue

                    po = pos[0]
                    date_planned_str = po["date_planned"]

                    if isinstance(date_planned_str, str):
                        date_planned = datetime.strptime(
                            date_planned_str, "%Y-%m-%d %H:%M:%S"
                        )
                    else:
                        date_planned = date_planned_str

                    pos_to_process.append({
                        "po_id": po_id,
                        "po_name": po["name"],
                        "date_planned": date_planned,
                    })

                except Exception as e:
                    self.log.error(
                        f"Error processing picking {picking_id}",
                        record_id=picking_id,
                        error=str(e),
                    )

        if not pos_to_process:
            self.log.info("No purchase orders to process")
            result.kpis = self._build_kpis(result, 0, 0, 0, 0, {})
            result.complete()
            return result

        self.log.info(f"Processing {len(pos_to_process)} purchase orders")

        # Process each PO
        for po_data in pos_to_process:
            pos_checked += 1
            result.records_checked += 1

            po_id = po_data["po_id"]
            po_name = po_data["po_name"]
            date_planned = po_data["date_planned"]

            try:
                # Get open pickings for this PO
                pickings = po_ops.get_open_pickings_for_po(po_id)

                if not pickings:
                    result.records_skipped += 1
                    skip_reasons["no_open_pickings"] = skip_reasons.get("no_open_pickings", 0) + 1
                    continue

                po_pickings_updated = 0
                po_moves_updated = 0

                for picking in pickings:
                    picking_id = picking["id"]
                    picking_name = picking.get("name") or f"picking-{picking_id}"

                    # Parse old dates for comparison
                    old_scheduled = picking.get("scheduled_date")
                    old_deadline = picking.get("date_deadline")

                    if isinstance(old_scheduled, str):
                        old_scheduled = datetime.strptime(old_scheduled, "%Y-%m-%d %H:%M:%S")
                    if isinstance(old_deadline, str):
                        old_deadline = datetime.strptime(old_deadline, "%Y-%m-%d %H:%M:%S")

                    # Check if update is needed
                    target_date = date_planned.date() if hasattr(date_planned, 'date') else date_planned
                    old_scheduled_date = old_scheduled.date() if old_scheduled and hasattr(old_scheduled, 'date') else None
                    old_deadline_date = old_deadline.date() if old_deadline and hasattr(old_deadline, 'date') else None

                    needs_update = (
                        old_scheduled_date != target_date or
                        old_deadline_date != target_date
                    )

                    if not needs_update:
                        continue

                    # Sync picking dates
                    pick_result = po_ops.sync_picking_dates(
                        picking_id=picking_id,
                        new_date=date_planned,
                        picking_name=picking_name,
                    )
                    result.add_operation(pick_result)

                    if pick_result.success:
                        po_pickings_updated += 1
                        pickings_updated += 1

                        # Sync move dates (header-level)
                        picking_moves_updated = 0
                        move_results = po_ops.sync_move_dates(
                            picking_id=picking_id,
                            new_date=date_planned,
                        )
                        for mr in move_results:
                            result.add_operation(mr)
                            if mr.success:
                                picking_moves_updated += 1
                                po_moves_updated += 1
                                moves_updated += 1

                        # Post chatter message on picking
                        picking_msg = po_ops.post_picking_date_sync_message(
                            picking_id=picking_id,
                            picking_name=picking_name,
                            new_date=date_planned,
                            po_name=po_name,
                            old_scheduled=old_scheduled,
                            old_deadline=old_deadline,
                            moves_updated=picking_moves_updated,
                        )
                        result.add_operation(picking_msg)

                # Line-level sync (bonus feature)
                if sync_line_level:
                    line_results = po_ops.sync_move_dates_to_line_planned(po_id)
                    for lr in line_results:
                        result.add_operation(lr)
                        if lr.success:
                            line_level_moves_updated += 1

                # Post chatter message on PO if anything was updated
                if po_pickings_updated > 0:
                    msg_result = po_ops.post_po_date_sync_message(
                        po_id=po_id,
                        po_name=po_name,
                        old_scheduled=None,  # Multiple pickings, can't show single old date
                        old_deadline=None,
                        new_date=date_planned,
                        pickings_updated=po_pickings_updated,
                        moves_updated=po_moves_updated + (line_level_moves_updated if sync_line_level else 0),
                        line_level_sync=sync_line_level,
                    )
                    result.add_operation(msg_result)

                    result.records_updated += 1

                    self.log.success(
                        po_id,
                        f"Synced dates for {po_name}: {po_pickings_updated} pickings, {po_moves_updated} moves",
                    )
                else:
                    result.records_skipped += 1
                    skip_reasons["dates_match"] = skip_reasons.get("dates_match", 0) + 1

            except Exception as e:
                self.log.error(
                    f"Exception processing PO {po_name}",
                    record_id=po_id,
                    error=str(e),
                )
                result.errors.append(f"PO {po_name}: {e}")

        # Set KPIs
        result.kpis = self._build_kpis(
            result, pos_checked, pickings_updated, moves_updated, line_level_moves_updated, skip_reasons
        )

        result.complete()
        return result

    def _build_kpis(
        self,
        result: JobResult,
        pos_checked: int,
        pickings_updated: int,
        moves_updated: int,
        line_level_moves_updated: int,
        skip_reasons: dict[str, int],
    ) -> dict:
        """Build KPIs dict for the job result."""
        kpis = {
            "pos_checked": pos_checked,
            "pickings_updated": pickings_updated,
            "pos_skipped": sum(skip_reasons.values()),
            "moves_updated": moves_updated,
            "line_level_moves_updated": line_level_moves_updated,
            "exceptions": len(result.errors),
        }
        if skip_reasons:
            kpis["skip_reasons"] = skip_reasons
        return kpis


if __name__ == "__main__":
    import sys
    print("\n" + "=" * 70)
    print("Sync PO Picking Dates Job")
    print("=" * 70)
    print("\nUsage:")
    print("-" * 70)
    print("\n# Simple: By PO IDs (full sync - header + moves)")
    print("python main.py run sync_po_picking_dates --dry-run po_ids=100,101")
    print("\n# With line-level sync (each move gets its PO line date)")
    print("python main.py run sync_po_picking_dates --dry-run po_ids=100 sync_line_level=True")
    print("\n# By picking IDs")
    print("python main.py run sync_po_picking_dates --dry-run picking_ids=2001,2002")
    print("\n# Live execution")
    print("python main.py run sync_po_picking_dates po_ids=100,101")
    print("\n" + "-" * 70)
    print("\nAdvanced: Pass candidates from BQ with granular flags (Python):")
    print("-" * 70)
    print("""
candidates = [
    # Header only
    {"po_id": 100, "picking_id": 1001, "po_date_planned": "2025-02-01",
     "needs_header_update": True, "needs_line_update": False},
    # Line only
    {"po_id": 100, "picking_id": 1001, "move_id": 5001,
     "pol_date_planned": "2025-02-05",
     "needs_header_update": False, "needs_line_update": True},
]
job.execute(candidates=candidates)
""")
    print("-" * 70)
    print("\nBQ Query to find candidates:")
    print("-" * 70)
    print("""
WITH picking_base AS (
    SELECT po.id AS po_id, po.name AS po_name, po.date_planned AS po_date_planned,
           sp.id AS picking_id, sp.name AS picking_name, sp.scheduled_date,
           DATE(sp.scheduled_date) != DATE(po.date_planned) AS needs_header_update
    FROM `alohas-analytics.prod_staging.stg_odoo__purchase_order` po
    JOIN `alohas-analytics.prod_staging.stg_bq_odoo__stock_picking` sp
        ON sp.origin = po.name
    WHERE sp.state NOT IN ('done', 'cancel')
      AND po.date_planned IS NOT NULL
),
move_details AS (
    SELECT sm.picking_id, pol.id AS pol_id, pol.product_id,
           pol.date_planned AS pol_date_planned, sm.id AS move_id,
           sm.date AS move_date,
           DATE(sm.date) != DATE(pol.date_planned) AS needs_line_update
    FROM `alohas-analytics.prod_staging.stg_odoo__purchase_order_line` pol
    JOIN `alohas-analytics.prod_staging.stg_odoo__stock_move` sm
        ON sm.purchase_line_id = pol.id
    WHERE sm.state NOT IN ('done', 'cancel')
)
SELECT pb.*, md.pol_id, md.product_id, md.pol_date_planned,
       md.move_id, md.move_date, md.needs_line_update,
       CONCAT('https://odoo.alohas.com/web#id=', CAST(pb.po_id AS STRING),
              '&model=purchase.order&view_type=form') AS po_url
FROM picking_base pb
LEFT JOIN move_details md ON md.picking_id = pb.picking_id
WHERE pb.needs_header_update = TRUE
   OR md.needs_line_update = TRUE
   OR md.move_id IS NULL
""")
    print("=" * 70 + "\n")
    sys.exit(0)
