"""
MCP (Model Context Protocol) Adapter

Exposes sentinel-ops capabilities as MCP tools for AI agents.
Tools are organized into categories:
- Job tools: List and execute jobs
- Task tools: Query, claim, plan, and resolve intervention tasks
"""

from typing import Any, Optional


def get_mcp_tools() -> list[dict]:
    """
    Get MCP tool definitions for sentinel-ops.

    Returns:
        List of MCP tool definitions
    """
    from core.jobs import list_jobs

    tools = []

    # =========================================================================
    # Job Tools
    # =========================================================================
    tools.extend([
        {
            "name": "sentinel_list_jobs",
            "description": "List available sentinel-ops jobs",
            "parameters": {},
        },
        {
            "name": "sentinel_execute_job",
            "description": "Execute a sentinel-ops job",
            "parameters": {
                "job": {
                    "type": "string",
                    "description": "Job name to execute",
                    "required": True,
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Run in dry-run mode (no mutations)",
                    "default": True,
                },
                "params": {
                    "type": "object",
                    "description": "Job-specific parameters",
                },
            },
        },
    ])

    # Add job-specific tools
    for job in list_jobs():
        tools.append({
            "name": f"sentinel_job_{job['name']}",
            "description": job.get("description", f"Execute {job['name']} job"),
            "parameters": {
                "dry_run": {
                    "type": "boolean",
                    "description": "Run in dry-run mode",
                    "default": True,
                },
            },
        })

    # =========================================================================
    # Task Query Tools
    # =========================================================================
    tools.extend([
        {
            "name": "task_get_available",
            "description": "Get intervention tasks available for pickup (open, unassigned). "
                          "Filter by capabilities to find tasks this agent can handle.",
            "parameters": {
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task types this agent can handle (e.g., ['qty_mismatch', 'delivery_exception'])",
                },
                "department": {
                    "type": "string",
                    "description": "Filter by department (operations, finance, customer_service)",
                },
                "max_tasks": {
                    "type": "integer",
                    "description": "Maximum number of tasks to return",
                    "default": 10,
                },
            },
        },
        {
            "name": "task_get",
            "description": "Get full details of a specific task by ID",
            "parameters": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID to retrieve",
                    "required": True,
                },
            },
        },
        {
            "name": "task_get_my_tasks",
            "description": "Get tasks assigned to this agent",
            "parameters": {
                "assignee_id": {
                    "type": "string",
                    "description": "Agent ID",
                    "required": True,
                },
            },
        },
        {
            "name": "task_get_pending_approvals",
            "description": "Get AI plans awaiting human approval",
            "parameters": {
                "department": {
                    "type": "string",
                    "description": "Filter by department",
                },
            },
        },
    ])

    # =========================================================================
    # Task Claim & Assignment Tools
    # =========================================================================
    tools.extend([
        {
            "name": "task_claim",
            "description": "Atomically claim an unassigned task. Returns false if already claimed.",
            "parameters": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID to claim",
                    "required": True,
                },
                "assignee_id": {
                    "type": "string",
                    "description": "Agent ID claiming the task",
                    "required": True,
                },
                "assignee_type": {
                    "type": "string",
                    "description": "Type of assignee (ai_agent or human)",
                    "enum": ["ai_agent", "human"],
                    "default": "ai_agent",
                },
            },
        },
        {
            "name": "task_update_status",
            "description": "Update task status",
            "parameters": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID",
                    "required": True,
                },
                "status": {
                    "type": "string",
                    "description": "New status",
                    "enum": ["open", "assigned", "planning", "awaiting_approval",
                            "executing", "in_progress", "blocked", "resolved",
                            "closed", "failed", "escalated", "snoozed"],
                    "required": True,
                },
                "updated_by": {
                    "type": "string",
                    "description": "Agent or user ID making the update",
                    "required": True,
                },
                "notes": {
                    "type": "string",
                    "description": "Optional notes about the update",
                },
            },
        },
    ])

    # =========================================================================
    # AI Planning Tools (Plan-Execute Pattern)
    # =========================================================================
    tools.extend([
        {
            "name": "task_submit_plan",
            "description": "Submit a remediation plan for a task. If requires_approval is true, "
                          "the plan will await human approval before execution.",
            "parameters": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID",
                    "required": True,
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID submitting the plan",
                    "required": True,
                },
                "agent_model": {
                    "type": "string",
                    "description": "Model/version of the agent (e.g., 'claude-opus-4')",
                    "required": True,
                },
                "planned_action": {
                    "type": "object",
                    "description": "Structured plan: {action: string, params: object}",
                    "required": True,
                },
                "plan_reasoning": {
                    "type": "string",
                    "description": "Explanation of why this plan was chosen",
                    "required": True,
                },
                "plan_confidence": {
                    "type": "number",
                    "description": "Confidence score 0.0-1.0",
                    "required": True,
                },
                "requires_approval": {
                    "type": "boolean",
                    "description": "Whether human approval is required. Auto-determined if not specified.",
                },
                "plan_alternatives": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Alternative plans considered",
                },
            },
        },
        {
            "name": "task_approve_plan",
            "description": "Approve an AI agent's plan (human use only)",
            "parameters": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID",
                    "required": True,
                },
                "approved_by": {
                    "type": "string",
                    "description": "Human approver ID/email",
                    "required": True,
                },
                "notes": {
                    "type": "string",
                    "description": "Approval notes",
                },
            },
        },
        {
            "name": "task_reject_plan",
            "description": "Reject an AI agent's plan, sending back for replanning",
            "parameters": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID",
                    "required": True,
                },
                "rejected_by": {
                    "type": "string",
                    "description": "Human rejector ID/email",
                    "required": True,
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for rejection",
                    "required": True,
                },
            },
        },
    ])

    # =========================================================================
    # Execution Tools
    # =========================================================================
    tools.extend([
        {
            "name": "task_start_execution",
            "description": "Mark that execution has started for a task",
            "parameters": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID",
                    "required": True,
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID executing the task",
                    "required": True,
                },
            },
        },
        {
            "name": "task_log_step",
            "description": "Log an execution step",
            "parameters": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID",
                    "required": True,
                },
                "action": {
                    "type": "string",
                    "description": "Action performed",
                    "required": True,
                },
                "result": {
                    "type": "object",
                    "description": "Result of the action",
                    "required": True,
                },
            },
        },
        {
            "name": "task_complete_execution",
            "description": "Mark execution as completed (success or failure)",
            "parameters": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID",
                    "required": True,
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID",
                    "required": True,
                },
                "result": {
                    "type": "object",
                    "description": "Execution result data",
                    "required": True,
                },
                "success": {
                    "type": "boolean",
                    "description": "Whether execution was successful",
                    "default": True,
                },
            },
        },
    ])

    # =========================================================================
    # Resolution Tools
    # =========================================================================
    tools.extend([
        {
            "name": "task_resolve",
            "description": "Mark a task as resolved",
            "parameters": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID",
                    "required": True,
                },
                "resolution_type": {
                    "type": "string",
                    "description": "How the task was resolved",
                    "enum": ["fixed", "escalated", "wont_fix", "duplicate"],
                    "required": True,
                },
                "resolved_by": {
                    "type": "string",
                    "description": "Agent or user ID",
                    "required": True,
                },
                "resolution_notes": {
                    "type": "string",
                    "description": "Notes about the resolution",
                },
                "resolution_data": {
                    "type": "object",
                    "description": "Additional resolution data",
                },
            },
        },
        {
            "name": "task_snooze",
            "description": "Snooze a task until a specific date",
            "parameters": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID",
                    "required": True,
                },
                "until": {
                    "type": "string",
                    "description": "ISO timestamp to snooze until",
                    "required": True,
                },
                "snoozed_by": {
                    "type": "string",
                    "description": "Agent or user ID",
                    "required": True,
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for snoozing",
                },
            },
        },
    ])

    # =========================================================================
    # Stats Tools
    # =========================================================================
    tools.extend([
        {
            "name": "task_get_stats",
            "description": "Get task statistics for monitoring and dashboards",
            "parameters": {
                "department": {
                    "type": "string",
                    "description": "Filter by department",
                },
                "days": {
                    "type": "integer",
                    "description": "Number of days to include",
                    "default": 30,
                },
            },
        },
        {
            "name": "task_get_agent_performance",
            "description": "Get performance stats for an AI agent",
            "parameters": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID",
                    "required": True,
                },
                "days": {
                    "type": "integer",
                    "description": "Number of days to include",
                    "default": 30,
                },
            },
        },
    ])

    return tools


