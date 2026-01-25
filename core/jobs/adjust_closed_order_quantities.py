"""
Adjust Closed Order Quantities Job

Finds orders where ah_status is delivered/cancelled/closed and adjusts
line quantities to match delivered quantities.
"""

import logging
from typing import Optional

from core.jobs.registry import register_job
from core.interventions import intervention_detector
from core.jobs.base import BaseJob
from core.operations.orders import OrderOperations
from core.operations.transfers import TransferOperations
from core.result import JobResult

logger = logging.getLogger(__name__)

# Default ah_status values for closed orders
# Note: "cancelled" removed - we skip orders where state='cancel' anyway
DEFAULT_CLOSED_STATUSES = ["delivered", "closed"]


@register_job(
    name="adjust_closed_order_quantities",
    description="Adjust line quantities to match delivered qty on closed/delivered/cancelled orders",
    tags=["sales", "cleanup", "orders"],
)
@intervention_detector(
    issue_type="qty_mismatch",
    document_type="sale.order",
    priority="medium",
    department="operations",
    enabled=False,  # Flip to True when ready to track interventions
)
class AdjustClosedOrderQuantitiesJob(BaseJob):
    """
    Adjust order line quantities on closed orders.

    Pattern: detect -> verify -> remediate -> log

    Workflow:
    1. Find orders where ah_status is delivered/cancelled/closed
    2. For each order, find lines where qty_delivered != product_uom_qty
    3. Adjust product_uom_qty to match qty_delivered
    4. Post chatter message documenting all changes
    5. Return results with KPIs

    Use Cases:
    - Cleanup orders where quantities don't match after closure
    - Ensure order data consistency for reporting
    """

    def run(
        self,
        ah_statuses: Optional[list[str]] = None,
        limit: Optional[int] = None,
        order_ids: Optional[list[int]] = None,
        days: Optional[int] = None,
        order_name_pattern: Optional[str] = None,
        exclude_shipping: bool = True,
        **_params
    ) -> JobResult:
        """
        Execute the adjust closed order quantities job.

        Args:
            ah_statuses: List of ah_status values to filter by
                        Defaults to ['delivered', 'cancelled', 'closed']
            limit: Maximum number of orders to process
            order_ids: Optional list of specific order IDs to process (for testing)
            days: Optional - only check orders from last N days
            order_name_pattern: Pattern to match order name (e.g., "S0%", "INT%")
                               Use % as wildcard. Examples:
                               - "S0%" = orders starting with S0
                               - "%INT%" = orders containing INT
                               - "S00%" = orders starting with S00
            exclude_shipping: Exclude shipping/delivery fee products (default: True)
                             Set to False to include shipping lines

        Returns:
            JobResult with execution details
        """
        # Create result with full context for audit trail
        result = JobResult.from_context(self.ctx, parameters={
            "ah_statuses": ah_statuses,
            "limit": limit,
            "order_ids": order_ids,
            "days": days,
            "order_name_pattern": order_name_pattern,
            "exclude_shipping": exclude_shipping,
        })

        # Use default statuses if not provided
        if ah_statuses is None:
            ah_statuses = DEFAULT_CLOSED_STATUSES

        # Initialize operations
        order_ops = OrderOperations(self.odoo, self.ctx, self.log)
        transfer_ops = TransferOperations(self.odoo, self.ctx, self.log)

        # Determine which products to exclude
        exclude_product_ids = None  # Will use defaults (shipping products)
        if not exclude_shipping:
            exclude_product_ids = []  # Empty list = include all

        # =====================================================================
        # PHASE 1: DISCOVERY - Find candidate orders
        # =====================================================================
        self.log.info(
            "Finding closed orders with qty mismatch",
            data={
                "ah_statuses": ah_statuses,
                "limit": limit,
                "order_ids": order_ids,
                "days": days,
                "order_name_pattern": order_name_pattern,
                "exclude_shipping": exclude_shipping,
            },
        )

        try:
            orders, discovery_stats = order_ops.find_closed_orders_with_qty_mismatch(
                ah_statuses=ah_statuses,
                limit=limit,
                order_ids=order_ids,
                days=days,
                order_name_pattern=order_name_pattern,
                exclude_product_ids=exclude_product_ids,
            )
        except Exception as e:
            self.log.error("Failed to find qualifying orders", error=str(e))
            result.errors.append(f"Search failed: {e}")
            result.kpis = self._build_empty_kpis(limit)
            result.complete()
            return result

        if not orders:
            self.log.info("No orders found with qty mismatch")
            result.kpis = self._build_empty_kpis(limit, discovery_stats)
            result.complete()
            return result

        # Calculate totals for metrics
        total_lines = sum(len(o["mismatched_lines"]) for o in orders)
        self.log.info(f"Found {len(orders)} orders with {total_lines} mismatched lines to process")

        # =====================================================================
        # PHASE 2: ENRICHMENT - Get open stock moves for all lines
        # =====================================================================
        all_line_ids = [
            line["id"]
            for order_data in orders
            for line in order_data["mismatched_lines"]
        ]
        open_moves_by_line = transfer_ops.get_open_moves_by_line(all_line_ids)
        lines_with_open_moves = len([lid for lid in all_line_ids if lid in open_moves_by_line])

        # =====================================================================
        # PHASE 3: PROCESSING - Evaluate and adjust each order/line
        # =====================================================================
        # Track processing metrics
        orders_adjusted = 0
        orders_skipped_all_correct = 0
        orders_with_errors = 0
        lines_adjusted = 0
        lines_skipped_already_correct = 0
        lines_skipped_negative = 0
        lines_with_errors = 0

        for order_data in orders:
            order_id = order_data["order_id"]
            order_name = order_data["order_name"]
            mismatched_lines = order_data["mismatched_lines"]

            result.records_checked += 1
            order_lines_adjusted = 0
            order_lines_skipped = 0
            order_has_error = False
            adjusted_lines_for_message = []

            try:
                # Evaluate and adjust each line
                for line in mismatched_lines:
                    line_id = line["id"]
                    ordered_qty = line["product_uom_qty"]
                    delivered_qty = line["qty_delivered"]

                    # Get open moves for this line (for logging only)
                    open_moves = open_moves_by_line.get(line_id, [])
                    total_open_move_qty = sum(m["qty"] for m in open_moves)

                    # For closed orders: target = delivered (ignore open moves)
                    # Open moves on closed orders are orphaned and shouldn't be counted
                    target_qty = delivered_qty

                    # Skip if target is negative (safety)
                    if target_qty < 0:
                        lines_skipped_negative += 1
                        order_lines_skipped += 1
                        result.records_skipped += 1
                        continue

                    # Skip if ordered qty already matches target
                    if abs(ordered_qty - target_qty) < 0.01:
                        lines_skipped_already_correct += 1
                        order_lines_skipped += 1
                        result.records_skipped += 1
                        continue

                    # Store values for chatter message
                    line["_target_qty"] = target_qty
                    line["_open_move_qty"] = total_open_move_qty

                    # Perform adjustment
                    op_result = order_ops.adjust_line_qty_to_delivered_qty(
                        line, order_name=order_name, target_qty=target_qty
                    )
                    result.add_operation(op_result)

                    if op_result.success:
                        order_lines_adjusted += 1
                        lines_adjusted += 1
                        adjusted_lines_for_message.append(line)
                    else:
                        lines_with_errors += 1
                        order_has_error = True
                        self.log.warning(
                            f"Failed to adjust line {line['id']} on order {order_name}",
                            data={"error": op_result.error},
                        )

                # Post chatter message if any lines were adjusted
                if adjusted_lines_for_message:
                    msg_result = order_ops.post_qty_adjustment_message(
                        order_id=order_id,
                        order_name=order_name,
                        adjusted_lines=adjusted_lines_for_message,
                    )

                    if not msg_result.success and not self.dry_run:
                        self.log.warning(
                            f"Failed to post chatter message on order {order_name}",
                            data={"error": msg_result.error},
                        )

                    orders_adjusted += 1
                    self.log.success(
                        order_id,
                        f"Adjusted {order_lines_adjusted} line(s) on {order_name}",
                    )
                else:
                    # All lines were skipped for this order
                    orders_skipped_all_correct += 1

                if order_has_error:
                    orders_with_errors += 1

            except Exception as e:
                self.log.error(
                    f"Exception processing order {order_name}",
                    record_id=order_id,
                    error=str(e),
                )
                result.errors.append(f"Order {order_name}: {e}")
                orders_with_errors += 1

        # =====================================================================
        # BUILD KPIs - Structured funnel metrics
        # =====================================================================
        result.kpis = self._build_kpis(
            limit=limit,
            discovery=discovery_stats,
            orders_processed=len(orders),
            orders_adjusted=orders_adjusted,
            orders_skipped_all_correct=orders_skipped_all_correct,
            orders_with_errors=orders_with_errors,
            lines_processed=total_lines,
            lines_adjusted=lines_adjusted,
            lines_skipped_already_correct=lines_skipped_already_correct,
            lines_skipped_negative=lines_skipped_negative,
            lines_with_errors=lines_with_errors,
            lines_with_open_moves=lines_with_open_moves,
        )

        result.complete()
        return result

    def _build_empty_kpis(
        self,
        limit: Optional[int],
        discovery: Optional[dict] = None,
    ) -> dict:
        """Build KPIs when no orders are found."""
        return self._build_kpis(
            limit=limit,
            discovery=discovery or {},
            orders_processed=0,
            orders_adjusted=0,
            orders_skipped_all_correct=0,
            orders_with_errors=0,
            lines_processed=0,
            lines_adjusted=0,
            lines_skipped_already_correct=0,
            lines_skipped_negative=0,
            lines_with_errors=0,
            lines_with_open_moves=0,
        )

    def _build_kpis(
        self,
        limit: Optional[int],
        discovery: dict,
        orders_processed: int,
        orders_adjusted: int,
        orders_skipped_all_correct: int,
        orders_with_errors: int,
        lines_processed: int,
        lines_adjusted: int,
        lines_skipped_already_correct: int,
        lines_skipped_negative: int,
        lines_with_errors: int,
        lines_with_open_moves: int,
    ) -> dict:
        """
        Build structured KPIs showing the processing funnel.

        Structure:
        - discovery: What we found in Odoo
        - orders: Order-level processing breakdown
        - lines: Line-level processing breakdown
        """
        return {
            # Discovery phase - what we found
            "discovery": {
                "lines_from_query": discovery.get("lines_from_query", 0),
                "lines_with_mismatch": discovery.get("lines_with_mismatch", 0),
                "orders_with_mismatch": discovery.get("orders_with_mismatch", 0),
                "limit_param": limit,
                "limit_reached": discovery.get("limit_reached", False),
            },
            # Order-level processing
            "orders": {
                "processed": orders_processed,
                "adjusted": orders_adjusted,
                "skipped_all_lines_correct": orders_skipped_all_correct,
                "with_errors": orders_with_errors,
            },
            # Line-level processing
            "lines": {
                "processed": lines_processed,
                "adjusted": lines_adjusted,
                "skipped_already_correct": lines_skipped_already_correct,
                "skipped_negative_qty": lines_skipped_negative,
                "with_errors": lines_with_errors,
            },
            # Context about open moves
            "open_moves": {
                "lines_with_moves": lines_with_open_moves,
                "lines_without_moves": lines_processed - lines_with_open_moves,
            },
        }


