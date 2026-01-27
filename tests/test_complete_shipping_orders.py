"""
Tests for Complete Shipping Only Orders Job
"""

import pytest
from unittest.mock import Mock, patch

from core.jobs import get_job
from core.jobs.complete_shipping_only_orders import (
    CompleteShippingOnlyOrdersJob,
    DEFAULT_SHIPPING_PRODUCT_IDS,
)
from core.operations.orders import OrderOperations
from core.result import JobResult, ResultStatus, OperationResult


# --- Fixtures ---


@pytest.fixture
def shipping_product_ids():
    """Test shipping product IDs."""
    return [15743, 23506]


@pytest.fixture
def sample_order_with_shipping_only():
    """
    Sample data for an order where only shipping is pending.
    - Product lines: fully delivered
    - Shipping line: pending delivery
    """
    return {
        "order_id": 455346,
        "order_name": "S00455346",
        "all_lines": [
            {
                "id": 1001,
                "order_id": (455346, "S00455346"),
                "product_id": (100, "ALOHAS Sandal"),
                "product_uom_qty": 1.0,
                "qty_delivered": 1.0,  # Fully delivered
                "name": "ALOHAS Sandal",
            },
            {
                "id": 1002,
                "order_id": (455346, "S00455346"),
                "product_id": (15743, "Shipping Fee"),
                "product_uom_qty": 1.0,
                "qty_delivered": 0.0,  # Pending
                "name": "Shipping Fee",
            },
        ],
        "pending_shipping_lines": [
            {
                "id": 1002,
                "order_id": (455346, "S00455346"),
                "product_id": (15743, "Shipping Fee"),
                "product_uom_qty": 1.0,
                "qty_delivered": 0.0,
                "name": "Shipping Fee",
            }
        ],
    }


@pytest.fixture
def sample_order_with_mixed_pending():
    """
    Sample data for an order with both shipping and product pending.
    Should NOT qualify.
    """
    return {
        "order_id": 455347,
        "order_name": "S00455347",
        "all_lines": [
            {
                "id": 2001,
                "order_id": (455347, "S00455347"),
                "product_id": (100, "ALOHAS Sandal"),
                "product_uom_qty": 2.0,
                "qty_delivered": 1.0,  # Partially delivered
                "name": "ALOHAS Sandal",
            },
            {
                "id": 2002,
                "order_id": (455347, "S00455347"),
                "product_id": (15743, "Shipping Fee"),
                "product_uom_qty": 1.0,
                "qty_delivered": 0.0,  # Pending
                "name": "Shipping Fee",
            },
        ],
    }


# --- Job Registry Tests ---


class TestCompleteShippingOnlyOrdersRegistry:
    """Tests for job registration."""

    def test_job_registered(self):
        """Test that the job is registered."""
        job_class = get_job("complete_shipping_only_orders")
        assert job_class is not None
        assert job_class == CompleteShippingOnlyOrdersJob

    def test_job_metadata(self):
        """Test job metadata from decorator."""
        assert CompleteShippingOnlyOrdersJob._job_name == "complete_shipping_only_orders"
        assert "shipping" in CompleteShippingOnlyOrdersJob._job_tags
        assert "sales" in CompleteShippingOnlyOrdersJob._job_tags

    def test_default_shipping_product_ids(self):
        """Test that default shipping product IDs are defined."""
        assert len(DEFAULT_SHIPPING_PRODUCT_IDS) > 0
        assert 15743 in DEFAULT_SHIPPING_PRODUCT_IDS


# --- OrderOperations Tests for Shipping ---


