"""
Generate Packing List PDFs Job

Reads packing list CSV files from FTP, transforms them into formatted PDFs,
and uploads the PDFs back to the same FTP folder.
"""

import csv
import logging
from datetime import datetime
from io import StringIO
from typing import Optional

from core.clients.ftp import FTPClient, get_ftp_client
from core.context import RequestContext
from core.jobs.base import BaseJob
from core.jobs.registry import register_job
from core.operations.pdf_generator import PDFGeneratorOperations
from core.result import JobResult

logger = logging.getLogger(__name__)


@register_job(
    name="generate_packing_list_pdfs",
    description="Generate formatted PDF packing lists from FTP CSV files",
    tags=["ftp", "pdf", "export", "packing_list"],
)
class GeneratePackingListPDFs(BaseJob):
    """
    Job to generate PDF packing lists from CSV files on FTP.

    Workflow:
    1. Connect to FTP
    2. Find PL_*.csv files in subfolder
    3. Check BQ for already-processed files
    4. For each unprocessed CSV:
       - Download and parse
       - Generate PDF
       - Upload PDF (unless dry-run)
       - Track in BQ
    """

    def run(
        self,
        folder: str = "/FTP-RMT-ALOHAS/alohas/outbounds/b2b",
        subfolder: Optional[str] = None,
        limit: int = 100,
        file_pattern: str = "PL_*.csv",
        **params,
    ) -> JobResult:
        """
        Generate PDFs from packing list CSVs.

        Args:
            folder: Base FTP folder to search
            subfolder: Specific subfolder to process (if None, search all)
            limit: Maximum number of files to process
            file_pattern: Pattern for CSV files

        Returns:
            JobResult with processing details
        """
        result = JobResult.from_context(self.ctx, parameters={
            "folder": folder,
            "subfolder": subfolder,
            "limit": limit,
            "file_pattern": file_pattern,
        })

        # Initialize operations
        pdf_ops = PDFGeneratorOperations(self.ctx, self.log)

        # Track metrics
        files_found = 0
        files_processed = 0
        files_skipped = 0
        files_failed = 0
        pdfs_generated = 0
        skip_reasons: dict[str, int] = {}

        try:
            # Get FTP client
            ftp = get_ftp_client()

            with ftp:
                # Determine search path
                if subfolder:
                    search_path = f"{folder.rstrip('/')}/{subfolder}"
                    csv_files = ftp.list_files(search_path, file_pattern)
                else:
                    # Search all subfolders
                    csv_files = ftp.find_files_recursive(folder, file_pattern)

                files_found = len(csv_files)
                self.log.info(f"Found {files_found} CSV files matching {file_pattern}")

                if files_found == 0:
                    result.records_checked = 0
                    result.kpis = {
                        "files_found": 0,
                        "files_processed": 0,
                        "pdfs_generated": 0,
                    }
                    result.complete()
                    return result

                # Get already-processed files from BQ
                processed_files = self._get_processed_files()
                self.log.info(f"Found {len(processed_files)} already-processed files in BQ")

                # Process each CSV
                for csv_file in csv_files[:limit]:
                    result.records_checked += 1
                    source_path = csv_file["path"]

                    # Check if already processed
                    if source_path in processed_files:
                        files_skipped += 1
                        skip_reasons["already_processed"] = skip_reasons.get("already_processed", 0) + 1
                        self.log.debug(f"Skipping already-processed: {source_path}")
                        continue

                    # Process the CSV
                    try:
                        success = self._process_csv_file(
                            ftp=ftp,
                            csv_file=csv_file,
                            pdf_ops=pdf_ops,
                            result=result,
                        )
                        if success:
                            files_processed += 1
                            pdfs_generated += 1
                        else:
                            files_failed += 1

                    except Exception as e:
                        files_failed += 1
                        error_msg = f"Error processing {source_path}: {e}"
                        logger.exception(error_msg)
                        result.errors.append(error_msg)

        except Exception as e:
            error_msg = f"Job failed: {e}"
            logger.exception(error_msg)
            result.errors.append(error_msg)

        # Set KPIs
        result.records_updated = pdfs_generated
        result.records_skipped = files_skipped
        result.kpis = {
            "files_found": files_found,
            "files_processed": files_processed,
            "files_skipped": files_skipped,
            "files_failed": files_failed,
            "pdfs_generated": pdfs_generated,
            "skip_reasons": skip_reasons,
        }

        result.complete()
        return result

    def _get_processed_files(self) -> set[str]:
        """
        Get set of already-processed source file paths from BigQuery.

        Returns:
            Set of source file paths that have been processed
        """
        try:
            # Query the audit log for previous successful runs
            sql = f"""
            SELECT DISTINCT
                JSON_EXTRACT_SCALAR(data, '$.source_file') as source_file
            FROM `{self.bq.project}.{self.bq.dataset}.{self.bq.audit_table}`
            WHERE job_name = 'generate_packing_list_pdfs'
              AND event_type = 'pdf_generated'
              AND dry_run = FALSE
              AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
            """
            rows = self.bq.query(sql)
            return {row["source_file"] for row in rows if row.get("source_file")}
        except Exception as e:
            logger.warning(f"Could not query processed files: {e}")
            return set()

    def _process_csv_file(
        self,
        ftp: FTPClient,
        csv_file: dict,
        pdf_ops: PDFGeneratorOperations,
        result: JobResult,
    ) -> bool:
        """
        Process a single CSV file: download, generate PDF, upload.

        Returns:
            True if successful
        """
        source_path = csv_file["path"]
        self.log.info(f"Processing: {source_path}")

        # Download CSV
        csv_bytes = ftp.download(source_path)
        csv_text = csv_bytes.decode("utf-8-sig")  # Handle BOM if present

        # Parse CSV (semicolon delimiter)
        reader = csv.DictReader(StringIO(csv_text), delimiter=";")
        rows = list(reader)

        if not rows:
            self.log.warning(f"Empty CSV file: {source_path}")
            result.errors.append(f"Empty CSV: {source_path}")
            return False

        # Extract header info from first row
        first_row = rows[0]
        customer_name = first_row.get("CustomerName", "Unknown Customer")
        sales_order = first_row.get("SalesOrder", "Unknown")
        picking_id = first_row.get("PickingId", "Unknown")

        # Get date from filename or use today
        # Filename format: PL_YYYYMMDD_HHMMSS.csv
        filename = csv_file["name"]
        try:
            # Extract date from filename like PL_20250130_123456.csv
            date_part = filename.split("_")[1] if "_" in filename else ""
            if len(date_part) == 8:
                date = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}"
            else:
                date = datetime.now().strftime("%Y-%m-%d")
        except Exception:
            date = datetime.now().strftime("%Y-%m-%d")

        # Generate PDF
        pdf_bytes, op_result = pdf_ops.generate_packing_list_pdf(
            rows=rows,
            customer_name=customer_name,
            sales_order=sales_order,
            picking_id=picking_id,
            date=date,
        )

        if not op_result.success:
            result.errors.append(f"PDF generation failed: {op_result.error}")
            return False

        if self.dry_run:
            self.log.skip(0, f"Would upload PDF for {picking_id}")
            return True

        # Upload PDF to same folder
        pdf_path = source_path.replace(".csv", ".pdf")
        try:
            ftp.upload(pdf_path, pdf_bytes)
            self.log.success(0, f"Uploaded PDF: {pdf_path}")
        except Exception as e:
            result.errors.append(f"Upload failed for {pdf_path}: {e}")
            return False

        # Log to BQ audit for tracking
        box_count = len(set(row.get("BoxId", "") for row in rows))
        item_count = sum(int(row.get("Qty", 0) or 0) for row in rows)

        self.bq.log_audit(
            self.ctx,
            "pdf_generated",
            data={
                "source_file": source_path,
                "pdf_file": pdf_path,
                "customer_name": customer_name,
                "sales_order": sales_order,
                "picking_id": picking_id,
                "box_count": box_count,
                "item_count": item_count,
                "pdf_size": len(pdf_bytes),
            },
        )

        return True
