"""
Request Context for threading audit information through operations.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid


@dataclass
class RequestContext:
    """
    Context object that flows through all operations for audit trail purposes.

    Attributes:
        request_id: Unique identifier for this request
        job_name: Name of the job being executed
        triggered_by: Source of the trigger (http, scheduler, cli, mcp)
        triggered_at: Timestamp when request was initiated
        dry_run: If True, no mutations should be performed
        debug: If True, enable verbose output
        user_id: Optional user ID if available
        correlation_id: Optional ID for correlating related requests
    """
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    job_name: str = ""
    triggered_by: str = "unknown"
    triggered_at: datetime = field(default_factory=datetime.utcnow)
    dry_run: bool = False
    debug: bool = False
    user_id: Optional[str] = None
    correlation_id: Optional[str] = None

    def to_audit_dict(self) -> dict:
        """Convert context to dict for audit logging."""
        return {
            "request_id": self.request_id,
            "job_name": self.job_name,
            "triggered_by": self.triggered_by,
            "triggered_at": self.triggered_at.isoformat(),
            "dry_run": self.dry_run,
            "debug": self.debug,
            "user_id": self.user_id,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def for_http(cls, job_name: str, dry_run: bool = False, **kwargs) -> "RequestContext":
        """Create context for HTTP request."""
        return cls(
            job_name=job_name,
            triggered_by="http",
            dry_run=dry_run,
            **kwargs
        )

    @classmethod
    def for_scheduler(cls, job_name: str, dry_run: bool = False, **kwargs) -> "RequestContext":
        """Create context for scheduled job."""
        return cls(
            job_name=job_name,
            triggered_by="scheduler",
            dry_run=dry_run,
            **kwargs
        )

    @classmethod
    def for_cli(cls, job_name: str, dry_run: bool = False, **kwargs) -> "RequestContext":
        """Create context for CLI invocation."""
        return cls(
            job_name=job_name,
            triggered_by="cli",
            dry_run=dry_run,
            **kwargs
        )

    @classmethod
    def for_mcp(cls, job_name: str, dry_run: bool = False, **kwargs) -> "RequestContext":
        """Create context for MCP (Model Context Protocol) invocation."""
        return cls(
            job_name=job_name,
            triggered_by="mcp",
            dry_run=dry_run,
            **kwargs
        )