class TestOrderOperationsFindShippingOnly:
    """Tests for find_orders_with_only_shipping_pending operation."""

    def test_find_no_pending_shipping(
        self, mock_odoo, test_context, mock_logger, shipping_product_ids
    ):
        """Test when no pending shipping lines exist."""
        mock_odoo.search_read.return_value = []

        ops = OrderOperations(mock_odoo, test_context, mock_logger)
        result = ops.find_orders_with_only_shipping_pending(
            shipping_product_ids=shipping_product_ids
        )

        assert result == []

    def test_find_orders_with_only_shipping_pending(
        self,
        mock_odoo,
        test_context,
        mock_logger,
        shipping_product_ids,
        sample_order_with_shipping_only,
    ):
        """Test finding orders where only shipping is pending."""
        # First search_read returns pending shipping lines
        # Second search_read returns non-shipping lines (empty or all completed)
        mock_odoo.search_read.side_effect = [
            sample_order_with_shipping_only["pending_shipping_lines"],  # shipping lines
            [],  # non-shipping lines (none, so no pending)
        ]
        # Return order details
        mock_odoo.read.return_value = [
            {"id": 455346, "name": "S00455346"}
        ]

        ops = OrderOperations(mock_odoo, test_context, mock_logger)
        result = ops.find_orders_with_only_shipping_pending(
            shipping_product_ids=shipping_product_ids
        )

        assert len(result) == 1
        assert result[0]["order_id"] == 455346
        assert result[0]["order_name"] == "S00455346"
        assert len(result[0]["pending_shipping_lines"]) == 1

    def test_filter_orders_with_non_shipping_pending(
        self,
        mock_odoo,
        test_context,
        mock_logger,
        shipping_product_ids,
        sample_order_with_shipping_only,
    ):
        """Test that orders with non-shipping pending are excluded."""
        # First search_read returns pending shipping lines
        # Second search_read returns non-shipping line with pending qty
        mock_odoo.search_read.side_effect = [
            sample_order_with_shipping_only["pending_shipping_lines"],  # shipping lines
            [{"id": 999, "product_uom_qty": 2.0, "qty_delivered": 1.0}],  # pending non-shipping
        ]

        ops = OrderOperations(mock_odoo, test_context, mock_logger)
        result = ops.find_orders_with_only_shipping_pending(
            shipping_product_ids=shipping_product_ids
        )

        # Order should be excluded
        assert result == []

    def test_find_with_specific_order_ids(
        self, mock_odoo, test_context, mock_logger, shipping_product_ids
    ):
        """Test finding with specific order IDs filter."""
        mock_odoo.search_read.return_value = []

        ops = OrderOperations(mock_odoo, test_context, mock_logger)
        ops.find_orders_with_only_shipping_pending(
            shipping_product_ids=shipping_product_ids,
            order_ids=[455346],
        )

        # Verify the order_ids filter was applied
        call_args = mock_odoo.search_read.call_args
        domain = call_args[0][1]
        assert any(
            item == ("order_id", "in", [455346])
            for item in domain
            if isinstance(item, tuple)
        )


class TestOrderOperationsCompleteShippingLine:
    """Tests for complete_shipping_line operation."""

    def test_complete_shipping_line_dry_run(
        self, mock_odoo, test_context, mock_logger
    ):
        """Test completing shipping line in dry-run mode."""
        ops = OrderOperations(mock_odoo, test_context, mock_logger)

        line = {"id": 1002, "product_uom_qty": 1.0, "qty_delivered": 0.0}
        result = ops.complete_shipping_line(line)

        # Should not write in dry-run
        mock_odoo.write.assert_not_called()
        assert result.success
        assert result.action == "skipped"

    def test_complete_shipping_line_live(
        self, mock_odoo, live_context, mock_logger
    ):
        """Test completing shipping line in live mode."""
        ops = OrderOperations(mock_odoo, live_context, mock_logger)

        line = {"id": 1002, "product_uom_qty": 1.0, "qty_delivered": 0.0}
        result = ops.complete_shipping_line(line)

        # Should write in live mode with tracking disabled
        mock_odoo.write.assert_called_once_with(
            "sale.order.line",
            [1002],
            {"qty_delivered": 1.0},
            context={
                "tracking_disable": True,
                "mail_notrack": True,
                "mail_create_nolog": True,
                "mail_auto_subscribe_no_notify": True,
            },
        )
        assert result.success


