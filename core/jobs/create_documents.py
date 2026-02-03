"""
Create Documents Job

Creates Odoo documents (sale.order, stock.picking) from JSON or TSV input with full validation.

Supports:
- JSON input (existing format)
- TSV template input (new format with automatic grouping)
"""

import csv
import io
import json
import logging
from pathlib import Path
from typing import Optional

from core.config import get_settings
from core.jobs.base import BaseJob
from core.jobs.registry import register_job
from core.operations.documents import DocumentCreationOperations, ValidationError
from core.result import JobResult, JobType

logger = logging.getLogger(__name__)


def _build_odoo_url(record_id: int, model: str, base_url: str = "") -> str:
    """Build Odoo URL for a record."""
    if not base_url:
        settings = get_settings()
        base_url = settings.odoo_url
    base_url = base_url.rstrip("/")
    return f"{base_url}/web#id={record_id}&model={model}&view_type=form"


def _get_dev_odoo_client():
    """Get an Odoo client connected to the development instance."""
    from core.clients.odoo import OdooClient
    settings = get_settings()

    if not settings.is_dev_odoo_configured():
        raise ValueError(
            "Development Odoo not configured. Set ODOO_DEV_URL and ODOO_DEV_DB in .env.local"
        )

    dev_config = settings.get_dev_odoo_config()
    return OdooClient(
        url=dev_config["url"],
        db=dev_config["db"],
        username=dev_config["username"],
        password=dev_config["password"],
    )


