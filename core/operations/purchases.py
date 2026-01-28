"""
Purchase Order Operations

Operations related to purchase orders and PO picking synchronization.
"""

from datetime import datetime
from typing import Optional

from core.operations.base import BaseOperation
from core.result import OperationResult


class PurchaseOperations(BaseOperation):
    """
    Operations for purchase orders and related pickings.

    Handles:
    - Picking date synchronization to PO date_planned
    - Line-level move date synchronization
    """

    # Models
    PO_MODEL = "purchase.order"
    PO_LINE_MODEL = "purchase.order.line"
    PICKING_MODEL = "stock.picking"
    MOVE_MODEL = "stock.move"

    # Open picking states (not done or cancelled)
    OPEN_PICKING_STATES = ["draft", "waiting", "confirmed", "assigned"]

    def get_open_pickings_for_po(
        self,
        po_id: int,
    ) -> list[dict]:
        """
        Get open pickings (not done/cancel) for a purchase order.

        Args:
            po_id: Purchase order ID

        Returns:
            List of picking dicts with id, name, scheduled_date, date_deadline
        """
        return self.odoo.search_read(
            self.PICKING_MODEL,
            [
                ("purchase_id", "=", po_id),
                ("state", "in", self.OPEN_PICKING_STATES),
            ],
            fields=["id", "name", "scheduled_date", "date_deadline"],
        )

    def sync_picking_dates(
        self,
        picking_id: int,
        new_date: datetime,
        picking_name: str,
    ) -> OperationResult:
        """
        Sync picking scheduled_date and date_deadline to a new date.

        Args:
            picking_id: Stock picking ID
            new_date: New date to set
            picking_name: Picking name for logging

        Returns:
            OperationResult
        """
        date_str = new_date.strftime("%Y-%m-%d %H:%M:%S")

        return self._safe_write(
            model=self.PICKING_MODEL,
            ids=[picking_id],
            values={
                "scheduled_date": date_str,
                "date_deadline": date_str,
            },
            action="sync_picking_dates",
            record_name=picking_name,
        )

    def sync_move_dates(
        self,
        picking_id: int,
        new_date: datetime,
    ) -> list[OperationResult]:
        """
        Sync stock.move.date for all moves in a picking.

        Args:
            picking_id: Stock picking ID
            new_date: New date to set

        Returns:
            List of OperationResults
        """
        results = []
        date_str = new_date.strftime("%Y-%m-%d %H:%M:%S")

        # Find all moves in this picking
        moves = self.odoo.search_read(
            self.MOVE_MODEL,
            [("picking_id", "=", picking_id)],
            fields=["id", "name"],
        )

        for move in moves:
            result = self._safe_write(
                model=self.MOVE_MODEL,
                ids=[move["id"]],
                values={"date": date_str},
                action="sync_move_date",
                record_name=move.get("name"),
                silent=True,  # Don't log each move individually
            )
            results.append(result)

        return results

    def sync_single_move_date(
        self,
        move_id: int,
        new_date: datetime,
        move_name: Optional[str] = None,
    ) -> OperationResult:
        """
        Sync a single stock.move.date to a specific date.

        Args:
            move_id: Stock move ID
            new_date: New date to set
            move_name: Move name for logging (optional)

        Returns:
            OperationResult
        """
        date_str = new_date.strftime("%Y-%m-%d %H:%M:%S")

        return self._safe_write(
            model=self.MOVE_MODEL,
            ids=[move_id],
            values={"date": date_str},
            action="sync_move_date",
            record_name=move_name or f"move-{move_id}",
        )

    def sync_move_dates_to_line_planned(
        self,
        po_id: int,
    ) -> list[OperationResult]:
        """
        Sync stock.move.date to purchase.order.line.date_planned (line-level).

        This is the bonus line-level sync where each move gets the date
        from its corresponding PO line.

        Args:
            po_id: Purchase order ID

        Returns:
            List of OperationResults
        """
        results = []

        # Get PO lines with their date_planned
        po_lines = self.odoo.search_read(
            self.PO_LINE_MODEL,
            [("order_id", "=", po_id)],
            fields=["id", "date_planned"],
        )

        for line in po_lines:
            line_id = line["id"]
            date_planned = line.get("date_planned")

            if not date_planned:
                continue

            # Parse date if it's a string
            if isinstance(date_planned, str):
                date_planned = datetime.strptime(date_planned, "%Y-%m-%d %H:%M:%S")

            date_str = date_planned.strftime("%Y-%m-%d %H:%M:%S")

            # Find moves linked to this PO line
            moves = self.odoo.search_read(
                self.MOVE_MODEL,
                [("purchase_line_id", "=", line_id)],
                fields=["id", "name"],
            )

            for move in moves:
                result = self._safe_write(
                    model=self.MOVE_MODEL,
                    ids=[move["id"]],
                    values={"date": date_str},
                    action="sync_move_to_line_date",
                    record_name=move.get("name"),
                    silent=True,
                )
                results.append(result)

        return results

    def post_picking_date_sync_message(
        self,
        picking_id: int,
        picking_name: str,
        new_date: datetime,
        po_name: str,
        old_scheduled: Optional[datetime] = None,
        old_deadline: Optional[datetime] = None,
        moves_updated: int = 0,
    ) -> OperationResult:
        """
        Post chatter message on picking documenting PO date sync.

        Args:
            picking_id: Stock picking ID
            picking_name: Picking name for logging
            new_date: New date set on the picking
            po_name: Parent PO name for reference
            old_scheduled: Original scheduled_date (None if unknown)
            old_deadline: Original date_deadline (None if unknown)
            moves_updated: Number of moves updated in this picking

        Returns:
            OperationResult
        """
        old_scheduled_str = old_scheduled.strftime('%Y-%m-%d') if old_scheduled else "N/A"
        old_deadline_str = old_deadline.strftime('%Y-%m-%d') if old_deadline else "N/A"
        new_date_str = new_date.strftime('%Y-%m-%d')

        body = f"""
<p><strong>Date Compliance: Picking Dates Synchronized</strong></p>
<p>Dates updated to match {po_name} date_planned ({new_date_str}).</p>
<ul>
    <li><strong>Scheduled Date:</strong> {old_scheduled_str} → {new_date_str}</li>
    <li><strong>Date Deadline:</strong> {old_deadline_str} → {new_date_str}</li>
    <li><strong>Moves Updated:</strong> {moves_updated}</li>
</ul>
<p><em>Updated by Sentinel-Ops: sync_po_picking_dates</em></p>
"""

        return self._safe_message_post(
            model=self.PICKING_MODEL,
            record_id=picking_id,
            body=body.strip(),
            message_type="comment",
            record_name=picking_name,
        )

    def post_po_date_sync_message(
        self,
        po_id: int,
        po_name: str,
        old_scheduled: Optional[datetime],
        old_deadline: Optional[datetime],
        new_date: datetime,
        pickings_updated: int,
        moves_updated: int,
        line_level_sync: bool = False,
    ) -> OperationResult:
        """
        Post chatter message on PO documenting date sync.

        Args:
            po_id: Purchase order ID
            po_name: Purchase order name
            old_scheduled: Original scheduled_date (None if unknown)
            old_deadline: Original date_deadline (None if unknown)
            new_date: New date set
            pickings_updated: Number of pickings updated
            moves_updated: Number of moves updated
            line_level_sync: Whether line-level sync was performed

        Returns:
            OperationResult
        """
        old_scheduled_str = old_scheduled.strftime('%Y-%m-%d') if old_scheduled else "N/A"
        old_deadline_str = old_deadline.strftime('%Y-%m-%d') if old_deadline else "N/A"
        new_date_str = new_date.strftime('%Y-%m-%d')

        sync_type = "Line-level" if line_level_sync else "Header-level"

        body = f"""
<p><strong>Date Compliance: PO Dates Synchronized</strong></p>
<p>{sync_type} date sync to match date_planned ({new_date_str}).</p>
<ul>
    <li><strong>Pickings Updated:</strong> {pickings_updated}</li>
    <li><strong>Moves Updated:</strong> {moves_updated}</li>
</ul>
<p><em>Updated by Sentinel-Ops: sync_po_picking_dates</em></p>
"""

        return self._safe_message_post(
            model=self.PO_MODEL,
            record_id=po_id,
            body=body.strip(),
            message_type="comment",
            record_name=po_name,
        )
