"""
PDF Generator Operations

Generate formatted PDF documents from structured data.
"""

import logging
from io import BytesIO
from typing import Optional

from core.context import RequestContext
from core.logging.sentinel_logger import SentinelLogger
from core.operations.base import BaseOperation
from core.result import OperationResult

logger = logging.getLogger(__name__)


class PDFGeneratorOperations(BaseOperation):
    """
    PDF generation operations.

    Note: This operation doesn't use Odoo, so we pass None for the odoo client.
    """

    def __init__(
        self,
        ctx: RequestContext,
        log: Optional[SentinelLogger] = None,
    ):
        # Pass None for odoo since PDF generation doesn't need it
        super().__init__(odoo=None, ctx=ctx, log=log)

    def generate_packing_list_pdf(
        self,
        rows: list[dict],
        customer_name: str,
        sales_order: str,
        picking_id: str,
        date: str,
    ) -> tuple[bytes, OperationResult]:
        """
        Generate a packing list PDF with hybrid box+items layout.

        Args:
            rows: List of CSV row dicts with packing data
            customer_name: Customer name for header
            sales_order: Sales order number for header
            picking_id: Picking ID for header
            date: Date for header

        Returns:
            Tuple of (PDF bytes, OperationResult)
        """
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
            from reportlab.lib.units import mm
            from reportlab.platypus import (
                Paragraph,
                SimpleDocTemplate,
                Spacer,
                Table,
                TableStyle,
            )
        except ImportError as e:
            logger.error("reportlab not installed. Run: pip install reportlab")
            return b"", OperationResult.fail(
                error=f"reportlab not installed: {e}",
                action="generate_pdf",
            )

        if self.dry_run:
            self.log.skip(0, f"Would generate PDF for {picking_id}")
            return b"", OperationResult.skipped(
                record_id=0,
                reason=f"Dry run: would generate PDF for {picking_id}",
            )

        try:
            # Create PDF buffer
            buffer = BytesIO()
            doc = SimpleDocTemplate(
                buffer,
                pagesize=A4,
                rightMargin=15 * mm,
                leftMargin=15 * mm,
                topMargin=15 * mm,
                bottomMargin=15 * mm,
            )

            # Styles
            styles = getSampleStyleSheet()
            title_style = ParagraphStyle(
                "Title",
                parent=styles["Heading1"],
                fontSize=18,
                alignment=1,  # Center
                spaceAfter=10,
            )
            header_style = ParagraphStyle(
                "Header",
                parent=styles["Normal"],
                fontSize=11,
                spaceAfter=3,
            )
            box_header_style = ParagraphStyle(
                "BoxHeader",
                parent=styles["Heading2"],
                fontSize=12,
                spaceBefore=15,
                spaceAfter=5,
                backColor=colors.Color(0.9, 0.9, 0.9),
            )

            # Build content
            elements = []

            # Title
            elements.append(Paragraph("PACKING LIST", title_style))
            elements.append(Spacer(1, 5 * mm))

            # Header info
            elements.append(Paragraph(f"<b>Customer:</b> {customer_name}", header_style))
            elements.append(Paragraph(f"<b>Order:</b> {sales_order}", header_style))
            elements.append(Paragraph(f"<b>Picking:</b> {picking_id}", header_style))
            elements.append(Paragraph(f"<b>Date:</b> {date}", header_style))
            elements.append(Spacer(1, 10 * mm))

            # Group rows by BoxId
            boxes = {}
            for row in rows:
                box_id = row.get("BoxId", "Unknown")
                if box_id not in boxes:
                    boxes[box_id] = {
                        "size": row.get("BoxSize", ""),
                        "weight": row.get("BoxWeight", ""),
                        "volume": row.get("BoxVolume", ""),
                        "items": [],
                    }
                boxes[box_id]["items"].append(row)

            # Calculate totals
            total_boxes = len(boxes)
            total_items = sum(
                int(row.get("Qty", 0) or 0) for row in rows
            )
            total_weight = sum(
                float(row.get("ContentWeight", 0) or 0) for row in rows
            )

            # Render each box
            for box_id, box_data in boxes.items():
                # Box header
                weight_str = f"{box_data['weight']} kg" if box_data["weight"] else ""
                volume_str = f"{box_data['volume']} m³" if box_data["volume"] else ""
                size_str = f"({box_data['size']})" if box_data["size"] else ""
                box_info = f"  {size_str} - {weight_str}, {volume_str}".strip(" -,")

                elements.append(
                    Paragraph(f"<b>BOX: {box_id}</b>{box_info}", box_header_style)
                )

                # Items table
                table_data = [["SKU", "Description", "Size", "EAN", "Qty"]]
                for item in box_data["items"]:
                    desc = item.get("Description", "")
                    # Truncate description if too long
                    if len(desc) > 30:
                        desc = desc[:27] + "..."
                    table_data.append([
                        item.get("SKU", ""),
                        desc,
                        item.get("Size", ""),
                        item.get("EAN", ""),
                        item.get("Qty", ""),
                    ])

                # Create table with column widths
                col_widths = [35 * mm, 55 * mm, 20 * mm, 40 * mm, 15 * mm]
                table = Table(table_data, colWidths=col_widths)
                table.setStyle(TableStyle([
                    # Header row
                    ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.8, 0.8, 0.8)),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 9),
                    # Data rows
                    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 1), (-1, -1), 8),
                    # Grid
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    # Alignment
                    ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                    ("ALIGN", (4, 1), (4, -1), "CENTER"),  # Qty column
                    # Padding
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]))
                elements.append(table)

            # Totals footer
            elements.append(Spacer(1, 15 * mm))
            totals_style = ParagraphStyle(
                "Totals",
                parent=styles["Normal"],
                fontSize=11,
                backColor=colors.Color(0.95, 0.95, 0.95),
            )
            elements.append(
                Paragraph(
                    f"<b>TOTALS:</b> {total_boxes} boxes | {total_items} items | {total_weight:.1f} kg",
                    totals_style,
                )
            )

            # Build PDF
            doc.build(elements)
            pdf_bytes = buffer.getvalue()

            self.log.success(0, f"Generated PDF for {picking_id}: {len(pdf_bytes)} bytes")

            return pdf_bytes, OperationResult.ok(
                record_id=0,
                action="generate_pdf",
                message=f"Generated PDF for {picking_id}",
                data={
                    "picking_id": picking_id,
                    "sales_order": sales_order,
                    "customer_name": customer_name,
                    "box_count": total_boxes,
                    "item_count": total_items,
                    "total_weight": total_weight,
                    "pdf_size": len(pdf_bytes),
                },
            )

        except Exception as e:
            logger.exception(f"Failed to generate PDF for {picking_id}")
            return b"", OperationResult.fail(
                error=str(e),
                action="generate_pdf",
            )
