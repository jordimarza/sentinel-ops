"""
BigQuery Client for Audit Trail and KPIs

Provides audit-aware BigQuery operations with automatic schema handling.
"""

import logging
from datetime import datetime
from functools import lru_cache
from typing import Any, Optional

from core.config import Settings, get_settings
from core.context import RequestContext

logger = logging.getLogger(__name__)


class BigQueryClient:
    """
    BigQuery client for audit logging and KPI tracking.

    Usage:
        client = BigQueryClient(project, dataset)
        client.log_audit(context, "job_started", {"job": "clean_old_orders"})
        client.write_kpis(job_result.to_kpi_dict())
    """

    def __init__(
        self,
        project: str,
        dataset: str = "sentinel_ops",
        audit_table: str = "audit_log",
        kpi_table: str = "job_kpis",
    ):
        self.project = project
        self.dataset = dataset
        self.audit_table = audit_table
        self.kpi_table = kpi_table
        self._client = None

    def _get_client(self):
        """Get or create BigQuery client."""
        if self._client is None:
            try:
                from google.cloud import bigquery
                self._client = bigquery.Client(project=self.project)
            except ImportError:
                logger.warning("google-cloud-bigquery not installed")
                raise
        return self._client

    def _get_table_id(self, table: str) -> str:
        """Get fully qualified table ID."""
        return f"{self.project}.{self.dataset}.{table}"

    def _ensure_dataset(self) -> None:
        """Create dataset if it doesn't exist."""
        try:
            from google.cloud import bigquery as bq
            client = self._get_client()

            dataset_ref = bq.DatasetReference(self.project, self.dataset)
            try:
                client.get_dataset(dataset_ref)
            except Exception:
                dataset = bq.Dataset(dataset_ref)
                dataset.location = "US"
                client.create_dataset(dataset)
                logger.info(f"Created dataset {self.dataset}")
        except Exception as e:
            logger.warning(f"Could not ensure dataset: {e}")

    def _ensure_audit_table(self) -> None:
        """Create audit table if it doesn't exist."""
        try:
            from google.cloud import bigquery as bq
            client = self._get_client()

            table_id = self._get_table_id(self.audit_table)
            schema = [
                bq.SchemaField("timestamp", "TIMESTAMP", mode="REQUIRED"),
                bq.SchemaField("request_id", "STRING", mode="REQUIRED"),
                bq.SchemaField("job_name", "STRING"),
                bq.SchemaField("triggered_by", "STRING"),
                bq.SchemaField("event_type", "STRING", mode="REQUIRED"),
                bq.SchemaField("dry_run", "BOOLEAN"),
                bq.SchemaField("user_id", "STRING"),
                bq.SchemaField("data", "JSON"),
            ]

            try:
                client.get_table(table_id)
            except Exception:
                table = bq.Table(table_id, schema=schema)
                table.time_partitioning = bq.TimePartitioning(
                    type_=bq.TimePartitioningType.DAY,
                    field="timestamp",
                )
                client.create_table(table)
                logger.info(f"Created audit table {table_id}")
        except Exception as e:
            logger.warning(f"Could not ensure audit table: {e}")

    def _ensure_kpi_table(self) -> None:
        """Create KPI table if it doesn't exist."""
        try:
            from google.cloud import bigquery as bq
            client = self._get_client()

            table_id = self._get_table_id(self.kpi_table)
            schema = [
                bq.SchemaField("job_name", "STRING", mode="REQUIRED"),
                bq.SchemaField("status", "STRING", mode="REQUIRED"),
                bq.SchemaField("started_at", "TIMESTAMP", mode="REQUIRED"),
                bq.SchemaField("completed_at", "TIMESTAMP"),
                bq.SchemaField("duration_seconds", "FLOAT"),
                bq.SchemaField("records_checked", "INTEGER"),
                bq.SchemaField("records_updated", "INTEGER"),
                bq.SchemaField("records_skipped", "INTEGER"),
                bq.SchemaField("error_count", "INTEGER"),
                bq.SchemaField("dry_run", "BOOLEAN"),
                bq.SchemaField("extra_kpis", "JSON"),
            ]

            try:
                client.get_table(table_id)
            except Exception:
                table = bq.Table(table_id, schema=schema)
                table.time_partitioning = bq.TimePartitioning(
                    type_=bq.TimePartitioningType.DAY,
                    field="started_at",
                )
                client.create_table(table)
                logger.info(f"Created KPI table {table_id}")
        except Exception as e:
            logger.warning(f"Could not ensure KPI table: {e}")

    def log_audit(
        self,
        ctx: RequestContext,
        event_type: str,
        data: Optional[dict] = None,
    ) -> bool:
        """
        Log an audit event.

        Args:
            ctx: Request context
            event_type: Type of event (job_started, job_completed, error, etc.)
            data: Additional event data

        Returns:
            True if logged successfully
        """
        try:
            import json
            client = self._get_client()
            table_id = self._get_table_id(self.audit_table)

            row = {
                "timestamp": datetime.utcnow().isoformat(),
                "request_id": ctx.request_id,
                "job_name": ctx.job_name,
                "triggered_by": ctx.triggered_by,
                "event_type": event_type,
                "dry_run": ctx.dry_run,
                "user_id": ctx.user_id,
                "data": json.dumps(data) if data else None,
            }

            errors = client.insert_rows_json(table_id, [row])
            if errors:
                logger.error(f"BigQuery audit insert errors: {errors}")
                return False

            logger.debug(f"Logged audit event: {event_type}")
            return True

        except Exception as e:
            logger.error(f"Failed to log audit event: {e}")
            return False

    def write_kpis(self, kpi_data: dict) -> bool:
        """
        Write job KPIs to BigQuery.

        Args:
            kpi_data: KPI data from JobResult.to_kpi_dict()

        Returns:
            True if written successfully
        """
        try:
            import json
            client = self._get_client()
            table_id = self._get_table_id(self.kpi_table)

            # Extract standard fields, put rest in extra_kpis
            standard_fields = {
                "job_name", "status", "started_at", "completed_at",
                "duration_seconds", "records_checked", "records_updated",
                "records_skipped", "error_count", "dry_run"
            }

            row = {k: v for k, v in kpi_data.items() if k in standard_fields}
            extra = {k: v for k, v in kpi_data.items() if k not in standard_fields}

            if extra:
                row["extra_kpis"] = json.dumps(extra)

            errors = client.insert_rows_json(table_id, [row])
            if errors:
                logger.error(f"BigQuery KPI insert errors: {errors}")
                return False

            logger.info(f"Wrote KPIs for job {kpi_data.get('job_name')}")
            return True

        except Exception as e:
            logger.error(f"Failed to write KPIs: {e}")
            return False

    def query(self, sql: str, params: Optional[dict] = None) -> list[dict]:
        """
        Execute a query and return results.

        Args:
            sql: SQL query string
            params: Optional query parameters

        Returns:
            List of result rows as dicts
        """
        try:
            from google.cloud import bigquery as bq
            client = self._get_client()

            job_config = None
            if params:
                job_config = bq.QueryJobConfig(
                    query_parameters=[
                        bq.ScalarQueryParameter(k, "STRING", v)
                        for k, v in params.items()
                    ]
                )

            query_job = client.query(sql, job_config=job_config)
            results = query_job.result()

            return [dict(row) for row in results]

        except Exception as e:
            logger.error(f"Query failed: {e}")
            raise

    def ensure_tables(self) -> None:
        """Ensure all required tables exist."""
        self._ensure_dataset()
        self._ensure_audit_table()
        self._ensure_kpi_table()


