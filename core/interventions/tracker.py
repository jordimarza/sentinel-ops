"""
Intervention Tracker

High-level API for jobs to detect and resolve interventions.
Wraps the InterventionStore with job-specific context.
"""

import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.context import RequestContext
    from core.interventions.config import InterventionConfig
    from core.interventions.store import InterventionStore

logger = logging.getLogger(__name__)


class InterventionTracker:
    """
    High-level API for intervention tracking in jobs.

    Provides a clean interface for jobs to detect issues and log resolutions.
    Automatically applies configuration from @intervention_detector decorator.

    Usage in jobs:
        # Detect an issue (append-only, creates new row)
        self.interventions.detect(
            document_id=order_id,
            title="Qty mismatch found",
            detection_data={"expected": 10, "actual": 5},
        )

        # Log a resolution by AI (append-only, creates row with status=resolved)
        self.interventions.resolve(
            document_id=order_id,
            title="Qty adjusted",
            resolution_type="auto_adjusted",
            resolution_data={"old_qty": 10, "new_qty": 5},
        )
    """

    def __init__(
        self,
        store: "InterventionStore",
        ctx: "RequestContext",
        config: Optional["InterventionConfig"] = None,
        job_name: str = None,
    ):
        """
        Initialize the tracker.

        Args:
            store: InterventionStore instance
            ctx: Request context
            config: Intervention config from @intervention_detector (optional)
            job_name: Name of the job using this tracker
        """
        self._store = store
        self._ctx = ctx
        self._config = config
        self._job_name = job_name

    @property
    def enabled(self) -> bool:
        """Check if intervention tracking is enabled."""
        return self._config is not None and self._config.enabled

    @property
    def config(self) -> Optional["InterventionConfig"]:
        """Get the intervention configuration."""
        return self._config

    def detect(
        self,
        document_id: int,
        title: str,
        document_name: str = None,
        description: str = None,
        detection_data: dict = None,
        financial_data: dict = None,
        priority: str = None,
        department: str = None,
        metadata: dict = None,
    ) -> Optional[str]:
        """
        Detect an issue (append-only pattern).

        Only logs if intervention tracking is enabled via @intervention_detector.
        Each call creates a new row in BigQuery. Status is derived from
        partition presence (today = open, not in today = resolved by human).

        Args:
            document_id: Odoo record ID
            title: Human-readable summary
            document_name: Display name (e.g., "S00123")
            description: Detailed description
            detection_data: Issue-specific details (JSON)
            financial_data: Financial context (JSON)
            priority: Override default priority
            department: Override default department
            metadata: Flexible field for future expansion

        Returns:
            intervention_id if logged, None if disabled or error
        """
        if not self.enabled:
            logger.debug("Intervention tracking disabled, skipping detect()")
            return None

        config = self._config
        return self._store.log_detection(
            ctx=self._ctx,
            document_type=config.document_type,
            document_id=document_id,
            issue_type=config.issue_type,
            title=title,
            priority=priority or config.priority,
            document_name=document_name,
            description=description,
            detection_data=detection_data,
            financial_data=financial_data,
            department=department or config.department,
            metadata=metadata,
        )

    def resolve(
        self,
        document_id: int,
        title: str,
        resolution_type: str,
        document_name: str = None,
        resolution_notes: str = None,
        resolution_data: dict = None,
        detection_data: dict = None,
        metadata: dict = None,
    ) -> Optional[str]:
        """
        Log an AI-driven resolution (append-only pattern).

        Only logs if intervention tracking is enabled via @intervention_detector.
        Creates a row with status='resolved' so analytics can distinguish
        AI resolutions from human resolutions (inferred from partition absence).

        Args:
            document_id: Odoo record ID
            title: Human-readable summary
            resolution_type: How it was resolved (e.g., "auto_adjusted")
            document_name: Display name (e.g., "S00123")
            resolution_notes: Human-readable resolution description
            resolution_data: Resolution details (JSON)
            detection_data: Original issue details (JSON)
            metadata: Flexible field for future expansion

        Returns:
            intervention_id if logged, None if disabled or error
        """
        if not self.enabled:
            logger.debug("Intervention tracking disabled, skipping resolve()")
            return None

        config = self._config
        return self._store.log_resolution(
            ctx=self._ctx,
            document_type=config.document_type,
            document_id=document_id,
            issue_type=config.issue_type,
            title=title,
            resolution_type=resolution_type,
            resolved_by=self._job_name or self._ctx.job_name,
            priority=config.priority,
            document_name=document_name,
            resolution_notes=resolution_notes,
            resolution_data=resolution_data,
            detection_data=detection_data,
            metadata=metadata,
        )

    # =========================================================================
    # Direct Store Access (for advanced use cases)
    # =========================================================================

    def create(
        self,
        document_type: str,
        document_id: int,
        issue_type: str,
        title: str,
        **kwargs,
    ) -> Optional[str]:
        """
        Create an intervention directly (bypasses decorator config).

        Use this when you need to create interventions with different
        document_type or issue_type than configured in the decorator.
        """
        return self._store.create(
            ctx=self._ctx,
            document_type=document_type,
            document_id=document_id,
            issue_type=issue_type,
            title=title,
            **kwargs,
        )

    def create_if_not_exists(
        self,
        document_type: str,
        document_id: int,
        issue_type: str,
        title: str,
        **kwargs,
    ) -> tuple[Optional[str], bool]:
        """
        Create an intervention if one doesn't already exist.

        Returns (intervention_id, created).
        """
        return self._store.create_if_not_exists(
            ctx=self._ctx,
            document_type=document_type,
            document_id=document_id,
            issue_type=issue_type,
            title=title,
            **kwargs,
        )

    @property
    def store(self) -> "InterventionStore":
        """
        Get direct access to the store for advanced queries.

        Example:
            stats = self.interventions.store.get_stats(department="operations")
        """
        return self._store


class NoOpInterventionTracker(InterventionTracker):
    """
    No-op tracker for when intervention tracking is disabled.

    All operations are no-ops that return None.
    """

    def __init__(self):
        self._store = None
        self._ctx = None
        self._config = None
        self._job_name = None

    @property
    def enabled(self) -> bool:
        return False

    def detect(self, document_id, title, **kwargs) -> None:
        return None

    def resolve(self, document_id, title, resolution_type, **kwargs) -> None:
        return None

    def create(self, document_type, document_id, issue_type, title, **kwargs) -> None:
        return None

    def create_if_not_exists(self, document_type, document_id, issue_type, title, **kwargs) -> tuple[None, bool]:
        return None, False

    @property
    def store(self):
        return None
