"""
Base Job Class

Foundation for all sentinel-ops jobs.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional, TYPE_CHECKING

from core.clients.odoo import OdooClient, get_odoo_client
from core.clients.bigquery import BigQueryClient, get_bigquery_client
from core.config import get_settings
from core.context import RequestContext
from core.result import JobResult
from core.logging.sentinel_logger import SentinelLogger, get_logger
from core.alerts.slack import SlackAlerter, get_alerter

if TYPE_CHECKING:
    from core.interventions.config import InterventionConfig
    from core.interventions.tracker import InterventionTracker

logger = logging.getLogger(__name__)


class BaseJob(ABC):
    """
    Base class for all jobs.

    Provides:
    - Standardized execution flow (setup -> run -> teardown)
    - Automatic audit logging
    - Error handling and alerting
    - Dry-run support

    Usage:
        @register_job(name="my_job", description="Does something useful")
        class MyJob(BaseJob):
            def run(self) -> JobResult:
                result = JobResult.create(self.name, self.ctx.dry_run)
                # ... do work ...
                result.complete()
                return result
    """

    # Set by @register_job decorator
    _job_name: str = ""
    _job_description: str = ""
    _job_tags: list[str] = []
    _notify_on_success: bool = True  # Send Slack alert on success

    # Set by @intervention_detector decorator
    _intervention_config: Optional["InterventionConfig"] = None

    def __init__(
        self,
        ctx: RequestContext,
        odoo: Optional[OdooClient] = None,
        bq: Optional[BigQueryClient] = None,
        alerter: Optional[SlackAlerter] = None,
        log: Optional[SentinelLogger] = None,
    ):
        """
        Initialize job with dependencies.

        Args:
            ctx: Request context (required)
            odoo: Odoo client (auto-created if not provided)
            bq: BigQuery client (auto-created if not provided)
            alerter: Slack alerter (auto-created if not provided)
            log: Logger (auto-created if not provided)
        """
        self.ctx = ctx

        # Lazy-load clients if not provided
        self._odoo = odoo
        self._bq = bq
        self._alerter = alerter
        self._log = log
        self._interventions: Optional["InterventionTracker"] = None

    @property
    def name(self) -> str:
        """Get job name."""
        return self._job_name or self.__class__.__name__

    @property
    def description(self) -> str:
        """Get job description."""
        return self._job_description

    @property
    def dry_run(self) -> bool:
        """Check if this is a dry-run."""
        return self.ctx.dry_run

    @property
    def odoo(self) -> OdooClient:
        """Get Odoo client (lazy-loaded)."""
        if self._odoo is None:
            self._odoo = get_odoo_client()
        return self._odoo

    @property
    def bq(self) -> BigQueryClient:
        """Get BigQuery client (lazy-loaded)."""
        if self._bq is None:
            self._bq = get_bigquery_client()
        return self._bq

    @property
    def alerter(self) -> SlackAlerter:
        """Get Slack alerter (lazy-loaded)."""
        if self._alerter is None:
            self._alerter = get_alerter()
        return self._alerter

    @property
    def log(self) -> SentinelLogger:
        """Get logger (lazy-loaded)."""
        if self._log is None:
            self._log = get_logger(self.ctx, self._bq)
        return self._log

    @property
    def interventions(self) -> "InterventionTracker":
        """
        Get intervention tracker (lazy-loaded).

        Provides API for detecting issues and logging resolutions.
        Enabled via @intervention_detector decorator on the job class.

        Usage:
            self.interventions.detect(order_id, "Issue found", ...)
            self.interventions.resolve(order_id, "Issue fixed", ...)
        """
        if self._interventions is None:
            from core.interventions.store import InterventionStore, NoOpInterventionStore
            from core.interventions.tracker import InterventionTracker, NoOpInterventionTracker

            config = getattr(self.__class__, "_intervention_config", None)

            # If no config or not enabled, use NoOp tracker
            if config is None or not config.enabled:
                self._interventions = NoOpInterventionTracker()
            else:
                # Create store from BQ client
                if hasattr(self.bq, '_get_client') and self.bq._get_client() is not None:
                    store = InterventionStore(self.bq)
                else:
                    store = NoOpInterventionStore()

                self._interventions = InterventionTracker(
                    store=store,
                    ctx=self.ctx,
                    config=config,
                    job_name=self.name,
                )

        return self._interventions

    def execute(self, **params) -> JobResult:
        """
        Execute the job with full lifecycle management.

        This method handles:
        - Logging job start
        - Running the job
        - Logging completion/failure
        - Sending alerts on failure
        - Writing KPIs

        Args:
            **params: Additional parameters for the job

        Returns:
            JobResult
        """
        self.log.job_started(data={"params": params, "dry_run": self.dry_run})

        try:
            # Setup phase
            self.setup(**params)

            # Run phase
            result = self.run(**params)

            # Ensure result is complete
            if result.completed_at is None:
                result.complete()

            # Teardown phase
            self.teardown(result)

            # Log completion
            self.log.job_completed(data=result.to_dict())

            # Alert on success (if enabled for this job)
            if self._notify_on_success:
                self.alerter.alert_job_completed(self.ctx, result)

            # Write KPIs with Odoo URL for record links
            odoo_url = get_settings().odoo_url
            self.bq.write_kpis(result.to_kpi_dict(odoo_url=odoo_url))

            return result

        except Exception as e:
            logger.exception(f"Job {self.name} failed")

            # Log failure
            self.log.job_failed(str(e))

            # Alert on failure
            self.alerter.alert_job_failed(self.ctx, str(e))

            # Create failure result with context for audit trail
            result = JobResult.from_context(self.ctx, parameters=params)
            result.errors.append(str(e))
            result.complete()

            # Write KPIs even for failures
            odoo_url = get_settings().odoo_url
            self.bq.write_kpis(result.to_kpi_dict(odoo_url=odoo_url))

            raise

    def setup(self, **params) -> None:
        """
        Setup phase before running the job.

        Override to perform any necessary setup.
        Default implementation does nothing.

        Args:
            **params: Job parameters
        """
        pass

    @abstractmethod
    def run(self, **params) -> JobResult:
        """
        Main job execution logic.

        Must be implemented by subclasses.

        Args:
            **params: Job parameters

        Returns:
            JobResult with execution details
        """
        raise NotImplementedError

    def teardown(self, result: JobResult) -> None:
        """
        Teardown phase after running the job.

        Override to perform any necessary cleanup.
        Default implementation does nothing.

        Args:
            result: Job result from run phase
        """
        pass

    def create_intervention(
        self,
        document_type: str,
        document_id: int,
        issue_type: str,
        title: str,
        priority: str = "medium",
        **kwargs,
    ) -> Optional[str]:
        """
        Create an intervention with deduplication.

        Use this when the job detects an issue that requires human or AI
        intervention that cannot be auto-resolved.

        For append-only pattern (recommended), use self.interventions.detect() instead.

        Args:
            document_type: Odoo model (e.g., "sale.order")
            document_id: Odoo record ID
            issue_type: Type of issue (e.g., "qty_mismatch")
            title: Human-readable summary
            priority: Priority (low, medium, high, critical)
            **kwargs: Additional fields (description, department, etc.)

        Returns:
            intervention_id if created (or existing), None on error
        """
        intervention_id, created = self.interventions.create_if_not_exists(
            document_type=document_type,
            document_id=document_id,
            issue_type=issue_type,
            title=title,
            priority=priority,
            **kwargs,
        )

        if created:
            self.log.info(f"Created intervention: {title}", data={
                "intervention_id": intervention_id,
                "document_type": document_type,
                "document_id": document_id,
                "issue_type": issue_type,
            })
        elif intervention_id:
            self.log.debug(f"Intervention already exists: {intervention_id}")

        return intervention_id

    @classmethod
    def create_and_execute(
        cls,
        ctx: RequestContext,
        **params
    ) -> JobResult:
        """
        Convenience method to create a job instance and execute it.

        Args:
            ctx: Request context
            **params: Job parameters

        Returns:
            JobResult
        """
        job = cls(ctx)
        return job.execute(**params)
