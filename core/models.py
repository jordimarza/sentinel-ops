"""
Core Models for AI-Driven Job System

This module defines the core data models that enable:
1. Semantic job discovery (AI understands what jobs can do)
2. Safe execution (preview → approve → execute → rollback)
3. Learning loop (feedback on results)
4. Intent-based execution (describe goal, system picks approach)
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


# =============================================================================
# Job Capabilities & Metadata
# =============================================================================

class Capability(str, Enum):
    """What a job can do - for AI discovery."""
    # Data operations
    READ_ORDERS = "read_orders"
    MODIFY_ORDERS = "modify_orders"
    READ_INVENTORY = "read_inventory"
    MODIFY_INVENTORY = "modify_inventory"
    READ_PARTNERS = "read_partners"
    MODIFY_PARTNERS = "modify_partners"

    # Actions
    SEND_NOTIFICATION = "send_notification"
    GENERATE_REPORT = "generate_report"
    SYNC_EXTERNAL = "sync_external"

    # Analysis
    VALIDATE_DATA = "validate_data"
    CALCULATE_METRICS = "calculate_metrics"
    DETECT_ANOMALIES = "detect_anomalies"


class RiskLevel(str, Enum):
    """Risk level for human oversight decisions."""
    LOW = "low"          # Safe to auto-execute
    MEDIUM = "medium"    # Recommend review
    HIGH = "high"        # Require approval
    CRITICAL = "critical"  # Require multi-approval


@dataclass
class JobCapabilities:
    """
    Structured metadata about what a job can do.
    Enables AI to discover and select appropriate jobs.
    """
    # What the job can do
    capabilities: list[Capability] = field(default_factory=list)

    # What it affects
    models_read: list[str] = field(default_factory=list)   # ["sale.order", "stock.picking"]
    models_write: list[str] = field(default_factory=list)  # ["sale.order.line"]

    # Constraints
    risk_level: RiskLevel = RiskLevel.MEDIUM
    max_records_per_run: Optional[int] = None
    requires_approval: bool = False
    idempotent: bool = False  # Safe to run multiple times

    # Scheduling hints
    typical_duration_seconds: Optional[float] = None
    recommended_schedule: Optional[str] = None  # "daily", "hourly", "on_demand"

    # Dependencies
    requires_jobs: list[str] = field(default_factory=list)  # Must run after these
    conflicts_with: list[str] = field(default_factory=list)  # Don't run together

    def to_dict(self) -> dict:
        return {
            "capabilities": [c.value for c in self.capabilities],
            "models_read": self.models_read,
            "models_write": self.models_write,
            "risk_level": self.risk_level.value,
            "max_records_per_run": self.max_records_per_run,
            "requires_approval": self.requires_approval,
            "idempotent": self.idempotent,
            "typical_duration_seconds": self.typical_duration_seconds,
            "recommended_schedule": self.recommended_schedule,
            "requires_jobs": self.requires_jobs,
            "conflicts_with": self.conflicts_with,
        }


# =============================================================================
# Execution Plan (Preview before Execute)
# =============================================================================

@dataclass
class PlannedChange:
    """A single change that will be made."""
    record_id: int
    record_name: str
    model: str
    action: str  # "update", "create", "delete", "message"
    field_changes: dict[str, dict] = field(default_factory=dict)  # {"qty": {"from": 10, "to": 5}}
    reason: str = ""
    risk_level: RiskLevel = RiskLevel.LOW
    odoo_url: str = ""

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "record_name": self.record_name,
            "model": self.model,
            "action": self.action,
            "field_changes": self.field_changes,
            "reason": self.reason,
            "risk_level": self.risk_level.value,
            "odoo_url": self.odoo_url,
        }


@dataclass
class ExecutionPlan:
    """
    A plan of changes to be approved before execution.

    Workflow:
    1. Job runs in plan mode → generates ExecutionPlan
    2. Human/AI reviews plan
    3. Approves all, some, or none
    4. Job executes only approved changes
    """
    plan_id: str
    job_name: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None

    # The planned changes
    changes: list[PlannedChange] = field(default_factory=list)

    # Approval tracking
    approved_ids: list[int] = field(default_factory=list)  # Record IDs approved
    rejected_ids: list[int] = field(default_factory=list)  # Record IDs rejected
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None

    # Summary
    total_changes: int = 0
    high_risk_count: int = 0

    def add_change(self, change: PlannedChange) -> None:
        self.changes.append(change)
        self.total_changes += 1
        if change.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            self.high_risk_count += 1

    def approve_all(self, approved_by: str) -> None:
        self.approved_ids = [c.record_id for c in self.changes]
        self.rejected_ids = []
        self.approved_by = approved_by
        self.approved_at = datetime.utcnow()

    def approve_selected(self, record_ids: list[int], approved_by: str) -> None:
        all_ids = {c.record_id for c in self.changes}
        self.approved_ids = [rid for rid in record_ids if rid in all_ids]
        self.rejected_ids = [rid for rid in all_ids if rid not in record_ids]
        self.approved_by = approved_by
        self.approved_at = datetime.utcnow()

    def is_approved(self, record_id: int) -> bool:
        return record_id in self.approved_ids

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "job_name": self.job_name,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "total_changes": self.total_changes,
            "high_risk_count": self.high_risk_count,
            "changes": [c.to_dict() for c in self.changes],
            "approved_ids": self.approved_ids,
            "rejected_ids": self.rejected_ids,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
        }


# =============================================================================
# Rollback Support
# =============================================================================

@dataclass
class RecordSnapshot:
    """Snapshot of a record before modification."""
    record_id: int
    model: str
    timestamp: datetime
    field_values: dict[str, Any]  # Original values before change


@dataclass
class RollbackInfo:
    """
    Information needed to rollback changes.

    Stored with job result for potential undo.
    """
    request_id: str
    job_name: str
    executed_at: datetime
    snapshots: list[RecordSnapshot] = field(default_factory=list)
    can_rollback: bool = True
    rollback_expires_at: Optional[datetime] = None  # Can't rollback after this

    def add_snapshot(self, record_id: int, model: str, values: dict) -> None:
        self.snapshots.append(RecordSnapshot(
            record_id=record_id,
            model=model,
            timestamp=datetime.utcnow(),
            field_values=values,
        ))


# =============================================================================
# Feedback Loop (Learning)
# =============================================================================

class FeedbackRating(str, Enum):
    """Human feedback on job execution."""
    CORRECT = "correct"          # Job did the right thing
    INCORRECT = "incorrect"      # Job made wrong changes
    PARTIAL = "partial"          # Some right, some wrong
    UNNECESSARY = "unnecessary"  # Didn't need to run
    MISSED = "missed"            # Should have caught more


@dataclass
class ExecutionFeedback:
    """
    Feedback on a job execution for learning.

    Enables:
    - Tracking job quality over time
    - AI learning from mistakes
    - Identifying jobs that need tuning
    """
    request_id: str
    job_name: str
    rating: FeedbackRating
    feedback_by: str
    feedback_at: datetime = field(default_factory=datetime.utcnow)

    # Details
    comment: str = ""
    incorrect_record_ids: list[int] = field(default_factory=list)  # Which records were wrong
    missed_record_ids: list[int] = field(default_factory=list)     # Which should have been caught

    # For learning
    suggested_params: Optional[dict] = None  # "Should have used days=60 instead"
    should_have_been: Optional[str] = None   # "Should have skipped this order"


# =============================================================================
# Intent-Based Execution
# =============================================================================

@dataclass
class Intent:
    """
    High-level intent that AI/user wants to achieve.

    Instead of: "Run clean_old_orders with days=30"
    Express as: "Fix stuck partial orders older than a month"

    System matches intent to capable jobs.
    """
    description: str  # Natural language description
    goal: str         # What outcome is desired
    constraints: dict = field(default_factory=dict)  # {"max_changes": 100, "models": ["sale.order"]}
    urgency: str = "normal"  # "low", "normal", "high", "critical"
    context: dict = field(default_factory=dict)  # Additional context for job selection


@dataclass
class IntentMatch:
    """A job that can fulfill an intent."""
    job_name: str
    confidence: float  # 0.0 to 1.0
    suggested_params: dict
    explanation: str  # Why this job matches
    risk_assessment: str  # What could go wrong


# =============================================================================
# Cost & Resource Tracking
# =============================================================================

@dataclass
class ResourceUsage:
    """Track resources consumed by a job."""
    # API calls
    odoo_read_calls: int = 0
    odoo_write_calls: int = 0
    odoo_search_calls: int = 0

    # BigQuery
    bq_queries: int = 0
    bq_bytes_processed: int = 0

    # Time
    total_duration_ms: int = 0
    odoo_time_ms: int = 0
    bq_time_ms: int = 0

    # Records
    records_read: int = 0
    records_written: int = 0

    def to_dict(self) -> dict:
        return {
            "odoo_read_calls": self.odoo_read_calls,
            "odoo_write_calls": self.odoo_write_calls,
            "odoo_search_calls": self.odoo_search_calls,
            "bq_queries": self.bq_queries,
            "bq_bytes_processed": self.bq_bytes_processed,
            "total_duration_ms": self.total_duration_ms,
            "odoo_time_ms": self.odoo_time_ms,
            "bq_time_ms": self.bq_time_ms,
            "records_read": self.records_read,
            "records_written": self.records_written,
        }


# =============================================================================
# Workflow / Job Chaining
# =============================================================================

class WorkflowStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# =============================================================================
# Intervention Task System
# =============================================================================

class TaskStatus(str, Enum):
    """Status workflow for intervention tasks."""
    OPEN = "open"                           # New, unassigned
    ASSIGNED = "assigned"                   # Claimed by worker (human or AI)
    PLANNING = "planning"                   # AI agent analyzing and creating plan
    AWAITING_APPROVAL = "awaiting_approval" # AI plan ready, needs human sign-off
    EXECUTING = "executing"                 # Plan approved, action being taken
    IN_PROGRESS = "in_progress"             # Human working (no plan/execute cycle)
    BLOCKED = "blocked"                     # Cannot proceed, waiting on external
    RESOLVED = "resolved"                   # Issue addressed
    CLOSED = "closed"                       # Final state
    FAILED = "failed"                       # Execution failed, needs review
    ESCALATED = "escalated"                 # Moved to higher level
    SNOOZED = "snoozed"                     # Deferred until date


class TaskPriority(str, Enum):
    """Priority levels for intervention tasks."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskType(str, Enum):
    """Types of intervention tasks."""
    QTY_MISMATCH = "qty_mismatch"
    INVOICE_MISMATCH = "invoice_mismatch"
    DELIVERY_EXCEPTION = "delivery_exception"
    PAYMENT_ISSUE = "payment_issue"
    RETURN_PENDING = "return_pending"
    STOCK_DISCREPANCY = "stock_discrepancy"


