"""
Date Compliance All Job

Wrapper job that runs all date compliance checks in sequence,
passing order_ids from Job 1 to Job 2 explicitly.
"""

import logging
from typing import Optional

from core.jobs.registry import register_job, get_job
from core.jobs.base import BaseJob
from core.result import JobResult

logger = logging.getLogger(__name__)


@register_job(
    name="date_compliance_all",
    description="Run all date compliance checks in sequence",
    tags=["compliance", "dates", "wrapper"],
)
class DateComplianceAllJob(BaseJob):
    """
    Run all date compliance jobs in sequence.

    Orchestration:
    1. check_ar_hold_violations → returns processed_order_ids
    2. sync_so_picking_dates (with explicit IDs from Job 1 + optional BQ query)
    3. sync_po_picking_dates

    This ensures:
    - AR-HOLD violations are processed first
    - Order IDs from Job 1 are immediately passed to Job 2 (avoids BQ lag)
    - Job 2 can also find additional mismatches via BQ query
    - PO date sync runs independently
    """

    def run(
        self,
        order_ids: Optional[list[int]] = None,
        po_ids: Optional[list[int]] = None,
        limit: Optional[int] = None,
        extension_days: int = 15,
        include_bq_query: bool = True,
        sync_line_level: bool = False,
        skip_ar_hold: bool = False,
        skip_so_sync: bool = False,
        skip_po_sync: bool = False,
        **_params
    ) -> JobResult:
        """
        Execute all date compliance jobs in sequence.

        Args:
            order_ids: Order IDs for AR-HOLD check (Job 1) and SO sync (Job 2)
            po_ids: Purchase order IDs for PO sync (Job 3)
            limit: Maximum records per job
            extension_days: Days to extend commitment_date in Job 1
            include_bq_query: Include BQ query in Job 2 for additional mismatches
            sync_line_level: Enable line-level sync in Job 3
            skip_ar_hold: Skip Job 1 (AR-HOLD violations)
            skip_so_sync: Skip Job 2 (SO picking sync)
            skip_po_sync: Skip Job 3 (PO picking sync)

        Returns:
            JobResult with combined execution details
        """
        # Create result with full context for audit trail
        result = JobResult.from_context(self.ctx, parameters={
            "order_ids": order_ids,
            "po_ids": po_ids,
            "limit": limit,
            "extension_days": extension_days,
            "include_bq_query": include_bq_query,
            "sync_line_level": sync_line_level,
            "skip_ar_hold": skip_ar_hold,
            "skip_so_sync": skip_so_sync,
            "skip_po_sync": skip_po_sync,
        })

        # Track IDs to pass between jobs
        ar_hold_order_ids: list[int] = []

        # Job 1: AR-HOLD violations (discovers from BQ if no order_ids)
        if not skip_ar_hold:
            self.log.info("Running Job 1: check_ar_hold_violations")

            try:
                job1_class = get_job("check_ar_hold_violations")
                job1 = job1_class(self.ctx)
                r1 = job1.execute(
                    order_ids=order_ids,
                    limit=limit,
                    extension_days=extension_days,
                )

                # Merge results
                self._merge_result(result, r1, "ar_hold")

                # Get order_ids to pass to Job 2
                ar_hold_order_ids = r1.data.get("processed_order_ids", [])

                self.log.info(
                    f"Job 1 complete: {r1.records_updated} orders processed, "
                    f"{len(ar_hold_order_ids)} IDs to pass to Job 2"
                )

            except Exception as e:
                self.log.error("Job 1 failed", error=str(e))
                result.errors.append(f"Job 1 (check_ar_hold_violations): {e}")
        else:
            self.log.info("Skipping Job 1: check_ar_hold_violations")

        # Job 2: SO picking date sync
        if not skip_so_sync:
            self.log.info("Running Job 2: sync_so_picking_dates")

            try:
                job2_class = get_job("sync_so_picking_dates")
                job2 = job2_class(self.ctx)

                # Combine ar_hold_order_ids with any explicit order_ids not yet processed
                job2_order_ids = list(set(ar_hold_order_ids + (order_ids or [])))

                r2 = job2.execute(
                    order_ids=job2_order_ids if job2_order_ids else None,
                    limit=limit,
                    include_bq_query=include_bq_query,
                )

                # Merge results
                self._merge_result(result, r2, "so_sync")

                self.log.info(
                    f"Job 2 complete: {r2.kpis.get('pickings_updated', 0)} pickings updated"
                )

            except Exception as e:
                self.log.error("Job 2 failed", error=str(e))
                result.errors.append(f"Job 2 (sync_so_picking_dates): {e}")
        else:
            self.log.info("Skipping Job 2: sync_so_picking_dates")

        # Job 3: PO picking date sync (discovers from BQ if no po_ids)
        if not skip_po_sync:
            self.log.info("Running Job 3: sync_po_picking_dates")

            try:
                job3_class = get_job("sync_po_picking_dates")
                job3 = job3_class(self.ctx)
                r3 = job3.execute(
                    po_ids=po_ids or None,
                    limit=limit,
                    sync_line_level=sync_line_level,
                )

                # Merge results
                self._merge_result(result, r3, "po_sync")

                self.log.info(
                    f"Job 3 complete: {r3.kpis.get('pickings_updated', 0)} pickings updated"
                )

            except Exception as e:
                self.log.error("Job 3 failed", error=str(e))
                result.errors.append(f"Job 3 (sync_po_picking_dates): {e}")
        else:
            self.log.info("Skipping Job 3: sync_po_picking_dates")

        # Set combined KPIs
        result.kpis = self._build_combined_kpis(result)

        result.complete()
        return result

    def _merge_result(
        self,
        main_result: JobResult,
        sub_result: JobResult,
        prefix: str,
    ) -> None:
        """
        Merge a sub-job result into the main result.

        Args:
            main_result: The main job result to merge into
            sub_result: The sub-job result to merge from
            prefix: Prefix for storing sub-result data
        """
        main_result.records_checked += sub_result.records_checked
        main_result.records_updated += sub_result.records_updated
        main_result.records_skipped += sub_result.records_skipped
        main_result.errors.extend(sub_result.errors)

        # Store sub-result details
        main_result.data[f"{prefix}_kpis"] = sub_result.kpis
        main_result.data[f"{prefix}_status"] = sub_result.status

    def _build_combined_kpis(self, result: JobResult) -> dict:
        """Build combined KPIs from all sub-jobs."""
        kpis = {
            "total_records_checked": result.records_checked,
            "total_records_updated": result.records_updated,
            "total_exceptions": len(result.errors),
        }

        # Add per-job KPIs if available
        for key in ["ar_hold_kpis", "so_sync_kpis", "po_sync_kpis"]:
            if key in result.data:
                kpis[key] = result.data[key]

        return kpis


