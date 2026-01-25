"""
Interventions Module

Provides issue detection, tracking, and resolution capabilities for jobs.

Usage:
    @register_job(name="my_job", ...)
    @intervention_detector(
        issue_type="qty_mismatch",
        document_type="sale.order",
        enabled=True,
    )
    class MyJob(BaseJob):
        def run(self, **params) -> JobResult:
            # Detect an issue
            self.interventions.detect(
                document_id=order_id,
                title="Qty mismatch found",
                ...
            )

            # Resolve an issue (when AI fixes it)
            self.interventions.resolve(
                document_id=order_id,
                title="Qty adjusted",
                resolution_type="auto_adjusted",
                ...
            )
"""

from core.interventions.config import InterventionConfig
from core.interventions.decorator import intervention_detector
from core.interventions.tracker import InterventionTracker
from core.interventions.store import InterventionStore

__all__ = [
    "InterventionConfig",
    "intervention_detector",
    "InterventionTracker",
    "InterventionStore",
]
