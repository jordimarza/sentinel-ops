"""
Intervention Configuration

Dataclass for configuring intervention detection on jobs.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class InterventionConfig:
    """
    Configuration for intervention detection.

    Used by the @intervention_detector decorator to configure how a job
    detects and logs issues to BigQuery (append-only pattern).

    Attributes:
        issue_type: Type of issue (e.g., "qty_mismatch", "stuck_transfer")
        document_type: Odoo model (e.g., "sale.order", "stock.picking")
        enabled: Whether intervention tracking is active
        priority: Default priority for detected issues
        department: Default department attribution
        defaults: Additional default values for issue creation
    """

    issue_type: str
    document_type: str
    enabled: bool = False
    priority: str = "medium"
    department: Optional[str] = None
    defaults: dict = field(default_factory=dict)
