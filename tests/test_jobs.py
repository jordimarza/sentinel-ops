"""
Tests for Jobs Module
"""

import pytest
from unittest.mock import Mock, patch

from core.jobs import get_job, list_jobs, JOB_REGISTRY
from core.jobs.clean_old_orders import CleanOldOrdersJob
from core.result import JobResult, ResultStatus


class TestJobRegistry:
    """Tests for job registry functionality."""

    def test_job_registered(self):
        """Test that clean_old_orders job is registered."""
        job_class = get_job("clean_old_orders")
        assert job_class is not None
        assert job_class == CleanOldOrdersJob

    def test_list_jobs(self):
        """Test listing all jobs."""
        jobs = list_jobs()

        assert len(jobs) >= 1
        job_names = [j["name"] for j in jobs]
        assert "clean_old_orders" in job_names

    def test_job_metadata(self):
        """Test job metadata from decorator."""
        assert CleanOldOrdersJob._job_name == "clean_old_orders"
        assert "cleanup" in CleanOldOrdersJob._job_tags

    def test_unknown_job(self):
        """Test getting an unknown job."""
        job_class = get_job("nonexistent_job")
        assert job_class is None


class TestCleanOldOrdersJob:
    """Tests for CleanOldOrdersJob."""

    def test_job_dry_run_no_records(
        self, mock_odoo, mock_bq, mock_alerter, mock_logger, test_context
    ):
        """Test job execution with no records found."""
        mock_odoo.search.return_value = []

        job = CleanOldOrdersJob(
            ctx=test_context,
            odoo=mock_odoo,
            bq=mock_bq,
            alerter=mock_alerter,
            log=mock_logger,
        )

        result = job.run(days=30)

        # In dry-run mode, status is DRY_RUN even if no records found
        assert result.status == ResultStatus.DRY_RUN
        assert result.records_checked == 0
        assert result.records_updated == 0

    def test_job_dry_run_with_records(
        self,
        mock_odoo,
        mock_bq,
        mock_alerter,
        mock_logger,
        test_context,
        sample_order_lines,
    ):
        """Test job execution with records in dry-run mode."""
        # Setup mocks
        mock_odoo.search.return_value = [100, 101]
        mock_odoo.search_read.return_value = sample_order_lines[:2]
        mock_odoo.search_count.return_value = 0  # No open moves

        job = CleanOldOrdersJob(
            ctx=test_context,
            odoo=mock_odoo,
            bq=mock_bq,
            alerter=mock_alerter,
            log=mock_logger,
        )

        result = job.run(days=30)

        # In dry-run, records should be skipped, not updated
        assert result.dry_run is True
        # Note: actual behavior depends on implementation details
        result.complete()
        assert result.status in [ResultStatus.DRY_RUN, ResultStatus.SUCCESS]

    def test_job_live_execution(
        self,
        mock_odoo,
        mock_bq,
        mock_alerter,
        mock_logger,
        live_context,
        sample_order_lines,
    ):
        """Test job execution in live mode."""
        # Setup mocks
        mock_odoo.search.return_value = [100, 101]
        mock_odoo.search_read.return_value = sample_order_lines[:2]
        mock_odoo.search_count.return_value = 0  # No open moves

        job = CleanOldOrdersJob(
            ctx=live_context,
            odoo=mock_odoo,
            bq=mock_bq,
            alerter=mock_alerter,
            log=mock_logger,
        )

        result = job.run(days=30)
        result.complete()

        # In live mode, records should be updated
        assert result.dry_run is False
        assert mock_odoo.write.called

    def test_job_skips_lines_with_open_moves(
        self,
        mock_odoo,
        mock_bq,
        mock_alerter,
        mock_logger,
        live_context,
        sample_order_lines,
    ):
        """Test that job skips lines with open stock moves."""
        # Setup mocks
        mock_odoo.search.return_value = [100, 101]
        mock_odoo.search_read.return_value = sample_order_lines[:2]
        # First line has open moves, second doesn't
        mock_odoo.search_count.side_effect = [1, 0]

        job = CleanOldOrdersJob(
            ctx=live_context,
            odoo=mock_odoo,
            bq=mock_bq,
            alerter=mock_alerter,
            log=mock_logger,
        )

        result = job.run(days=30)
        result.complete()

        # Should skip first line, process second
        assert result.records_skipped >= 1

    def test_job_kpis_format(
        self,
        mock_odoo,
        mock_bq,
        mock_alerter,
        mock_logger,
        live_context,  # Use live context for this test
    ):
        """Test that job returns KPIs in expected format."""
        mock_odoo.search.return_value = []

        job = CleanOldOrdersJob(
            ctx=live_context,
            odoo=mock_odoo,
            bq=mock_bq,
            alerter=mock_alerter,
            log=mock_logger,
        )

        result = job.run(days=30)

        # Check KPI format matches original
        assert "lines_checked" in result.kpis
        assert "lines_updated" in result.kpis
        assert "exceptions" in result.kpis


class TestJobResult:
    """Tests for JobResult functionality."""

    def test_create_result(self):
        """Test creating a new job result."""
        result = JobResult.create("test_job", dry_run=True)

        assert result.job_name == "test_job"
        assert result.dry_run is True
        assert result.records_checked == 0
        assert result.started_at is not None

    def test_add_operation(self):
        """Test adding operations to result."""
        from core.result import OperationResult

        result = JobResult.create("test_job")

        # Add successful operation
        result.add_operation(OperationResult.ok(1, "model", "action"))
        assert result.records_checked == 1
        assert result.records_updated == 1

        # Add skipped operation
        result.add_operation(OperationResult.skipped(2, "model", "reason"))
        assert result.records_checked == 2
        assert result.records_skipped == 1

        # Add failed operation
        result.add_operation(OperationResult.fail(3, "model", "action", "error"))
        assert result.records_checked == 3
        assert len(result.errors) == 1

    def test_complete_sets_status(self):
        """Test that complete() sets appropriate status."""
        # Success case
        result = JobResult.create("test")
        result.records_checked = 10
        result.records_updated = 10
        result.complete()
        assert result.status == ResultStatus.SUCCESS

        # Partial case
        result = JobResult.create("test")
        result.records_checked = 10
        result.records_updated = 5
        result.errors.append("Some error")
        result.complete()
        assert result.status == ResultStatus.PARTIAL

        # Failure case
        result = JobResult.create("test")
        result.errors.append("Error")
        result.complete()
        assert result.status == ResultStatus.FAILURE

        # Dry run case
        result = JobResult.create("test", dry_run=True)
        result.records_checked = 10
        result.complete()
        assert result.status == ResultStatus.DRY_RUN
