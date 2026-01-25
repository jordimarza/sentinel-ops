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

        # Note: Odoo domains can't compare two fields directly (qty_delivered < product_uom_qty)
        # So we fetch lines with qty_delivered > 0 and filter in Python

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
        order_name: Optional[str] = None,
    ) -> OperationResult:
        """
        Adjust order line quantity to match delivered quantity.

        Args:
            line: Order line dict with id, qty_delivered
            order_name: Display name of the parent order

        Returns:
            OperationResult
        """
        line_id = line["id"]
        delivered_qty = line["qty_delivered"]
        line_name = line.get("name", "") or f"Line #{line_id}"
        record_name = f"{order_name}/{line_name}" if order_name else line_name

        return self._safe_write(
            model=self.SO_LINE_MODEL,
            ids=[line_id],
            values={"product_uom_qty": delivered_qty},
            action="adjust_qty_to_delivered",
            record_name=record_name,
        )

    def tag_order_exception(
        self,
        order_id: int,
        reason: str,
        order_name: Optional[str] = None,
    ) -> OperationResult:
        """
        Tag an order as having an exception and add a note.

        Args:
            order_id: Sale order ID
            reason: Reason for the exception
            order_name: Display name of the order

        Returns:
            OperationResult
        """
        record_name = order_name or f"Order #{order_id}"

        # Post the note first
        note_result = self._safe_message_post(
            model=self.SO_MODEL,
            record_id=order_id,
            body=f"[SentinelOps] Exception: {reason}",
            message_type="notification",
            record_name=record_name,
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
            record_name=record_name,
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
                # Fetch all non-shipping lines and filter in Python
                # (Odoo domains can't compare two fields directly)
                non_shipping_lines = self.odoo.search_read(
                    self.SO_LINE_MODEL,
                    [
                        ("order_id", "=", order_id),
                        ("product_id", "not in", shipping_product_ids),
                    ],
                    fields=["id", "product_uom_qty", "qty_delivered"],
                )

                # Count lines where qty_delivered < product_uom_qty
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
        order_name: Optional[str] = None,
        silent: bool = True,
    ) -> OperationResult:
        """
        Complete a shipping line by setting qty_delivered = product_uom_qty.

        Args:
            line: Order line dict with id, product_uom_qty
            order_name: Display name of the parent order (e.g., "S00455346")
            silent: If True, don't log per-line (useful when logging at order level)

        Returns:
            OperationResult
        """
        line_id = line["id"]
        target_qty = line["product_uom_qty"]
        # Use line name or order name for record identification
        line_name = line.get("name", "") or f"Line #{line_id}"
        record_name = f"{order_name}/{line_name}" if order_name else line_name

        return self._safe_write(
            model=self.SO_LINE_MODEL,
            ids=[line_id],
            values={"qty_delivered": target_qty},
            action="complete_shipping_line",
            record_name=record_name,
            silent=silent,
        )

    # --- Closed/Delivered order quantity adjustment operations ---

    # AH Status values for delivered/closed orders
    # Note: "cancelled" removed - we skip orders where state='cancel' anyway
    AH_STATUS_CLOSED = ["delivered", "closed"]

    # ==========================================================================
    # VIRTUAL PRODUCT IDs TO EXCLUDE FROM QTY ADJUSTMENTS
    # These are service-type products that don't have physical delivery
    # Last updated: 2025-01-24 (full service product audit)
    # ==========================================================================
    # fmt: off

    # --- SHIPPING & DELIVERY ---
    DEFAULT_SHIPPING_PRODUCT_IDS = [
        # Original shipping fees
        15743, 23506, 22407, 9216, 23233, 25499,
        # Shipping Cost variants
        27642, 78499, 81925, 82539, 78919,
        # Free Shipping
        1, 4065, 4107, 4156, 20455, 81217, 26546,
        # Paid Shipping
        3996, 4048, 4155, 4170, 21762, 25015, 86689,
        # Influencer Shipping
        4066, 4198, 21215, 45139, 50100,
        # Shopify Shipping Products
        26976, 81327, 81328, 81329, 81330, 81331, 82660, 86294, 87361,
        # Carrier products
        39118, 51389, 27644, 86327,
        # DHL
        2, 3644, 3742, 19397, 20462,
        # UPS
        4, 5, 20457, 20463, 20640, 20466, 82369,
        # USPS
        20635, 20920,
        # FedEx
        78496, 78497, 45144,
        # Other carriers
        48081, 20456, 70700, 70701, 26978, 33877,
        # Standard/Home delivery
        20638, 20637, 27643, 33442, 27253,
        # Partial Shipment
        69023, 69024, 69025, 69026, 69027, 69028, 69029, 69030, 52585, 52586,
        # Transport charges
        20650, 38590, 65270,
    ]

    # --- DISCOUNTS ---
    DEFAULT_DISCOUNT_PRODUCT_IDS = [
        15192, 17426, 21348, 21382, 26322, 34707,
        78156, 82296, 83941, 83942, 83943, 83944, 83945, 86690,
    ]

    # --- GIFT CARDS ---
    DEFAULT_GIFT_CARD_PRODUCT_IDS = [
        # ALOHAS Gift Cards
        27176, 27177, 27178, 27179, 27180, 27181, 27182, 27183, 51390, 55774,
        # Shopify Gift Cards
        26975, 34708,
    ]

    # --- CHARGEBACKS & FEES ---
    DEFAULT_CHARGEBACK_PRODUCT_IDS = [
        # Customer chargebacks
        82468, 82469, 82470, 82471,
        # Chargeback received
        84018, 84019, 84020, 84021, 84022, 84023,
    ]

    # --- TIPS ---
    DEFAULT_TIP_PRODUCT_IDS = [
        26977, 34709,
    ]

    # --- DUTIES & CUSTOMS ---
    DEFAULT_DUTIES_PRODUCT_IDS = [
        78495,  # Shopify Duties Product
        20641, 20642, 20643, 20644,  # DUA Valoración IVA
        78490, 78491, 78492,  # DUA VAT Valuation
        21399,  # DUTIES REFUND
    ]

    # --- COMMISSIONS ---
    DEFAULT_COMMISSION_PRODUCT_IDS = [
        27243, 39949, 63088, 65926,
    ]

    # --- OTHER FEES ---
    DEFAULT_OTHER_FEE_PRODUCT_IDS = [
        18190,  # Handling Cost
        15190,  # Carbon Offset
        15191,  # Down payment
    ]

    # Combined list of ALL virtual products to exclude
    DEFAULT_EXCLUDE_PRODUCT_IDS = (
        DEFAULT_SHIPPING_PRODUCT_IDS +
        DEFAULT_DISCOUNT_PRODUCT_IDS +
        DEFAULT_GIFT_CARD_PRODUCT_IDS +
        DEFAULT_CHARGEBACK_PRODUCT_IDS +
        DEFAULT_TIP_PRODUCT_IDS +
        DEFAULT_DUTIES_PRODUCT_IDS +
        DEFAULT_COMMISSION_PRODUCT_IDS +
        DEFAULT_OTHER_FEE_PRODUCT_IDS
    )
    # fmt: on

    def find_closed_orders_with_qty_mismatch(
        self,
        ah_statuses: Optional[list[str]] = None,
        limit: Optional[int] = None,
        order_ids: Optional[list[int]] = None,
        days: Optional[int] = None,
        order_name_pattern: Optional[str] = None,
        exclude_product_ids: Optional[list[int]] = None,
    ) -> tuple[list[dict], dict]:
        """
        Find orders where ah_status is delivered/cancelled/closed but line quantities don't match.

        An order qualifies if:
        - ah_status is in the given list (default: delivered, cancelled, closed)
        - At least one non-shipping line has product_uom_qty != qty_delivered

        Args:
            ah_statuses: List of ah_status values to filter by
            limit: Maximum number of orders to return
            order_ids: Optional list of specific order IDs to check
            days: Optional - only check orders from last N days
            order_name_pattern: Optional pattern to match order name (e.g., "S0%", "INT%")
                               Use % as wildcard (SQL LIKE syntax)
            exclude_product_ids: Product IDs to exclude (default: shipping + discount products)
                                Set to empty list [] to include all products

        Returns:
            Tuple of (orders_list, discovery_stats):
            - orders_list: List of dicts with order info and their mismatched lines
            - discovery_stats: Dict with discovery phase metrics for KPI tracking
        """
        if ah_statuses is None:
            ah_statuses = self.AH_STATUS_CLOSED

        # Default: exclude virtual products (shipping + discounts)
        if exclude_product_ids is None:
            exclude_product_ids = self.DEFAULT_EXCLUDE_PRODUCT_IDS

        self.log.info(
            "Searching for closed orders with qty mismatch",
            data={
                "ah_statuses": ah_statuses,
                "limit": limit,
                "order_ids": order_ids,
                "days": days,
                "order_name_pattern": order_name_pattern,
                "exclude_product_ids": exclude_product_ids,
            },
        )

        try:
            # Start from LINES, not orders - more efficient as delivered orders grow
            # Use Odoo's dot notation to filter by related order fields
            line_domain = [
                ("order_id.ah_status", "in", ah_statuses),
                ("order_id.state", "=", "sale"),  # Only confirmed orders (not draft/sent/cancel)
            ]

            # Exclude virtual products (shipping, discounts, etc.)
            if exclude_product_ids:
                line_domain.append(("product_id", "not in", exclude_product_ids))

            # Optional filters on the order
            if order_ids:
                line_domain.append(("order_id", "in", order_ids))

            if days:
                cutoff_date = datetime.utcnow() - timedelta(days=days)
                cutoff_str = cutoff_date.strftime("%Y-%m-%d")
                line_domain.append(("order_id.date_order", ">=", cutoff_str))

            if order_name_pattern:
                line_domain.append(("order_id.name", "=ilike", order_name_pattern))

            # Single query: get all candidate lines with order info
            all_lines = self.odoo.search_read(
                self.SO_LINE_MODEL,
                line_domain,
                fields=["id", "name", "product_id", "product_uom_qty", "qty_delivered", "order_id"],
            )

            lines_from_query = len(all_lines)
            self.log.info(f"Fetched {lines_from_query} candidate lines")

            # Filter in Python for qty mismatch (can't compare fields in Odoo domain)
            # Also exclude negative qty_delivered (safety)
            from collections import defaultdict
            lines_by_order: dict[int, list[dict]] = defaultdict(list)
            order_names: dict[int, str] = {}

            for line in all_lines:
                if (line["qty_delivered"] != line["product_uom_qty"]
                        and line["qty_delivered"] >= 0):
                    # Extract order_id and name from the tuple (id, name)
                    order_id, order_name = line["order_id"]
                    lines_by_order[order_id].append(line)
                    order_names[order_id] = order_name

            # Count mismatched lines for discovery stats
            lines_with_mismatch = sum(len(lines) for lines in lines_by_order.values())

            # Build result list
            qualifying_orders = []
            for order_id, mismatched_lines in lines_by_order.items():
                qualifying_orders.append({
                    "order_id": order_id,
                    "order_name": order_names[order_id],
                    "ah_status": ah_statuses[0] if len(ah_statuses) == 1 else "mixed",
                    "mismatched_lines": mismatched_lines,
                })

            total_orders_before_limit = len(qualifying_orders)

            # Randomize order to avoid always processing the same orders first
            import random
            random.shuffle(qualifying_orders)

            # Apply limit after shuffle
            limit_reached = False
            if limit and len(qualifying_orders) > limit:
                qualifying_orders = qualifying_orders[:limit]
                limit_reached = True

            # Build discovery stats for KPI tracking
            discovery_stats = {
                "lines_from_query": lines_from_query,
                "lines_with_mismatch": lines_with_mismatch,
                "orders_with_mismatch": total_orders_before_limit,
                "limit_reached": limit_reached,
            }

            self.log.info(
                f"Found {len(qualifying_orders)} orders with qty mismatches "
                f"(total: {total_orders_before_limit}, limit: {limit}, reached: {limit_reached})",
                data={
                    "discovery": discovery_stats,
                    "orders_after_limit": len(qualifying_orders),
                    "total_mismatched_lines_after_limit": sum(len(o["mismatched_lines"]) for o in qualifying_orders),
                },
            )

            return qualifying_orders, discovery_stats

        except Exception as e:
            self.log.error(
                "Failed to search for closed orders with qty mismatch",
                error=str(e),
            )
            raise

    def adjust_line_qty_to_delivered_qty(
        self,
        line: dict,
        order_name: Optional[str] = None,
        silent: bool = True,
        target_qty: Optional[float] = None,
    ) -> OperationResult:
        """
        Adjust order line quantity (product_uom_qty).

        By default sets product_uom_qty = qty_delivered.
        If target_qty is provided, uses that value instead.

        Args:
            line: Order line dict with id, qty_delivered, product_uom_qty
            order_name: Display name of the parent order
            silent: If True, don't log per-line (useful when logging at order level)
            target_qty: Explicit target quantity (if None, uses qty_delivered)

        Returns:
            OperationResult
        """
        line_id = line["id"]
        old_qty = line["product_uom_qty"]
        new_qty = target_qty if target_qty is not None else line["qty_delivered"]
        line_name = line.get("name", "") or f"Line #{line_id}"
        record_name = f"{order_name}/{line_name}" if order_name else line_name

        # Safety: never set quantities below 0
        if new_qty < 0:
            return OperationResult.skipped(
                record_id=line_id,
                model=self.SO_LINE_MODEL,
                reason=f"Skipped: target qty is negative ({new_qty})",
                record_name=record_name,
            )

        result = self._safe_write(
            model=self.SO_LINE_MODEL,
            ids=[line_id],
            values={"product_uom_qty": new_qty},
            action="adjust_qty",
            record_name=record_name,
            silent=silent,
        )

        # Add old/new qty to result data for tracking
        if result.data:
            result.data["old_qty"] = old_qty
            result.data["new_qty"] = new_qty
        else:
            result.data = {"old_qty": old_qty, "new_qty": new_qty}

        return result

    def post_qty_adjustment_message(
        self,
        order_id: int,
        order_name: str,
        adjusted_lines: list[dict],
    ) -> OperationResult:
        """
        Post a chatter message to the sale order about quantity adjustments.

        Args:
            order_id: Sale order ID
            order_name: Sale order name (e.g., S00455346)
            adjusted_lines: List of line dicts with adjustment details

        Returns:
            OperationResult
        """
        request_id = self.ctx.request_id if self.ctx else "N/A"

        # Build line details HTML
        line_details = ""
        for line in adjusted_lines:
            line_name = line.get("name", f"Line #{line['id']}")
            old_qty = line.get("product_uom_qty", "?")
            delivered = line.get("qty_delivered", 0)
            open_moves = line.get("_open_move_qty", 0)
            target = line.get("_target_qty", delivered)

            if open_moves > 0:
                # Show breakdown: delivered + open moves = target
                line_details += f"<li>{line_name}: {old_qty} → {target} (delivered: {delivered} + pending: {open_moves})</li>\n"
            else:
                line_details += f"<li>{line_name}: {old_qty} → {target}</li>\n"

        body = f"""<div style="font-family: Arial, sans-serif; line-height: 1.6;">
    <p><strong>Sentinel-Ops: Order Quantity Adjustment</strong></p>
    <p><strong>Action:</strong> Adjusted {len(adjusted_lines)} line(s) to match actual fulfillment</p>
    <p><strong>Formula:</strong> new_qty = delivered + pending_moves</p>
    <p><strong>Lines adjusted:</strong></p>
    <ul style="margin: 10px 0; padding-left: 20px;">
        {line_details}
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
            record_name=order_name,
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
            record_name=order_name,
        )