class NoOpBigQueryClient(BigQueryClient):
    """
    No-op BigQuery client for testing/development without BQ access.

    All operations log to console but don't actually write to BigQuery.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(project="noop", dataset="noop")
        logger.info("Using NoOp BigQuery client (no actual BigQuery writes)")

    def _get_client(self):
        return None

    def log_audit(self, ctx: RequestContext, event_type: str, data: Optional[dict] = None) -> bool:
        logger.info(f"[NOOP AUDIT] {event_type}: {ctx.job_name} - {data}")
        return True

    def write_kpis(self, kpi_data: dict) -> bool:
        logger.info(f"[NOOP KPI] {kpi_data}")
        return True

    def query(self, sql: str, params: Optional[dict] = None) -> list[dict]:
        logger.info(f"[NOOP QUERY] {sql}")
        return []

    def ensure_tables(self) -> None:
        logger.info("[NOOP] Would ensure tables exist")


@lru_cache(maxsize=1)
def get_bigquery_client(settings: Optional[Settings] = None) -> BigQueryClient:
    """
    Get or create a cached BigQuery client instance.

    Args:
        settings: Optional settings (uses get_settings() if not provided)

    Returns:
        BigQueryClient or NoOpBigQueryClient if BQ not configured
    """
    if settings is None:
        settings = get_settings()

    # Use NoOp if BQ not configured or has placeholder value
    if not settings.is_bq_configured():
        logger.info("BigQuery not configured, using NoOp client (audit to console only)")
        return NoOpBigQueryClient()

    client = BigQueryClient(
        project=settings.bq_project,
        dataset=settings.bq_dataset,
        audit_table=settings.bq_audit_table,
        kpi_table=settings.bq_kpi_table,
    )

    # Ensure tables exist
    try:
        client.ensure_tables()
    except Exception as e:
        logger.warning(f"Could not ensure BQ tables: {e}")

    return client
