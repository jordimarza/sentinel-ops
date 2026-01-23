"""
Pytest Fixtures for Sentinel-Ops Tests

Provides mock clients and test utilities.
"""

import pytest
from unittest.mock import Mock, MagicMock
from datetime import datetime

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.context import RequestContext
from core.result import JobResult, OperationResult, ResultStatus


@pytest.fixture
def mock_odoo():
    """Create a mock Odoo client."""
    client = Mock()

    # Mock authentication
    client.authenticate.return_value = 1
    client.uid = 1

    # Mock version
    client.version.return_value = {
        "server_version": "16.0",
        "server_version_info": [16, 0, 0, "final", 0],
    }

    # Mock search_read - returns empty by default
    client.search_read.return_value = []

    # Mock search - returns empty by default
    client.search.return_value = []

    # Mock search_count
    client.search_count.return_value = 0

    # Mock write - returns True
    client.write.return_value = True

    # Mock create - returns ID
    client.create.return_value = 100

    # Mock message_post - returns message ID
    client.message_post.return_value = 200

    # Mock add_tag - returns True
    client.add_tag.return_value = True

    return client


@pytest.fixture
def mock_bq():
    """Create a mock BigQuery client."""
    client = Mock()

    # Mock insert operations
    client.log_audit.return_value = True
    client.write_kpis.return_value = True
    client.query.return_value = []
    client.ensure_tables.return_value = None

    return client


@pytest.fixture
def mock_alerter():
    """Create a mock Slack alerter."""
    alerter = Mock()

    alerter.alert_job_failed.return_value = True
    alerter.alert_job_completed.return_value = True
    alerter.alert_custom.return_value = True

    return alerter


@pytest.fixture
def mock_logger():
    """Create a mock SentinelLogger."""
    logger = Mock()

    logger.info.return_value = None
    logger.debug.return_value = None
    logger.warning.return_value = None
    logger.error.return_value = None
    logger.success.return_value = None
    logger.skip.return_value = None
    logger.job_started.return_value = None
    logger.job_completed.return_value = None
    logger.job_failed.return_value = None

    return logger


@pytest.fixture
def test_context():
    """Create a test RequestContext."""
    return RequestContext(
        request_id="test-request-123",
        job_name="test_job",
        triggered_by="test",
        dry_run=True,
    )


@pytest.fixture
def live_context():
    """Create a live (non-dry-run) RequestContext."""
    return RequestContext(
        request_id="live-request-456",
        job_name="live_job",
        triggered_by="test",
        dry_run=False,
    )


@pytest.fixture
def sample_order_lines():
    """Sample order line data for testing."""
    return [
        {
            "id": 1,
            "order_id": (100, "SO001"),
            "product_id": (10, "Product A"),
            "product_uom_qty": 10.0,
            "qty_delivered": 5.0,
            "name": "Product A",
        },
        {
            "id": 2,
            "order_id": (101, "SO002"),
            "product_id": (11, "Product B"),
            "product_uom_qty": 20.0,
            "qty_delivered": 15.0,
            "name": "Product B",
        },
        {
            "id": 3,
            "order_id": (102, "SO003"),
            "product_id": (12, "Product C"),
            "product_uom_qty": 5.0,
            "qty_delivered": 5.0,  # Fully delivered
            "name": "Product C",
        },
    ]


@pytest.fixture
def sample_stock_moves():
    """Sample stock move data for testing."""
    return [
        {
            "id": 1001,
            "name": "Move 1",
            "state": "waiting",
            "product_id": (10, "Product A"),
            "product_uom_qty": 5.0,
            "quantity_done": 0.0,
            "sale_line_id": 1,
        },
        {
            "id": 1002,
            "name": "Move 2",
            "state": "done",
            "product_id": (11, "Product B"),
            "product_uom_qty": 15.0,
            "quantity_done": 15.0,
            "sale_line_id": 2,
        },
    ]
