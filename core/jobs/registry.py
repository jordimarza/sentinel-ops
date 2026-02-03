"""
Job Registry

Decorator-based job registration system with AI-friendly metadata.
"""

import logging
from typing import Optional, Type, TYPE_CHECKING

if TYPE_CHECKING:
    from core.jobs.base import BaseJob
    from core.models import JobCapabilities

logger = logging.getLogger(__name__)

# Global job registry
JOB_REGISTRY: dict[str, Type["BaseJob"]] = {}


def register_job(
    name: Optional[str] = None,
    description: str = "",
    tags: Optional[list[str]] = None,
    capabilities: Optional["JobCapabilities"] = None,
    notify_on_success: bool = True,
):
    """
    Decorator to register a job class.

    Usage:
        @register_job(
            name="clean_old_orders",
            description="Clean up old partial orders",
            tags=["orders", "cleanup"],
            capabilities=JobCapabilities(
                capabilities=[Capability.MODIFY_ORDERS],
                models_write=["sale.order.line"],
                risk_level=RiskLevel.MEDIUM,
            ),
            notify_on_success=True,  # Set False for frequent/hourly jobs
        )
        class CleanOldOrdersJob(BaseJob):
            ...

    Args:
        name: Job name (defaults to class name in snake_case)
        description: Human-readable description
        tags: Optional tags for categorization
        capabilities: Structured metadata for AI discovery
        notify_on_success: Send Slack alert on success (default True, set False for hourly jobs)

    Returns:
        Decorator function
    """
    def decorator(cls: Type["BaseJob"]) -> Type["BaseJob"]:
        job_name = name or _to_snake_case(cls.__name__)

        # Store metadata on the class
        cls._job_name = job_name
        cls._job_description = description
        cls._job_tags = tags or []
        cls._job_capabilities = capabilities
        cls._notify_on_success = notify_on_success

        # Only register if not already registered (prevents warning on __main__ reimport)
        if job_name not in JOB_REGISTRY:
            JOB_REGISTRY[job_name] = cls
            logger.debug(f"Registered job: {job_name}")

        return cls

    return decorator


def get_job(name: str) -> Optional[Type["BaseJob"]]:
    """
    Get a job class by name.

    Args:
        name: Job name

    Returns:
        Job class or None if not found
    """
    return JOB_REGISTRY.get(name)


def list_jobs(
    tags: Optional[list[str]] = None,
    capability: Optional[str] = None,
    include_capabilities: bool = False,
) -> list[dict]:
    """
    List all registered jobs with optional filtering.

    Args:
        tags: Filter by tags (any match)
        capability: Filter by capability
        include_capabilities: Include full capabilities in output

    Returns:
        List of job info dicts
    """
    jobs = []
    for name, cls in sorted(JOB_REGISTRY.items()):
        job_tags = getattr(cls, "_job_tags", [])
        job_caps = getattr(cls, "_job_capabilities", None)

        # Filter by tags
        if tags and not any(t in job_tags for t in tags):
            continue

        # Filter by capability
        if capability and job_caps:
            cap_values = [c.value for c in job_caps.capabilities]
            if capability not in cap_values:
                continue

        job_info = {
            "name": name,
            "description": getattr(cls, "_job_description", ""),
            "tags": job_tags,
        }

        if include_capabilities and job_caps:
            job_info["capabilities"] = job_caps.to_dict()

        jobs.append(job_info)

    return jobs


def find_jobs_for_intent(intent_keywords: list[str]) -> list[dict]:
    """
    Find jobs that might match an intent (for AI discovery).

    Args:
        intent_keywords: Keywords from the intent description

    Returns:
        List of matching jobs with relevance scores
    """
    matches = []
    keywords_lower = [k.lower() for k in intent_keywords]

    for name, cls in JOB_REGISTRY.items():
        score = 0
        desc = getattr(cls, "_job_description", "").lower()
        tags = [t.lower() for t in getattr(cls, "_job_tags", [])]

        # Score based on keyword matches
        for kw in keywords_lower:
            if kw in name.lower():
                score += 3
            if kw in desc:
                score += 2
            if any(kw in t for t in tags):
                score += 1

        if score > 0:
            job_caps = getattr(cls, "_job_capabilities", None)
            matches.append({
                "name": name,
                "description": getattr(cls, "_job_description", ""),
                "relevance_score": score,
                "risk_level": job_caps.risk_level.value if job_caps else "unknown",
                "capabilities": job_caps.to_dict() if job_caps else None,
            })

    # Sort by relevance
    matches.sort(key=lambda x: x["relevance_score"], reverse=True)
    return matches


def _to_snake_case(name: str) -> str:
    """Convert CamelCase to snake_case."""
    import re
    # Remove 'Job' suffix if present
    if name.endswith("Job"):
        name = name[:-3]
    # Convert to snake_case
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()
