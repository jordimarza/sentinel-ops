"""
Tests for PDF Generator Operations

Tests the packing list PDF generation functionality.
"""

import pytest
from unittest.mock import Mock, patch

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.context import RequestContext
from core.operations.pdf_generator import PDFGeneratorOperations


@pytest.fixture
def pdf_context():
    """Create a context for PDF operations."""
    return RequestContext(
        request_id="pdf-test-123",
        job_name="test_pdf_generator",
        triggered_by="test",
        dry_run=False,
    )


@pytest.fixture
def dry_run_context():
    """Create a dry-run context."""
    return RequestContext(
        request_id="pdf-dry-run-123",
        job_name="test_pdf_generator",
        triggered_by="test",
        dry_run=True,
    )


@pytest.fixture
def sample_packing_rows():
    """Sample packing list CSV data."""
    return [
        {
            "PickingId": "PL_12345",
            "SalesOrder": "SO67890",
            "CustomerId": "C001",
            "CustomerName": "ACME Corporation",
            "BoxId": "BOX001",
            "BoxSize": "Large",
            "BoxWeight": "12.5",
            "BoxVolume": "0.08",
            "EAN": "8401234567890",
            "SKU": "SHOE-001",
            "Size": "42",
            "Description": "Leather Oxford Shoes - Black",
            "Qty": "2",
            "ContentWeight": "1.2",
        },
        {
            "PickingId": "PL_12345",
            "SalesOrder": "SO67890",
            "CustomerId": "C001",
            "CustomerName": "ACME Corporation",
            "BoxId": "BOX001",
            "BoxSize": "Large",
            "BoxWeight": "12.5",
            "BoxVolume": "0.08",
            "EAN": "8401234567891",
            "SKU": "SHOE-002",
            "Size": "38",
            "Description": "Canvas Sneakers - White",
            "Qty": "1",
            "ContentWeight": "0.8",
        },
        {
            "PickingId": "PL_12345",
            "SalesOrder": "SO67890",
            "CustomerId": "C001",
            "CustomerName": "ACME Corporation",
            "BoxId": "BOX002",
            "BoxSize": "Medium",
            "BoxWeight": "8.2",
            "BoxVolume": "0.05",
            "EAN": "8401234567892",
            "SKU": "SHOE-003",
            "Size": "40",
            "Description": "Running Shoes - Blue with extra long description that should be truncated",
            "Qty": "3",
            "ContentWeight": "1.5",
        },
    ]


