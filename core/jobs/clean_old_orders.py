"""
Clean Old Orders Job

Migrated from workflows/sales_order_cleanup.py

Finds partial sale orders older than X days and adjusts line quantities
to match delivered quantities (cleaning up stuck orders).
"""

import logging
from typing import Optional

from core.jobs.registry import register_job
from core.jobs.base import BaseJob
from core.operations.orders import OrderOperations
from core.operations.transfers import TransferOperations
from core.result import JobResult

logger = logging.getLogger(__name__)


@register_job(
    name="clean_old_orders",
    description="Clean up old partial orders by adjusting quantities to delivered amounts",
    tags=["sales", "cleanup", "orders"],
)
class CleanOldOrdersJob(BaseJob):
    """
    Clean up old partial sale orders.

    Pattern: detect -> check -> remediate -> log

    Workflow:
    1. Find partial sale order lines older than X days
    2. For each line, check if there are open stock moves
    3. If no open moves, adjust quantity to delivered amount
    4. If adjustment fails, tag order as exception

    Original pattern from workflows/sales_order_cleanup.py preserved:
    - Results include: lines_checked, lines_updated, exceptions
    """

    def run(
        self,
        days: int = 30,
        limit: Optional[int] = None,
        **params
    ) -> JobResult:
        """
        Execute the clean old orders job.

        Args:
            days: Number of days to look back (default: 30)
            limit: Maximum number of lines to process

        Returns:
            JobResult with execution details
        """
        result = JobResult.create(self.name, self.dry_run)

        # Initialize operations
        order_ops = OrderOperations(self.odoo, self.ctx, self.log)
        transfer_ops = TransferOperations(self.odoo, self.ctx, self.log)

        # Step 1: Find partial order lines
        self.log.info(
            f"Finding partial order lines older than {days} days",
            data={"days": days, "limit": limit},
        )

        try:
            lines = order_ops.find_partial_orders_older_than(days=days, limit=limit)
        except Exception as e:
            self.log.error("Failed to find partial orders", error=str(e))
            result.errors.append(f"Search failed: {e}")
            result.complete()
            return result

        if not lines:
            self.log.info("No partial order lines found")
            # Set KPIs even when no records found
            result.kpis = {
                "lines_checked": 0,
                "lines_updated": 0,
                "exceptions": 0,
            }
            result.complete()
            return result

        self.log.info(f"Found {len(lines)} partial order lines to process")

        # Step 2 & 3: Process each line
        for line in lines:
            line_id = line["id"]
            order_id = line["order_id"][0] if isinstance(line["order_id"], (list, tuple)) else line["order_id"]

            try:
                # Check for open stock moves
                has_open = transfer_ops.has_open_moves(line_id)

                if has_open:
                    # Skip lines with open moves
                    self.log.skip(line_id, "Has open stock moves")
                    result.records_skipped += 1
                    result.records_checked += 1
                    continue

                # Adjust quantity to delivered
                op_result = order_ops.adjust_line_qty_to_delivered(line)
                result.add_operation(op_result)

                if op_result.success:
                    self.log.success(line_id, "Adjusted to delivered qty")
                else:
                    # Tag order as exception on failure
                    self.log.warning(
                        f"Adjustment failed for line {line_id}, tagging order",
                    )
                    order_ops.tag_order_exception(
                        order_id,
                        f"Failed to adjust line {line_id}: {op_result.error}",
                    )

            except Exception as e:
                self.log.error(
                    f"Exception processing line {line_id}",
                    record_id=line_id,
                    error=str(e),
                )
                result.errors.append(f"Line {line_id}: {e}")
                result.records_checked += 1

                # Tag order as exception
                try:
                    order_ops.tag_order_exception(order_id, str(e))
                except Exception as tag_error:
                    self.log.error(
                        f"Failed to tag exception on order {order_id}",
                        error=str(tag_error),
                    )

        # Add custom KPIs (preserving original format)
        result.kpis = {
            "lines_checked": result.records_checked,
            "lines_updated": result.records_updated,
            "exceptions": len(result.errors),
        }

        result.complete()
        return result


# --- Direct execution for testing ---

if __name__ == "__main__":
    """
    Run this job directly for testing.

    Usage:
        python -m core.jobs.clean_old_orders --dry-run
        python -m core.jobs.clean_old_orders --dry-run --days 60
        python -m core.jobs.clean_old_orders --dry-run --limit 10
        python -m core.jobs.clean_old_orders --days 60 --limit 5  # Live!
    """
    import argparse
    import sys
    import os

    # Add project root to path
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

    from core.context import RequestContext

    parser = argparse.ArgumentParser(
        description="Clean Old Orders - Adjust partial order line quantities to delivered amounts",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in dry-run mode (no mutations)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to look back (default: 30)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of lines to process",
    )

    args = parser.parse_args()

    # Create context
    ctx = RequestContext.for_cli(
        job_name="clean_old_orders",
        dry_run=args.dry_run,
    )

    print(f"\n{'='*60}")
    print(f"Clean Old Orders")
    print(f"{'='*60}")
    print(f"  Mode: {'DRY-RUN' if args.dry_run else 'LIVE'}")
    print(f"  Days: {args.days}")
    print(f"  Limit: {args.limit or 'none'}")
    print(f"  Request ID: {ctx.request_id}")
    print(f"{'='*60}\n")

    if not args.dry_run:
        confirm = input("WARNING: This is a LIVE run. Type 'yes' to continue: ")
        if confirm.lower() != "yes":
            print("Aborted.")
            sys.exit(0)

    # Run job
    job = CleanOldOrdersJob(ctx)
    result = job.execute(days=args.days, limit=args.limit)

    # Print results
    print(f"\n{'='*60}")
    print(f"Results")
    print(f"{'='*60}")
    print(f"  Status: {result.status.value}")
    print(f"  Lines checked: {result.kpis.get('lines_checked', 0)}")
    print(f"  Lines updated: {result.kpis.get('lines_updated', 0)}")
    print(f"  Errors: {result.kpis.get('exceptions', 0)}")
    if result.duration_seconds:
        print(f"  Duration: {result.duration_seconds:.2f}s")

    if result.errors:
        print(f"\nErrors:")
        for error in result.errors[:10]:
            print(f"  - {error}")
    print()
