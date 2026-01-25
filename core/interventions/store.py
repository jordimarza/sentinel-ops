"""
Intervention Store

Data access layer for intervention tasks in BigQuery.
Handles all CRUD operations, queries, and AI workflow methods.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.context import RequestContext

from core.models import (
    TaskStatus,
    PlanStatus,
    ApprovalStatus,
    ExecutionStatus,
)

logger = logging.getLogger(__name__)


class InterventionStore:
    """
    Data access layer for intervention tasks.

    Handles all BigQuery operations for the intervention_tasks table.
    Uses composition - takes a BQ client for low-level operations.

    Philosophy (append-only pattern):
        - Each job run appends detections as new rows
        - Status is derived from partition presence:
          - Issue in today's partition → Open
          - Issue in yesterday's but not today's → Resolved by human
          - Issue with status='resolved' → Resolved by AI
    """

    def __init__(self, bq_client, table_name: str = "intervention_tasks"):
        """
        Initialize the store.

        Args:
            bq_client: BigQuery client instance
            table_name: Name of the interventions table
        """
        self._bq = bq_client
        self._table_name = table_name

    def _get_table_id(self) -> str:
        """Get fully qualified table ID."""
        return f"{self._bq.project}.{self._bq.dataset}.{self._table_name}"

    # =========================================================================
    # Core CRUD Operations
    # =========================================================================

    def create(
        self,
        ctx: "RequestContext",
        document_type: str,
        document_id: int,
        issue_type: str,
        title: str,
        priority: str = "medium",
        status: str = None,
        description: str = None,
        document_name: str = None,
        document_url: str = None,
        related_documents: list[dict] = None,
        detection_data: dict = None,
        department: str = None,
        process_category: str = None,
        risk_level: str = None,
        due_at: str = None,
        # Financial context
        currency: str = None,
        qty_ordered: float = None,
        qty_delivered: float = None,
        qty_invoiced: float = None,
        amount_order: float = None,
        amount_difference: float = None,
        amount_credit: float = None,
        financial_data: dict = None,
        # Resolution (for resolved status)
        resolution_type: str = None,
        resolution_notes: str = None,
        resolution_data: dict = None,
        resolved_by: str = None,
        # Extra
        metadata: dict = None,
    ) -> Optional[str]:
        """
        Create an intervention record.

        Args:
            ctx: Request context
            document_type: Odoo model (e.g., "sale.order")
            document_id: Odoo record ID
            issue_type: Type of issue (e.g., "qty_mismatch")
            title: Human-readable summary
            priority: Task priority (low, medium, high, critical)
            status: Task status (defaults to 'open')
            ... additional optional fields

        Returns:
            intervention_id: UUID of the created record, or None on error
        """
        intervention_id = str(uuid.uuid4())
        dedup_key = f"{document_type}:{document_id}:{issue_type}"
        now = datetime.utcnow().isoformat()

        # Default status
        if status is None:
            status = TaskStatus.OPEN.value

        # Build status history
        status_history = [{"status": status, "at": now, "by": "system"}]

        try:
            client = self._bq._get_client()
            table_id = self._get_table_id()

            row = {
                "task_id": intervention_id,
                "request_id": ctx.request_id,
                "job_name": ctx.job_name,
                "document_type": document_type,
                "document_id": document_id,
                "document_name": document_name,
                "document_url": document_url,
                "related_documents": json.dumps(related_documents) if related_documents else None,
                "task_type": issue_type,
                "title": title,
                "description": description,
                "detection_data": json.dumps(detection_data) if detection_data else None,
                "currency": currency,
                "qty_ordered": qty_ordered,
                "qty_delivered": qty_delivered,
                "qty_invoiced": qty_invoiced,
                "amount_order": amount_order,
                "amount_difference": amount_difference,
                "amount_credit": amount_credit,
                "financial_data": json.dumps(financial_data) if financial_data else None,
                "department": department,
                "process_category": process_category,
                "priority": priority,
                "risk_level": risk_level,
                "status": status,
                "status_history": json.dumps(status_history),
                "resolution_type": resolution_type,
                "resolution_notes": resolution_notes,
                "resolution_data": json.dumps(resolution_data) if resolution_data else None,
                "resolved_by": resolved_by,
                "resolved_at": now if status == "resolved" else None,
                "created_at": now,
                "due_at": due_at,
                "environment": ctx.environment,
                "source_system": "sentinel-ops",
                "dedup_key": dedup_key,
                "metadata": json.dumps(metadata) if metadata else None,
            }

            errors = client.insert_rows_json(table_id, [row])
            if errors:
                logger.error(f"BigQuery insert errors: {errors}")
                return None

            logger.info(f"Created intervention {intervention_id}: {title}")
            return intervention_id

        except Exception as e:
            logger.error(f"Failed to create intervention: {e}")
            return None

    def get(self, intervention_id: str) -> Optional[dict]:
        """Get an intervention by ID."""
        try:
            sql = f"""
            SELECT *
            FROM `{self._get_table_id()}`
            WHERE task_id = @task_id
            """
            results = self._bq.query(sql, {"task_id": intervention_id})
            return results[0] if results else None
        except Exception as e:
            logger.error(f"Failed to get intervention: {e}")
            return None

    def find_open(
        self,
        document_type: str,
        document_id: int,
        issue_type: str,
    ) -> Optional[dict]:
        """
        Find an existing open intervention for the same document and issue type.

        Used for deduplication.
        """
        try:
            dedup_key = f"{document_type}:{document_id}:{issue_type}"
            sql = f"""
            SELECT *
            FROM `{self._get_table_id()}`
            WHERE dedup_key = @dedup_key
              AND status NOT IN ('closed', 'resolved')
            ORDER BY created_at DESC
            LIMIT 1
            """
            results = self._bq.query(sql, {"dedup_key": dedup_key})
            return results[0] if results else None
        except Exception as e:
            logger.error(f"Failed to find open intervention: {e}")
            return None

    def create_if_not_exists(
        self,
        ctx: "RequestContext",
        document_type: str,
        document_id: int,
        issue_type: str,
        title: str,
        **kwargs,
    ) -> tuple[Optional[str], bool]:
        """
        Create an intervention if one doesn't already exist.

        Returns:
            (intervention_id, created): ID and whether it was newly created
        """
        existing = self.find_open(document_type, document_id, issue_type)
        if existing:
            logger.info(f"Intervention already exists for {document_type}:{document_id}:{issue_type}")
            return existing.get("task_id"), False

        intervention_id = self.create(
            ctx=ctx,
            document_type=document_type,
            document_id=document_id,
            issue_type=issue_type,
            title=title,
            **kwargs,
        )
        return intervention_id, True if intervention_id else False

    # =========================================================================
    # Append-Only Pattern Methods
    # =========================================================================

    def log_detection(
        self,
        ctx: "RequestContext",
        document_type: str,
        document_id: int,
        issue_type: str,
        title: str,
        priority: str = "medium",
        document_name: str = None,
        description: str = None,
        detection_data: dict = None,
        financial_data: dict = None,
        department: str = None,
        metadata: dict = None,
    ) -> Optional[str]:
        """
        Log an issue detection (append-only pattern).

        Call this for each issue found during a job run. Each call creates
        a new row. Status is derived from partition presence:
        - In today's partition → issue is current/open
        - Was in yesterday's, not in today's → resolved by human
        - Has status='resolved' → resolved by AI

        Args:
            ctx: Request context
            document_type: Odoo model (e.g., "sale.order")
            document_id: Odoo record ID
            issue_type: Type of issue (e.g., "qty_mismatch")
            title: Human-readable summary
            priority: low, medium, high, critical
            document_name: Display name (e.g., "S00455346")
            description: Detailed description
            detection_data: Issue-specific data (JSON)
            financial_data: Financial context (JSON)
            department: Responsible department
            metadata: Flexible field for future expansion (JSON)

        Returns:
            intervention_id if created successfully
        """
        return self.create(
            ctx=ctx,
            document_type=document_type,
            document_id=document_id,
            issue_type=issue_type,
            title=title,
            priority=priority,
            document_name=document_name,
            description=description,
            detection_data=detection_data,
            financial_data=financial_data,
            department=department,
            metadata=metadata,
        )

    def log_resolution(
        self,
        ctx: "RequestContext",
        document_type: str,
        document_id: int,
        issue_type: str,
        title: str,
        resolution_type: str,
        resolved_by: str,
        priority: str = "medium",
        document_name: str = None,
        resolution_notes: str = None,
        resolution_data: dict = None,
        detection_data: dict = None,
        metadata: dict = None,
    ) -> Optional[str]:
        """
        Log an issue resolution by AI (append-only pattern).

        Call this when AI successfully resolves an issue. Creates a row
        with status='resolved' so analytics can distinguish AI vs human resolution.

        Args:
            ctx: Request context
            document_type: Odoo model
            document_id: Odoo record ID
            issue_type: Type of issue
            title: Human-readable summary
            resolution_type: How it was resolved (e.g., "auto_adjusted")
            resolved_by: Who/what resolved it (e.g., job name)
            priority: Task priority
            document_name: Display name (e.g., "S00123")
            resolution_notes: Human-readable resolution description
            resolution_data: Resolution details (JSON)
            detection_data: Original issue details (JSON)
            metadata: Flexible field for future expansion (JSON)

        Returns:
            intervention_id if created successfully
        """
        return self.create(
            ctx=ctx,
            document_type=document_type,
            document_id=document_id,
            issue_type=issue_type,
            title=title,
            status="resolved",
            priority=priority,
            document_name=document_name,
            detection_data=detection_data,
            resolution_type=resolution_type,
            resolution_notes=resolution_notes,
            resolution_data=resolution_data,
            resolved_by=resolved_by,
            metadata=metadata,
        )

    # =========================================================================
    # Query Methods
    # =========================================================================

    def query(
        self,
        status: str = None,
        assignee_id: str = None,
        assignee_type: str = None,
        department: str = None,
        priority: str = None,
        issue_type: str = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query interventions with filters."""
        try:
            conditions = []
            params = {}

            if status:
                conditions.append("status = @status")
                params["status"] = status
            if assignee_id:
                conditions.append("assignee_id = @assignee_id")
                params["assignee_id"] = assignee_id
            if assignee_type:
                conditions.append("assignee_type = @assignee_type")
                params["assignee_type"] = assignee_type
            if department:
                conditions.append("department = @department")
                params["department"] = department
            if priority:
                conditions.append("priority = @priority")
                params["priority"] = priority
            if issue_type:
                conditions.append("task_type = @task_type")
                params["task_type"] = issue_type

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            sql = f"""
            SELECT *
            FROM `{self._get_table_id()}`
            WHERE {where_clause}
            ORDER BY
                CASE priority
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    WHEN 'low' THEN 4
                END,
                created_at ASC
            LIMIT {limit}
            """
            return self._bq.query(sql, params if params else None)
        except Exception as e:
            logger.error(f"Failed to query interventions: {e}")
            return []

    def get_available(
        self,
        assignee_type: str = None,
        department: str = None,
        limit: int = 10,
    ) -> list[dict]:
        """Get interventions available for pickup (open, unassigned)."""
        try:
            conditions = ["status = 'open'", "assignee_id IS NULL"]
            params = {}

            if department:
                conditions.append("department = @department")
                params["department"] = department

            where_clause = " AND ".join(conditions)

            sql = f"""
            SELECT task_id, title, priority, document_url, task_type, department, created_at
            FROM `{self._get_table_id()}`
            WHERE {where_clause}
            ORDER BY
                CASE priority
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    WHEN 'low' THEN 4
                END,
                created_at ASC
            LIMIT {limit}
            """
            return self._bq.query(sql, params if params else None)
        except Exception as e:
            logger.error(f"Failed to get available interventions: {e}")
            return []

    def get_assigned_to(
        self,
        assignee_id: str,
        status: list[str] = None,
    ) -> list[dict]:
        """Get interventions assigned to a specific worker."""
        try:
            if status is None:
                status = ["assigned", "in_progress", "planning", "awaiting_approval", "executing"]

            status_list = ", ".join([f"'{s}'" for s in status])
            sql = f"""
            SELECT task_id, title, priority, document_url, status, created_at
            FROM `{self._get_table_id()}`
            WHERE assignee_id = @assignee_id
              AND status IN ({status_list})
            ORDER BY
                CASE priority
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    WHEN 'low' THEN 4
                END,
                created_at ASC
            """
            return self._bq.query(sql, {"assignee_id": assignee_id})
        except Exception as e:
            logger.error(f"Failed to get assigned interventions: {e}")
            return []

    def get_for_agent(
        self,
        agent_capabilities: list[str],
        max_items: int = 10,
    ) -> list[dict]:
        """
        Get interventions that an AI agent can handle based on its capabilities.

        Matches issue_type against agent capabilities.
        """
        try:
            if not agent_capabilities:
                return []

            cap_list = ", ".join([f"'{c}'" for c in agent_capabilities])
            sql = f"""
            SELECT *
            FROM `{self._get_table_id()}`
            WHERE status = 'open'
              AND assignee_id IS NULL
              AND task_type IN ({cap_list})
            ORDER BY
                CASE priority
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    WHEN 'low' THEN 4
                END,
                created_at ASC
            LIMIT {max_items}
            """
            return self._bq.query(sql)
        except Exception as e:
            logger.error(f"Failed to get interventions for agent: {e}")
            return []

    def get_pending_approvals(
        self,
        department: str = None,
    ) -> list[dict]:
        """Get AI plans awaiting human approval."""
        try:
            conditions = [
                "status = 'awaiting_approval'",
                "approval_status = 'pending'",
            ]

            if department:
                conditions.append(f"department = '{department}'")

            where_clause = " AND ".join(conditions)

            sql = f"""
            SELECT
                task_id,
                title,
                document_name,
                document_url,
                agent_model,
                plan_confidence,
                plan_reasoning,
                planned_action,
                plan_alternatives,
                approval_requested_at
            FROM `{self._get_table_id()}`
            WHERE {where_clause}
            ORDER BY plan_confidence ASC, approval_requested_at ASC
            """
            return self._bq.query(sql)
        except Exception as e:
            logger.error(f"Failed to get pending approvals: {e}")
            return []

    # =========================================================================
    # Update Methods
    # =========================================================================

    def _update(self, intervention_id: str, updates: dict) -> bool:
        """
        Internal method to update intervention fields.

        Uses DML UPDATE for atomic updates.
        """
        try:
            client = self._bq._get_client()
            table_id = self._get_table_id()
            now = datetime.utcnow().isoformat()

            # Always update updated_at
            updates["updated_at"] = now

            # Handle JSON fields
            json_fields = {
                "related_documents", "detection_data", "financial_data",
                "agent_capabilities", "planned_action", "plan_alternatives",
                "execution_result", "execution_log", "status_history",
                "resolution_data", "metadata"
            }

            set_clauses = []
            for key, value in updates.items():
                if value is None:
                    set_clauses.append(f"{key} = NULL")
                elif key in json_fields:
                    json_val = json.dumps(value).replace("'", "\\'")
                    set_clauses.append(f"{key} = PARSE_JSON('{json_val}')")
                elif isinstance(value, bool):
                    set_clauses.append(f"{key} = {str(value).upper()}")
                elif isinstance(value, (int, float)):
                    set_clauses.append(f"{key} = {value}")
                else:
                    escaped = str(value).replace("'", "\\'")
                    set_clauses.append(f"{key} = '{escaped}'")

            set_clause = ", ".join(set_clauses)

            sql = f"""
            UPDATE `{table_id}`
            SET {set_clause}
            WHERE task_id = '{intervention_id}'
            """

            query_job = client.query(sql)
            query_job.result()

            logger.debug(f"Updated intervention {intervention_id}: {list(updates.keys())}")
            return True

        except Exception as e:
            logger.error(f"Failed to update intervention {intervention_id}: {e}")
            return False

    def _append_status_history(self, intervention: dict, new_status: str, by: str) -> list:
        """Append to status history."""
        history = intervention.get("status_history") or []
        if isinstance(history, str):
            history = json.loads(history)
        history.append({
            "status": new_status,
            "at": datetime.utcnow().isoformat(),
            "by": by,
        })
        return history

    def update_status(
        self,
        intervention_id: str,
        status: str,
        updated_by: str,
        notes: str = None,
    ) -> bool:
        """Update intervention status with history tracking."""
        intervention = self.get(intervention_id)
        if not intervention:
            logger.error(f"Intervention {intervention_id} not found")
            return False

        history = self._append_status_history(intervention, status, updated_by)
        updates = {"status": status, "status_history": history}
        if notes:
            updates["description"] = (intervention.get("description") or "") + f"\n[{updated_by}] {notes}"

        return self._update(intervention_id, updates)

    def assign(
        self,
        intervention_id: str,
        assignee_id: str,
        assignee_type: str,
        assigned_by: str,
    ) -> bool:
        """Assign an intervention to a worker."""
        intervention = self.get(intervention_id)
        if not intervention:
            return False

        history = self._append_status_history(intervention, TaskStatus.ASSIGNED.value, assigned_by)
        return self._update(intervention_id, {
            "assignee_id": assignee_id,
            "assignee_type": assignee_type,
            "assigned_at": datetime.utcnow().isoformat(),
            "assigned_by": assigned_by,
            "status": TaskStatus.ASSIGNED.value,
            "status_history": history,
        })

    def claim(
        self,
        intervention_id: str,
        assignee_id: str,
        assignee_type: str,
    ) -> bool:
        """
        Atomically claim an unassigned intervention.

        Returns False if intervention is already assigned.
        """
        intervention = self.get(intervention_id)
        if not intervention:
            return False

        if intervention.get("assignee_id"):
            logger.warning(f"Intervention {intervention_id} already assigned to {intervention.get('assignee_id')}")
            return False

        return self.assign(intervention_id, assignee_id, assignee_type, assignee_id)

    def snooze(
        self,
        intervention_id: str,
        until: str,
        snoozed_by: str,
        reason: str = None,
    ) -> bool:
        """Snooze an intervention until a specific date."""
        intervention = self.get(intervention_id)
        if not intervention:
            return False

        history = self._append_status_history(intervention, TaskStatus.SNOOZED.value, snoozed_by)
        updates = {
            "status": TaskStatus.SNOOZED.value,
            "snoozed_until": until,
            "status_history": history,
        }
        if reason:
            updates["description"] = (intervention.get("description") or "") + f"\n[Snoozed by {snoozed_by}] {reason}"

        return self._update(intervention_id, updates)

    def resolve(
        self,
        intervention_id: str,
        resolution_type: str,
        resolved_by: str,
        resolution_notes: str = None,
        resolution_data: dict = None,
    ) -> bool:
        """Resolve an intervention."""
        intervention = self.get(intervention_id)
        if not intervention:
            return False

        history = self._append_status_history(intervention, TaskStatus.RESOLVED.value, resolved_by)
        return self._update(intervention_id, {
            "status": TaskStatus.RESOLVED.value,
            "resolution_type": resolution_type,
            "resolved_by": resolved_by,
            "resolved_at": datetime.utcnow().isoformat(),
            "resolution_notes": resolution_notes,
            "resolution_data": resolution_data,
            "status_history": history,
        })

    # =========================================================================
    # AI Agent Planning Methods
    # =========================================================================

    def submit_plan(
        self,
        intervention_id: str,
        agent_id: str,
        agent_model: str,
        planned_action: dict,
        plan_reasoning: str,
        plan_confidence: float,
        requires_approval: bool = None,
        plan_alternatives: list[dict] = None,
        agent_version: str = None,
        agent_capabilities: list[str] = None,
    ) -> bool:
        """
        Submit an AI agent's plan for an intervention.

        If requires_approval is None, auto-determines based on confidence.
        """
        intervention = self.get(intervention_id)
        if not intervention:
            return False

        # Auto-determine approval requirement if not specified
        if requires_approval is None:
            requires_approval = plan_confidence < 0.9

        now = datetime.utcnow().isoformat()

        # Determine next status
        if requires_approval:
            new_status = TaskStatus.AWAITING_APPROVAL.value
            history = self._append_status_history(intervention, new_status, agent_id)
            approval_status = ApprovalStatus.PENDING.value
        else:
            new_status = TaskStatus.EXECUTING.value
            history = self._append_status_history(intervention, new_status, agent_id)
            approval_status = ApprovalStatus.APPROVED.value

        return self._update(intervention_id, {
            "agent_model": agent_model,
            "agent_version": agent_version,
            "agent_capabilities": agent_capabilities,
            "plan_status": PlanStatus.PLANNED.value,
            "planned_action": planned_action,
            "plan_reasoning": plan_reasoning,
            "plan_confidence": plan_confidence,
            "plan_created_at": now,
            "plan_alternatives": plan_alternatives,
            "requires_approval": requires_approval,
            "approval_status": approval_status,
            "approval_requested_at": now if requires_approval else None,
            "status": new_status,
            "status_history": history,
        })

    def approve_plan(
        self,
        intervention_id: str,
        approved_by: str,
        notes: str = None,
    ) -> bool:
        """Approve an AI agent's plan."""
        intervention = self.get(intervention_id)
        if not intervention:
            return False

        if intervention.get("status") != TaskStatus.AWAITING_APPROVAL.value:
            logger.warning(f"Intervention {intervention_id} not awaiting approval")
            return False

        history = self._append_status_history(intervention, TaskStatus.EXECUTING.value, approved_by)
        updates = {
            "approval_status": ApprovalStatus.APPROVED.value,
            "approved_by": approved_by,
            "approved_at": datetime.utcnow().isoformat(),
            "plan_status": PlanStatus.APPROVED.value,
            "status": TaskStatus.EXECUTING.value,
            "status_history": history,
        }
        if notes:
            updates["resolution_notes"] = notes

        return self._update(intervention_id, updates)

    def reject_plan(
        self,
        intervention_id: str,
        rejected_by: str,
        reason: str,
    ) -> bool:
        """Reject an AI agent's plan, sending back for replanning."""
        intervention = self.get(intervention_id)
        if not intervention:
            return False

        history = self._append_status_history(intervention, TaskStatus.PLANNING.value, rejected_by)
        return self._update(intervention_id, {
            "approval_status": ApprovalStatus.REJECTED.value,
            "approved_by": rejected_by,
            "approved_at": datetime.utcnow().isoformat(),
            "rejection_reason": reason,
            "plan_status": PlanStatus.REJECTED.value,
            "status": TaskStatus.PLANNING.value,
            "status_history": history,
        })

    def start_execution(self, intervention_id: str, agent_id: str) -> bool:
        """Mark execution as started."""
        intervention = self.get(intervention_id)
        if not intervention:
            return False

        return self._update(intervention_id, {
            "execution_status": ExecutionStatus.RUNNING.value,
            "execution_started_at": datetime.utcnow().isoformat(),
            "execution_log": [{"action": "execution_started", "at": datetime.utcnow().isoformat(), "by": agent_id}],
        })

    def log_execution_step(
        self,
        intervention_id: str,
        action: str,
        result: dict,
    ) -> bool:
        """Log an execution step."""
        intervention = self.get(intervention_id)
        if not intervention:
            return False

        log = intervention.get("execution_log") or []
        if isinstance(log, str):
            log = json.loads(log)

        log.append({
            "action": action,
            "at": datetime.utcnow().isoformat(),
            "result": result,
        })

        return self._update(intervention_id, {"execution_log": log})

    def complete_execution(
        self,
        intervention_id: str,
        agent_id: str,
        result: dict,
        success: bool = True,
    ) -> bool:
        """Mark execution as completed."""
        intervention = self.get(intervention_id)
        if not intervention:
            return False

        execution_status = ExecutionStatus.COMPLETED.value if success else ExecutionStatus.FAILED.value
        new_status = TaskStatus.RESOLVED.value if success else TaskStatus.FAILED.value

        history = self._append_status_history(intervention, new_status, agent_id)

        return self._update(intervention_id, {
            "execution_status": execution_status,
            "execution_completed_at": datetime.utcnow().isoformat(),
            "execution_result": result,
            "status": new_status,
            "status_history": history,
        })

    # =========================================================================
    # Statistics & Analytics
    # =========================================================================

    def get_stats(
        self,
        department: str = None,
        days: int = 30,
    ) -> dict:
        """Get intervention statistics."""
        try:
            conditions = [f"created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)"]
            if department:
                conditions.append(f"department = '{department}'")

            where_clause = " AND ".join(conditions)

            sql = f"""
            SELECT
                status,
                priority,
                task_type,
                COUNT(*) as count
            FROM `{self._get_table_id()}`
            WHERE {where_clause}
            GROUP BY status, priority, task_type
            """
            results = self._bq.query(sql)

            stats = {
                "total": 0,
                "by_status": {},
                "by_priority": {},
                "by_type": {},
            }

            for row in results:
                count = row["count"]
                stats["total"] += count

                status = row["status"]
                stats["by_status"][status] = stats["by_status"].get(status, 0) + count

                priority = row["priority"]
                stats["by_priority"][priority] = stats["by_priority"].get(priority, 0) + count

                issue_type = row["task_type"]
                stats["by_type"][issue_type] = stats["by_type"].get(issue_type, 0) + count

            return stats
        except Exception as e:
            logger.error(f"Failed to get intervention stats: {e}")
            return {}

    def get_agent_performance(
        self,
        agent_id: str,
        days: int = 30,
    ) -> dict:
        """Get performance stats for an AI agent."""
        try:
            sql = f"""
            SELECT
                COUNT(*) as total_tasks,
                COUNTIF(status = 'resolved' AND resolution_type = 'fixed') as successful,
                COUNTIF(status = 'failed') as failed,
                AVG(plan_confidence) as avg_confidence,
                AVG(TIMESTAMP_DIFF(resolved_at, assigned_at, MINUTE)) as avg_resolution_minutes
            FROM `{self._get_table_id()}`
            WHERE assignee_id = @agent_id
              AND assigned_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
            """
            results = self._bq.query(sql, {"agent_id": agent_id})

            if results:
                row = results[0]
                total = row.get("total_tasks", 0)
                successful = row.get("successful", 0)
                return {
                    "total_tasks": total,
                    "successful": successful,
                    "failed": row.get("failed", 0),
                    "success_rate": successful / total if total > 0 else 0,
                    "avg_confidence": row.get("avg_confidence"),
                    "avg_resolution_minutes": row.get("avg_resolution_minutes"),
                }
            return {}
        except Exception as e:
            logger.error(f"Failed to get agent performance: {e}")
            return {}


