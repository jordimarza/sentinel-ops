"""
Sentinel-Ops Adapters Module

Transport layer adapters for HTTP, MCP, etc.
"""

# HTTP adapter functions are imported lazily to avoid Flask dependency
# when running CLI commands that don't need HTTP

__all__ = [
    "handle_request",
    "handle_health",
    "handle_jobs",
    "handle_execute",
    "handle_query",
]


def __getattr__(name):
    """Lazy import HTTP handlers to avoid Flask dependency in CLI mode."""
    if name in __all__:
        from adapters.http import (
            handle_request,
            handle_health,
            handle_jobs,
            handle_execute,
            handle_query,
        )
        return locals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
