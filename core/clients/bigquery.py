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
        plans_table: str = "execution_plans",
        feedback_table: str = "execution_feedback",
        tasks_table: str = "intervention_tasks",
    ):
        self.project = project
        self.dataset = dataset
        self.audit_table = audit_table
        self.kpi_table = kpi_table
        self.plans_table = plans_table
        self.feedback_table = feedback_table
        self.tasks_table = tasks_table
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
                # Core identifiers
                bq.SchemaField("timestamp", "TIMESTAMP", mode="REQUIRED"),
                bq.SchemaField("request_id", "STRING", mode="REQUIRED"),
                bq.SchemaField("job_name", "STRING"),

                # Execution context
                bq.SchemaField("event_type", "STRING", mode="REQUIRED"),
                bq.SchemaField("triggered_by", "STRING"),  # cli, http, scheduler, n8n, mcp
                bq.SchemaField("environment", "STRING"),   # development, staging, production
                bq.SchemaField("dry_run", "BOOLEAN"),
                bq.SchemaField("debug", "BOOLEAN"),
                bq.SchemaField("user_id", "STRING"),

                # Record-level tracking (for easier querying)
                bq.SchemaField("record_id", "INTEGER"),
                bq.SchemaField("record_model", "STRING"),
                bq.SchemaField("record_name", "STRING"),

                # Flexible data
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
                # Core identifiers
                bq.SchemaField("request_id", "STRING", mode="REQUIRED"),
                bq.SchemaField("job_name", "STRING", mode="REQUIRED"),

                # Job classification
                bq.SchemaField("job_type", "STRING"),  # modification, validation, query, sync, health_check
                bq.SchemaField("status", "STRING", mode="REQUIRED"),  # success, partial, failure, dry_run, skipped

                # Execution context
                bq.SchemaField("triggered_by", "STRING"),  # cli, http, scheduler, n8n, mcp
                bq.SchemaField("environment", "STRING"),   # development, staging, production
                bq.SchemaField("dry_run", "BOOLEAN"),

                # Timing
                bq.SchemaField("started_at", "TIMESTAMP", mode="REQUIRED"),
                bq.SchemaField("completed_at", "TIMESTAMP"),
                bq.SchemaField("duration_seconds", "FLOAT"),

                # Counters (for modification/query jobs)
                bq.SchemaField("records_checked", "INTEGER"),
                bq.SchemaField("records_updated", "INTEGER"),
                bq.SchemaField("records_skipped", "INTEGER"),
                bq.SchemaField("error_count", "INTEGER"),

                # Validation results (for validation jobs)
                bq.SchemaField("tests_passed", "INTEGER"),
                bq.SchemaField("tests_failed", "INTEGER"),

                # Flexible data (JSON)
                bq.SchemaField("parameters", "JSON"),       # Job input parameters
                bq.SchemaField("result_data", "JSON"),      # Query/sync results, validation details
                bq.SchemaField("errors", "JSON"),           # Error messages list
                bq.SchemaField("modified_records", "JSON"), # Records modified (modification jobs)
                bq.SchemaField("action_summary", "JSON"),   # Summary by action type
                bq.SchemaField("extra_kpis", "JSON"),       # Job-specific KPIs
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
        record_id: Optional[int] = None,
        record_model: Optional[str] = None,
        record_name: Optional[str] = None,
    ) -> bool:
        """
        Log an audit event.

        Args:
            ctx: Request context
            event_type: Type of event (job_started, job_completed, error, etc.)
            data: Additional event data
            record_id: Optional record ID being operated on
            record_model: Optional Odoo model name
            record_name: Optional display name of the record

        Returns:
            True if logged successfully
        """
        try:
            import json
            client = self._get_client()
            table_id = self._get_table_id(self.audit_table)

            # Extract record info from data if not provided directly
            if data and not record_id:
                record_id = data.get("record_id")
            if data and not record_model:
                record_model = data.get("model") or data.get("record_model")
            if data and not record_name:
                record_name = data.get("record_name")

            row = {
                "timestamp": datetime.utcnow().isoformat(),
                "request_id": ctx.request_id,
                "job_name": ctx.job_name,
                "event_type": event_type,
                "triggered_by": ctx.triggered_by,
                "environment": ctx.environment,
                "dry_run": ctx.dry_run,
                "debug": ctx.debug,
                "user_id": ctx.user_id,
                "record_id": record_id,
                "record_model": record_model,
                "record_name": record_name,
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

            # Fields that map directly to columns
            standard_fields = {
                "request_id", "job_name", "job_type", "status", "triggered_by", "environment",
                "dry_run", "started_at", "completed_at", "duration_seconds",
                "records_checked", "records_updated", "records_skipped", "error_count",
                "tests_passed", "tests_failed",
            }

            # JSON fields that need serialization
            json_fields = {"parameters", "result_data", "errors", "modified_records", "action_summary"}

            row = {}

            # Copy standard fields
            for key in standard_fields:
                if key in kpi_data:
                    row[key] = kpi_data[key]

            # Serialize JSON fields
            for key in json_fields:
                if key in kpi_data and kpi_data[key]:
                    row[key] = json.dumps(kpi_data[key])

            # Put everything else in extra_kpis
            extra = {
                k: v for k, v in kpi_data.items()
                if k not in standard_fields and k not in json_fields
            }
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

    def _ensure_plans_table(self) -> None:
        """Create execution plans table if it doesn't exist."""
        try:
            from google.cloud import bigquery as bq
            client = self._get_client()

            table_id = self._get_table_id(self.plans_table)
            schema = [
                # Core identifiers
                bq.SchemaField("plan_id", "STRING", mode="REQUIRED"),
                bq.SchemaField("request_id", "STRING"),
                bq.SchemaField("job_name", "STRING", mode="REQUIRED"),

                # Timing
                bq.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
                bq.SchemaField("expires_at", "TIMESTAMP"),
                bq.SchemaField("approved_at", "TIMESTAMP"),

                # Approval
                bq.SchemaField("status", "STRING"),  # pending, approved, rejected, expired, executed
                bq.SchemaField("approved_by", "STRING"),
                bq.SchemaField("total_changes", "INTEGER"),
                bq.SchemaField("approved_count", "INTEGER"),
                bq.SchemaField("rejected_count", "INTEGER"),
                bq.SchemaField("high_risk_count", "INTEGER"),

                # Details
                bq.SchemaField("changes", "JSON"),  # The planned changes
                bq.SchemaField("approved_ids", "JSON"),
                bq.SchemaField("rejected_ids", "JSON"),
            ]

            try:
                client.get_table(table_id)
            except Exception:
                table = bq.Table(table_id, schema=schema)
                table.time_partitioning = bq.TimePartitioning(
                    type_=bq.TimePartitioningType.DAY,
                    field="created_at",
                )
                client.create_table(table)
                logger.info(f"Created plans table {table_id}")
        except Exception as e:
            logger.warning(f"Could not ensure plans table: {e}")

    def _ensure_feedback_table(self) -> None:
        """Create execution feedback table if it doesn't exist."""
        try:
            from google.cloud import bigquery as bq
            client = self._get_client()

            table_id = self._get_table_id(self.feedback_table)
            schema = [
                # Core identifiers
                bq.SchemaField("feedback_id", "STRING", mode="REQUIRED"),
                bq.SchemaField("request_id", "STRING", mode="REQUIRED"),
                bq.SchemaField("job_name", "STRING", mode="REQUIRED"),

                # Feedback
                bq.SchemaField("rating", "STRING", mode="REQUIRED"),  # correct, incorrect, partial, unnecessary, missed
                bq.SchemaField("feedback_by", "STRING"),
                bq.SchemaField("feedback_at", "TIMESTAMP", mode="REQUIRED"),
                bq.SchemaField("comment", "STRING"),

                # Details for learning
                bq.SchemaField("incorrect_record_ids", "JSON"),
                bq.SchemaField("missed_record_ids", "JSON"),
                bq.SchemaField("suggested_params", "JSON"),
                bq.SchemaField("should_have_been", "STRING"),
            ]

            try:
                client.get_table(table_id)
            except Exception:
                table = bq.Table(table_id, schema=schema)
                table.time_partitioning = bq.TimePartitioning(
                    type_=bq.TimePartitioningType.DAY,
                    field="feedback_at",
                )
                client.create_table(table)
                logger.info(f"Created feedback table {table_id}")
        except Exception as e:
            logger.warning(f"Could not ensure feedback table: {e}")

    def save_execution_plan(self, plan: dict) -> bool:
        """Save an execution plan for approval."""
        try:
            import json
            client = self._get_client()
            table_id = self._get_table_id(self.plans_table)

            row = {
                "plan_id": plan["plan_id"],
                "request_id": plan.get("request_id"),
                "job_name": plan["job_name"],
                "created_at": plan["created_at"],
                "expires_at": plan.get("expires_at"),
                "status": "pending",
                "total_changes": plan.get("total_changes", 0),
                "high_risk_count": plan.get("high_risk_count", 0),
                "changes": json.dumps(plan.get("changes", [])),
            }

            errors = client.insert_rows_json(table_id, [row])
            if errors:
                logger.error(f"BigQuery plan insert errors: {errors}")
                return False
            return True
        except Exception as e:
            logger.error(f"Failed to save execution plan: {e}")
            return False

    def save_feedback(self, feedback: dict) -> bool:
        """Save execution feedback for learning."""
        try:
            import json
            import uuid
            client = self._get_client()
            table_id = self._get_table_id(self.feedback_table)

            row = {
                "feedback_id": feedback.get("feedback_id", str(uuid.uuid4())),
                "request_id": feedback["request_id"],
                "job_name": feedback["job_name"],
                "rating": feedback["rating"],
                "feedback_by": feedback.get("feedback_by"),
                "feedback_at": feedback.get("feedback_at", datetime.utcnow().isoformat()),
                "comment": feedback.get("comment"),
                "incorrect_record_ids": json.dumps(feedback.get("incorrect_record_ids", [])),
                "missed_record_ids": json.dumps(feedback.get("missed_record_ids", [])),
                "suggested_params": json.dumps(feedback.get("suggested_params")) if feedback.get("suggested_params") else None,
                "should_have_been": feedback.get("should_have_been"),
            }

            errors = client.insert_rows_json(table_id, [row])
            if errors:
                logger.error(f"BigQuery feedback insert errors: {errors}")
                return False
            return True
        except Exception as e:
            logger.error(f"Failed to save feedback: {e}")
            return False

    def get_job_feedback_stats(self, job_name: str, days: int = 30) -> dict:
        """Get feedback statistics for a job (for learning)."""
        try:
            sql = f"""
            SELECT
                rating,
                COUNT(*) as count
            FROM `{self._get_table_id(self.feedback_table)}`
            WHERE job_name = @job_name
              AND feedback_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
            GROUP BY rating
            """
            results = self.query(sql, {"job_name": job_name})

            stats = {"total": 0, "ratings": {}}
            for row in results:
                stats["ratings"][row["rating"]] = row["count"]
                stats["total"] += row["count"]

            if stats["total"] > 0:
                correct = stats["ratings"].get("correct", 0)
                stats["accuracy"] = correct / stats["total"]

            return stats
        except Exception as e:
            logger.error(f"Failed to get feedback stats: {e}")
            return {}

    def _ensure_tasks_table(self) -> None:
        """Create intervention tasks table if it doesn't exist."""
        try:
            from google.cloud import bigquery as bq
            client = self._get_client()

            table_id = self._get_table_id(self.tasks_table)
            schema = [
                # Core identifiers
                bq.SchemaField("task_id", "STRING", mode="REQUIRED"),
                bq.SchemaField("request_id", "STRING"),
                bq.SchemaField("job_name", "STRING"),

                # Document tracking
                bq.SchemaField("document_type", "STRING", mode="REQUIRED"),
                bq.SchemaField("document_id", "INT64", mode="REQUIRED"),
                bq.SchemaField("document_name", "STRING"),
                bq.SchemaField("document_url", "STRING"),
                bq.SchemaField("related_documents", "JSON"),

                # Issue details
                bq.SchemaField("task_type", "STRING", mode="REQUIRED"),
                bq.SchemaField("title", "STRING", mode="REQUIRED"),
                bq.SchemaField("description", "STRING"),
                bq.SchemaField("detection_data", "JSON"),

                # Financial context
                bq.SchemaField("currency", "STRING"),
                bq.SchemaField("qty_ordered", "FLOAT64"),
                bq.SchemaField("qty_delivered", "FLOAT64"),
                bq.SchemaField("qty_invoiced", "FLOAT64"),
                bq.SchemaField("amount_order", "FLOAT64"),
                bq.SchemaField("amount_difference", "FLOAT64"),
                bq.SchemaField("amount_credit", "FLOAT64"),
                bq.SchemaField("financial_data", "JSON"),

                # Attribution
                bq.SchemaField("department", "STRING"),
                bq.SchemaField("process_category", "STRING"),
                bq.SchemaField("priority", "STRING", mode="REQUIRED"),
                bq.SchemaField("risk_level", "STRING"),

                # Assignment
                bq.SchemaField("assignee_type", "STRING"),
                bq.SchemaField("assignee_id", "STRING"),
                bq.SchemaField("assigned_at", "TIMESTAMP"),
                bq.SchemaField("assigned_by", "STRING"),

                # AI Agent details
                bq.SchemaField("agent_model", "STRING"),
                bq.SchemaField("agent_version", "STRING"),
                bq.SchemaField("agent_capabilities", "JSON"),

                # AI Planning
                bq.SchemaField("plan_status", "STRING"),
                bq.SchemaField("planned_action", "JSON"),
                bq.SchemaField("plan_reasoning", "STRING"),
                bq.SchemaField("plan_confidence", "FLOAT64"),
                bq.SchemaField("plan_created_at", "TIMESTAMP"),
                bq.SchemaField("plan_alternatives", "JSON"),
                bq.SchemaField("requires_approval", "BOOL"),

                # Human approval
                bq.SchemaField("approval_status", "STRING"),
                bq.SchemaField("approval_requested_at", "TIMESTAMP"),
                bq.SchemaField("approved_by", "STRING"),
                bq.SchemaField("approved_at", "TIMESTAMP"),
                bq.SchemaField("rejection_reason", "STRING"),

                # Execution tracking
                bq.SchemaField("execution_status", "STRING"),
                bq.SchemaField("execution_started_at", "TIMESTAMP"),
                bq.SchemaField("execution_completed_at", "TIMESTAMP"),
                bq.SchemaField("execution_result", "JSON"),
                bq.SchemaField("execution_log", "JSON"),

                # Status workflow
                bq.SchemaField("status", "STRING", mode="REQUIRED"),
                bq.SchemaField("status_history", "JSON"),

                # Resolution
                bq.SchemaField("resolution_type", "STRING"),
                bq.SchemaField("resolution_notes", "STRING"),
                bq.SchemaField("resolution_data", "JSON"),
                bq.SchemaField("resolved_at", "TIMESTAMP"),
                bq.SchemaField("resolved_by", "STRING"),

                # Timestamps
                bq.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
                bq.SchemaField("updated_at", "TIMESTAMP"),
                bq.SchemaField("due_at", "TIMESTAMP"),
                bq.SchemaField("snoozed_until", "TIMESTAMP"),

                # Environment & dedup
                bq.SchemaField("environment", "STRING"),
                bq.SchemaField("source_system", "STRING"),
                bq.SchemaField("dedup_key", "STRING", mode="REQUIRED"),

                # Flexible
                bq.SchemaField("metadata", "JSON"),
            ]

            try:
                client.get_table(table_id)
            except Exception:
                table = bq.Table(table_id, schema=schema)
                table.time_partitioning = bq.TimePartitioning(
                    type_=bq.TimePartitioningType.DAY,
                    field="created_at",
                )
                table.clustering_fields = ["status", "assignee_type", "department"]
                client.create_table(table)
                logger.info(f"Created tasks table {table_id}")
        except Exception as e:
            logger.warning(f"Could not ensure tasks table: {e}")

    def ensure_tables(self) -> None:
        """Ensure all required tables exist."""
        self._ensure_dataset()
        self._ensure_audit_table()
        self._ensure_kpi_table()
        self._ensure_plans_table()
        self._ensure_feedback_table()
        self._ensure_tasks_table()



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

    def log_audit(
        self,
        ctx: RequestContext,
        event_type: str,
        data: Optional[dict] = None,
        record_id: Optional[int] = None,
        record_model: Optional[str] = None,
        record_name: Optional[str] = None,
    ) -> bool:
        logger.info(f"[NOOP AUDIT] {event_type}: {ctx.job_name} [{ctx.environment}] dry_run={ctx.dry_run} - {data}")
        return True

    def write_kpis(self, kpi_data: dict) -> bool:
        logger.info(f"[NOOP KPI] {kpi_data}")
        return True

    def query(self, sql: str, params: Optional[dict] = None) -> list[dict]:
        logger.warning(
            "[NOOP QUERY] BigQuery not configured - returning empty results. "
            "Set BQ_PROJECT env var or bq-project secret to enable BQ queries."
        )
        logger.debug(f"[NOOP QUERY] Would have executed: {sql[:200]}...")
        raise RuntimeError(
            "BigQuery not configured. Set BQ_PROJECT env var or bq-project secret. "
            "Query cannot be executed in NoOp mode."
        )

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