if __name__ == "__main__":
    import sys
    print("\n" + "=" * 70)
    print("Date Compliance All (Wrapper Job)")
    print("=" * 70)
    print("\nRuns all date compliance jobs in sequence:")
    print("  1. check_ar_hold_violations -> passes order_ids to Job 2")
    print("  2. sync_so_picking_dates")
    print("  3. sync_po_picking_dates")
    print("\nUsage:")
    print("-" * 70)
    print("\n# Run all 3 jobs")
    print("python main.py run date_compliance_all --dry-run order_ids=123,456 po_ids=789")
    print("\n# Skip AR-HOLD check (run only SO and PO sync)")
    print("python main.py run date_compliance_all --dry-run order_ids=123 skip_ar_hold=True")
    print("\n# Skip PO sync")
    print("python main.py run date_compliance_all --dry-run order_ids=123 skip_po_sync=True")
    print("\n# With line-level PO sync")
    print("python main.py run date_compliance_all --dry-run order_ids=123 po_ids=789 sync_line_level=True")
    print("\n# Custom extension days for AR-HOLD")
    print("python main.py run date_compliance_all --dry-run order_ids=123 extension_days=30")
    print("\n# With limit")
    print("python main.py run date_compliance_all --dry-run order_ids=123,456,789 limit=5")
    print("\n# Live execution")
    print("python main.py run date_compliance_all order_ids=123,456 po_ids=789")
    print("\n" + "-" * 70)
    print("\nIndividual jobs (run separately):")
    print("-" * 70)
    print("python core/jobs/check_ar_hold_violations.py")
    print("python core/jobs/sync_so_picking_dates.py")
    print("python core/jobs/sync_po_picking_dates.py")
    print("\n" + "=" * 70 + "\n")
    sys.exit(0)
