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
        record_name: Display name of the record (e.g., "S00455346")
    """
    success: bool
    record_id: Optional[int] = None
    model: str = ""
    action: str = ""
    message: str = ""
    data: Optional[dict] = None
    error: Optional[str] = None
    record_name: Optional[str] = None

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
            "record_name": self.record_name,
        }

    def to_kpi_record(self, odoo_url: str = "") -> dict:
        """
        Convert to a record summary for KPI tracking.

        Args:
            odoo_url: Base Odoo URL for generating record links

        Returns:
            Dict with record details including Odoo URL
        """
        record = {
            "record_id": self.record_id,
            "record_name": self.record_name,
            "model": self.model,
            "action": self.action,
            "success": self.success,
        }

        # Generate Odoo URL if base URL provided
        if odoo_url and self.record_id and self.model:
            base = odoo_url.rstrip("/")
            record["odoo_url"] = f"{base}/web#id={self.record_id}&model={self.model}&view_type=form"

        if self.error:
            record["error"] = self.error

        return record

    @classmethod
    def ok(
        cls,
        record_id: int,
        model: str = "",
        action: str = "",
        message: str = "",
        data: Optional[dict] = None,
        record_name: Optional[str] = None,
    ) -> "OperationResult":
        """Create a successful result."""
        return cls(
            success=True,
            record_id=record_id,
            model=model,
            action=action,
            message=message,
            data=data,
            record_name=record_name,
        )

    @classmethod
    def fail(
        cls,
        record_id: Optional[int] = None,
        model: str = "",
        action: str = "",
        error: str = "",
        data: Optional[dict] = None,
        record_name: Optional[str] = None,
    ) -> "OperationResult":
        """Create a failed result."""
        return cls(
            success=False,
            record_id=record_id,
            model=model,
            action=action,
            error=error,
            data=data,
            record_name=record_name,
        )

    @classmethod
    def skipped(
        cls,
        record_id: int,
        model: str = "",
        reason: str = "",
        record_name: Optional[str] = None,
    ) -> "OperationResult":
        """Create a skipped result."""
        return cls(
            success=True,
            record_id=record_id,
            model=model,
            action="skipped",
            message=reason,
            record_name=record_name,
        )


class JobType(str, Enum):
    """Type of job for categorization."""
    MODIFICATION = "modification"  # Modifies Odoo records
    VALIDATION = "validation"      # Validates data, returns pass/fail
    QUERY = "query"                # Queries data, returns results
    SYNC = "sync"                  # Syncs data between systems
    HEALTH_CHECK = "health_check"  # Monitors system health
    METRIC = "metric"              # Collects and returns metrics


@dataclass
class JobResult:
    """
    Result of a job execution.

    Supports different job types:
    - Modification: records_checked/updated/skipped, modified_records
    - Validation: tests_passed/failed, result_data with test details
    - Query: result_data with query results
    - Metric: result_data with metric values

    Attributes:
        status: Overall job status
        job_name: Name of the job
        job_type: Type of job (modification, validation, query, etc.)
        started_at: When the job started
        completed_at: When the job completed
        records_checked: Number of records examined (modification jobs)
        records_updated: Number of records modified (modification jobs)
        records_skipped: Number of records skipped (modification jobs)
        tests_passed: Number of tests passed (validation jobs)
        tests_failed: Number of tests failed (validation jobs)
        errors: List of error messages
        operations: List of individual operation results
        result_data: Flexible result data (query results, metrics, etc.)
        kpis: Additional KPI data for tracking
        dry_run: Whether this was a dry run
        request_id: Unique identifier for this request
        triggered_by: Source of the trigger (cli, http, scheduler, n8n, mcp)
        environment: Runtime environment (development, staging, production)
        parameters: Job parameters used for this execution
    """
    status: ResultStatus
    job_name: str = ""
    job_type: JobType = JobType.MODIFICATION
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    # For modification jobs
    records_checked: int = 0
    records_updated: int = 0
    records_skipped: int = 0
    # For validation jobs
    tests_passed: int = 0
    tests_failed: int = 0
    # Common fields
    errors: list[str] = field(default_factory=list)
    operations: list[OperationResult] = field(default_factory=list)
    result_data: Optional[dict] = None  # Query results, metrics, validation details
    data: dict[str, Any] = field(default_factory=dict)  # Arbitrary data for passing between jobs
    kpis: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False
    # Context info for audit trail
    request_id: str = ""
    triggered_by: str = ""
    environment: str = ""
    parameters: Optional[dict] = None

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
            # Has errors - check if partial success
            if self.job_type == JobType.VALIDATION:
                if self.tests_passed > 0:
                    self.status = ResultStatus.PARTIAL
                else:
                    self.status = ResultStatus.FAILURE
            elif self.records_updated > 0:
                self.status = ResultStatus.PARTIAL
            else:
                self.status = ResultStatus.FAILURE
        elif self.job_type == JobType.VALIDATION:
            # Validation job - success if no failures
            if self.tests_failed > 0:
                self.status = ResultStatus.PARTIAL if self.tests_passed > 0 else ResultStatus.FAILURE
            elif self.tests_passed == 0:
                self.status = ResultStatus.SKIPPED
            else:
                self.status = ResultStatus.SUCCESS
        elif self.records_checked == 0 and self.result_data is None:
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
        result = {
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
        # Include result_data if present (validation errors, created docs, etc.)
        if self.result_data:
            result["result_data"] = self.result_data
        return result

    def to_kpi_dict(self, odoo_url: str = "") -> dict:
        """
        Convert to dict for KPI tracking (BigQuery).

        Args:
            odoo_url: Base Odoo URL for generating record links

        Returns:
            Dict with job KPIs including detailed record modifications
        """
        result = {
            # Core identifiers
            "request_id": self.request_id,
            "job_name": self.job_name,

            # Job classification
            "job_type": self.job_type.value,
            "status": self.status.value,

            # Execution context
            "triggered_by": self.triggered_by,
            "environment": self.environment,
            "dry_run": self.dry_run,

            # Timing
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,

            # Counters (modification jobs)
            "records_checked": self.records_checked,
            "records_updated": self.records_updated,
            "records_skipped": self.records_skipped,
            "error_count": len(self.errors),

            # Counters (validation jobs)
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,

            # Details
            "parameters": self.parameters,
            "result_data": self.result_data,  # Query results, metrics, validation details
            "errors": self.errors[:50] if self.errors else None,  # Limit errors

            # Spread any custom KPIs
            **self.kpis,
        }

        # Add detailed record modifications (for modification jobs)
        if self.operations:
            modified_records = [
                op.to_kpi_record(odoo_url)
                for op in self.operations
                if op.action != "skipped"  # Don't include skipped in detail
            ]
            if modified_records:
                result["modified_records"] = modified_records

            # Add a summary by action type
            action_summary = {}
            for op in self.operations:
                key = f"{op.action}_{op.model}" if op.model else op.action
                if key not in action_summary:
                    action_summary[key] = {"count": 0, "success": 0, "failed": 0}
                action_summary[key]["count"] += 1
                if op.success:
                    action_summary[key]["success"] += 1
                else:
                    action_summary[key]["failed"] += 1
            result["action_summary"] = action_summary

        return result

    @classmethod
    def create(
        cls,
        job_name: str,
        dry_run: bool = False,
        job_type: JobType = JobType.MODIFICATION,
        request_id: str = "",
        triggered_by: str = "",
        environment: str = "",
        parameters: Optional[dict] = None,
    ) -> "JobResult":
        """
        Create a new job result.

        Args:
            job_name: Name of the job
            dry_run: Whether this is a dry run
            job_type: Type of job (modification, validation, query, etc.)
            request_id: Unique request identifier
            triggered_by: Source of the trigger
            environment: Runtime environment
            parameters: Job parameters
        """
        return cls(
            status=ResultStatus.SUCCESS,  # Will be updated on complete()
            job_name=job_name,
            job_type=job_type,
            dry_run=dry_run,
            request_id=request_id,
            triggered_by=triggered_by,
            environment=environment,
            parameters=parameters,
        )

    @classmethod
    def from_context(
        cls,
        ctx: "RequestContext",
        parameters: Optional[dict] = None,
        job_type: JobType = JobType.MODIFICATION,
    ) -> "JobResult":
        """
        Create a new job result from a RequestContext.

        Args:
            ctx: Request context with execution info
            parameters: Job parameters
            job_type: Type of job (modification, validation, query, etc.)
        """
        # Import here to avoid circular import
        from core.context import RequestContext

        return cls(
            status=ResultStatus.SUCCESS,
            job_name=ctx.job_name,
            job_type=job_type,
            dry_run=ctx.dry_run,
            request_id=ctx.request_id,
            triggered_by=ctx.triggered_by,
            environment=ctx.environment,
            parameters=parameters or ctx.parameters,
        )

    # --- Helper methods for different job types ---

    def add_test_result(self, passed: bool, name: str = "", details: Optional[dict] = None) -> None:
        """
        Add a test result (for validation jobs).

        Args:
            passed: Whether the test passed
            name: Name of the test
            details: Additional test details
        """
        if passed:
            self.tests_passed += 1
        else:
            self.tests_failed += 1
            if name:
                self.errors.append(f"Test failed: {name}")

        # Store details in result_data
        if details or name:
            if self.result_data is None:
                self.result_data = {"tests": []}
            if "tests" not in self.result_data:
                self.result_data["tests"] = []
            self.result_data["tests"].append({
                "name": name,
                "passed": passed,
                "details": details,
            })

    def set_metric(self, name: str, value: Any, unit: str = "") -> None:
        """
        Set a metric value (for metric jobs).

        Args:
            name: Metric name
            value: Metric value
            unit: Optional unit (e.g., "ms", "count", "%")
        """
        if self.result_data is None:
            self.result_data = {"metrics": {}}
        if "metrics" not in self.result_data:
            self.result_data["metrics"] = {}
        self.result_data["metrics"][name] = {
            "value": value,
            "unit": unit,
        } if unit else value

    def set_result(self, data: dict) -> None:
        """
        Set result data (for query/sync jobs).

        Args:
            data: Result data to store
        """
        if self.result_data is None:
            self.result_data = data
        else:
            self.result_data.update(data)
