"""
Sentinel-Ops Operations Module

Reusable Odoo operations as building blocks for jobs.
"""

from core.operations.base import BaseOperation
from core.operations.orders import OrderOperations
from core.operations.transfers import TransferOperations
from core.operations.documents import DocumentCreationOperations
from core.operations.pdf_generator import PDFGeneratorOperations

__all__ = [
    "BaseOperation",
    "OrderOperations",
    "TransferOperations",
    "DocumentCreationOperations",
    "PDFGeneratorOperations",
]
