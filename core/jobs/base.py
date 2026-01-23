"""
Base Job Class

Foundation for all sentinel-ops jobs.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

from core.clients.odoo import OdooClient, get_odoo_client
from core.clients.bigquery import BigQueryClient, get_bigquery_client
from core.context import RequestContext
from core.result import JobResult
from core.logging.sentinel_logger import SentinelLogger, get_logger
from core.alerts.slack import SlackAlerter, get_alerter

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

            # Write KPIs
            self.bq.write_kpis(result.to_kpi_dict())

            return result

        except Exception as e:
            logger.exception(f"Job {self.name} failed")

            # Log failure
            self.log.job_failed(str(e))

            # Alert on failure
            self.alerter.alert_job_failed(self.ctx, str(e))

            # Create failure result
            result = JobResult.create(self.name, self.dry_run)
            result.errors.append(str(e))
            result.complete()

            # Write KPIs even for failures
            self.bq.write_kpis(result.to_kpi_dict())

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
