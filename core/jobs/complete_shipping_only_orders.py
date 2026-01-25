"""
Complete Shipping Only Orders Job

Finds orders where the only pending delivery items are shipping fee products
and auto-completes those shipping lines by setting qty_delivered = product_uom_qty.
"""

import logging
from typing import Optional

from core.jobs.registry import register_job
from core.jobs.base import BaseJob
from core.operations.orders import OrderOperations
from core.result import JobResult

logger = logging.getLogger(__name__)

# Default shipping product IDs for ALOHAS
# All shipping/delivery fee product IDs in ALOHAS Odoo
# Query: product.product where name ilike 'shipping' or 'delivery' or 'envio'
DEFAULT_SHIPPING_PRODUCT_IDS = [
    # Original list
    15743,  # Shipping Fee
    23506, 22407, 9216, 23233, 25499,
    # Shipping Cost variants
    27642,  # Shipping Cost
    78499,  # SHIPPING COST
    81925,  # Shipping cost
    82539,  # Shipping fee
    78919,  # Shipping cotst (typo)
    # Free Shipping
    1,      # Free delivery charges
    4065,   # Free Shipping Worldwide - DHL
    4107,   # Free Shipping Worldwide - UPS
    4156,   # Free Shipping Worldwide - DHL
    20455,  # Free Shipping EU - DHL
    81217,  # Free Shipping - UPS
    26546,  # Envío gratis
    # Paid Shipping
    3996,   # Shipping Worldwide - DHL
    4048,   # Shipping - UPS
    4155,   # Shipping Worldwide - UPS
    4170,   # Shipping EU - DHL
    21762,  # Shipping Worldwide - UPS
    25015,  # Shipping Worldwide - UPS
    86689,  # Shipping Worldwide - DHL
    # Influencer Shipping
    4066,   # Influencers Shipping - UPS
    4198,   # Influencers Shipping Worldwide - DHL
    21215,  # Influencers Shipping Worldwide - UPS
    45139,  # Influencers Shipping - UPS
    50100,  # Influencers Shipping EU - DHL
    # Shopify Shipping Products
    26976,  # Shopify Shipping Product
    81327, 81328, 81329, 81330, 81331,  # Shopify Shipping Product 1,4,5,6,7
    82660,  # Shopify Shipping Product 10
    86294,  # Shopify Shipping Product 11
    87361,  # Shopify Shipping Product 13
    # Other carriers
    39118,  # FedEx delivery
    51389,  # GLS Delivery
    # Carrier products (shippypro, etc.)
    27644,  # shippypro carrier
    86327,  # carrier shipping cost
]


