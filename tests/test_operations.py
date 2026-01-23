"""
Tests for Operations Module
"""

import pytest
from unittest.mock import Mock

from core.operations.orders import OrderOperations
from core.operations.transfers import TransferOperations
from core.result import OperationResult


class TestOrderOperations:
    """Tests for OrderOperations class."""

    def test_find_partial_orders_empty(self, mock_odoo, test_context, mock_logger):
        """Test finding partial orders when none exist."""
        mock_odoo.search.return_value = []

        ops = OrderOperations(mock_odoo, test_context, mock_logger)
        result = ops.find_partial_orders_older_than(days=30)

        assert result == []
        mock_logger.info.assert_called()

    def test_find_partial_orders_with_results(
        self, mock_odoo, test_context, mock_logger, sample_order_lines
    ):
        """Test finding partial orders with results."""
        # Mock order search
        mock_odoo.search.return_value = [100, 101, 102]

        # Mock line search - return only partial lines
        mock_odoo.search_read.return_value = sample_order_lines

        ops = OrderOperations(mock_odoo, test_context, mock_logger)
        result = ops.find_partial_orders_older_than(days=30)

        # Should filter to only partial deliveries (line 1 and 2)
        assert len(result) == 2
        assert all(r["qty_delivered"] < r["product_uom_qty"] for r in result)

    def test_adjust_line_qty_dry_run(self, mock_odoo, test_context, mock_logger):
        """Test adjusting line qty in dry-run mode."""
        ops = OrderOperations(mock_odoo, test_context, mock_logger)

        line = {"id": 1, "qty_delivered": 5.0}
        result = ops.adjust_line_qty_to_delivered(line)

        # Should not actually write in dry-run
        mock_odoo.write.assert_not_called()
        assert result.success
        assert result.action == "skipped"

    def test_adjust_line_qty_live(self, mock_odoo, live_context, mock_logger):
        """Test adjusting line qty in live mode."""
        ops = OrderOperations(mock_odoo, live_context, mock_logger)

        line = {"id": 1, "qty_delivered": 5.0}
        result = ops.adjust_line_qty_to_delivered(line)

        # Should write in live mode
        mock_odoo.write.assert_called_once_with(
            "sale.order.line",
            [1],
            {"product_uom_qty": 5.0},
        )
        assert result.success

    def test_tag_order_exception_dry_run(self, mock_odoo, test_context, mock_logger):
        """Test tagging order exception in dry-run mode."""
        ops = OrderOperations(mock_odoo, test_context, mock_logger)

        result = ops.tag_order_exception(100, "Test error")

        # Should not write in dry-run
        mock_odoo.message_post.assert_not_called()
        mock_odoo.add_tag.assert_not_called()
        assert result.success


class TestTransferOperations:
    """Tests for TransferOperations class."""

    def test_has_open_moves_true(self, mock_odoo, test_context, mock_logger):
        """Test checking for open moves when they exist."""
        mock_odoo.search_count.return_value = 2

        ops = TransferOperations(mock_odoo, test_context, mock_logger)
        result = ops.has_open_moves(sale_line_id=1)

        assert result is True
        mock_odoo.search_count.assert_called_once()

    def test_has_open_moves_false(self, mock_odoo, test_context, mock_logger):
        """Test checking for open moves when none exist."""
        mock_odoo.search_count.return_value = 0

        ops = TransferOperations(mock_odoo, test_context, mock_logger)
        result = ops.has_open_moves(sale_line_id=1)

        assert result is False

    def test_has_open_moves_error_conservative(self, mock_odoo, test_context, mock_logger):
        """Test that errors default to assuming open moves exist."""
        mock_odoo.search_count.side_effect = Exception("Connection error")

        ops = TransferOperations(mock_odoo, test_context, mock_logger)
        result = ops.has_open_moves(sale_line_id=1)

        # Conservative: assume open moves on error
        assert result is True

    def test_find_stalled_pickings(self, mock_odoo, test_context, mock_logger):
        """Test finding stalled pickings."""
        mock_odoo.search_read.return_value = [
            {"id": 1, "name": "PICK001", "state": "waiting"},
            {"id": 2, "name": "PICK002", "state": "confirmed"},
        ]

        ops = TransferOperations(mock_odoo, test_context, mock_logger)
        result = ops.find_stalled_pickings(days_waiting=14)

        assert len(result) == 2
        mock_odoo.search_read.assert_called_once()
