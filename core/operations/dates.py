"""
Date Compliance Operations

Operations for date synchronization and AR-HOLD tag management.
"""

import re
from datetime import datetime, timedelta
from typing import Optional

from core.operations.base import BaseOperation
from core.result import OperationResult


class DateComplianceOperations(BaseOperation):
    """
    Operations for date compliance checks and remediation.

    Handles:
    - AR-HOLD tag increment/management
    - Commitment date extension
    - Picking date synchronization
    - Move date synchronization
    """

    # Models
    SO_MODEL = "sale.order"
    PICKING_MODEL = "stock.picking"
    MOVE_MODEL = "stock.move"
    PARTNER_MODEL = "res.partner"
    PARTNER_CATEGORY_MODEL = "res.partner.category"

    # AR-HOLD tag configuration
    AR_HOLD_TAG_MODEL = "ah_order_tags"
    AR_HOLD_TAG_FIELD = "ah_sales_order_tags_ids"
    AR_HOLD_TAG_PREFIX = "AR-HOLD:"
    DEFAULT_HOLD_EXTENSION_DAYS = 15

    # Open picking states (not done or cancelled)
    OPEN_PICKING_STATES = ["draft", "waiting", "confirmed", "assigned"]

    def find_ar_hold_tag_on_order(
        self,
        order_id: int,
    ) -> Optional[tuple[int, int, str]]:
        """
        Find existing AR-HOLD:N tag on an order.

        Args:
            order_id: Sale order ID

        Returns:
            Tuple of (tag_id, current_N, tag_name) if found, None otherwise
        """
        tags = self.odoo.find_tags_by_prefix(
            tag_model=self.AR_HOLD_TAG_MODEL,
            prefix=self.AR_HOLD_TAG_PREFIX,
            record_model=self.SO_MODEL,
            record_id=order_id,
            tag_field=self.AR_HOLD_TAG_FIELD,
        )

        if not tags:
            return None

        # Parse the number from the tag name (e.g., "AR-HOLD:2" -> 2)
        for tag in tags:
            name = tag.get("name", "")
            match = re.match(rf"{re.escape(self.AR_HOLD_TAG_PREFIX)}(\d+)$", name)
            if match:
                return (tag["id"], int(match.group(1)), name)

        return None

    def increment_ar_hold_tag(
        self,
        order_id: int,
        order_name: str,
    ) -> tuple[OperationResult, int]:
        """
        Increment AR-HOLD tag: find AR-HOLD:N, remove it, add AR-HOLD:N+1.
        If no existing tag, adds AR-HOLD:1.

        Args:
            order_id: Sale order ID
            order_name: Sale order name for logging

        Returns:
            Tuple of (OperationResult, new_hold_count)
        """
        existing = self.find_ar_hold_tag_on_order(order_id)

        if existing:
            tag_id, current_n, tag_name = existing
            new_n = current_n + 1

            # Remove old tag
            remove_result = self._safe_remove_tag(
                model=self.SO_MODEL,
                record_ids=[order_id],
                tag_id=tag_id,
                tag_name=tag_name,
                tag_field=self.AR_HOLD_TAG_FIELD,
                record_name=order_name,
            )
            if not remove_result.success:
                return (remove_result, current_n)
        else:
            new_n = 1

        # Add new tag
        new_tag_name = f"{self.AR_HOLD_TAG_PREFIX}{new_n}"
        add_result = self._safe_add_tag(
            model=self.SO_MODEL,
            record_ids=[order_id],
            tag_name=new_tag_name,
            tag_model=self.AR_HOLD_TAG_MODEL,
            tag_field=self.AR_HOLD_TAG_FIELD,
            record_name=order_name,
        )

        return (add_result, new_n)

    def extend_commitment_date(
        self,
        order_id: int,
        order_name: str,
        current_commitment_date: datetime,
        days: int = DEFAULT_HOLD_EXTENSION_DAYS,
    ) -> tuple[OperationResult, Optional[datetime]]:
        """
        Extend sale.order.commitment_date by N days.

        Args:
            order_id: Sale order ID
            order_name: Sale order name for logging
            current_commitment_date: Current commitment date
            days: Number of days to add (default: 15)

        Returns:
            Tuple of (OperationResult, new_commitment_date)
        """
        new_date = current_commitment_date + timedelta(days=days)

        result = self._safe_write(
            model=self.SO_MODEL,
            ids=[order_id],
            values={"commitment_date": new_date.strftime("%Y-%m-%d %H:%M:%S")},
            action="extend_commitment_date",
            record_name=order_name,
        )

        if result.success:
            return (result, new_date)
        return (result, None)

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

    def get_open_pickings_for_order(
        self,
        order_id: int,
    ) -> list[dict]:
        """
        Get open pickings (not done/cancel) for a sale order.

        Args:
            order_id: Sale order ID

        Returns:
            List of picking dicts with id, name, scheduled_date, date_deadline
        """
        return self.odoo.search_read(
            self.PICKING_MODEL,
            [
                ("sale_id", "=", order_id),
                ("state", "in", self.OPEN_PICKING_STATES),
            ],
            fields=["id", "name", "scheduled_date", "date_deadline"],
        )

    def check_partner_has_block_tag(
        self,
        partner_id: int,
    ) -> bool:
        """
        Check if partner has a tag containing "block" (case-insensitive).

        Args:
            partner_id: Partner ID

        Returns:
            True if partner has a blocking tag
        """
        # Read partner's category IDs
        partners = self.odoo.read(
            self.PARTNER_MODEL,
            [partner_id],
            ["category_id"],
        )
        if not partners:
            return False

        category_ids = partners[0].get("category_id", [])
        if not category_ids:
            return False

        # Read categories and check for "block" in name
        categories = self.odoo.read(
            self.PARTNER_CATEGORY_MODEL,
            category_ids,
            ["name"],
        )

        for cat in categories:
            name = cat.get("name", "").lower()
            if "block" in name:
                return True

        return False

    def post_ar_hold_message(
        self,
        order_id: int,
        order_name: str,
        old_commitment: datetime,
        new_commitment: datetime,
        old_hold_count: int,
        new_hold_count: int,
        pickings_updated: int,
        moves_updated: int,
    ) -> OperationResult:
        """
        Post chatter message documenting AR-HOLD violation remediation.

        Args:
            order_id: Sale order ID
            order_name: Sale order name
            old_commitment: Original commitment date
            new_commitment: New commitment date
            old_hold_count: Previous AR-HOLD count (0 if first)
            new_hold_count: New AR-HOLD count
            pickings_updated: Number of pickings updated
            moves_updated: Number of moves updated

        Returns:
            OperationResult
        """
        old_tag = f"AR-HOLD:{old_hold_count}" if old_hold_count > 0 else "None"
        new_tag = f"AR-HOLD:{new_hold_count}"

        body = f"""
<p><strong>Date Compliance: AR-HOLD Violation</strong></p>
<p>Partner is blocked - commitment date extended.</p>
<ul>
    <li><strong>Commitment Date:</strong> {old_commitment.strftime('%Y-%m-%d')} → {new_commitment.strftime('%Y-%m-%d')}</li>
    <li><strong>AR-HOLD Tag:</strong> {old_tag} → {new_tag}</li>
    <li><strong>Pickings Updated:</strong> {pickings_updated}</li>
    <li><strong>Moves Updated:</strong> {moves_updated}</li>
</ul>
<p><em>Updated by Sentinel-Ops: check_ar_hold_violations</em></p>
"""

        return self._safe_message_post(
            model=self.SO_MODEL,
            record_id=order_id,
            body=body.strip(),
            message_type="comment",
            record_name=order_name,
        )

    def post_date_sync_message(
        self,
        model: str,
        record_id: int,
        record_name: str,
        old_scheduled: Optional[datetime],
        old_deadline: Optional[datetime],
        new_date: datetime,
        reference_field: str,
        reference_value: datetime,
        moves_updated: int = 0,
        job_name: str = "sync_picking_dates",
    ) -> OperationResult:
        """
        Post chatter message documenting date synchronization.

        Args:
            model: Model of the record (e.g., "stock.picking")
            record_id: Record ID
            record_name: Record name for logging
            old_scheduled: Original scheduled_date (None if unknown)
            old_deadline: Original date_deadline (None if unknown)
            new_date: New date set
            reference_field: Name of reference field (e.g., "commitment_date")
            reference_value: Value of reference field
            moves_updated: Number of moves updated
            job_name: Name of the job for attribution

        Returns:
            OperationResult
        """
        old_scheduled_str = old_scheduled.strftime('%Y-%m-%d') if old_scheduled else "N/A"
        old_deadline_str = old_deadline.strftime('%Y-%m-%d') if old_deadline else "N/A"
        new_date_str = new_date.strftime('%Y-%m-%d')
        ref_date_str = reference_value.strftime('%Y-%m-%d')

        body = f"""
<p><strong>Date Compliance: Dates Synchronized</strong></p>
<p>Dates updated to match {reference_field} ({ref_date_str}).</p>
<ul>
    <li><strong>Scheduled Date:</strong> {old_scheduled_str} → {new_date_str}</li>
    <li><strong>Date Deadline:</strong> {old_deadline_str} → {new_date_str}</li>
    <li><strong>Moves Updated:</strong> {moves_updated}</li>
</ul>
<p><em>Updated by Sentinel-Ops: {job_name}</em></p>
"""

        return self._safe_message_post(
            model=model,
            record_id=record_id,
            body=body.strip(),
            message_type="comment",
            record_name=record_name,
        )
