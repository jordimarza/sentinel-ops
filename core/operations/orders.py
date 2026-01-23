"""
Order Operations

Operations related to sale orders and order lines.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from core.operations.base import BaseOperation
from core.result import OperationResult

logger = logging.getLogger(__name__)


class OrderOperations(BaseOperation):
    """
    Operations for sale orders and order lines.

    Provides:
    - Finding partial orders older than X days
    - Adjusting order line quantities
    - Tagging orders with exceptions
    """

    # Constants
    EXCEPTION_TAG = "Sentinel-Exception"
    SO_LINE_MODEL = "sale.order.line"
    SO_MODEL = "sale.order"

    def find_partial_orders_older_than(
        self,
        days: int = 30,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """
        Find sale order lines that are partially delivered and older than X days.

        Criteria:
        - Order line has qty_delivered > 0
        - Order line has qty_delivered < product_uom_qty
        - Order is in 'sale' state
        - Order date is older than X days

        Args:
            days: Number of days to look back
            limit: Maximum number of records to return

        Returns:
            List of order line dicts with id, order_id, product_uom_qty, qty_delivered
        """
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        cutoff_str = cutoff_date.strftime("%Y-%m-%d")

        domain = [
            ("order_id.state", "=", "sale"),
            ("order_id.date_order", "<", cutoff_str),
            ("qty_delivered", ">", 0),
            ("qty_delivered", "<", "product_uom_qty"),  # Partial delivery
        ]

        # Note: This domain requires checking qty_delivered < product_uom_qty
        # which can be tricky in some Odoo versions. Alternative approach:
        # 1. Fetch all lines with qty_delivered > 0 from old orders
        # 2. Filter in Python where qty_delivered < product_uom_qty

        fields = [
            "id",
            "order_id",
            "product_id",
            "product_uom_qty",
            "qty_delivered",
            "name",
        ]

        kwargs = {}
        if limit:
            kwargs["limit"] = limit

        self.log.info(
            f"Searching for partial order lines older than {days} days",
            data={"cutoff": cutoff_str},
        )

        try:
            # First, get orders older than cutoff in sale state
            orders = self.odoo.search(
                self.SO_MODEL,
                [
                    ("state", "=", "sale"),
                    ("date_order", "<", cutoff_str),
                ],
            )

            if not orders:
                self.log.info("No orders found in date range")
                return []

            # Then find lines with partial delivery
            lines = self.odoo.search_read(
                self.SO_LINE_MODEL,
                [
                    ("order_id", "in", orders),
                    ("qty_delivered", ">", 0),
                ],
                fields=fields,
                **kwargs,
            )

            # Filter to only partial deliveries
            partial_lines = [
                line for line in lines
                if line["qty_delivered"] < line["product_uom_qty"]
            ]

            self.log.info(
                f"Found {len(partial_lines)} partial order lines",
                data={"total_checked": len(lines)},
            )

            return partial_lines

        except Exception as e:
            self.log.error(
                "Failed to search for partial orders",
                error=str(e),
            )
            raise

    def adjust_line_qty_to_delivered(
        self,
        line: dict,
    ) -> OperationResult:
        """
        Adjust order line quantity to match delivered quantity.

        Args:
            line: Order line dict with id, qty_delivered

        Returns:
            OperationResult
        """
        line_id = line["id"]
        delivered_qty = line["qty_delivered"]

        return self._safe_write(
            model=self.SO_LINE_MODEL,
            ids=[line_id],
            values={"product_uom_qty": delivered_qty},
            action="adjust_qty_to_delivered",
        )

    def tag_order_exception(
        self,
        order_id: int,
        reason: str,
    ) -> OperationResult:
        """
        Tag an order as having an exception and add a note.

        Args:
            order_id: Sale order ID
            reason: Reason for the exception

        Returns:
            OperationResult
        """
        # Post the note first
        note_result = self._safe_message_post(
            model=self.SO_MODEL,
            record_id=order_id,
            body=f"[SentinelOps] Exception: {reason}",
            message_type="notification",
        )

        if not note_result.success and not self.dry_run:
            return note_result

        # Then add the tag (using sale.order tag field)
        # Note: sale.order uses 'tag_ids' with 'crm.tag' in some setups
        # Adjust tag_model and tag_field as needed for your Odoo version
        tag_result = self._safe_add_tag(
            model=self.SO_MODEL,
            record_ids=[order_id],
            tag_name=self.EXCEPTION_TAG,
            tag_model="crm.tag",
            tag_field="tag_ids",
        )

        return tag_result

    def get_order_details(
        self,
        order_id: int,
        fields: Optional[list[str]] = None,
    ) -> Optional[dict]:
        """
        Get details for a sale order.

        Args:
            order_id: Sale order ID
            fields: Fields to retrieve

        Returns:
            Order dict or None if not found
        """
        if fields is None:
            fields = ["id", "name", "state", "partner_id", "date_order", "amount_total"]

        try:
            orders = self.odoo.read(self.SO_MODEL, [order_id], fields)
            return orders[0] if orders else None
        except Exception as e:
            self.log.error(
                f"Failed to get order details for {order_id}",
                error=str(e),
            )
            return None

    # --- Shipping completion operations ---

    def find_orders_with_only_shipping_pending(
        self,
        shipping_product_ids: list[int],
        limit: Optional[int] = None,
        order_ids: Optional[list[int]] = None,
    ) -> list[dict]:
        """
        Find orders where the ONLY pending delivery items are shipping fee products.

        An order qualifies if:
        - It has at least one shipping line with qty_delivered < product_uom_qty
        - ALL non-shipping lines have qty_delivered >= product_uom_qty

        Args:
            shipping_product_ids: Product IDs that represent shipping fees
            limit: Maximum number of orders to return
            order_ids: Optional list of specific order IDs to check

        Returns:
            List of dicts with order info and their pending shipping lines:
            [
                {
                    "order_id": 123,
                    "order_name": "S00455346",
                    "pending_shipping_lines": [
                        {"id": 456, "product_id": 15743, "product_uom_qty": 1.0, "qty_delivered": 0.0}
                    ]
                }
            ]
        """
        self.log.info(
            "Searching for orders with only shipping pending",
            data={
                "shipping_product_ids": shipping_product_ids,
                "limit": limit,
                "order_ids": order_ids,
            },
        )

        try:
            # Step 1: Find pending shipping lines (qty_delivered < product_uom_qty)
            shipping_domain = [
                ("product_id", "in", shipping_product_ids),
                ("order_id.state", "=", "sale"),
            ]

            if order_ids:
                shipping_domain.append(("order_id", "in", order_ids))

            shipping_lines = self.odoo.search_read(
                self.SO_LINE_MODEL,
                shipping_domain,
                fields=["id", "order_id", "product_id", "product_uom_qty", "qty_delivered", "name"],
            )

            # Filter to only pending shipping lines (qty_delivered < product_uom_qty)
            pending_shipping = [
                line for line in shipping_lines
                if line["qty_delivered"] < line["product_uom_qty"]
            ]

            if not pending_shipping:
                self.log.info("No pending shipping lines found")
                return []

            # Group by order_id
            order_shipping_map: dict[int, list[dict]] = {}
            for line in pending_shipping:
                oid = line["order_id"][0] if isinstance(line["order_id"], (list, tuple)) else line["order_id"]
                if oid not in order_shipping_map:
                    order_shipping_map[oid] = []
                order_shipping_map[oid].append(line)

            self.log.info(
                f"Found {len(pending_shipping)} pending shipping lines across {len(order_shipping_map)} orders",
            )

            # Step 2: For each order, check if any non-shipping lines are pending
            qualifying_orders = []

            for order_id, shipping_lines_for_order in order_shipping_map.items():
                # Count pending non-shipping lines
                pending_non_shipping_count = self.odoo.search_count(
                    self.SO_LINE_MODEL,
                    [
                        ("order_id", "=", order_id),
                        ("product_id", "not in", shipping_product_ids),
                        ("qty_delivered", "<", "product_uom_qty"),
                    ],
                )

                # Alternative approach if search_count with < doesn't work:
                # Fetch all non-shipping lines and filter in Python
                if pending_non_shipping_count is None:
                    non_shipping_lines = self.odoo.search_read(
                        self.SO_LINE_MODEL,
                        [
                            ("order_id", "=", order_id),
                            ("product_id", "not in", shipping_product_ids),
                        ],
                        fields=["id", "product_uom_qty", "qty_delivered"],
                    )
                    pending_non_shipping_count = sum(
                        1 for line in non_shipping_lines
                        if line["qty_delivered"] < line["product_uom_qty"]
                    )

                if pending_non_shipping_count == 0:
                    # Get order name
                    order = self.get_order_details(order_id, fields=["id", "name"])
                    order_name = order["name"] if order else f"Order #{order_id}"

                    qualifying_orders.append({
                        "order_id": order_id,
                        "order_name": order_name,
                        "pending_shipping_lines": shipping_lines_for_order,
                    })

                    if limit and len(qualifying_orders) >= limit:
                        break

            self.log.info(
                f"Found {len(qualifying_orders)} orders where only shipping is pending",
            )

            return qualifying_orders

        except Exception as e:
            self.log.error(
                "Failed to search for orders with only shipping pending",
                error=str(e),
            )
            raise

    def complete_shipping_line(
        self,
        line: dict,
    ) -> OperationResult:
        """
        Complete a shipping line by setting qty_delivered = product_uom_qty.

        Args:
            line: Order line dict with id, product_uom_qty

        Returns:
            OperationResult
        """
        line_id = line["id"]
        target_qty = line["product_uom_qty"]

        return self._safe_write(
            model=self.SO_LINE_MODEL,
            ids=[line_id],
            values={"qty_delivered": target_qty},
            action="complete_shipping_line",
        )

    def post_shipping_completion_message(
        self,
        order_id: int,
        order_name: str,
        lines_completed: int,
    ) -> OperationResult:
        """
        Post a chatter message to the sale order about shipping completion.

        Args:
            order_id: Sale order ID
            order_name: Sale order name (e.g., S00455346)
            lines_completed: Number of shipping lines completed

        Returns:
            OperationResult
        """
        request_id = self.ctx.request_id if self.ctx else "N/A"

        body = f"""<div style="font-family: Arial, sans-serif; line-height: 1.6;">
    <p><strong>Sentinel-Ops: Shipping Line Completion</strong></p>
    <ul style="margin: 10px 0; padding-left: 20px;">
        <li><strong>Order:</strong> {order_name}</li>
        <li><strong>Action:</strong> Auto-completed {lines_completed} shipping line(s)</li>
        <li><strong>Reason:</strong> Only pending items were shipping fees</li>
    </ul>
    <p style="color: #666; font-size: 0.9em;">
        Request ID: {request_id}
    </p>
</div>"""

        return self._safe_message_post(
            model=self.SO_MODEL,
            record_id=order_id,
            body=body,
            message_type="notification",
        )
