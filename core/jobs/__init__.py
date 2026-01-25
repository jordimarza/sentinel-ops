"""
Sentinel-Ops Jobs Module

Job framework with registry and base classes.
"""

from core.jobs.registry import register_job, get_job, list_jobs, JOB_REGISTRY
from core.jobs.base import BaseJob

# Import jobs to register them
from core.jobs import clean_old_orders  # noqa: F401
from core.jobs import complete_shipping_only_orders  # noqa: F401
from core.jobs import adjust_closed_order_quantities  # noqa: F401
from core.jobs import check_ar_hold_violations  # noqa: F401
from core.jobs import sync_so_picking_dates  # noqa: F401
from core.jobs import sync_po_picking_dates  # noqa: F401
from core.jobs import date_compliance_all  # noqa: F401
from core.jobs import clean_empty_draft_transfers  # noqa: F401

__all__ = [
    "register_job",
    "get_job",
    "list_jobs",
    "JOB_REGISTRY",
    "BaseJob",
]
