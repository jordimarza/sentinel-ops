"""
Create Documents Job

Creates Odoo documents (sale.order, stock.picking) from JSON input with full validation.
"""

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
        file: Optional[str] = None,
        confirm: bool = False,
        use_dev: Optional[bool] = None,
        **params,
    ) -> JobResult:
        """
        Create documents from JSON input.

        Args:
            json_input: JSON string with document data
            file: Path to JSON file with document data
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

        Returns:
            JobResult with created document IDs, names, and URLs or validation errors
        """
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

        # Load input
        data = self._load_input(json_input, file)
        if not data:
            result.errors.append("No input provided. Use json_input or file parameter.")
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
                doc_record = {
                    "row_number": row_number,
                    "document_type": doc_type,
                    "record_id": op_result.record_id,
                    "record_name": op_result.record_name,
                    "odoo_url": _build_odoo_url(op_result.record_id, op_result.model, odoo_base_url),
                    "lines_created": len(lines),
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
            "total_lines_created": sum(d["lines_created"] for d in created_documents),
            "confirm_requested": confirm,
        }

        result.complete()
        return result

    def _load_input(
        self,
        json_input: Optional[str],
        file: Optional[str],
    ) -> Optional[dict]:
        """
        Load input from JSON string or file.

        Args:
            json_input: JSON string
            file: Path to JSON file

        Returns:
            Parsed dict or None if error
        """
        if json_input:
            try:
                return json.loads(json_input)
            except json.JSONDecodeError as e:
                self.log.error(f"Invalid JSON input: {e}")
                return None

        if file:
            path = Path(file)
            if not path.exists():
                self.log.error(f"File not found: {file}")
                return None
            try:
                with open(path) as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                self.log.error(f"Invalid JSON in file {file}: {e}")
                return None

        return None
