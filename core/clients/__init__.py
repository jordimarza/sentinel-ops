"""
Sentinel-Ops Clients Module

External service clients for Odoo and BigQuery.
"""

from core.clients.odoo import OdooClient, get_odoo_client
from core.clients.bigquery import BigQueryClient, get_bigquery_client

__all__ = [
    "OdooClient",
    "get_odoo_client",
    "BigQueryClient",
    "get_bigquery_client",
]