class AssigneeType(str, Enum):
    """Types of task assignees."""
    HUMAN = "human"
    AI_AGENT = "ai_agent"


class ResolutionType(str, Enum):
    """Types of task resolutions."""
    FIXED = "fixed"
    ESCALATED = "escalated"
    WONT_FIX = "wont_fix"
    DUPLICATE = "duplicate"


class PlanStatus(str, Enum):
    """Status of AI agent plan."""
    NONE = "none"
    PLANNING = "planning"
    PLANNED = "planned"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalStatus(str, Enum):
    """Status of human approval for AI plans."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ExecutionStatus(str, Enum):
    """Status of plan execution."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Department(str, Enum):
    """Departments for task routing."""
    OPERATIONS = "operations"
    FINANCE = "finance"
    CUSTOMER_SERVICE = "customer_service"


@dataclass
class WorkflowStep:
    """A step in a workflow."""
    step_id: str
    job_name: str
    parameters: dict = field(default_factory=dict)

    # Conditions
    run_if: Optional[str] = None  # Expression: "prev.records_updated > 0"
    skip_if: Optional[str] = None

    # Flow control
    on_success: Optional[str] = None  # Next step ID
    on_failure: Optional[str] = None  # Step ID or "abort"

    # State
    status: WorkflowStepStatus = WorkflowStepStatus.PENDING
    result_request_id: Optional[str] = None


@dataclass
class Workflow:
    """
    A sequence of jobs to execute.

    Example:
    1. validate_orders → if failures, alert and stop
    2. clean_old_orders → continue
    3. complete_shipping → continue
    4. generate_report → done
    """
    workflow_id: str
    name: str
    description: str
    steps: list[WorkflowStep] = field(default_factory=list)

    # State
    current_step_index: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    status: WorkflowStepStatus = WorkflowStepStatus.PENDING

    def add_step(
        self,
        job_name: str,
        parameters: dict = None,
        run_if: str = None,
    ) -> str:
        step_id = f"step_{len(self.steps) + 1}"
        self.steps.append(WorkflowStep(
            step_id=step_id,
            job_name=job_name,
            parameters=parameters or {},
            run_if=run_if,
        ))
        return step_id
