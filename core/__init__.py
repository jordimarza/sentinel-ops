"""
Sentinel-Ops Core Module

Transport-agnostic core for ERP operations, monitoring, and automated remediation.
"""

from core.context import RequestContext
from core.config import Settings, get_settings
from core.result import OperationResult, JobResult

__all__ = [
    "RequestContext",
    "Settings",
    "get_settings",
    "OperationResult",
    "JobResult",
]
