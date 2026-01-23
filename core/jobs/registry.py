"""
Job Registry

Decorator-based job registration system.
"""

import logging
from typing import Callable, Optional, Type, TYPE_CHECKING

if TYPE_CHECKING:
    from core.jobs.base import BaseJob

logger = logging.getLogger(__name__)

# Global job registry
JOB_REGISTRY: dict[str, Type["BaseJob"]] = {}


def register_job(
    name: Optional[str] = None,
    description: str = "",
    tags: Optional[list[str]] = None,
):
    """
    Decorator to register a job class.

    Usage:
        @register_job(name="clean_old_orders", description="Clean up old partial orders")
        class CleanOldOrdersJob(BaseJob):
            ...

    Args:
        name: Job name (defaults to class name in snake_case)
        description: Human-readable description
        tags: Optional tags for categorization

    Returns:
        Decorator function
    """
    def decorator(cls: Type["BaseJob"]) -> Type["BaseJob"]:
        job_name = name or _to_snake_case(cls.__name__)

        # Store metadata on the class
        cls._job_name = job_name
        cls._job_description = description
        cls._job_tags = tags or []

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


def list_jobs() -> list[dict]:
    """
    List all registered jobs.

    Returns:
        List of job info dicts with name, description, tags
    """
    jobs = []
    for name, cls in sorted(JOB_REGISTRY.items()):
        jobs.append({
            "name": name,
            "description": getattr(cls, "_job_description", ""),
            "tags": getattr(cls, "_job_tags", []),
        })
    return jobs


def _to_snake_case(name: str) -> str:
    """Convert CamelCase to snake_case."""
    import re
    # Remove 'Job' suffix if present
    if name.endswith("Job"):
        name = name[:-3]
    # Convert to snake_case
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()