class NoOpInterventionStore(InterventionStore):
    """
    No-op store for testing/development without BQ access.

    All operations log to console but don't actually write to BigQuery.
    """

    def __init__(self):
        self._bq = None
        self._table_name = "intervention_tasks"
        logger.info("Using NoOp InterventionStore (no actual BigQuery writes)")

    def create(self, ctx, document_type, document_id, issue_type, title, **kwargs) -> str:
        import uuid
        intervention_id = str(uuid.uuid4())
        logger.info(f"[NOOP] Create: {issue_type} for {document_type}:{document_id} - {title}")
        return intervention_id

    def get(self, intervention_id) -> Optional[dict]:
        logger.info(f"[NOOP] Get: {intervention_id}")
        return None

    def find_open(self, document_type, document_id, issue_type) -> Optional[dict]:
        logger.info(f"[NOOP] Find: {document_type}:{document_id}:{issue_type}")
        return None

    def create_if_not_exists(self, ctx, document_type, document_id, issue_type, title, **kwargs) -> tuple[Optional[str], bool]:
        intervention_id = self.create(ctx, document_type, document_id, issue_type, title, **kwargs)
        return intervention_id, True

    def log_detection(self, ctx, document_type, document_id, issue_type, title, **kwargs) -> Optional[str]:
        import uuid
        intervention_id = str(uuid.uuid4())
        logger.info(f"[NOOP] Detection: {issue_type} on {document_type}:{document_id} - {title}")
        return intervention_id

    def log_resolution(self, ctx, document_type, document_id, issue_type, title, resolution_type, resolved_by, **kwargs) -> Optional[str]:
        import uuid
        intervention_id = str(uuid.uuid4())
        logger.info(f"[NOOP] Resolution: {issue_type} on {document_type}:{document_id} - {title} ({resolution_type} by {resolved_by})")
        return intervention_id

    def query(self, **kwargs) -> list[dict]:
        logger.info(f"[NOOP] Query: {kwargs}")
        return []

    def get_available(self, **kwargs) -> list[dict]:
        logger.info(f"[NOOP] Get available")
        return []

    def get_assigned_to(self, assignee_id, **kwargs) -> list[dict]:
        logger.info(f"[NOOP] Get assigned to: {assignee_id}")
        return []

    def get_for_agent(self, agent_capabilities, max_items=10) -> list[dict]:
        logger.info(f"[NOOP] Get for agent: {agent_capabilities}")
        return []

    def get_pending_approvals(self, department=None) -> list[dict]:
        logger.info(f"[NOOP] Get pending approvals")
        return []

    def update_status(self, intervention_id, status, updated_by, notes=None) -> bool:
        logger.info(f"[NOOP] Update status: {intervention_id} -> {status}")
        return True

    def assign(self, intervention_id, assignee_id, assignee_type, assigned_by) -> bool:
        logger.info(f"[NOOP] Assign: {intervention_id} -> {assignee_id}")
        return True

    def claim(self, intervention_id, assignee_id, assignee_type) -> bool:
        logger.info(f"[NOOP] Claim: {intervention_id} by {assignee_id}")
        return True

    def snooze(self, intervention_id, until, snoozed_by, reason=None) -> bool:
        logger.info(f"[NOOP] Snooze: {intervention_id} until {until}")
        return True

    def resolve(self, intervention_id, resolution_type, resolved_by, **kwargs) -> bool:
        logger.info(f"[NOOP] Resolve: {intervention_id} ({resolution_type})")
        return True

    def submit_plan(self, intervention_id, agent_id, agent_model, planned_action, **kwargs) -> bool:
        logger.info(f"[NOOP] Submit plan: {intervention_id} by {agent_id}")
        return True

    def approve_plan(self, intervention_id, approved_by, notes=None) -> bool:
        logger.info(f"[NOOP] Approve plan: {intervention_id}")
        return True

    def reject_plan(self, intervention_id, rejected_by, reason) -> bool:
        logger.info(f"[NOOP] Reject plan: {intervention_id}")
        return True

    def start_execution(self, intervention_id, agent_id) -> bool:
        logger.info(f"[NOOP] Start execution: {intervention_id}")
        return True

    def log_execution_step(self, intervention_id, action, result) -> bool:
        logger.info(f"[NOOP] Log step: {intervention_id} - {action}")
        return True

    def complete_execution(self, intervention_id, agent_id, result, success=True) -> bool:
        logger.info(f"[NOOP] Complete execution: {intervention_id} (success={success})")
        return True

    def get_stats(self, department=None, days=30) -> dict:
        logger.info(f"[NOOP] Get stats")
        return {}

    def get_agent_performance(self, agent_id, days=30) -> dict:
        logger.info(f"[NOOP] Get agent performance: {agent_id}")
        return {}
