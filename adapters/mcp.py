"""
MCP (Model Context Protocol) Adapter - Placeholder

Future: Expose sentinel-ops jobs as MCP tools for AI agents.
"""

# Placeholder for MCP adapter implementation
# This will allow AI agents to:
# - List available jobs
# - Execute jobs with parameters
# - Query job results

# Example MCP tool definition:
# {
#     "name": "sentinel_execute_job",
#     "description": "Execute a sentinel-ops job",
#     "parameters": {
#         "job": {"type": "string", "description": "Job name"},
#         "params": {"type": "object", "description": "Job parameters"},
#         "dry_run": {"type": "boolean", "description": "Dry run mode"}
#     }
# }


def get_mcp_tools() -> list[dict]:
    """
    Get MCP tool definitions for sentinel-ops.

    Returns:
        List of MCP tool definitions
    """
    from core.jobs import list_jobs

    tools = [
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
    ]

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

    return tools
