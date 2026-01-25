"""
Intervention Detector Decorator

Decorator for configuring intervention detection on job classes.
"""

import logging
from typing import Optional, Type, TYPE_CHECKING

from core.interventions.config import InterventionConfig

if TYPE_CHECKING:
    from core.jobs.base import BaseJob

logger = logging.getLogger(__name__)


def intervention_detector(
    issue_type: str,
    document_type: str,
    enabled: bool = False,
    priority: str = "medium",
    department: Optional[str] = None,
    **defaults,
):
    """
    Decorator to configure intervention detection for a job.

    When enabled, provides helper methods for detecting issues and
    logging resolutions to BigQuery using the append-only pattern.

    Usage:
        @register_job(name="my_job", ...)
        @intervention_detector(
            issue_type="qty_mismatch",
            document_type="sale.order",
            priority="medium",
            department="operations",
            enabled=True,
        )
        class MyJob(BaseJob):
            def run(self, **params):
                # When enabled, use:
                # self.interventions.detect(order_id, "Issue found", ...)
                # self.interventions.resolve(order_id, "Issue fixed", ...)
                pass

    Args:
        issue_type: Type of issue (e.g., "qty_mismatch", "stuck_transfer")
        document_type: Odoo model (e.g., "sale.order", "stock.picking")
        enabled: Whether intervention tracking is active (default: False)
        priority: Default priority (low, medium, high, critical)
        department: Default department attribution
        **defaults: Additional default values for issue creation

    Returns:
        Decorator function
    """

    def decorator(cls: Type["BaseJob"]) -> Type["BaseJob"]:
        # Store config on the class
        cls._intervention_config = InterventionConfig(
            issue_type=issue_type,
            document_type=document_type,
            enabled=enabled,
            priority=priority,
            department=department,
            defaults=defaults,
        )

        logger.debug(
            f"Intervention detector configured for {cls.__name__}: "
            f"{issue_type}/{document_type} (enabled={enabled})"
        )

        return cls

    return decorator
