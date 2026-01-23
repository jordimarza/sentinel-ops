"""
Complete Shipping Only Orders Job

Finds orders where the only pending delivery items are shipping fee products
and auto-completes those shipping lines by setting qty_delivered = product_uom_qty.
"""

import logging
from typing import Optional

from core.jobs.registry import register_job
from core.jobs.base import BaseJob
from core.operations.orders import OrderOperations
from core.result import JobResult

logger = logging.getLogger(__name__)

# Default shipping product IDs for ALOHAS
DEFAULT_SHIPPING_PRODUCT_IDS = [15743, 23506, 22407, 9216, 23233]


@register_job(
    name="complete_shipping_only_orders",
    description="Auto-complete shipping lines on orders where only shipping is pending",
    tags=["sales", "shipping", "automation"],
)
class CompleteShippingOnlyOrdersJob(BaseJob):
    """
    Auto-complete shipping lines on orders where only shipping is pending.

    Pattern: detect -> verify -> remediate -> log

    Workflow:
    1. Find orders where ONLY shipping fee lines have pending delivery
    2. For each order, complete the shipping line(s)
    3. Post chatter message documenting the action
    4. Return results with KPIs

    Use Cases:
    - Orders stuck because shipping fee wasn't marked delivered
    - Cleanup of orders where all products shipped but shipping line incomplete
    """

    def run(
        self,
        shipping_product_ids: Optional[list[int]] = None,
        limit: Optional[int] = None,
        order_ids: Optional[list[int]] = None,
        **params
    ) -> JobResult:
        """
        Execute the complete shipping only orders job.

        Args:
            shipping_product_ids: List of product IDs that represent shipping fees
                                  Defaults to ALOHAS standard shipping products
            limit: Maximum number of orders to process
            order_ids: Optional list of specific order IDs to process (for testing)

        Returns:
            JobResult with execution details
        """
        result = JobResult.create(self.name, self.dry_run)

        # Use default shipping product IDs if not provided
        if shipping_product_ids is None:
            shipping_product_ids = DEFAULT_SHIPPING_PRODUCT_IDS

        # Initialize operations
        order_ops = OrderOperations(self.odoo, self.ctx, self.log)

        # Step 1: Find qualifying orders
        self.log.info(
            "Finding orders with only shipping pending",
            data={
                "shipping_product_ids": shipping_product_ids,
                "limit": limit,
                "order_ids": order_ids,
            },
        )

        try:
            orders = order_ops.find_orders_with_only_shipping_pending(
                shipping_product_ids=shipping_product_ids,
                limit=limit,
                order_ids=order_ids,
            )
        except Exception as e:
            self.log.error("Failed to find qualifying orders", error=str(e))
            result.errors.append(f"Search failed: {e}")
            result.kpis = self._build_kpis(result, 0, 0)
            result.complete()
            return result

        if not orders:
            self.log.info("No orders found with only shipping pending")
            result.kpis = self._build_kpis(result, 0, 0)
            result.complete()
            return result

        self.log.info(f"Found {len(orders)} orders to process")

        # Track KPIs
        orders_completed = 0
        lines_completed = 0

        # Step 2 & 3: Process each order
        for order_data in orders:
            order_id = order_data["order_id"]
            order_name = order_data["order_name"]
            pending_lines = order_data["pending_shipping_lines"]

            result.records_checked += 1
            order_lines_completed = 0

            try:
                # Complete each pending shipping line
                for line in pending_lines:
                    op_result = order_ops.complete_shipping_line(line)
                    result.add_operation(op_result)

                    if op_result.success:
                        order_lines_completed += 1
                        lines_completed += 1
                    else:
                        self.log.warning(
                            f"Failed to complete shipping line {line['id']} on order {order_name}",
                            data={"error": op_result.error},
                        )

                # Post chatter message if any lines were completed
                if order_lines_completed > 0:
                    msg_result = order_ops.post_shipping_completion_message(
                        order_id=order_id,
                        order_name=order_name,
                        lines_completed=order_lines_completed,
                    )

                    if not msg_result.success and not self.dry_run:
                        self.log.warning(
                            f"Failed to post chatter message on order {order_name}",
                            data={"error": msg_result.error},
                        )

                    orders_completed += 1
                    self.log.success(
                        order_id,
                        f"Completed {order_lines_completed} shipping line(s) on {order_name}",
                    )

            except Exception as e:
                self.log.error(
                    f"Exception processing order {order_name}",
                    record_id=order_id,
                    error=str(e),
                )
                result.errors.append(f"Order {order_name}: {e}")

        # Set KPIs
        result.kpis = self._build_kpis(result, orders_completed, lines_completed)

        result.complete()
        return result

    def _build_kpis(
        self,
        result: JobResult,
        orders_completed: int,
        lines_completed: int,
    ) -> dict:
        """Build KPIs dict for the job result."""
        return {
            "orders_checked": result.records_checked,
            "orders_completed": orders_completed,
            "lines_completed": lines_completed,
            "exceptions": len(result.errors),
        }


