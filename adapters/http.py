"""
HTTP Adapter for Cloud Functions

Routes HTTP requests to the appropriate handlers.
"""

import json
import logging
from typing import Any, Callable, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Request

from core.context import RequestContext
from core.jobs import get_job, list_jobs
from core.result import JobResult

logger = logging.getLogger(__name__)

# Type alias for HTTP response
HttpResponse = Tuple[dict, int]


def handle_request(request: "Request") -> HttpResponse:
    """
    Main HTTP request router.

    Routes:
        POST /health - Health check
        POST /jobs - List available jobs
        POST /execute - Execute a job
        POST /query - Query job results (future)

    Args:
        request: Flask request object

    Returns:
        Tuple of (response_dict, status_code)
    """
    path = request.path.rstrip("/")

    # Route to appropriate handler
    routes: dict[str, Callable[["Request"], HttpResponse]] = {
        "/health": handle_health,
        "/jobs": handle_jobs,
        "/execute": handle_execute,
        "/query": handle_query,
    }

    # Also handle root path
    if path == "" or path == "/":
        return handle_health(request)

    handler = routes.get(path)
    if handler:
        return handler(request)

    return {"error": f"Unknown path: {path}", "available": list(routes.keys())}, 404


def handle_health(request: "Request") -> HttpResponse:
    """
    Health check endpoint.

    Returns:
        Health status and available jobs
    """
    jobs = list_jobs()
    return {
        "status": "healthy",
        "service": "sentinel-ops",
        "jobs_available": len(jobs),
        "jobs": [j["name"] for j in jobs],
    }, 200


def handle_jobs(request: "Request") -> HttpResponse:
    """
    List available jobs endpoint.

    Returns:
        List of registered jobs with metadata
    """
    jobs = list_jobs()
    return {
        "jobs": jobs,
        "count": len(jobs),
    }, 200


def handle_execute(request: "Request") -> HttpResponse:
    """
    Execute a job endpoint.

    Request body:
        {
            "job": "job_name",
            "params": {"key": "value"},
            "dry_run": true/false
        }

    Returns:
        Job execution result
    """
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return {"error": "Invalid JSON body"}, 400

    job_name = data.get("job")
    if not job_name:
        return {"error": "Missing 'job' field"}, 400

    # Get job class
    job_class = get_job(job_name)
    if not job_class:
        available = [j["name"] for j in list_jobs()]
        return {
            "error": f"Unknown job: {job_name}",
            "available_jobs": available,
        }, 404

    # Extract parameters
    params = data.get("params", {})
    dry_run = data.get("dry_run", False)

    # Create context
    ctx = RequestContext.for_http(
        job_name=job_name,
        dry_run=dry_run,
        user_id=data.get("user_id"),
        correlation_id=data.get("correlation_id"),
    )

    logger.info(f"Executing job: {job_name} (dry_run={dry_run})")

    try:
        # Create and execute job
        job = job_class(ctx)
        result = job.execute(**params)

        return {
            "success": True,
            "job": job_name,
            "request_id": ctx.request_id,
            "result": result.to_dict(),
        }, 200

    except Exception as e:
        logger.exception(f"Job execution failed: {job_name}")
        return {
            "success": False,
            "job": job_name,
            "request_id": ctx.request_id,
            "error": str(e),
        }, 500


def handle_query(request: "Request") -> HttpResponse:
    """
    Query endpoint for job results and metrics.

    Request body:
        {
            "query_type": "job_history" | "metrics",
            "job_name": "optional filter",
            "limit": 10
        }

    Returns:
        Query results
    """
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return {"error": "Invalid JSON body"}, 400

    query_type = data.get("query_type", "job_history")

    # For now, return a placeholder
    # Full implementation would query BigQuery
    return {
        "query_type": query_type,
        "message": "Query endpoint - implementation pending",
        "note": "Will query BigQuery for job history and metrics",
    }, 200