class TestOrderOperationsPostMessage:
    """Tests for post_shipping_completion_message operation."""

    def test_post_message_dry_run(
        self, mock_odoo, test_context, mock_logger
    ):
        """Test posting message in dry-run mode."""
        ops = OrderOperations(mock_odoo, test_context, mock_logger)

        result = ops.post_shipping_completion_message(
            order_id=455346,
            order_name="S00455346",
            lines_completed=1,
        )

        # Should not post in dry-run
        mock_odoo.message_post.assert_not_called()
        assert result.success

    def test_post_message_live(
        self, mock_odoo, live_context, mock_logger
    ):
        """Test posting message in live mode."""
        ops = OrderOperations(mock_odoo, live_context, mock_logger)

        result = ops.post_shipping_completion_message(
            order_id=455346,
            order_name="S00455346",
            lines_completed=2,
        )

        # Should post in live mode
        mock_odoo.message_post.assert_called_once()
        call_args = mock_odoo.message_post.call_args
        assert call_args[0][0] == "sale.order"
        assert call_args[0][1] == 455346
        # Check message contains key info
        body = call_args[0][2]
        assert "S00455346" in body
        assert "2 shipping line" in body
        assert result.success


# --- Job Tests ---


class TestCompleteShippingOnlyOrdersJob:
    """Tests for CompleteShippingOnlyOrdersJob."""

    def test_job_dry_run_no_orders(
        self, mock_odoo, mock_bq, mock_alerter, mock_logger, test_context
    ):
        """Test job execution with no orders found."""
        mock_odoo.search_read.return_value = []

        job = CompleteShippingOnlyOrdersJob(
            ctx=test_context,
            odoo=mock_odoo,
            bq=mock_bq,
            alerter=mock_alerter,
            log=mock_logger,
        )

        result = job.run()

        assert result.status == ResultStatus.DRY_RUN
        assert result.records_checked == 0
        assert result.kpis["orders_checked"] == 0
        assert result.kpis["orders_completed"] == 0
        assert result.kpis["lines_completed"] == 0

    def test_job_dry_run_with_qualifying_order(
        self,
        mock_odoo,
        mock_bq,
        mock_alerter,
        mock_logger,
        test_context,
        sample_order_with_shipping_only,
    ):
        """Test job execution with qualifying orders in dry-run mode."""
        # Setup mocks - search_read is called twice:
        # 1. Find shipping lines
        # 2. Find non-shipping lines for the order
        pending_shipping = sample_order_with_shipping_only["pending_shipping_lines"]
        mock_odoo.search_read.side_effect = [
            pending_shipping,  # shipping lines
            [],  # non-shipping lines (empty = all completed)
        ]
        mock_odoo.read.return_value = [{"id": 455346, "name": "S00455346"}]

        job = CompleteShippingOnlyOrdersJob(
            ctx=test_context,
            odoo=mock_odoo,
            bq=mock_bq,
            alerter=mock_alerter,
            log=mock_logger,
        )

        result = job.run()

        # Should find and process orders in dry-run
        assert result.dry_run is True
        assert result.status == ResultStatus.DRY_RUN
        # In dry-run, operations are skipped but counted
        assert result.kpis["orders_checked"] >= 1

    def test_job_live_execution(
        self,
        mock_odoo,
        mock_bq,
        mock_alerter,
        mock_logger,
        live_context,
        sample_order_with_shipping_only,
    ):
        """Test job execution in live mode."""
        # Setup mocks - search_read is called twice:
        # 1. Find shipping lines
        # 2. Find non-shipping lines for the order
        pending_shipping = sample_order_with_shipping_only["pending_shipping_lines"]
        mock_odoo.search_read.side_effect = [
            pending_shipping,  # shipping lines
            [],  # non-shipping lines (empty = all completed)
        ]
        mock_odoo.read.return_value = [{"id": 455346, "name": "S00455346"}]

        job = CompleteShippingOnlyOrdersJob(
            ctx=live_context,
            odoo=mock_odoo,
            bq=mock_bq,
            alerter=mock_alerter,
            log=mock_logger,
        )

        result = job.run()

        # Should complete shipping lines
        assert result.dry_run is False
        mock_odoo.write.assert_called()
        mock_odoo.message_post.assert_called()
        assert result.kpis["orders_completed"] >= 1
        assert result.kpis["lines_completed"] >= 1

    def test_job_with_specific_order_ids(
        self, mock_odoo, mock_bq, mock_alerter, mock_logger, test_context
    ):
        """Test job with specific order_ids parameter."""
        mock_odoo.search_read.return_value = []

        job = CompleteShippingOnlyOrdersJob(
            ctx=test_context,
            odoo=mock_odoo,
            bq=mock_bq,
            alerter=mock_alerter,
            log=mock_logger,
        )

        result = job.run(order_ids=[455346])

        # Verify order_ids was passed through
        mock_logger.info.assert_called()

    def test_job_with_custom_shipping_products(
        self, mock_odoo, mock_bq, mock_alerter, mock_logger, test_context
    ):
        """Test job with custom shipping product IDs."""
        mock_odoo.search_read.return_value = []

        job = CompleteShippingOnlyOrdersJob(
            ctx=test_context,
            odoo=mock_odoo,
            bq=mock_bq,
            alerter=mock_alerter,
            log=mock_logger,
        )

        custom_ids = [99999, 88888]
        result = job.run(shipping_product_ids=custom_ids)

        # Just verify it doesn't error
        assert result.status == ResultStatus.DRY_RUN

    def test_job_with_limit(
        self,
        mock_odoo,
        mock_bq,
        mock_alerter,
        mock_logger,
        test_context,
        sample_order_with_shipping_only,
    ):
        """Test job with limit parameter."""
        # Setup mocks - return multiple pending shipping lines
        pending_shipping = sample_order_with_shipping_only["pending_shipping_lines"]
        mock_odoo.search_read.return_value = pending_shipping * 5  # 5 copies
        mock_odoo.search_count.return_value = 0
        mock_odoo.read.return_value = [{"id": 455346, "name": "S00455346"}]

        job = CompleteShippingOnlyOrdersJob(
            ctx=test_context,
            odoo=mock_odoo,
            bq=mock_bq,
            alerter=mock_alerter,
            log=mock_logger,
        )

        result = job.run(limit=2)

        # Should still work with limit
        assert result.status == ResultStatus.DRY_RUN

    def test_job_kpis_format(
        self, mock_odoo, mock_bq, mock_alerter, mock_logger, live_context
    ):
        """Test that job returns KPIs in expected format."""
        mock_odoo.search_read.return_value = []

        job = CompleteShippingOnlyOrdersJob(
            ctx=live_context,
            odoo=mock_odoo,
            bq=mock_bq,
            alerter=mock_alerter,
            log=mock_logger,
        )

        result = job.run()

        # Check KPI format
        assert "orders_checked" in result.kpis
        assert "orders_completed" in result.kpis
        assert "lines_completed" in result.kpis
        assert "exceptions" in result.kpis

    def test_job_handles_operation_error(
        self,
        mock_odoo,
        mock_bq,
        mock_alerter,
        mock_logger,
        live_context,
        sample_order_with_shipping_only,
    ):
        """Test job handles errors during shipping line completion."""
        # Setup mocks - search_read is called twice:
        # 1. Find shipping lines
        # 2. Find non-shipping lines for the order
        pending_shipping = sample_order_with_shipping_only["pending_shipping_lines"]
        mock_odoo.search_read.side_effect = [
            pending_shipping,  # shipping lines
            [],  # non-shipping lines (empty = all completed)
        ]
        mock_odoo.read.return_value = [{"id": 455346, "name": "S00455346"}]
        # Make write fail
        mock_odoo.write.side_effect = Exception("Write failed")

        job = CompleteShippingOnlyOrdersJob(
            ctx=live_context,
            odoo=mock_odoo,
            bq=mock_bq,
            alerter=mock_alerter,
            log=mock_logger,
        )

        result = job.run()

        # Should handle error gracefully - errors go into operation results
        assert len(result.operations) > 0
        assert not result.operations[0].success
        assert result.operations[0].error is not None

    def test_job_handles_search_error(
        self, mock_odoo, mock_bq, mock_alerter, mock_logger, test_context
    ):
        """Test job handles search errors."""
        mock_odoo.search_read.side_effect = Exception("Search failed")

        job = CompleteShippingOnlyOrdersJob(
            ctx=test_context,
            odoo=mock_odoo,
            bq=mock_bq,
            alerter=mock_alerter,
            log=mock_logger,
        )

        result = job.run()

        # Should handle error and complete
        assert "Search failed" in result.errors[0]
        assert result.status == ResultStatus.DRY_RUN  # dry_run overrides failure
