"""
BigQuery Queries Module

Stores SQL queries as functions for:
- Easy debugging (print the SQL)
- Version control (changes tracked in git)
- Testing (can validate SQL syntax)
- Reuse across jobs

Each query function returns a SQL string.
"""

from core.data.queries.orders import (
    orders_with_qty_mismatch_sql,
)

__all__ = [
    "orders_with_qty_mismatch_sql",
]