# --- Direct execution for testing ---

if __name__ == "__main__":
    """
    Run this job directly for testing.

    Usage:
        python -m core.jobs.complete_shipping_only_orders --dry-run
        python -m core.jobs.complete_shipping_only_orders --dry-run --order-ids 455346
        python -m core.jobs.complete_shipping_only_orders --dry-run --limit 10
        python -m core.jobs.complete_shipping_only_orders --order-ids 455346  # Live!
    """
    import argparse
    import sys
    import os

    # Add project root to path
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

    from core.context import RequestContext

    parser = argparse.ArgumentParser(
        description="Complete Shipping Only Orders - Auto-complete shipping lines where only shipping is pending",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in dry-run mode (no mutations)",
    )
    parser.add_argument(
        "--order-ids",
        type=str,
        help="Comma-separated order IDs to process (e.g., 455346,455347)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of orders to process",
    )
    parser.add_argument(
        "--shipping-products",
        type=str,
        help="Comma-separated shipping product IDs (overrides defaults)",
    )

    args = parser.parse_args()

    # Parse list arguments
    order_ids = None
    if args.order_ids:
        order_ids = [int(x.strip()) for x in args.order_ids.split(",")]

    shipping_product_ids = None
    if args.shipping_products:
        shipping_product_ids = [int(x.strip()) for x in args.shipping_products.split(",")]

    # Create context
    ctx = RequestContext.for_cli(
        job_name="complete_shipping_only_orders",
        dry_run=args.dry_run,
    )

    print(f"\n{'='*60}")
    print(f"Complete Shipping Only Orders")
    print(f"{'='*60}")
    print(f"  Mode: {'DRY-RUN' if args.dry_run else 'LIVE'}")
    print(f"  Order IDs: {order_ids or 'all qualifying'}")
    print(f"  Limit: {args.limit or 'none'}")
    print(f"  Request ID: {ctx.request_id}")
    print(f"{'='*60}\n")

    if not args.dry_run:
        confirm = input("WARNING: This is a LIVE run. Type 'yes' to continue: ")
        if confirm.lower() != "yes":
            print("Aborted.")
            sys.exit(0)

    # Run job
    job = CompleteShippingOnlyOrdersJob(ctx)
    result = job.execute(
        order_ids=order_ids,
        limit=args.limit,
        shipping_product_ids=shipping_product_ids,
    )

    # Print results
    print(f"\n{'='*60}")
    print(f"Results")
    print(f"{'='*60}")
    print(f"  Status: {result.status.value}")
    print(f"  Orders checked: {result.kpis.get('orders_checked', 0)}")
    print(f"  Orders completed: {result.kpis.get('orders_completed', 0)}")
    print(f"  Lines completed: {result.kpis.get('lines_completed', 0)}")
    print(f"  Errors: {result.kpis.get('exceptions', 0)}")
    if result.duration_seconds:
        print(f"  Duration: {result.duration_seconds:.2f}s")

    if result.errors:
        print(f"\nErrors:")
        for error in result.errors[:10]:
            print(f"  - {error}")
    print()
