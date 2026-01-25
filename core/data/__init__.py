"""
Data Layer

Provides abstracted data access with support for multiple sources:
- Odoo (real-time, source of truth)
- BigQuery (fast queries, analytical)
- Hybrid (BQ for candidates, Odoo for verification)

Usage:
    from core.data import get_candidate_provider

    # In job or operation
    provider = get_candidate_provider(source="hybrid", odoo=self.odoo, bq=self.bq)
    candidates = provider.get_orders_with_qty_mismatch(...)

    # Candidates come from BQ (fast), then verified against Odoo (accurate)
"""

from core.data.providers import (
    CandidateProvider,
    OdooCandidateProvider,
    BigQueryCandidateProvider,
    HybridCandidateProvider,
    get_candidate_provider,
)

__all__ = [
    "CandidateProvider",
    "OdooCandidateProvider",
    "BigQueryCandidateProvider",
    "HybridCandidateProvider",
    "get_candidate_provider",
]