@register_job(
    name="complete_shipping_only_orders",
    description="Auto-complete shipping lines on orders where only shipping is pending",
    tags=["sales", "shipping", "automation"],
)
class CompleteShippingOnlyOrdersJob(BaseJob):
    """
    Auto-complete shipping lines on orders where only shipping is pending.

    Pattern: detect -> verify -> remediate -> log

    Workflow:
    1. Find orders where ONLY shipping fee lines have pending delivery
    2. For each order, complete the shipping line(s)
    3. Post chatter message documenting the action
    4. Return results with KPIs

    Use Cases:
    - Orders stuck because shipping fee wasn't marked delivered
    - Cleanup of orders where all products shipped but shipping line incomplete
    """

    def run(
        self,
        shipping_product_ids: Optional[list[int]] = None,
        limit: Optional[int] = None,
        order_ids: Optional[list[int]] = None,
        **_params
    ) -> JobResult:
        """
        Execute the complete shipping only orders job.

        Args:
            shipping_product_ids: List of product IDs that represent shipping fees
                                  Defaults to ALOHAS standard shipping products
            limit: Maximum number of orders to process
            order_ids: Optional list of specific order IDs to process (for testing)

        Returns:
            JobResult with execution details
        """
        # Create result with full context for audit trail
        result = JobResult.from_context(self.ctx, parameters={
            "shipping_product_ids": shipping_product_ids,
            "limit": limit,
            "order_ids": order_ids,
        })

        # Use default shipping product IDs if not provided
        if shipping_product_ids is None:
            shipping_product_ids = DEFAULT_SHIPPING_PRODUCT_IDS

        # Initialize operations
        order_ops = OrderOperations(self.odoo, self.ctx, self.log)

        # Step 1: Find qualifying orders
        self.log.info(
            "Finding orders with only shipping pending",
            data={
                "shipping_product_ids": shipping_product_ids,
                "limit": limit,
                "order_ids": order_ids,
            },
        )

        try:
            orders = order_ops.find_orders_with_only_shipping_pending(
                shipping_product_ids=shipping_product_ids,
                limit=limit,
                order_ids=order_ids,
            )
        except Exception as e:
            self.log.error("Failed to find qualifying orders", error=str(e))
            result.errors.append(f"Search failed: {e}")
            result.kpis = self._build_kpis(result, 0, 0)
            result.complete()
            return result

        if not orders:
            self.log.info("No orders found with only shipping pending")
            result.kpis = self._build_kpis(result, 0, 0)
            result.complete()
            return result

        self.log.info(f"Found {len(orders)} orders to process")

        # Track KPIs
        orders_completed = 0
        lines_completed = 0

        # Step 2 & 3: Process each order
        for order_data in orders:
            order_id = order_data["order_id"]
            order_name = order_data["order_name"]
            pending_lines = order_data["pending_shipping_lines"]

            result.records_checked += 1
            order_lines_completed = 0

            try:
                # Complete each pending shipping line
                for line in pending_lines:
                    op_result = order_ops.complete_shipping_line(line, order_name=order_name)
                    result.add_operation(op_result)

                    if op_result.success:
                        order_lines_completed += 1
                        lines_completed += 1
                    else:
                        self.log.warning(
                            f"Failed to complete shipping line {line['id']} on order {order_name}",
                            data={"error": op_result.error},
                        )

                # Post chatter message if any lines were completed
                if order_lines_completed > 0:
                    msg_result = order_ops.post_shipping_completion_message(
                        order_id=order_id,
                        order_name=order_name,
                        lines_completed=order_lines_completed,
                    )

                    if not msg_result.success and not self.dry_run:
                        self.log.warning(
                            f"Failed to post chatter message on order {order_name}",
                            data={"error": msg_result.error},
                        )

                    orders_completed += 1
                    self.log.success(
                        order_id,
                        f"Completed {order_lines_completed} shipping line(s) on {order_name}",
                    )

            except Exception as e:
                self.log.error(
                    f"Exception processing order {order_name}",
                    record_id=order_id,
                    error=str(e),
                )
                result.errors.append(f"Order {order_name}: {e}")

        # Set KPIs
        result.kpis = self._build_kpis(result, orders_completed, lines_completed)

        result.complete()
        return result

    def _build_kpis(
        self,
        result: JobResult,
        orders_completed: int,
        lines_completed: int,
    ) -> dict:
        """Build KPIs dict for the job result."""
        return {
            "orders_checked": result.records_checked,
            "orders_completed": orders_completed,
            "lines_completed": lines_completed,
            "exceptions": len(result.errors),
        }

if __name__ == "__main__":
    import sys
    print("\n" + "=" * 60)
    print("Use main.py to run jobs (avoids import warnings):")
    print("=" * 60)
    print("\n  python main.py run complete_shipping_only_orders --dry-run")
    print("  python main.py run complete_shipping_only_orders --dry-run order_ids=455346")
    print("  python main.py run complete_shipping_only_orders --dry-run limit=10")
    print("  python main.py run complete_shipping_only_orders order_ids=455346  # Live!")
    print("\n" + "=" * 60 + "\n")
    sys.exit(0)