# --- Quick Reference (Cheatsheet) ---
#
# Run via main.py (recommended):
#   python main.py run adjust_closed_order_quantities --dry-run
#   python main.py run adjust_closed_order_quantities --dry-run limit=10
#   python main.py run adjust_closed_order_quantities --dry-run order_ids=455346
#   python main.py run adjust_closed_order_quantities --dry-run days=30
#
# Filter by order name pattern (% is wildcard):
#   python main.py run adjust_closed_order_quantities --dry-run order_name_pattern=S0%     # S0xxxx orders
#   python main.py run adjust_closed_order_quantities --dry-run order_name_pattern=S00%    # S00xxx orders
#   python main.py run adjust_closed_order_quantities --dry-run order_name_pattern=INT%    # INT orders
#   python main.py run adjust_closed_order_quantities --dry-run "order_name_pattern=%INT%" # Contains INT
#
# Live execution:
#   python main.py run adjust_closed_order_quantities limit=10  # Live!
#
# With debug output:
#   python main.py run adjust_closed_order_quantities --dry-run --debug
#

if __name__ == "__main__":
    import sys
    print("\n" + "=" * 60)
    print("Use main.py to run jobs (avoids import warnings):")
    print("=" * 60)
    print("\n  python main.py run adjust_closed_order_quantities --dry-run")
    print("  python main.py run adjust_closed_order_quantities --dry-run limit=10")
    print("  python main.py run adjust_closed_order_quantities --dry-run days=30")
    print()
    print("  # Filter by order name pattern (% is wildcard):")
    print("  python main.py run adjust_closed_order_quantities --dry-run order_name_pattern=S0%")
    print("  python main.py run adjust_closed_order_quantities --dry-run order_name_pattern=INT%")
    print()
    print("  # Include shipping lines (excluded by default):")
    print("  python main.py run adjust_closed_order_quantities --dry-run exclude_shipping=false")
    print()
    print("  # Live execution:")
    print("  python main.py run adjust_closed_order_quantities order_name_pattern=S0% limit=10")
    print("\n" + "=" * 60 + "\n")
    sys.exit(0)
