"""
Candidate Providers

Abstracts data source for candidate discovery.
Supports Odoo (real-time), BigQuery (fast), or Hybrid (BQ + Odoo verification).
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from core.clients.odoo import OdooClient
    from core.clients.bigquery import BigQueryClient

logger = logging.getLogger(__name__)


class CandidateProvider(ABC):
    """
    Abstract base for candidate providers.

    Implement this to add new data sources (e.g., cached, mock, etc.)
    """

    @abstractmethod
    def get_orders_with_qty_mismatch(
        self,
        ah_statuses: list[str],
        limit: Optional[int] = None,
        order_ids: Optional[list[int]] = None,
        days: Optional[int] = None,
        order_name_pattern: Optional[str] = None,
        exclude_product_ids: Optional[list[int]] = None,
    ) -> tuple[list[dict], dict]:
        """
        Find orders with qty mismatch.

        Returns:
            (candidates, stats): List of order dicts and discovery statistics
        """
        pass

    @abstractmethod
    def verify_line(self, line_id: int, fields: list[str]) -> Optional[dict]:
        """
        Verify/refresh a single line from source of truth.

        Returns:
            Line data dict, or None if not found
        """
        pass


class OdooCandidateProvider(CandidateProvider):
    """
    Real-time candidate discovery from Odoo.

    Use when:
    - Data freshness is critical
    - Dataset is small enough for Odoo queries
    - Need guaranteed consistency
    """

    def __init__(self, odoo: "OdooClient"):
        self.odoo = odoo

    def get_orders_with_qty_mismatch(
        self,
        ah_statuses: list[str],
        limit: Optional[int] = None,
        order_ids: Optional[list[int]] = None,
        days: Optional[int] = None,
        order_name_pattern: Optional[str] = None,
        exclude_product_ids: Optional[list[int]] = None,
    ) -> tuple[list[dict], dict]:
        """Query Odoo directly for candidates."""
        from datetime import datetime, timedelta
        from collections import defaultdict

        # Build domain
        line_domain = [
            ("order_id.ah_status", "in", ah_statuses),
            ("order_id.state", "=", "sale"),
        ]

        if exclude_product_ids:
            line_domain.append(("product_id", "not in", exclude_product_ids))
        if order_ids:
            line_domain.append(("order_id", "in", order_ids))
        if days:
            cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
            line_domain.append(("order_id.date_order", ">=", cutoff))
        if order_name_pattern:
            line_domain.append(("order_id.name", "=ilike", order_name_pattern))

        # Query
        all_lines = self.odoo.search_read(
            "sale.order.line",
            line_domain,
            fields=["id", "name", "product_id", "product_uom_qty", "qty_delivered", "order_id"],
        )

        # Filter for mismatch
        lines_by_order: dict[int, list[dict]] = defaultdict(list)
        order_names: dict[int, str] = {}

        for line in all_lines:
            if line["qty_delivered"] != line["product_uom_qty"] and line["qty_delivered"] >= 0:
                order_id, order_name = line["order_id"]
                lines_by_order[order_id].append(line)
                order_names[order_id] = order_name

        # Build result
        import random
        qualifying_orders = [
            {
                "order_id": order_id,
                "order_name": order_names[order_id],
                "mismatched_lines": lines,
            }
            for order_id, lines in lines_by_order.items()
        ]
        random.shuffle(qualifying_orders)

        total_before_limit = len(qualifying_orders)
        limit_reached = False
        if limit and len(qualifying_orders) > limit:
            qualifying_orders = qualifying_orders[:limit]
            limit_reached = True

        stats = {
            "source": "odoo",
            "lines_from_query": len(all_lines),
            "lines_with_mismatch": sum(len(o["mismatched_lines"]) for o in lines_by_order.values()),
            "orders_with_mismatch": total_before_limit,
            "limit_reached": limit_reached,
        }

        return qualifying_orders, stats

    def verify_line(self, line_id: int, fields: list[str]) -> Optional[dict]:
        """Read line directly from Odoo."""
        result = self.odoo.search_read(
            "sale.order.line",
            [("id", "=", line_id)],
            fields=fields,
        )
        return result[0] if result else None


class BigQueryCandidateProvider(CandidateProvider):
    """
    Fast candidate discovery from BigQuery.

    Use when:
    - Dataset is large (100k+ records)
    - Complex joins/aggregations needed
    - Approximate freshness is acceptable (depends on sync frequency)

    Note: BQ data may be stale. Use verify_line() before mutations.
    """

    def __init__(self, bq: "BigQueryClient", odoo: "OdooClient"):
        self.bq = bq
        self.odoo = odoo  # For verification

    def get_orders_with_qty_mismatch(
        self,
        ah_statuses: list[str],
        limit: Optional[int] = None,
        order_ids: Optional[list[int]] = None,
        days: Optional[int] = None,
        order_name_pattern: Optional[str] = None,
        exclude_product_ids: Optional[list[int]] = None,
    ) -> tuple[list[dict], dict]:
        """Query BigQuery for candidates."""
        from core.data.queries.orders import orders_with_qty_mismatch_sql

        sql = orders_with_qty_mismatch_sql(
            project=self.bq.project,
            dataset=self.bq.dataset,
            ah_statuses=ah_statuses,
            limit=limit,
            order_ids=order_ids,
            days=days,
            order_name_pattern=order_name_pattern,
            exclude_product_ids=exclude_product_ids,
        )

        logger.debug(f"BQ query: {sql}")
        rows = self.bq.query(sql)

        # Group by order
        from collections import defaultdict
        lines_by_order: dict[int, list[dict]] = defaultdict(list)
        order_names: dict[int, str] = {}

        for row in rows:
            order_id = row["order_id"]
            lines_by_order[order_id].append({
                "id": row["line_id"],
                "name": row.get("line_name", ""),
                "product_id": row.get("product_id"),
                "product_uom_qty": row["ordered_qty"],
                "qty_delivered": row["delivered_qty"],
                "order_id": (order_id, row["order_name"]),
            })
            order_names[order_id] = row["order_name"]

        qualifying_orders = [
            {
                "order_id": order_id,
                "order_name": order_names[order_id],
                "mismatched_lines": lines,
            }
            for order_id, lines in lines_by_order.items()
        ]

        stats = {
            "source": "bigquery",
            "lines_from_query": len(rows),
            "lines_with_mismatch": len(rows),
            "orders_with_mismatch": len(qualifying_orders),
            "limit_reached": limit is not None and len(rows) >= limit,
        }

        return qualifying_orders, stats

    def verify_line(self, line_id: int, fields: list[str]) -> Optional[dict]:
        """Verify against Odoo (source of truth)."""
        result = self.odoo.search_read(
            "sale.order.line",
            [("id", "=", line_id)],
            fields=fields,
        )
        return result[0] if result else None


class HybridCandidateProvider(CandidateProvider):
    """
    Best of both: BQ for speed, Odoo for accuracy.

    Flow:
    1. Query BQ for candidates (fast, handles large datasets)
    2. Verify each candidate against Odoo before returning (accurate)

    Use when:
    - Dataset is large
    - Need guaranteed accuracy before mutations
    - Can afford extra Odoo calls for verification
    """

    def __init__(self, bq: "BigQueryClient", odoo: "OdooClient", verify: bool = True):
        self.bq_provider = BigQueryCandidateProvider(bq, odoo)
        self.odoo = odoo
        self.verify = verify

    def get_orders_with_qty_mismatch(
        self,
        ah_statuses: list[str],
        limit: Optional[int] = None,
        order_ids: Optional[list[int]] = None,
        days: Optional[int] = None,
        order_name_pattern: Optional[str] = None,
        exclude_product_ids: Optional[list[int]] = None,
    ) -> tuple[list[dict], dict]:
        """Get candidates from BQ, optionally verify with Odoo."""
        # Step 1: Get candidates from BQ
        candidates, stats = self.bq_provider.get_orders_with_qty_mismatch(
            ah_statuses=ah_statuses,
            limit=limit,
            order_ids=order_ids,
            days=days,
            order_name_pattern=order_name_pattern,
            exclude_product_ids=exclude_product_ids,
        )

        if not self.verify:
            stats["source"] = "bigquery_unverified"
            return candidates, stats

        # Step 2: Verify with Odoo
        verified_candidates = []
        stale_count = 0

        for order_data in candidates:
            verified_lines = []
            for line in order_data["mismatched_lines"]:
                # Re-fetch from Odoo
                fresh = self.verify_line(
                    line["id"],
                    ["id", "name", "product_id", "product_uom_qty", "qty_delivered", "order_id"],
                )
                if fresh and fresh["qty_delivered"] != fresh["product_uom_qty"]:
                    verified_lines.append(fresh)
                else:
                    stale_count += 1

            if verified_lines:
                verified_candidates.append({
                    "order_id": order_data["order_id"],
                    "order_name": order_data["order_name"],
                    "mismatched_lines": verified_lines,
                })

        stats["source"] = "hybrid"
        stats["stale_candidates_filtered"] = stale_count
        stats["orders_after_verification"] = len(verified_candidates)

        return verified_candidates, stats

    def verify_line(self, line_id: int, fields: list[str]) -> Optional[dict]:
        """Verify against Odoo."""
        result = self.odoo.search_read(
            "sale.order.line",
            [("id", "=", line_id)],
            fields=fields,
        )
        return result[0] if result else None


def get_candidate_provider(
    source: str,
    odoo: "OdooClient",
    bq: Optional["BigQueryClient"] = None,
    verify: bool = True,
) -> CandidateProvider:
    """
    Factory to get the appropriate candidate provider.

    Args:
        source: "odoo", "bq", or "hybrid"
        odoo: Odoo client (required)
        bq: BigQuery client (required for bq/hybrid)
        verify: For hybrid, whether to verify candidates against Odoo

    Returns:
        CandidateProvider instance
    """
    if source == "odoo":
        return OdooCandidateProvider(odoo)
    elif source == "bq":
        if bq is None:
            raise ValueError("BigQuery client required for source='bq'")
        return BigQueryCandidateProvider(bq, odoo)
    elif source == "hybrid":
        if bq is None:
            raise ValueError("BigQuery client required for source='hybrid'")
        return HybridCandidateProvider(bq, odoo, verify=verify)
    else:
        raise ValueError(f"Unknown source: {source}. Use 'odoo', 'bq', or 'hybrid'")