def handle_mcp_call(tool_name: str, params: dict) -> dict:
    """
    Handle an MCP tool call.

    Args:
        tool_name: Name of the tool being called
        params: Tool parameters

    Returns:
        Tool result as a dict
    """
    from core.clients.bigquery import get_bigquery_client
    from core.context import RequestContext
    from core.jobs import get_job, execute_job

    bq = get_bigquery_client()

    # Job tools
    if tool_name == "sentinel_list_jobs":
        from core.jobs import list_jobs
        return {"jobs": list_jobs()}

    if tool_name == "sentinel_execute_job":
        job_name = params["job"]
        dry_run = params.get("dry_run", True)
        job_params = params.get("params", {})
        ctx = RequestContext.for_mcp(job_name, dry_run=dry_run)
        result = execute_job(job_name, ctx, **job_params)
        return result.to_dict()

    if tool_name.startswith("sentinel_job_"):
        job_name = tool_name.replace("sentinel_job_", "")
        dry_run = params.get("dry_run", True)
        ctx = RequestContext.for_mcp(job_name, dry_run=dry_run)
        result = execute_job(job_name, ctx)
        return result.to_dict()

    # Task query tools
    if tool_name == "task_get_available":
        capabilities = params.get("capabilities")
        if capabilities:
            return {"tasks": bq.get_tasks_for_agent(capabilities, params.get("max_tasks", 10))}
        else:
            return {"tasks": bq.get_available_tasks(
                department=params.get("department"),
                limit=params.get("max_tasks", 10),
            )}

    if tool_name == "task_get":
        task = bq.get_task(params["task_id"])
        return {"task": task}

    if tool_name == "task_get_my_tasks":
        tasks = bq.get_my_tasks(params["assignee_id"])
        return {"tasks": tasks}

    if tool_name == "task_get_pending_approvals":
        tasks = bq.get_pending_approvals(params.get("department"))
        return {"tasks": tasks}

    # Task claim & assignment
    if tool_name == "task_claim":
        success = bq.claim_task(
            params["task_id"],
            params["assignee_id"],
            params.get("assignee_type", "ai_agent"),
        )
        return {"success": success}

    if tool_name == "task_update_status":
        success = bq.update_task_status(
            params["task_id"],
            params["status"],
            params["updated_by"],
            params.get("notes"),
        )
        return {"success": success}

    # AI planning tools
    if tool_name == "task_submit_plan":
        success = bq.submit_plan(
            task_id=params["task_id"],
            agent_id=params["agent_id"],
            agent_model=params["agent_model"],
            planned_action=params["planned_action"],
            plan_reasoning=params["plan_reasoning"],
            plan_confidence=params["plan_confidence"],
            requires_approval=params.get("requires_approval"),
            plan_alternatives=params.get("plan_alternatives"),
        )
        return {"success": success}

    if tool_name == "task_approve_plan":
        success = bq.approve_plan(
            params["task_id"],
            params["approved_by"],
            params.get("notes"),
        )
        return {"success": success}

    if tool_name == "task_reject_plan":
        success = bq.reject_plan(
            params["task_id"],
            params["rejected_by"],
            params["reason"],
        )
        return {"success": success}

    # Execution tools
    if tool_name == "task_start_execution":
        success = bq.start_execution(params["task_id"], params["agent_id"])
        return {"success": success}

    if tool_name == "task_log_step":
        success = bq.log_execution_step(
            params["task_id"],
            params["action"],
            params["result"],
        )
        return {"success": success}

    if tool_name == "task_complete_execution":
        success = bq.complete_execution(
            params["task_id"],
            params["agent_id"],
            params["result"],
            params.get("success", True),
        )
        return {"success": success}

    # Resolution tools
    if tool_name == "task_resolve":
        success = bq.resolve_task(
            task_id=params["task_id"],
            resolution_type=params["resolution_type"],
            resolved_by=params["resolved_by"],
            resolution_notes=params.get("resolution_notes"),
            resolution_data=params.get("resolution_data"),
        )
        return {"success": success}

    if tool_name == "task_snooze":
        success = bq.snooze_task(
            params["task_id"],
            params["until"],
            params["snoozed_by"],
            params.get("reason"),
        )
        return {"success": success}

    # Stats tools
    if tool_name == "task_get_stats":
        stats = bq.get_task_stats(
            params.get("department"),
            params.get("days", 30),
        )
        return {"stats": stats}

    if tool_name == "task_get_agent_performance":
        stats = bq.get_agent_performance(
            params["agent_id"],
            params.get("days", 30),
        )
        return {"stats": stats}

    raise ValueError(f"Unknown tool: {tool_name}")
