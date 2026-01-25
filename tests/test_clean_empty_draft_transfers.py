"""
Tests for Clean Empty Draft Transfers Job
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from core.jobs import get_job, JOB_REGISTRY
from core.jobs.clean_empty_draft_transfers import CleanEmptyDraftTransfersJob
from core.operations.transfers import TransferOperations
from core.result import JobResult, ResultStatus, OperationResult


# --- Fixtures ---


@pytest.fixture
def sample_empty_draft_picking():
    """Sample empty draft picking data."""
    return {
        "id": 12345,
        "name": "WH/OUT/00001",
        "state": "draft",
        "move_ids": [],
    }


@pytest.fixture
def sample_draft_picking_with_moves():
    """Sample draft picking that has moves (should be skipped)."""
    return {
        "id": 12346,
        "name": "WH/OUT/00002",
        "state": "draft",
        "move_ids": [1, 2, 3],
    }


@pytest.fixture
def sample_non_draft_picking():
    """Sample picking not in draft state (should be skipped)."""
    return {
        "id": 12347,
        "name": "WH/OUT/00003",
        "state": "confirmed",
        "move_ids": [],
    }


@pytest.fixture
def bq_query_results():
    """Sample BQ query results for empty draft pickings."""
    return [
        {
            "picking_id": 12345,
            "picking_name": "WH/OUT/00001",
            "picking_type_id": 2,
            "scheduled_date": "2025-01-01",
            "create_date": "2024-12-01",
            "sale_id": 455346,
            "origin": "S00455346",
        },
        {
            "picking_id": 12348,
            "picking_name": "WH/OUT/00004",
            "picking_type_id": 2,
            "scheduled_date": "2025-01-02",
            "create_date": "2024-12-02",
            "sale_id": 455347,
            "origin": "S00455347",
        },
    ]


# --- Job Registry Tests ---


class TestCleanEmptyDraftTransfersRegistry:
    """Test job registration and metadata."""

    def test_job_registered(self):
        """Test that job is properly registered."""
        job_class = get_job("clean_empty_draft_transfers")
        assert job_class == CleanEmptyDraftTransfersJob

    def test_job_metadata(self):
        """Test job has correct metadata."""
        job_class = JOB_REGISTRY["clean_empty_draft_transfers"]
        assert job_class._job_name == "clean_empty_draft_transfers"
        assert "transfers" in job_class._job_tags
        assert "cleanup" in job_class._job_tags


# --- TransferOperations Chatter Tests ---


class TestTransferOperationsChatter:
    """Test chatter message methods."""

    def test_post_picking_cancelled_message_dry_run(
        self, mock_odoo, mock_bq, test_context, mock_logger
    ):
        """Test chatter message is skipped in dry run."""
        ops = TransferOperations(mock_odoo, test_context, mock_logger)

        result = ops.post_picking_cancelled_message(
            picking_id=12345,
            picking_name="WH/OUT/00001",
            reason="Test reason",
        )

        assert result.success
        mock_odoo.message_post.assert_not_called()

    def test_post_picking_cancelled_message_live(
        self, mock_odoo, mock_bq, live_context, mock_logger
    ):
        """Test chatter message is posted in live mode."""
        ops = TransferOperations(mock_odoo, live_context, mock_logger)

        result = ops.post_picking_cancelled_message(
            picking_id=12345,
            picking_name="WH/OUT/00001",
            reason="Test reason",
            job_name="test_job",
        )

        assert result.success
        mock_odoo.message_post.assert_called_once()
        call_args = mock_odoo.message_post.call_args
        assert call_args[0][0] == "stock.picking"
        assert call_args[0][1] == 12345
        assert "Test reason" in call_args[0][2]
        assert "test_job" in call_args[0][2]

    def test_post_picking_deleted_message_live(
        self, mock_odoo, mock_bq, live_context, mock_logger
    ):
        """Test delete message is posted before deletion."""
        ops = TransferOperations(mock_odoo, live_context, mock_logger)

        result = ops.post_picking_deleted_message(
            picking_id=12345,
            picking_name="WH/OUT/00001",
            reason="Empty draft",
        )

        assert result.success
        mock_odoo.message_post.assert_called_once()
        call_args = mock_odoo.message_post.call_args
        assert "Deleted" in call_args[0][2]


# --- Job Verification Tests ---


class TestCleanEmptyDraftTransfersVerification:
    """Test picking verification logic."""

    def test_verify_empty_draft_valid(
        self, mock_odoo, mock_bq, test_context, mock_logger, sample_empty_draft_picking
    ):
        """Test verification passes for empty draft picking."""
        mock_odoo.search_read.return_value = [sample_empty_draft_picking]

        # Pass mocked clients to job
        job = CleanEmptyDraftTransfersJob(test_context, odoo=mock_odoo, bq=mock_bq, log=mock_logger)
        result = job._verify_empty_draft(12345)

        assert result is not None
        assert result["id"] == 12345
        assert result["state"] == "draft"

    def test_verify_empty_draft_has_moves(
        self, mock_odoo, mock_bq, test_context, mock_logger, sample_draft_picking_with_moves
    ):
        """Test verification fails for picking with moves."""
        mock_odoo.search_read.return_value = [sample_draft_picking_with_moves]

        job = CleanEmptyDraftTransfersJob(test_context, odoo=mock_odoo, bq=mock_bq, log=mock_logger)
        result = job._verify_empty_draft(12346)

        assert result is None

    def test_verify_empty_draft_not_draft_state(
        self, mock_odoo, mock_bq, test_context, mock_logger, sample_non_draft_picking
    ):
        """Test verification fails for non-draft picking."""
        mock_odoo.search_read.return_value = [sample_non_draft_picking]

        job = CleanEmptyDraftTransfersJob(test_context, odoo=mock_odoo, bq=mock_bq, log=mock_logger)
        result = job._verify_empty_draft(12347)

        assert result is None

    def test_verify_empty_draft_not_found(
        self, mock_odoo, mock_bq, test_context, mock_logger
    ):
        """Test verification fails for non-existent picking."""
        mock_odoo.search_read.return_value = []

        job = CleanEmptyDraftTransfersJob(test_context, odoo=mock_odoo, bq=mock_bq, log=mock_logger)
        result = job._verify_empty_draft(99999)

        assert result is None


# --- Job Execution Tests ---


class TestCleanEmptyDraftTransfersJob:
    """Test job execution."""

    def test_job_dry_run_no_pickings(
        self, mock_odoo, mock_bq, test_context, mock_logger
    ):
        """Test dry run with no pickings found."""
        mock_bq.query.return_value = []

        job = CleanEmptyDraftTransfersJob(test_context, odoo=mock_odoo, bq=mock_bq, log=mock_logger)
        result = job.execute()

        assert result.status == ResultStatus.DRY_RUN
        assert result.records_checked == 0
        assert result.records_updated == 0

    def test_job_dry_run_with_pickings_from_bq(
        self, mock_odoo, mock_bq, test_context, mock_logger, bq_query_results, sample_empty_draft_picking
    ):
        """Test dry run discovers pickings from BQ."""
        mock_bq.query.return_value = bq_query_results

        job = CleanEmptyDraftTransfersJob(test_context, odoo=mock_odoo, bq=mock_bq, log=mock_logger)
        result = job.execute(limit=10)

        assert result.status == ResultStatus.DRY_RUN
        assert result.kpis["pickings_found"] == 2

    def test_job_dry_run_with_explicit_ids(
        self, mock_odoo, mock_bq, test_context, mock_logger, sample_empty_draft_picking
    ):
        """Test dry run with explicit picking IDs."""
        mock_odoo.search_read.return_value = [sample_empty_draft_picking]

        job = CleanEmptyDraftTransfersJob(test_context, odoo=mock_odoo, bq=mock_bq, log=mock_logger)
        result = job.execute(picking_ids=[12345])

        assert result.status == ResultStatus.DRY_RUN
        # Should skip BQ query when explicit IDs provided
        mock_bq.query.assert_not_called()

    def test_job_live_cancel_picking(
        self, mock_odoo, mock_bq, live_context, mock_logger, sample_empty_draft_picking
    ):
        """Test live execution cancels picking."""
        mock_odoo.search_read.return_value = [sample_empty_draft_picking]
        mock_odoo.call.return_value = True
        mock_odoo.message_post.return_value = True

        job = CleanEmptyDraftTransfersJob(live_context, odoo=mock_odoo, bq=mock_bq, log=mock_logger)
        result = job.execute(picking_ids=[12345])

        assert result.status == ResultStatus.SUCCESS
        assert result.records_updated == 1
        assert result.kpis["pickings_cancelled"] == 1

        # Verify action_cancel was called
        mock_odoo.call.assert_called_once_with(
            "stock.picking", "action_cancel", [12345]
        )

        # Verify chatter message was posted
        mock_odoo.message_post.assert_called_once()

    def test_job_live_delete_picking(
        self, mock_odoo, mock_bq, live_context, mock_logger, sample_empty_draft_picking
    ):
        """Test live execution with delete option."""
        mock_odoo.search_read.return_value = [sample_empty_draft_picking]
        mock_odoo.unlink.return_value = True
        mock_odoo.message_post.return_value = True

        job = CleanEmptyDraftTransfersJob(live_context, odoo=mock_odoo, bq=mock_bq, log=mock_logger)
        result = job.execute(picking_ids=[12345], delete_instead_of_cancel=True)

        assert result.status == ResultStatus.SUCCESS
        assert result.records_updated == 1
        assert result.kpis["pickings_deleted"] == 1

        # Verify unlink was called
        mock_odoo.unlink.assert_called_once_with("stock.picking", [12345])

        # Verify chatter message was posted before delete
        mock_odoo.message_post.assert_called_once()

    def test_job_discover_from_odoo(
        self, mock_odoo, mock_bq, test_context, mock_logger, sample_empty_draft_picking
    ):
        """Test discovery from Odoo instead of BQ."""
        # First call: discover draft pickings
        mock_odoo.search_read.return_value = [sample_empty_draft_picking]

        job = CleanEmptyDraftTransfersJob(test_context, odoo=mock_odoo, bq=mock_bq, log=mock_logger)
        result = job.execute(discover_from_odoo=True, limit=10)

        assert result.status == ResultStatus.DRY_RUN
        # BQ should not be called
        mock_bq.query.assert_not_called()

    def test_job_kpis_format(
        self, mock_odoo, mock_bq, test_context, mock_logger, sample_empty_draft_picking
    ):
        """Test KPIs have expected format."""
        mock_odoo.search_read.return_value = [sample_empty_draft_picking]

        job = CleanEmptyDraftTransfersJob(test_context, odoo=mock_odoo, bq=mock_bq, log=mock_logger)
        result = job.execute(picking_ids=[12345])

        assert "pickings_found" in result.kpis
        assert "pickings_cancelled" in result.kpis
        assert "pickings_deleted" in result.kpis
        assert "pickings_cleaned" in result.kpis
        assert "exceptions" in result.kpis

    def test_job_handles_cancel_error(
        self, mock_odoo, mock_bq, live_context, mock_logger, sample_empty_draft_picking
    ):
        """Test job handles cancel errors gracefully."""
        mock_odoo.search_read.return_value = [sample_empty_draft_picking]
        mock_odoo.message_post.return_value = True
        mock_odoo.call.side_effect = Exception("Cancel failed")

        job = CleanEmptyDraftTransfersJob(live_context, odoo=mock_odoo, bq=mock_bq, log=mock_logger)
        result = job.execute(picking_ids=[12345])

        # Job should complete but with errors
        assert result.records_updated == 0
        assert len(result.errors) > 0
        assert result.kpis["exceptions"] > 0

    def test_job_with_limit(
        self, mock_odoo, mock_bq, test_context, mock_logger, bq_query_results, sample_empty_draft_picking
    ):
        """Test limit parameter is respected."""
        mock_bq.query.return_value = bq_query_results[:1]  # Only 1 result
        mock_odoo.search_read.return_value = [sample_empty_draft_picking]

        job = CleanEmptyDraftTransfersJob(test_context, odoo=mock_odoo, bq=mock_bq, log=mock_logger)
        result = job.execute(limit=1)

        # Should only process 1 picking
        assert result.kpis["pickings_found"] == 1