@register_job(
    name="create_documents",
    description="Create Odoo documents (sale.order, stock.picking) from JSON input with validation",
    tags=["creation", "orders", "transfers", "import"],
)
class CreateDocumentsJob(BaseJob):
    """
    Create Odoo documents from JSON input.

    Two-phase approach:
    1. VALIDATION: Validate ALL records first (partners, products, references)
    2. CREATION: Create documents only if 100% validation passes

    Input formats:
    - json_input: JSON string with documents
    - file: Path to JSON file

    JSON structure:
    {
        "metadata": {
            "source": "n8n",
            "owner": "jordi@alohas.com",
            "origin_folder": "gdrive://shared/imports/...",
            "filename": "intercompany_2025-01-29.csv",
            "total_rows": 65,
            "total_documents": 5
        },
        "preset": "intercompany_so",  # optional
        "documents": [
            {
                "row_number": 1,
                "document_type": "sale.order",
                "header": {
                    "partner_name": "SUNSET (BRITAIN) LTD",
                    "partner_id": 796360,  # or partner_name/partner_ref
                    "pricelist_id": 1672,
                    "warehouse_id": 65,
                    "client_order_ref": "P12909",
                    "commitment_date": "2025-02-15",
                    "tags": ["intercompany", "import"],
                    "custom_fields": {}
                },
                "lines": [
                    {
                        "row_number": 2,
                        "product_ref": "BTWEC1-3040",
                        "quantity": 3
                    }
                ]
            }
        ]
    }
    """

    def run(
        self,
        json_input: Optional[str] = None,
        tsv_input: Optional[str] = None,
        file: Optional[str] = None,
        confirm: bool = False,
        use_dev: Optional[bool] = None,
        default_picking_type_id: Optional[int] = None,
        **params,
    ) -> JobResult:
        """
        Create documents from JSON or TSV input.

        Args:
            json_input: JSON string with document data
            tsv_input: TSV string with template format (auto-grouped by partner+delivery)
                       Columns: document_type, partner_name, delivery_address, product_sku,
                                quantity, commitment_date, scheduled_date, notes
            file: Path to JSON or TSV file with document data (detected by extension)
            confirm: If True, confirm documents after creation (draft → confirmed)
                     - sale.order: action_confirm() (quotation → sales order)
                     - stock.picking: action_assign() (reserve stock)
                     - purchase.order: button_confirm() (RFQ → purchase order)
            use_dev: Use development Odoo instance.
                     - None (default): auto-detect from ENVIRONMENT
                       - development → use dev Odoo (safe testing)
                       - production → use production Odoo
                     - True: force dev Odoo
                     - False: force production Odoo
            default_picking_type_id: Default picking type ID for stock.picking documents
                                     when not specified in the input (required for TSV imports
                                     with stock.picking documents)

        Returns:
            JobResult with created document IDs, names, and URLs or validation errors

        TSV Template Format:
            document_type   partner_name   delivery_address   product_sku   quantity   commitment_date   scheduled_date   notes
            sale.order      Partner Inc    Delivery Addr      SKU123        10         2025-02-02
            stock.picking   Partner Inc                       SKU456        5                            2025-02-02

        TSV Column Reference:
            Required:
                - document_type: "sale.order", "stock.picking", or "purchase.order"
                - partner_name: Partner name to search in Odoo (or partner_id for explicit ID)
                - product_sku: Product reference/SKU (or product_ref, product_id)
                - quantity: Line quantity

            Optional - Delivery Address (choose one):
                - delivery_address: Address name to search (must be child of partner)
                - delivery_address_id: Explicit Odoo partner ID for shipping address
                - (empty): Falls back to partner address as delivery address

            Optional - Dates:
                - commitment_date: For sale.order (YYYY-MM-DD)
                - scheduled_date: For stock.picking (YYYY-MM-DD)

            Optional - Other:
                - notes: Line description/notes
                - picking_type_id: For stock.picking (or use default_picking_type_id param)

        Date Handling:
            - For sale.order: uses commitment_date (falls back to scheduled_date if only that is provided)
            - For stock.picking: uses scheduled_date (falls back to commitment_date if only that is provided)

        Delivery Address Fallback:
            - If delivery_address or delivery_address_id is provided: uses that address
            - If neither is provided: uses the partner's own address as delivery address
        """
        # Store default_picking_type_id for TSV parsing
        self._default_picking_type_id = default_picking_type_id
        result = JobResult.from_context(self.ctx, parameters=params)

        # Determine which Odoo instance to use
        settings = get_settings()

        # Auto-detect use_dev based on environment if not explicitly set
        if use_dev is None:
            use_dev = not settings.is_production()
            self.log.info(
                f"Auto-detected use_dev={use_dev} from ENVIRONMENT={settings.environment}"
            )

        if use_dev:
            if not settings.is_dev_odoo_configured():
                result.errors.append(
                    "Development Odoo not configured. "
                    "Set ODOO_DEV_URL and ODOO_DEV_DB in .env.local, "
                    "or pass use_dev=false to use production (careful!)."
                )
                result.complete()
                return result
            odoo_client = _get_dev_odoo_client()
            odoo_base_url = settings.odoo_dev_url
            self.log.info(
                f"Using DEVELOPMENT Odoo: {settings.odoo_dev_url}",
                data={"db": settings.odoo_dev_db},
            )
        else:
            odoo_client = self.odoo
            odoo_base_url = settings.odoo_url
            self.log.warning(
                "Using PRODUCTION Odoo - documents will be created in live system!",
                data={"url": settings.odoo_url},
            )

        # Load input (supports JSON and TSV formats)
        data = self._load_input(json_input, tsv_input, file, odoo_client)
        if not data:
            result.errors.append("No input provided. Use json_input, tsv_input, or file parameter.")
            result.complete()
            return result

        metadata = data.get("metadata", {})
        documents = data.get("documents", [])

        if not documents:
            result.errors.append("No documents provided in input.")
            result.complete()
            return result

        result.records_checked = len(documents)

        # Create operations helper with selected Odoo client
        ops = DocumentCreationOperations(odoo_client, self.ctx, self.log)

        # --- PHASE 1: VALIDATION ---
        self.log.info(
            f"Phase 1: Validating {len(documents)} documents",
            data={"metadata": metadata},
        )

        all_valid, validation_errors, validation_stats = ops.validate_all(documents)

        if not all_valid:
            # Validation failed - return errors with row numbers
            self.log.error(
                f"Validation failed with {len(validation_errors)} errors",
                data={
                    "errors": [e.to_dict() for e in validation_errors[:10]],
                    "stats": validation_stats,
                },
            )

            result.status = "validation_failed"
            result.result_data = {
                "status": "validation_failed",
                "errors": [e.to_dict() for e in validation_errors],
                "valid_count": validation_stats["valid_count"],
                "invalid_count": validation_stats["invalid_count"],
                "total_count": validation_stats["total_count"],
            }

            # Add summary error
            result.errors.append(
                f"Validation failed: {len(validation_errors)} errors in "
                f"{validation_stats['invalid_count']}/{validation_stats['total_count']} documents"
            )

            result.kpis = {
                "phase": "validation",
                "validation_errors": len(validation_errors),
                "documents_valid": validation_stats["valid_count"],
                "documents_invalid": validation_stats["invalid_count"],
            }

            result.complete()
            return result

        self.log.info("Phase 1 complete: All documents validated successfully")

        # --- PHASE 2: CREATION ---
        if self.dry_run:
            self.log.info(
                f"Phase 2: Dry run - would create {len(documents)} documents"
                + (f" and confirm them" if confirm else ""),
            )
            result.result_data = {
                "status": "dry_run",
                "would_create": len(documents),
                "would_confirm": confirm,
                "documents": [
                    {
                        "row_number": doc.get("row_number"),
                        "document_type": doc.get("document_type", "sale.order"),
                        "lines": len(doc.get("lines", [])),
                        "note": "odoo_url will be included in actual response",
                    }
                    for doc in documents
                ],
            }
            result.records_skipped = len(documents)
            result.kpis = {
                "phase": "creation",
                "dry_run": True,
                "documents_would_create": len(documents),
                "confirm_requested": confirm,
            }
            result.complete()
            return result

        self.log.info(f"Phase 2: Creating {len(documents)} documents")

        created_documents: list[dict] = []
        creation_errors: list[str] = []

        for doc in documents:
            doc_type = doc.get("document_type", "sale.order")
            header = doc.get("header", {})
            lines = doc.get("lines", [])
            row_number = doc.get("row_number")

            if doc_type == "sale.order":
                op_result = ops.create_sale_order(header, lines, metadata)
            elif doc_type == "stock.picking":
                op_result = ops.create_stock_picking(header, lines, metadata)
            elif doc_type == "purchase.order":
                op_result = ops.create_purchase_order(header, lines, metadata)
            else:
                creation_errors.append(
                    f"Row {row_number}: Unknown document_type: {doc_type}"
                )
                continue

            result.add_operation(op_result)

            if op_result.success:
                # Calculate total quantity from lines
                total_quantity = sum(
                    line.get("quantity", 0) for line in lines
                )
                # Get partner name from document metadata or header
                partner_name = doc.get("_partner_name") or header.get("partner_name", "")

                doc_record = {
                    "row_number": row_number,
                    "document_type": doc_type,
                    "record_id": op_result.record_id,
                    "record_name": op_result.record_name,
                    "odoo_url": _build_odoo_url(op_result.record_id, op_result.model, odoo_base_url),
                    "partner_name": partner_name,
                    "lines_count": len(lines),
                    "total_quantity": total_quantity,
                    "state": "draft",
                    "environment": "development" if use_dev else "production",
                }

                # Confirm document if requested
                if confirm:
                    confirm_result = ops.confirm_document(
                        doc_type, op_result.record_id
                    )
                    if confirm_result.success:
                        doc_record["state"] = "confirmed"
                        doc_record["confirmed"] = True
                    else:
                        doc_record["confirmed"] = False
                        doc_record["confirm_error"] = confirm_result.error

                created_documents.append(doc_record)
            else:
                creation_errors.append(
                    f"Row {row_number}: {op_result.error}"
                )

        # Update result
        result.result_data = {
            "status": "success" if not creation_errors else "partial",
            "environment": "development" if use_dev else "production",
            "odoo_url": odoo_base_url,
            "created": created_documents,
            "errors": creation_errors,
            "total_created": len(created_documents),
            "total_failed": len(creation_errors),
        }

        result.errors.extend(creation_errors)

        # Count confirmed documents
        confirmed_count = sum(1 for d in created_documents if d.get("confirmed"))

        result.kpis = {
            "phase": "creation",
            "environment": "development" if use_dev else "production",
            "documents_created": len(created_documents),
            "documents_confirmed": confirmed_count if confirm else None,
            "documents_failed": len(creation_errors),
            "total_lines_created": sum(d["lines_count"] for d in created_documents),
            "confirm_requested": confirm,
        }

        result.complete()
        return result

    def _load_input(
        self,
        json_input: Optional[str],
        tsv_input: Optional[str],
        file: Optional[str],
        odoo_client,
    ) -> Optional[dict]:
        """
        Load input from JSON string, TSV string, or file.

        Args:
            json_input: JSON string
            tsv_input: TSV string (template format)
            file: Path to JSON or TSV file (detected by extension or content)
            odoo_client: Odoo client for delivery address resolution in TSV mode

        Returns:
            Parsed dict or None if error
        """
        # Direct JSON input
        if json_input:
            try:
                return json.loads(json_input)
            except json.JSONDecodeError as e:
                self.log.error(f"Invalid JSON input: {e}")
                return None

        # Direct TSV input
        if tsv_input:
            return self._parse_tsv_input(tsv_input, odoo_client)

        # File input
        if file:
            path = Path(file)
            if not path.exists():
                self.log.error(f"File not found: {file}")
                return None

            # Detect format from extension or content
            content = path.read_text()

            if path.suffix.lower() in (".tsv", ".txt", ".csv"):
                # TSV/CSV file
                return self._parse_tsv_input(content, odoo_client)
            elif path.suffix.lower() == ".json":
                # JSON file
                try:
                    return json.loads(content)
                except json.JSONDecodeError as e:
                    self.log.error(f"Invalid JSON in file {file}: {e}")
                    return None
            else:
                # Try to auto-detect: if starts with { or [, it's JSON
                stripped = content.strip()
                if stripped.startswith("{") or stripped.startswith("["):
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError as e:
                        self.log.error(f"Invalid JSON in file {file}: {e}")
                        return None
                else:
                    # Assume TSV
                    return self._parse_tsv_input(content, odoo_client)

        return None

    def _parse_tsv_input(
        self,
        tsv_content: str,
        odoo_client,
    ) -> Optional[dict]:
        """
        Parse TSV template input and convert to document structure.

        Groups rows by (document_type, partner_name, delivery_address) to form documents.
        Each unique combination becomes a separate document, with rows becoming lines.

        Handles date fallback logic:
        - sale.order: prefers commitment_date, falls back to scheduled_date
        - stock.picking: prefers scheduled_date, falls back to commitment_date

        Args:
            tsv_content: TSV string with header row
            odoo_client: Odoo client for partner/address resolution

        Returns:
            Dict in standard document format or None if error
        """
        try:
            # Parse TSV
            reader = csv.DictReader(io.StringIO(tsv_content), delimiter="\t")

            # Normalize column names (strip whitespace, lowercase for matching)
            if reader.fieldnames:
                # Create a mapping from normalized names to original
                normalized_map = {}
                for name in reader.fieldnames:
                    normalized = name.strip().lower().replace(" ", "_")
                    normalized_map[normalized] = name

            # Group rows by (document_type, partner_name, delivery_address)
            groups: dict[tuple[str, str, str], list[dict]] = {}
            row_number = 1

            for row in reader:
                row_number += 1

                # Normalize row keys
                normalized_row = {}
                for key, value in row.items():
                    if key:
                        norm_key = key.strip().lower().replace(" ", "_")
                        normalized_row[norm_key] = (value or "").strip()

                doc_type = normalized_row.get("document_type", "sale.order")
                partner_name = normalized_row.get("partner_name", "")
                delivery_address = normalized_row.get("delivery_address", normalized_row.get("delivery_adress", ""))
                delivery_address_id = normalized_row.get("delivery_address_id", "")

                # Skip empty rows
                product_sku = normalized_row.get("product_sku", "")
                if not product_sku:
                    continue

                # Create group key (delivery_address_id takes precedence over delivery_address)
                delivery_key = delivery_address_id or delivery_address
                group_key = (doc_type, partner_name, delivery_key)

                if group_key not in groups:
                    groups[group_key] = []

                # Parse quantity
                qty_str = normalized_row.get("quantity", "1")
                try:
                    quantity = float(qty_str) if qty_str else 1.0
                except ValueError:
                    quantity = 1.0

                # Parse dates
                commitment_date = normalized_row.get("commitment_date", "")
                scheduled_date = normalized_row.get("scheduled_date", "")
                notes = normalized_row.get("notes", "")

                groups[group_key].append({
                    "row_number": row_number,
                    "product_sku": product_sku,
                    "quantity": quantity,
                    "commitment_date": commitment_date,
                    "scheduled_date": scheduled_date,
                    "notes": notes,
                    "delivery_address_id": delivery_address_id,  # Explicit ID if provided
                })

            # Convert groups to documents
            documents = []
            ops = DocumentCreationOperations(odoo_client, self.ctx, self.log)

            for (doc_type, partner_name, delivery_key), lines_data in groups.items():
                # Get first line's dates and delivery info as document-level values
                first_line = lines_data[0]
                commitment_date = first_line.get("commitment_date", "")
                scheduled_date = first_line.get("scheduled_date", "")
                delivery_address_id = first_line.get("delivery_address_id", "")
                # delivery_key is either delivery_address_id or delivery_address string
                delivery_address = "" if delivery_address_id else delivery_key

                # Smart date handling: use whichever date is provided
                # For sale.order: prefer commitment_date
                # For stock.picking: prefer scheduled_date
                effective_commitment = commitment_date or scheduled_date
                effective_scheduled = scheduled_date or commitment_date

                # Build header
                header = {
                    "partner_name": partner_name,
                }

                # Apply dates based on document type
                if doc_type == "sale.order":
                    if effective_commitment:
                        header["commitment_date"] = effective_commitment
                elif doc_type == "stock.picking":
                    if effective_scheduled:
                        header["scheduled_date"] = effective_scheduled
                    # Apply default picking type if set
                    if hasattr(self, "_default_picking_type_id") and self._default_picking_type_id:
                        header["picking_type_id"] = self._default_picking_type_id

                # Resolve delivery address
                # Priority: delivery_address_id > delivery_address > partner (fallback)
                parent_partner_id = header.get("partner_id")
                if not parent_partner_id and header.get("partner_name"):
                    partner_result = ops.resolve_partner(partner_name=header["partner_name"])
                    if partner_result.success:
                        parent_partner_id = partner_result.record_id

                if delivery_address_id:
                    # Explicit delivery address ID provided - use directly
                    try:
                        header["partner_shipping_id"] = int(delivery_address_id)
                        self.log.info(
                            f"Using explicit delivery_address_id: {delivery_address_id}"
                        )
                    except ValueError:
                        self.log.warning(
                            f"Invalid delivery_address_id '{delivery_address_id}', must be numeric"
                        )
                elif delivery_address:
                    # Resolve specific delivery address by name
                    delivery_result = ops.resolve_delivery_address(
                        delivery_address,
                        parent_partner_id=parent_partner_id
                    )
                    if delivery_result.success:
                        header["partner_shipping_id"] = delivery_result.record_id
                        self.log.info(
                            f"Resolved delivery address '{delivery_address}' → ID {delivery_result.record_id}"
                        )
                    else:
                        self.log.warning(
                            f"Could not resolve delivery address: {delivery_address}. "
                            f"Error: {delivery_result.error}"
                        )
                elif parent_partner_id:
                    # No delivery address provided - use partner as delivery address
                    header["partner_shipping_id"] = parent_partner_id
                    self.log.info(
                        f"No delivery address provided, using partner ID {parent_partner_id} as shipping address"
                    )

                # Build lines
                lines = []
                for line_data in lines_data:
                    line = {
                        "row_number": line_data["row_number"],
                        "product_sku": line_data["product_sku"],
                        "quantity": line_data["quantity"],
                    }
                    if line_data.get("notes"):
                        line["name"] = line_data["notes"]
                    lines.append(line)

                documents.append({
                    "row_number": lines_data[0]["row_number"],
                    "document_type": doc_type,
                    "_partner_name": partner_name,  # Store for result display
                    "header": header,
                    "lines": lines,
                })

            self.log.info(
                f"Parsed TSV: {row_number - 1} rows → {len(documents)} documents",
                data={"groups": len(groups)},
            )

            return {
                "metadata": {
                    "source": "tsv_import",
                    "total_rows": row_number - 1,
                    "total_documents": len(documents),
                },
                "documents": documents,
            }

        except Exception as e:
            self.log.error(f"Failed to parse TSV input: {e}")
            return None
