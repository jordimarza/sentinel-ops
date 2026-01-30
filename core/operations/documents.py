"""
Document Creation Operations

Operations for creating Odoo documents (sale.order, stock.picking) with validation.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from core.operations.base import BaseOperation
from core.result import OperationResult

logger = logging.getLogger(__name__)


@dataclass
class ValidationError:
    """A single validation error with row context."""

    row_number: int
    field: str
    value: str
    error: str

    def to_dict(self) -> dict:
        return {
            "row_number": self.row_number,
            "field": self.field,
            "value": str(self.value),
            "error": self.error,
        }


@dataclass
class ResolveResult:
    """Result of resolving a record (partner, product, etc.)."""

    success: bool
    record_id: Optional[int] = None
    error: Optional[str] = None
    matched_count: int = 0
    matched_ids: list[int] = field(default_factory=list)

    @classmethod
    def ok(cls, record_id: int) -> "ResolveResult":
        return cls(success=True, record_id=record_id, matched_count=1)

    @classmethod
    def fail(cls, error: str) -> "ResolveResult":
        return cls(success=False, error=error)

    @classmethod
    def multiple(cls, ids: list[int]) -> "ResolveResult":
        return cls(
            success=False,
            error=f"Multiple records match (found {len(ids)}): {ids[:10]}",
            matched_count=len(ids),
            matched_ids=ids[:10],
        )


class DocumentCreationOperations(BaseOperation):
    """
    Operations for creating Odoo documents with validation.

    Two-phase approach:
    1. Validate ALL records first (partners, products, references)
    2. Create documents only if 100% validation passes
    """

    # Model constants
    PARTNER_MODEL = "res.partner"
    PRODUCT_MODEL = "product.product"
    PRICELIST_MODEL = "product.pricelist"
    WAREHOUSE_MODEL = "stock.warehouse"
    PICKING_TYPE_MODEL = "stock.picking.type"
    LOCATION_MODEL = "stock.location"
    PAYMENT_TERM_MODEL = "account.payment.term"
    TAG_MODEL = "crm.tag"
    SO_MODEL = "sale.order"
    SO_LINE_MODEL = "sale.order.line"
    PICKING_MODEL = "stock.picking"
    MOVE_MODEL = "stock.move"
    PO_MODEL = "purchase.order"
    PO_LINE_MODEL = "purchase.order.line"
    # Additional models for validation
    USER_MODEL = "res.users"
    COMPANY_MODEL = "res.company"
    TEAM_MODEL = "crm.team"
    FISCAL_POSITION_MODEL = "account.fiscal.position"
    ANALYTIC_ACCOUNT_MODEL = "account.analytic.account"
    INCOTERM_MODEL = "account.incoterms"
    CARRIER_MODEL = "delivery.carrier"
    CURRENCY_MODEL = "res.currency"
    CAMPAIGN_MODEL = "utm.campaign"
    MEDIUM_MODEL = "utm.medium"
    SOURCE_MODEL = "utm.source"

    # --- Lookup/Resolution Methods ---

    def resolve_partner(
        self,
        partner_id: Optional[int] = None,
        partner_name: Optional[str] = None,
        partner_ref: Optional[str] = None,
    ) -> ResolveResult:
        """
        Resolve a partner by ID, name, or ref.

        Args:
            partner_id: Explicit partner ID
            partner_name: Partner display name to search
            partner_ref: Partner reference (customer code) to search

        Returns:
            ResolveResult with partner ID or error
        """
        if partner_id:
            # Verify the ID exists
            result = self.odoo.search(self.PARTNER_MODEL, [("id", "=", partner_id)])
            if result:
                return ResolveResult.ok(partner_id)
            return ResolveResult.fail(f"Partner ID {partner_id} not found")

        if partner_ref:
            # Search by reference first (more specific)
            partners = self.odoo.search(self.PARTNER_MODEL, [("ref", "=", partner_ref)])
            if len(partners) == 1:
                return ResolveResult.ok(partners[0])
            if len(partners) > 1:
                return ResolveResult.multiple(partners)
            # Fall through to name search if ref not found

        if partner_name:
            # Search by exact name match
            partners = self.odoo.search(
                self.PARTNER_MODEL, [("name", "=", partner_name)]
            )
            if len(partners) == 1:
                return ResolveResult.ok(partners[0])
            if len(partners) > 1:
                return ResolveResult.multiple(partners)
            return ResolveResult.fail(f"Partner not found: {partner_name}")

        return ResolveResult.fail("No partner identifier provided")

    def resolve_product(
        self,
        product_id: Optional[int] = None,
        product_ref: Optional[str] = None,
        product_name: Optional[str] = None,
    ) -> ResolveResult:
        """
        Resolve a product by ID, default_code (ref), or name.

        Args:
            product_id: Explicit product ID
            product_ref: Product default_code (SKU/internal reference)
            product_name: Product name to search

        Returns:
            ResolveResult with product ID or error
        """
        if product_id:
            result = self.odoo.search(self.PRODUCT_MODEL, [("id", "=", product_id)])
            if result:
                return ResolveResult.ok(product_id)
            return ResolveResult.fail(f"Product ID {product_id} not found")

        if product_ref:
            # Search by default_code (exact match)
            products = self.odoo.search(
                self.PRODUCT_MODEL, [("default_code", "=", product_ref)]
            )
            if len(products) == 1:
                return ResolveResult.ok(products[0])
            if len(products) > 1:
                return ResolveResult.multiple(products)
            return ResolveResult.fail(f"Product not found: {product_ref}")

        if product_name:
            products = self.odoo.search(
                self.PRODUCT_MODEL, [("name", "=", product_name)]
            )
            if len(products) == 1:
                return ResolveResult.ok(products[0])
            if len(products) > 1:
                return ResolveResult.multiple(products)
            return ResolveResult.fail(f"Product not found: {product_name}")

        return ResolveResult.fail("No product identifier provided")

    def verify_record_exists(
        self, model: str, record_id: int, field_name: str
    ) -> ResolveResult:
        """
        Verify a record exists in a model.

        Args:
            model: Odoo model name
            record_id: Record ID to verify
            field_name: Field name (for error message)

        Returns:
            ResolveResult
        """
        result = self.odoo.search(model, [("id", "=", record_id)])
        if result:
            return ResolveResult.ok(record_id)
        return ResolveResult.fail(f"{field_name} ID {record_id} not found in {model}")

    # --- Validation Methods (Phase 1) ---

    def validate_document(
        self, doc: dict, row_offset: int = 0
    ) -> list[ValidationError]:
        """
        Validate a single document (header + lines).

        Args:
            doc: Document dict with header and lines
            row_offset: Row number offset for error reporting

        Returns:
            List of validation errors (empty if valid)
        """
        errors: list[ValidationError] = []
        header = doc.get("header", {})
        lines = doc.get("lines", [])
        doc_row = doc.get("row_number", row_offset)
        doc_type = doc.get("document_type", "sale.order")

        # Validate partner
        partner_id = header.get("partner_id")
        partner_name = header.get("partner_name")
        partner_ref = header.get("partner_ref")

        if partner_id or partner_name or partner_ref:
            result = self.resolve_partner(partner_id, partner_name, partner_ref)
            if not result.success:
                errors.append(
                    ValidationError(
                        row_number=doc_row,
                        field="partner_name" if partner_name else "partner_id",
                        value=str(partner_name or partner_id or partner_ref),
                        error=result.error or "Partner not found",
                    )
                )
        else:
            errors.append(
                ValidationError(
                    row_number=doc_row,
                    field="partner",
                    value="",
                    error="No partner identifier provided",
                )
            )

        # Validate fixed record references in header
        # These are all the fields that reference other models and must exist
        fixed_fields = [
            # Sale order fields
            ("pricelist_id", self.PRICELIST_MODEL),
            ("warehouse_id", self.WAREHOUSE_MODEL),
            ("payment_term_id", self.PAYMENT_TERM_MODEL),
            ("user_id", self.USER_MODEL),  # Salesperson
            ("team_id", self.TEAM_MODEL),  # Sales team
            ("company_id", self.COMPANY_MODEL),
            ("fiscal_position_id", self.FISCAL_POSITION_MODEL),
            ("analytic_account_id", self.ANALYTIC_ACCOUNT_MODEL),
            ("incoterm", self.INCOTERM_MODEL),  # Incoterms
            ("carrier_id", self.CARRIER_MODEL),  # Delivery carrier
            ("currency_id", self.CURRENCY_MODEL),
            # Marketing/UTM fields
            ("campaign_id", self.CAMPAIGN_MODEL),
            ("medium_id", self.MEDIUM_MODEL),
            ("source_id", self.SOURCE_MODEL),
            # Stock picking fields
            ("picking_type_id", self.PICKING_TYPE_MODEL),
            ("location_id", self.LOCATION_MODEL),
            ("location_dest_id", self.LOCATION_MODEL),
            ("owner_id", self.PARTNER_MODEL),  # Owner for consignment
        ]

        for field_name, model in fixed_fields:
            value = header.get(field_name)
            if value is not None:
                result = self.verify_record_exists(model, value, field_name)
                if not result.success:
                    errors.append(
                        ValidationError(
                            row_number=doc_row,
                            field=field_name,
                            value=str(value),
                            error=result.error or f"{field_name} not found",
                        )
                    )

        # Validate partner_shipping_id and partner_invoice_id if provided
        shipping_partner = header.get("partner_shipping_id")
        if shipping_partner is not None:
            result = self.verify_record_exists(
                self.PARTNER_MODEL, shipping_partner, "partner_shipping_id"
            )
            if not result.success:
                errors.append(
                    ValidationError(
                        row_number=doc_row,
                        field="partner_shipping_id",
                        value=str(shipping_partner),
                        error=result.error or "Shipping partner not found",
                    )
                )

        invoice_partner = header.get("partner_invoice_id")
        if invoice_partner is not None:
            result = self.verify_record_exists(
                self.PARTNER_MODEL, invoice_partner, "partner_invoice_id"
            )
            if not result.success:
                errors.append(
                    ValidationError(
                        row_number=doc_row,
                        field="partner_invoice_id",
                        value=str(invoice_partner),
                        error=result.error or "Invoice partner not found",
                    )
                )

        # Validate lines
        for line in lines:
            line_row = line.get("row_number", doc_row)
            product_id = line.get("product_id")
            product_ref = line.get("product_ref") or line.get("product_sku")  # Accept both
            product_name = line.get("product_name")

            if product_id or product_ref or product_name:
                result = self.resolve_product(product_id, product_ref, product_name)
                if not result.success:
                    errors.append(
                        ValidationError(
                            row_number=line_row,
                            field="product_ref" if product_ref else "product_id",
                            value=str(product_ref or product_id or product_name),
                            error=result.error or "Product not found",
                        )
                    )
            else:
                errors.append(
                    ValidationError(
                        row_number=line_row,
                        field="product",
                        value="",
                        error="No product identifier provided",
                    )
                )

            # Validate quantity
            qty = line.get("quantity")
            if qty is None or qty <= 0:
                errors.append(
                    ValidationError(
                        row_number=line_row,
                        field="quantity",
                        value=str(qty),
                        error="Quantity must be positive",
                    )
                )

        return errors

    def validate_all(
        self, documents: list[dict]
    ) -> tuple[bool, list[ValidationError], dict]:
        """
        Validate all documents in a batch.

        Args:
            documents: List of document dicts

        Returns:
            Tuple of (all_valid, errors, stats)
        """
        all_errors: list[ValidationError] = []
        valid_count = 0
        invalid_count = 0

        for doc in documents:
            doc_errors = self.validate_document(doc)
            if doc_errors:
                all_errors.extend(doc_errors)
                invalid_count += 1
            else:
                valid_count += 1

        stats = {
            "valid_count": valid_count,
            "invalid_count": invalid_count,
            "total_count": len(documents),
        }

        return len(all_errors) == 0, all_errors, stats

    # --- Tag Management ---

    def _ensure_tag(self, tag_name: str) -> int:
        """
        Find or create a tag by name.

        Tags are NOT validated - they are created on-the-fly if needed.

        Args:
            tag_name: Name of the tag

        Returns:
            Tag ID
        """
        tags = self.odoo.search_read(
            self.TAG_MODEL, [("name", "=", tag_name)], fields=["id"], limit=1
        )
        if tags:
            return tags[0]["id"]

        # Create the tag
        tag_id = self.odoo.create(self.TAG_MODEL, {"name": tag_name})
        self.log.info(f"Created tag '{tag_name}' with id={tag_id}")
        return tag_id

    # --- Creation Methods (Phase 2) ---

    def create_sale_order(
        self,
        header: dict,
        lines: list[dict],
        metadata: dict,
    ) -> OperationResult:
        """
        Create a sale order with lines.

        Args:
            header: Order header fields (partner_id/name, pricelist_id, etc.)
            lines: List of line dicts (product_ref, quantity)
            metadata: Creation metadata for audit trail

        Returns:
            OperationResult with created order ID
        """
        if self.dry_run:
            self.log.skip(
                0,
                f"Would create sale.order with {len(lines)} lines",
            )
            return OperationResult.skipped(
                record_id=0,
                model=self.SO_MODEL,
                reason=f"Dry run: would create sale.order with {len(lines)} lines",
            )

        try:
            # Resolve partner
            partner_result = self.resolve_partner(
                header.get("partner_id"),
                header.get("partner_name"),
                header.get("partner_ref"),
            )
            if not partner_result.success:
                return OperationResult.fail(
                    model=self.SO_MODEL,
                    action="create",
                    error=f"Partner resolution failed: {partner_result.error}",
                )

            # Build order values
            order_vals = {
                "partner_id": partner_result.record_id,
            }

            # --- Core optional fields ---
            if header.get("pricelist_id"):
                order_vals["pricelist_id"] = header["pricelist_id"]
            if header.get("warehouse_id"):
                order_vals["warehouse_id"] = header["warehouse_id"]
            if header.get("payment_term_id"):
                order_vals["payment_term_id"] = header["payment_term_id"]

            # --- Reference fields ---
            if header.get("client_order_ref"):
                order_vals["client_order_ref"] = header["client_order_ref"]

            # --- Date fields ---
            if header.get("commitment_date"):
                order_vals["commitment_date"] = header["commitment_date"]
            if header.get("date_order"):
                order_vals["date_order"] = header["date_order"]
            if header.get("validity_date"):
                order_vals["validity_date"] = header["validity_date"]

            # --- Team/User fields ---
            if header.get("user_id"):
                order_vals["user_id"] = header["user_id"]
            if header.get("team_id"):
                order_vals["team_id"] = header["team_id"]

            # --- Company/Accounting fields ---
            if header.get("company_id"):
                order_vals["company_id"] = header["company_id"]
            if header.get("fiscal_position_id"):
                order_vals["fiscal_position_id"] = header["fiscal_position_id"]
            if header.get("analytic_account_id"):
                order_vals["analytic_account_id"] = header["analytic_account_id"]

            # --- Shipping fields ---
            if header.get("incoterm"):
                order_vals["incoterm"] = header["incoterm"]
            if header.get("incoterm_location"):
                order_vals["incoterm_location"] = header["incoterm_location"]
            if header.get("carrier_id"):
                order_vals["carrier_id"] = header["carrier_id"]

            # --- Delivery/Invoice addresses (if different from partner) ---
            if header.get("partner_shipping_id"):
                order_vals["partner_shipping_id"] = header["partner_shipping_id"]
            if header.get("partner_invoice_id"):
                order_vals["partner_invoice_id"] = header["partner_invoice_id"]

            # --- Notes/References ---
            if header.get("note"):
                order_vals["note"] = header["note"]
            if header.get("reference"):
                order_vals["reference"] = header["reference"]

            # --- Currency ---
            if header.get("currency_id"):
                order_vals["currency_id"] = header["currency_id"]

            # --- Expected/Scheduled dates ---
            if header.get("expected_date"):
                order_vals["expected_date"] = header["expected_date"]

            # --- ALOHAS-specific fields ---
            if header.get("ah_status"):
                order_vals["ah_status"] = header["ah_status"]
            if header.get("ah_prepayment_status"):
                order_vals["ah_prepayment_status"] = header["ah_prepayment_status"]

            # --- Marketing attribution ---
            if header.get("campaign_id"):
                order_vals["campaign_id"] = header["campaign_id"]
            if header.get("medium_id"):
                order_vals["medium_id"] = header["medium_id"]
            if header.get("source_id"):
                order_vals["source_id"] = header["source_id"]

            # --- Custom fields (any additional fields passed through as-is) ---
            custom_fields = header.get("custom_fields", {})
            for key, value in custom_fields.items():
                order_vals[key] = value

            # Add origin from metadata
            origin_parts = []
            if metadata.get("source"):
                origin_parts.append(f"[{metadata['source']}]")
            if metadata.get("filename"):
                origin_parts.append(metadata["filename"])
            if origin_parts:
                order_vals["origin"] = " ".join(origin_parts)

            # Create order
            order_id = self.odoo.create(self.SO_MODEL, order_vals)
            self.log.success(order_id, f"Created sale.order {order_id}")

            # Create order lines
            for line in lines:
                # Resolve product
                product_result = self.resolve_product(
                    line.get("product_id"),
                    line.get("product_ref") or line.get("product_sku"),  # Accept both
                    line.get("product_name"),
                )
                if not product_result.success:
                    self.log.error(
                        f"Product resolution failed for line: {product_result.error}",
                        record_id=order_id,
                    )
                    continue

                line_vals = {
                    "order_id": order_id,
                    "product_id": product_result.record_id,
                    "product_uom_qty": line["quantity"],
                }

                # --- Price fields ---
                if line.get("price_unit") is not None:
                    line_vals["price_unit"] = line["price_unit"]
                if line.get("discount") is not None:
                    line_vals["discount"] = line["discount"]

                # --- Description override ---
                if line.get("name"):
                    line_vals["name"] = line["name"]

                # --- UoM override (if not using product default) ---
                if line.get("product_uom"):
                    line_vals["product_uom"] = line["product_uom"]

                # --- Customer lead time ---
                if line.get("customer_lead") is not None:
                    line_vals["customer_lead"] = line["customer_lead"]

                # --- Analytic distribution (if line-level analytics) ---
                if line.get("analytic_distribution"):
                    line_vals["analytic_distribution"] = line["analytic_distribution"]

                # --- Line ordering ---
                if line.get("sequence") is not None:
                    line_vals["sequence"] = line["sequence"]

                # --- Custom fields for line ---
                line_custom = line.get("custom_fields", {})
                for key, value in line_custom.items():
                    line_vals[key] = value

                self.odoo.create(self.SO_LINE_MODEL, line_vals)

            # Add tags if specified
            tags = header.get("tags", [])
            for tag_name in tags:
                tag_id = self._ensure_tag(tag_name)
                self.odoo.write(
                    self.SO_MODEL, [order_id], {"tag_ids": [(4, tag_id)]}
                )

            # Post creation message
            self._post_creation_message(
                self.SO_MODEL, order_id, metadata, len(lines)
            )

            # Read back order name for result
            order_data = self.odoo.read(self.SO_MODEL, [order_id], ["name"])
            order_name = order_data[0]["name"] if order_data else f"Order #{order_id}"

            return OperationResult.ok(
                record_id=order_id,
                model=self.SO_MODEL,
                action="create",
                message=f"Created {order_name} with {len(lines)} lines",
                data={"order_name": order_name, "line_count": len(lines)},
                record_name=order_name,
            )

        except Exception as e:
            self.log.error(f"Failed to create sale.order: {e}")
            return OperationResult.fail(
                model=self.SO_MODEL,
                action="create",
                error=str(e),
            )

    def create_stock_picking(
        self,
        header: dict,
        lines: list[dict],
        metadata: dict,
    ) -> OperationResult:
        """
        Create a stock picking (transfer) with moves.

        Args:
            header: Picking header fields (partner_id, picking_type_id, locations)
            lines: List of move dicts (product_ref, quantity)
            metadata: Creation metadata for audit trail

        Returns:
            OperationResult with created picking ID
        """
        if self.dry_run:
            self.log.skip(
                0,
                f"Would create stock.picking with {len(lines)} moves",
            )
            return OperationResult.skipped(
                record_id=0,
                model=self.PICKING_MODEL,
                reason=f"Dry run: would create stock.picking with {len(lines)} moves",
            )

        try:
            # Resolve partner (optional for pickings)
            partner_id = None
            if header.get("partner_id") or header.get("partner_name"):
                partner_result = self.resolve_partner(
                    header.get("partner_id"),
                    header.get("partner_name"),
                    header.get("partner_ref"),
                )
                if partner_result.success:
                    partner_id = partner_result.record_id

            # Build picking values
            picking_vals = {
                "picking_type_id": header["picking_type_id"],
            }

            # --- Partner ---
            if partner_id:
                picking_vals["partner_id"] = partner_id

            # --- Location fields ---
            if header.get("location_id"):
                picking_vals["location_id"] = header["location_id"]
            if header.get("location_dest_id"):
                picking_vals["location_dest_id"] = header["location_dest_id"]

            # --- Date fields ---
            if header.get("scheduled_date"):
                picking_vals["scheduled_date"] = header["scheduled_date"]
            if header.get("date_deadline"):
                picking_vals["date_deadline"] = header["date_deadline"]
            if header.get("planned_date"):
                # Some Odoo versions use planned_date vs scheduled_date
                picking_vals["planned_date"] = header["planned_date"]

            # --- Company/User ---
            if header.get("company_id"):
                picking_vals["company_id"] = header["company_id"]
            if header.get("user_id"):
                picking_vals["user_id"] = header["user_id"]

            # --- Ownership/Consignment ---
            if header.get("owner_id"):
                picking_vals["owner_id"] = header["owner_id"]

            # --- Priority ---
            if header.get("priority"):
                picking_vals["priority"] = header["priority"]

            # --- Notes ---
            if header.get("note"):
                picking_vals["note"] = header["note"]

            # --- Related documents ---
            if header.get("sale_id"):
                picking_vals["sale_id"] = header["sale_id"]
            if header.get("purchase_id"):
                picking_vals["purchase_id"] = header["purchase_id"]

            # --- Move type (direct/one/multi-step) ---
            if header.get("move_type"):
                picking_vals["move_type"] = header["move_type"]

            # --- Carrier/Tracking ---
            if header.get("carrier_id"):
                picking_vals["carrier_id"] = header["carrier_id"]
            if header.get("carrier_tracking_ref"):
                picking_vals["carrier_tracking_ref"] = header["carrier_tracking_ref"]

            # --- ALOHAS-specific fields ---
            if header.get("ah_picking_status"):
                picking_vals["ah_picking_status"] = header["ah_picking_status"]

            # --- Custom fields (any additional fields passed through as-is) ---
            custom_fields = header.get("custom_fields", {})
            for key, value in custom_fields.items():
                picking_vals[key] = value

            # Add origin from metadata
            origin_parts = []
            if metadata.get("source"):
                origin_parts.append(f"[{metadata['source']}]")
            if metadata.get("filename"):
                origin_parts.append(metadata["filename"])
            if origin_parts:
                picking_vals["origin"] = " ".join(origin_parts)

            # Create picking
            picking_id = self.odoo.create(self.PICKING_MODEL, picking_vals)
            self.log.success(picking_id, f"Created stock.picking {picking_id}")

            # Get picking details for moves (need locations)
            picking_data = self.odoo.read(
                self.PICKING_MODEL,
                [picking_id],
                ["name", "location_id", "location_dest_id"],
            )[0]
            picking_name = picking_data["name"]

            # Create stock moves
            for line in lines:
                # Resolve product
                product_result = self.resolve_product(
                    line.get("product_id"),
                    line.get("product_ref") or line.get("product_sku"),  # Accept both
                    line.get("product_name"),
                )
                if not product_result.success:
                    self.log.error(
                        f"Product resolution failed for line: {product_result.error}",
                        record_id=picking_id,
                    )
                    continue

                # Get product details for move
                product_data = self.odoo.read(
                    self.PRODUCT_MODEL,
                    [product_result.record_id],
                    ["name", "uom_id"],
                )[0]

                move_vals = {
                    "picking_id": picking_id,
                    "product_id": product_result.record_id,
                    "product_uom_qty": line["quantity"],
                    "product_uom": product_data["uom_id"][0],
                    "name": line.get("name") or product_data["name"],
                    "location_id": picking_data["location_id"][0],
                    "location_dest_id": picking_data["location_dest_id"][0],
                }

                # --- Date override ---
                if line.get("date"):
                    move_vals["date"] = line["date"]

                # --- Lot/Serial (for tracked products) ---
                if line.get("lot_id"):
                    move_vals["lot_id"] = line["lot_id"]

                # --- Move ordering ---
                if line.get("sequence") is not None:
                    move_vals["sequence"] = line["sequence"]

                # --- Custom fields for move ---
                line_custom = line.get("custom_fields", {})
                for key, value in line_custom.items():
                    move_vals[key] = value

                self.odoo.create(self.MOVE_MODEL, move_vals)

            # Post creation message
            self._post_creation_message(
                self.PICKING_MODEL, picking_id, metadata, len(lines)
            )

            return OperationResult.ok(
                record_id=picking_id,
                model=self.PICKING_MODEL,
                action="create",
                message=f"Created {picking_name} with {len(lines)} moves",
                data={"picking_name": picking_name, "move_count": len(lines)},
                record_name=picking_name,
            )

        except Exception as e:
            self.log.error(f"Failed to create stock.picking: {e}")
            return OperationResult.fail(
                model=self.PICKING_MODEL,
                action="create",
                error=str(e),
            )

    def create_purchase_order(
        self,
        header: dict,
        lines: list[dict],
        metadata: dict,
    ) -> OperationResult:
        """
        Create a purchase order with lines.

        Args:
            header: Order header fields (partner_id/name, etc.)
            lines: List of line dicts (product_ref, quantity, price_unit)
            metadata: Creation metadata for audit trail

        Returns:
            OperationResult with created order ID
        """
        if self.dry_run:
            self.log.skip(
                0,
                f"Would create purchase.order with {len(lines)} lines",
            )
            return OperationResult.skipped(
                record_id=0,
                model=self.PO_MODEL,
                reason=f"Dry run: would create purchase.order with {len(lines)} lines",
            )

        try:
            # Resolve partner (supplier)
            partner_result = self.resolve_partner(
                header.get("partner_id"),
                header.get("partner_name"),
                header.get("partner_ref"),
            )
            if not partner_result.success:
                return OperationResult.fail(
                    model=self.PO_MODEL,
                    action="create",
                    error=f"Partner resolution failed: {partner_result.error}",
                )

            # Build order values
            order_vals = {
                "partner_id": partner_result.record_id,
            }

            # --- Core optional fields ---
            if header.get("currency_id"):
                order_vals["currency_id"] = header["currency_id"]
            if header.get("company_id"):
                order_vals["company_id"] = header["company_id"]
            if header.get("payment_term_id"):
                order_vals["payment_term_id"] = header["payment_term_id"]

            # --- Date fields ---
            if header.get("date_order"):
                order_vals["date_order"] = header["date_order"]
            if header.get("date_planned"):
                order_vals["date_planned"] = header["date_planned"]

            # --- Reference fields ---
            if header.get("partner_ref"):
                order_vals["partner_ref"] = header["partner_ref"]

            # --- Picking/Warehouse ---
            if header.get("picking_type_id"):
                order_vals["picking_type_id"] = header["picking_type_id"]

            # --- User ---
            if header.get("user_id"):
                order_vals["user_id"] = header["user_id"]

            # --- Fiscal position ---
            if header.get("fiscal_position_id"):
                order_vals["fiscal_position_id"] = header["fiscal_position_id"]

            # --- Incoterms ---
            if header.get("incoterm_id"):
                order_vals["incoterm_id"] = header["incoterm_id"]

            # --- Notes ---
            if header.get("notes"):
                order_vals["notes"] = header["notes"]

            # --- Custom fields ---
            custom_fields = header.get("custom_fields", {})
            for key, value in custom_fields.items():
                order_vals[key] = value

            # Add origin from metadata
            origin_parts = []
            if metadata.get("source"):
                origin_parts.append(f"[{metadata['source']}]")
            if metadata.get("filename"):
                origin_parts.append(metadata["filename"])
            if origin_parts:
                order_vals["origin"] = " ".join(origin_parts)

            # Create order
            order_id = self.odoo.create(self.PO_MODEL, order_vals)
            self.log.success(order_id, f"Created purchase.order {order_id}")

            # Create order lines
            for line in lines:
                # Resolve product
                product_result = self.resolve_product(
                    line.get("product_id"),
                    line.get("product_ref") or line.get("product_sku"),  # Accept both
                    line.get("product_name"),
                )
                if not product_result.success:
                    self.log.error(
                        f"Product resolution failed for line: {product_result.error}",
                        record_id=order_id,
                    )
                    continue

                # Get product details
                product_data = self.odoo.read(
                    self.PRODUCT_MODEL,
                    [product_result.record_id],
                    ["name", "uom_po_id", "uom_id"],
                )[0]

                # Use purchase UoM if available, otherwise default UoM
                uom_id = product_data.get("uom_po_id")
                if uom_id:
                    uom_id = uom_id[0]
                else:
                    uom_id = product_data["uom_id"][0]

                line_vals = {
                    "order_id": order_id,
                    "product_id": product_result.record_id,
                    "product_qty": line["quantity"],
                    "product_uom": uom_id,
                    "name": line.get("name") or product_data["name"],
                }

                # --- Price ---
                if line.get("price_unit") is not None:
                    line_vals["price_unit"] = line["price_unit"]

                # --- Date planned ---
                if line.get("date_planned"):
                    line_vals["date_planned"] = line["date_planned"]

                # --- Taxes (optional override) ---
                if line.get("taxes_id"):
                    line_vals["taxes_id"] = line["taxes_id"]

                # --- Analytic ---
                if line.get("analytic_distribution"):
                    line_vals["analytic_distribution"] = line["analytic_distribution"]

                # --- Line ordering ---
                if line.get("sequence") is not None:
                    line_vals["sequence"] = line["sequence"]

                # --- Custom fields for line ---
                line_custom = line.get("custom_fields", {})
                for key, value in line_custom.items():
                    line_vals[key] = value

                self.odoo.create(self.PO_LINE_MODEL, line_vals)

            # Post creation message
            self._post_creation_message(
                self.PO_MODEL, order_id, metadata, len(lines)
            )

            # Read back order name for result
            order_data = self.odoo.read(self.PO_MODEL, [order_id], ["name"])
            order_name = order_data[0]["name"] if order_data else f"PO #{order_id}"

            return OperationResult.ok(
                record_id=order_id,
                model=self.PO_MODEL,
                action="create",
                message=f"Created {order_name} with {len(lines)} lines",
                data={"order_name": order_name, "line_count": len(lines)},
                record_name=order_name,
            )

        except Exception as e:
            self.log.error(f"Failed to create purchase.order: {e}")
            return OperationResult.fail(
                model=self.PO_MODEL,
                action="create",
                error=str(e),
            )

    # --- Confirmation Methods ---

    def confirm_document(
        self,
        document_type: str,
        record_id: int,
    ) -> OperationResult:
        """
        Confirm a document (draft → confirmed state).

        Args:
            document_type: Type of document (sale.order, stock.picking, purchase.order)
            record_id: Record ID to confirm

        Returns:
            OperationResult
        """
        if self.dry_run:
            return OperationResult.skipped(
                record_id=record_id,
                model=document_type,
                reason=f"Dry run: would confirm {document_type} {record_id}",
            )

        try:
            if document_type == "sale.order":
                # Confirm quotation → sales order
                self.odoo.execute(self.SO_MODEL, "action_confirm", [[record_id]])
                self.log.success(record_id, f"Confirmed sale.order {record_id}")
                return OperationResult.ok(
                    record_id=record_id,
                    model=self.SO_MODEL,
                    action="confirm",
                    message=f"Confirmed sale.order {record_id}",
                )

            elif document_type == "stock.picking":
                # Reserve stock (action_assign)
                # Note: button_validate() would fully validate, but that requires
                # stock availability and lot/serial assignment
                self.odoo.execute(self.PICKING_MODEL, "action_assign", [[record_id]])
                self.log.success(record_id, f"Reserved stock for picking {record_id}")
                return OperationResult.ok(
                    record_id=record_id,
                    model=self.PICKING_MODEL,
                    action="confirm",
                    message=f"Reserved stock for picking {record_id}",
                )

            elif document_type == "purchase.order":
                # Confirm RFQ → purchase order
                self.odoo.execute(self.PO_MODEL, "button_confirm", [[record_id]])
                self.log.success(record_id, f"Confirmed purchase.order {record_id}")
                return OperationResult.ok(
                    record_id=record_id,
                    model=self.PO_MODEL,
                    action="confirm",
                    message=f"Confirmed purchase.order {record_id}",
                )

            else:
                return OperationResult.fail(
                    record_id=record_id,
                    model=document_type,
                    action="confirm",
                    error=f"Unknown document type for confirmation: {document_type}",
                )

        except Exception as e:
            self.log.error(f"Failed to confirm {document_type} {record_id}: {e}")
            return OperationResult.fail(
                record_id=record_id,
                model=document_type,
                action="confirm",
                error=str(e),
            )

    def _post_creation_message(
        self,
        model: str,
        record_id: int,
        metadata: dict,
        line_count: int,
    ) -> OperationResult:
        """
        Post a chatter message documenting the creation.

        Args:
            model: Odoo model name
            record_id: Record ID
            metadata: Creation metadata
            line_count: Number of lines created

        Returns:
            OperationResult
        """
        request_id = self.ctx.request_id if self.ctx else "N/A"

        # Build metadata details
        meta_items = []
        if metadata.get("source"):
            meta_items.append(f"<li><strong>Source:</strong> {metadata['source']}</li>")
        if metadata.get("owner"):
            meta_items.append(f"<li><strong>Owner:</strong> {metadata['owner']}</li>")
        if metadata.get("filename"):
            meta_items.append(
                f"<li><strong>File:</strong> {metadata['filename']}</li>"
            )
        if metadata.get("origin_folder"):
            meta_items.append(
                f"<li><strong>Folder:</strong> {metadata['origin_folder']}</li>"
            )

        meta_html = "\n".join(meta_items) if meta_items else "<li>N/A</li>"

        body = f"""<div style="font-family: Arial, sans-serif; line-height: 1.6;">
    <p><strong>Sentinel-Ops: Document Created</strong></p>
    <ul style="margin: 10px 0; padding-left: 20px;">
        <li><strong>Lines:</strong> {line_count}</li>
        {meta_html}
    </ul>
    <p style="color: #666; font-size: 0.9em;">
        Request ID: {request_id}
    </p>
</div>"""

        return self._safe_message_post(
            model=model,
            record_id=record_id,
            body=body,
            message_type="notification",
        )