class TestPDFGeneratorOperations:
    """Test PDF generation operations."""

    def test_generate_pdf_success(self, pdf_context, sample_packing_rows, mock_logger):
        """Test successful PDF generation."""
        ops = PDFGeneratorOperations(pdf_context, mock_logger)

        pdf_bytes, result = ops.generate_packing_list_pdf(
            rows=sample_packing_rows,
            customer_name="ACME Corporation",
            sales_order="SO67890",
            picking_id="PL_12345",
            date="2025-01-30",
        )

        # Should generate PDF bytes
        assert len(pdf_bytes) > 0
        assert result.success
        assert result.action == "generate_pdf"

        # Check result data
        assert result.data["picking_id"] == "PL_12345"
        assert result.data["sales_order"] == "SO67890"
        assert result.data["customer_name"] == "ACME Corporation"
        assert result.data["box_count"] == 2  # BOX001 and BOX002
        assert result.data["item_count"] == 6  # 2 + 1 + 3
        assert result.data["pdf_size"] > 0

    def test_generate_pdf_dry_run(self, dry_run_context, sample_packing_rows, mock_logger):
        """Test PDF generation in dry-run mode."""
        ops = PDFGeneratorOperations(dry_run_context, mock_logger)

        pdf_bytes, result = ops.generate_packing_list_pdf(
            rows=sample_packing_rows,
            customer_name="ACME Corporation",
            sales_order="SO67890",
            picking_id="PL_12345",
            date="2025-01-30",
        )

        # Should not generate PDF in dry-run
        assert pdf_bytes == b""
        assert result.success
        assert result.action == "skipped"
        assert "Dry run" in result.message

    def test_generate_pdf_empty_rows(self, pdf_context, mock_logger):
        """Test PDF generation with empty rows."""
        ops = PDFGeneratorOperations(pdf_context, mock_logger)

        pdf_bytes, result = ops.generate_packing_list_pdf(
            rows=[],
            customer_name="Test Customer",
            sales_order="SO00001",
            picking_id="PL_00001",
            date="2025-01-30",
        )

        # Should still generate a PDF (just empty)
        assert len(pdf_bytes) > 0
        assert result.success

    def test_generate_pdf_single_box(self, pdf_context, mock_logger):
        """Test PDF generation with a single box."""
        rows = [
            {
                "PickingId": "PL_99999",
                "SalesOrder": "SO99999",
                "CustomerName": "Single Box Customer",
                "BoxId": "SINGLE",
                "BoxSize": "Small",
                "BoxWeight": "2.0",
                "BoxVolume": "0.01",
                "EAN": "1234567890123",
                "SKU": "ITEM-001",
                "Size": "M",
                "Description": "Test Item",
                "Qty": "1",
                "ContentWeight": "0.5",
            },
        ]

        ops = PDFGeneratorOperations(pdf_context, mock_logger)

        pdf_bytes, result = ops.generate_packing_list_pdf(
            rows=rows,
            customer_name="Single Box Customer",
            sales_order="SO99999",
            picking_id="PL_99999",
            date="2025-01-30",
        )

        assert len(pdf_bytes) > 0
        assert result.success
        assert result.data["box_count"] == 1
        assert result.data["item_count"] == 1

    def test_generate_pdf_handles_missing_fields(self, pdf_context, mock_logger):
        """Test PDF generation with missing optional fields."""
        rows = [
            {
                "PickingId": "PL_MINIMAL",
                "SalesOrder": "SO_MINIMAL",
                "CustomerName": "Minimal Customer",
                "BoxId": "BOX",
                # Missing: BoxSize, BoxWeight, BoxVolume
                "SKU": "SKU-001",
                "Description": "Basic Item",
                "Qty": "5",
                # Missing: EAN, Size, ContentWeight
            },
        ]

        ops = PDFGeneratorOperations(pdf_context, mock_logger)

        pdf_bytes, result = ops.generate_packing_list_pdf(
            rows=rows,
            customer_name="Minimal Customer",
            sales_order="SO_MINIMAL",
            picking_id="PL_MINIMAL",
            date="2025-01-30",
        )

        # Should handle missing fields gracefully
        assert len(pdf_bytes) > 0
        assert result.success

    def test_pdf_starts_with_pdf_header(self, pdf_context, sample_packing_rows, mock_logger):
        """Test that generated PDF has valid PDF header."""
        ops = PDFGeneratorOperations(pdf_context, mock_logger)

        pdf_bytes, result = ops.generate_packing_list_pdf(
            rows=sample_packing_rows,
            customer_name="Test",
            sales_order="SO1",
            picking_id="PL1",
            date="2025-01-30",
        )

        # PDF files start with %PDF-
        assert pdf_bytes[:5] == b"%PDF-"


class TestFTPClient:
    """Test FTP client operations."""

    def test_ftp_client_context_manager(self):
        """Test FTP client can be used as context manager."""
        from core.clients.ftp import NoOpFTPClient

        client = NoOpFTPClient()
        with client:
            # Should not raise
            files = client.list_files("/test")
            assert files == []

    def test_noop_client_returns_empty(self):
        """Test NoOp client returns empty results."""
        from core.clients.ftp import NoOpFTPClient

        client = NoOpFTPClient()
        assert client.list_files("/test") == []
        assert client.list_directories("/test") == []
        assert client.download("/test/file.txt") == b""
        assert client.upload("/test/file.txt", b"data") is True
        assert client.file_exists("/test/file.txt") is False


class TestPackingListJob:
    """Test the packing list PDF generation job."""

    def test_job_registration(self):
        """Test job is registered correctly."""
        from core.jobs import get_job, list_jobs

        job_class = get_job("generate_packing_list_pdfs")
        assert job_class is not None

        jobs = list_jobs()
        job_names = [j["name"] for j in jobs]
        assert "generate_packing_list_pdfs" in job_names

    def test_job_tags(self):
        """Test job has correct tags."""
        from core.jobs import list_jobs

        jobs = list_jobs()
        job = next(j for j in jobs if j["name"] == "generate_packing_list_pdfs")

        assert "ftp" in job["tags"]
        assert "pdf" in job["tags"]
        assert "packing_list" in job["tags"]
