"""
Result types for operations and jobs.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class ResultStatus(str, Enum):
    """Status of an operation or job result."""
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class OperationResult:
    """
    Result of a single operation (e.g., updating an order line).

    Attributes:
        success: Whether the operation succeeded
        record_id: ID of the record operated on
        model: Odoo model name
        action: What action was taken
        message: Human-readable result message
        data: Optional additional data
        error: Error message if failed
    """
    success: bool
    record_id: Optional[int] = None
    model: str = ""
    action: str = ""
    message: str = ""
    data: Optional[dict] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dict for logging/serialization."""
        return {
            "success": self.success,
            "record_id": self.record_id,
            "model": self.model,
            "action": self.action,
            "message": self.message,
            "data": self.data,
            "error": self.error,
        }

    @classmethod
    def ok(
        cls,
        record_id: int,
        model: str = "",
        action: str = "",
        message: str = "",
        data: Optional[dict] = None
    ) -> "OperationResult":
        """Create a successful result."""
        return cls(
            success=True,
            record_id=record_id,
            model=model,
            action=action,
            message=message,
            data=data,
        )

    @classmethod
    def fail(
        cls,
        record_id: Optional[int] = None,
        model: str = "",
        action: str = "",
        error: str = "",
        data: Optional[dict] = None
    ) -> "OperationResult":
        """Create a failed result."""
        return cls(
            success=False,
            record_id=record_id,
            model=model,
            action=action,
            error=error,
            data=data,
        )

    @classmethod
    def skipped(
        cls,
        record_id: int,
        model: str = "",
        reason: str = ""
    ) -> "OperationResult":
        """Create a skipped result."""
        return cls(
            success=True,
            record_id=record_id,
            model=model,
            action="skipped",
            message=reason,
        )


@dataclass
class JobResult:
    """
    Result of a job execution.

    Attributes:
        status: Overall job status
        job_name: Name of the job
        started_at: When the job started
        completed_at: When the job completed
        records_checked: Number of records examined
        records_updated: Number of records modified
        records_skipped: Number of records skipped
        errors: List of error messages
        operations: List of individual operation results
        kpis: Additional KPI data for tracking
        dry_run: Whether this was a dry run
    """
    status: ResultStatus
    job_name: str = ""
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    records_checked: int = 0
    records_updated: int = 0
    records_skipped: int = 0
    errors: list[str] = field(default_factory=list)
    operations: list[OperationResult] = field(default_factory=list)
    kpis: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False

    def add_operation(self, result: OperationResult) -> None:
        """Add an operation result and update counters."""
        self.operations.append(result)
        # Note: records_checked should be incremented by the job, not here
        # since one record may have multiple operations

        if result.success:
            if result.action == "skipped":
                self.records_skipped += 1
            else:
                self.records_updated += 1
        else:
            if result.error:
                self.errors.append(result.error)

    def complete(self) -> None:
        """Mark the job as complete and determine final status."""
        self.completed_at = datetime.utcnow()

        if self.dry_run:
            self.status = ResultStatus.DRY_RUN
        elif self.errors:
            if self.records_updated > 0:
                self.status = ResultStatus.PARTIAL
            else:
                self.status = ResultStatus.FAILURE
        elif self.records_checked == 0:
            self.status = ResultStatus.SKIPPED
        else:
            self.status = ResultStatus.SUCCESS

    @property
    def duration_seconds(self) -> Optional[float]:
        """Get job duration in seconds."""
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def to_dict(self) -> dict:
        """Convert to dict for logging/serialization."""
        return {
            "status": self.status.value,
            "job_name": self.job_name,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "records_checked": self.records_checked,
            "records_updated": self.records_updated,
            "records_skipped": self.records_skipped,
            "error_count": len(self.errors),
            "errors": self.errors[:10],  # Limit errors in output
            "dry_run": self.dry_run,
            "kpis": self.kpis,
        }

    def to_kpi_dict(self) -> dict:
        """Convert to dict for KPI tracking (BigQuery)."""
        return {
            "job_name": self.job_name,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "records_checked": self.records_checked,
            "records_updated": self.records_updated,
            "records_skipped": self.records_skipped,
            "error_count": len(self.errors),
            "dry_run": self.dry_run,
            **self.kpis,
        }

    @classmethod
    def create(cls, job_name: str, dry_run: bool = False) -> "JobResult":
        """Create a new job result."""
        return cls(
            status=ResultStatus.SUCCESS,  # Will be updated on complete()
            job_name=job_name,
            dry_run=dry_run,
        )
