"""
Transfer Operations

Operations related to stock transfers and moves.
"""

import logging
from typing import Optional

from core.operations.base import BaseOperation
from core.result import OperationResult

logger = logging.getLogger(__name__)


class TransferOperations(BaseOperation):
    """
    Operations for stock transfers and moves.

    Provides:
    - Checking for open stock moves
    - Finding pending transfers
    """

    MOVE_MODEL = "stock.move"
    PICKING_MODEL = "stock.picking"

    def has_open_moves(
        self,
        sale_line_id: int,
    ) -> bool:
        """
        Check if a sale order line has any open (unprocessed) stock moves.

        Open moves are those that haven't been completed:
        - state not in ('done', 'cancel')

        Args:
            sale_line_id: Sale order line ID

        Returns:
            True if there are open moves, False otherwise
        """
        try:
            count = self.odoo.search_count(
                self.MOVE_MODEL,
                [
                    ("sale_line_id", "=", sale_line_id),
                    ("state", "not in", ["done", "cancel"]),
                ],
            )

            has_open = count > 0
            self.log.debug(
                f"Line {sale_line_id} has {count} open moves",
            )
            return has_open

        except Exception as e:
            self.log.error(
                f"Failed to check open moves for line {sale_line_id}",
                error=str(e),
            )
            # Conservative: assume there are open moves if we can't check
            return True

    def get_moves_for_line(
        self,
        sale_line_id: int,
        states: Optional[list[str]] = None,
        fields: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Get stock moves for a sale order line.

        Args:
            sale_line_id: Sale order line ID
            states: Filter by states (None for all)
            fields: Fields to retrieve

        Returns:
            List of stock move dicts
        """
        if fields is None:
            fields = [
                "id",
                "name",
                "state",
                "product_id",
                "product_uom_qty",
                "quantity_done",
                "picking_id",
            ]

        domain = [("sale_line_id", "=", sale_line_id)]

        if states:
            domain.append(("state", "in", states))

        try:
            return self.odoo.search_read(
                self.MOVE_MODEL,
                domain,
                fields=fields,
            )
        except Exception as e:
            self.log.error(
                f"Failed to get moves for line {sale_line_id}",
                error=str(e),
            )
            return []

    def find_stalled_pickings(
        self,
        days_waiting: int = 14,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """
        Find pickings (transfers) that have been waiting too long.

        Criteria:
        - State is 'waiting' or 'confirmed'
        - Scheduled date is older than X days

        Args:
            days_waiting: Number of days to consider stalled
            limit: Maximum number of records to return

        Returns:
            List of picking dicts
        """
        from datetime import datetime, timedelta

        cutoff_date = datetime.utcnow() - timedelta(days=days_waiting)
        cutoff_str = cutoff_date.strftime("%Y-%m-%d")

        domain = [
            ("state", "in", ["waiting", "confirmed"]),
            ("scheduled_date", "<", cutoff_str),
        ]

        fields = [
            "id",
            "name",
            "state",
            "scheduled_date",
            "partner_id",
            "origin",
            "move_ids_without_package",
        ]

        kwargs = {}
        if limit:
            kwargs["limit"] = limit

        try:
            pickings = self.odoo.search_read(
                self.PICKING_MODEL,
                domain,
                fields=fields,
                order="scheduled_date asc",
                **kwargs,
            )

            self.log.info(
                f"Found {len(pickings)} stalled pickings older than {days_waiting} days",
            )

            return pickings

        except Exception as e:
            self.log.error(
                "Failed to find stalled pickings",
                error=str(e),
            )
            return []

    def get_picking_details(
        self,
        picking_id: int,
        include_moves: bool = True,
    ) -> Optional[dict]:
        """
        Get detailed information about a picking.

        Args:
            picking_id: Stock picking ID
            include_moves: Whether to include related moves

        Returns:
            Picking dict with details, or None if not found
        """
        fields = [
            "id",
            "name",
            "state",
            "scheduled_date",
            "date_done",
            "partner_id",
            "origin",
            "picking_type_id",
            "location_id",
            "location_dest_id",
        ]

        try:
            pickings = self.odoo.read(self.PICKING_MODEL, [picking_id], fields)

            if not pickings:
                return None

            picking = pickings[0]

            if include_moves:
                picking["moves"] = self.odoo.search_read(
                    self.MOVE_MODEL,
                    [("picking_id", "=", picking_id)],
                    fields=["id", "product_id", "product_uom_qty", "quantity_done", "state"],
                )

            return picking

        except Exception as e:
            self.log.error(
                f"Failed to get picking details for {picking_id}",
                error=str(e),
            )
            return None
