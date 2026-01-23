"""
Sentinel Logger with BigQuery Audit Trail

Provides structured logging that writes to both console and BigQuery.
"""

import logging
from typing import Any, Optional

from core.context import RequestContext
from core.clients.bigquery import BigQueryClient, get_bigquery_client


class SentinelLogger:
    """
    Structured logger with BigQuery audit trail.

    Usage:
        logger = SentinelLogger(ctx, bq_client)
        logger.info("Processing started", data={"count": 100})
        logger.success(record_id=123, message="Updated order line")
        logger.error(record_id=456, message="Failed to update", error=str(e))
    """

    def __init__(
        self,
        ctx: RequestContext,
        bq_client: Optional[BigQueryClient] = None,
        name: str = "sentinel-ops",
    ):
        self.ctx = ctx
        self.bq_client = bq_client
        self._logger = logging.getLogger(name)

        # Ensure we have a handler
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            ))
            self._logger.addHandler(handler)
            self._logger.setLevel(logging.INFO)

    def _log(
        self,
        level: int,
        message: str,
        event_type: str = "log",
        record_id: Optional[int] = None,
        data: Optional[dict] = None,
        audit: bool = False,
    ) -> None:
        """Internal log method with optional BQ audit."""
        # Build log message
        prefix = f"[{self.ctx.request_id[:8]}]"
        if record_id:
            prefix += f" [id={record_id}]"
        if self.ctx.dry_run:
            prefix += " [DRY-RUN]"

        full_message = f"{prefix} {message}"
        self._logger.log(level, full_message)

        # Write to BigQuery if auditing
        if audit and self.bq_client:
            audit_data = {"message": message}
            if record_id:
                audit_data["record_id"] = record_id
            if data:
                audit_data.update(data)
            self.bq_client.log_audit(self.ctx, event_type, audit_data)

    def debug(self, message: str, **kwargs) -> None:
        """Log debug message."""
        self._log(logging.DEBUG, message, **kwargs)

    def info(self, message: str, audit: bool = False, **kwargs) -> None:
        """Log info message."""
        self._log(logging.INFO, message, event_type="info", audit=audit, **kwargs)

    def warning(self, message: str, audit: bool = True, **kwargs) -> None:
        """Log warning message (audited by default)."""
        self._log(logging.WARNING, message, event_type="warning", audit=audit, **kwargs)

    def error(
        self,
        message: str,
        record_id: Optional[int] = None,
        error: Optional[str] = None,
        audit: bool = True,
        **kwargs
    ) -> None:
        """Log error message (audited by default)."""
        if error:
            message = f"{message}: {error}"
        data = kwargs.get("data", {})
        if error:
            data["error"] = error
        self._log(
            logging.ERROR,
            message,
            event_type="error",
            record_id=record_id,
            data=data,
            audit=audit,
        )

    def success(
        self,
        record_id: int,
        message: str,
        audit: bool = True,
        **kwargs
    ) -> None:
        """Log successful operation."""
        self._log(
            logging.INFO,
            f"SUCCESS: {message}",
            event_type="success",
            record_id=record_id,
            audit=audit,
            **kwargs
        )

    def skip(
        self,
        record_id: int,
        reason: str,
        audit: bool = False,
        **kwargs
    ) -> None:
        """Log skipped operation."""
        self._log(
            logging.INFO,
            f"SKIPPED: {reason}",
            event_type="skip",
            record_id=record_id,
            audit=audit,
            **kwargs
        )

    def job_started(self, data: Optional[dict] = None) -> None:
        """Log job start (always audited)."""
        self._log(
            logging.INFO,
            f"Job started: {self.ctx.job_name}",
            event_type="job_started",
            data=data,
            audit=True,
        )

    def job_completed(self, data: Optional[dict] = None) -> None:
        """Log job completion (always audited)."""
        self._log(
            logging.INFO,
            f"Job completed: {self.ctx.job_name}",
            event_type="job_completed",
            data=data,
            audit=True,
        )

    def job_failed(self, error: str, data: Optional[dict] = None) -> None:
        """Log job failure (always audited)."""
        failure_data = {"error": error}
        if data:
            failure_data.update(data)
        self._log(
            logging.ERROR,
            f"Job failed: {self.ctx.job_name} - {error}",
            event_type="job_failed",
            data=failure_data,
            audit=True,
        )


def get_logger(
    ctx: RequestContext,
    bq_client: Optional[BigQueryClient] = None,
) -> SentinelLogger:
    """
    Create a SentinelLogger for the given context.

    Args:
        ctx: Request context
        bq_client: Optional BigQuery client (auto-created if not provided)

    Returns:
        Configured SentinelLogger
    """
    if bq_client is None:
        try:
            bq_client = get_bigquery_client()
        except Exception:
            bq_client = None

    return SentinelLogger(ctx, bq_client)
