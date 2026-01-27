#!/usr/bin/env python3
"""
Recreate BigQuery Tables with New Schema

This script drops and recreates the audit_log and job_kpis tables
with the improved schema that includes:
- environment (dev/staging/production)
- debug mode flag
- request_id correlation
- record-level tracking columns
- parameters and errors as separate JSON fields

Usage:
    python scripts/recreate_bq_tables.py --dry-run  # Show what would be done
    python scripts/recreate_bq_tables.py            # Actually recreate tables
"""

import argparse
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import get_settings


def recreate_tables(dry_run: bool = True):
    """Recreate BigQuery tables with new schema."""
    settings = get_settings()

    if not settings.is_bq_configured():
        print("ERROR: BigQuery not configured. Set GCP_PROJECT and BQ_DATASET.")
        return False

    project = settings.bq_project
    dataset = settings.bq_dataset

    print(f"\n{'='*60}")
    print(f"Recreate BigQuery Tables")
    print(f"{'='*60}")
    print(f"  Project: {project}")
    print(f"  Dataset: {dataset}")
    print(f"  Mode: {'DRY-RUN' if dry_run else 'LIVE'}")
    print(f"{'='*60}\n")

    try:
        from google.cloud import bigquery as bq
        client = bq.Client(project=project)
    except ImportError:
        print("ERROR: google-cloud-bigquery not installed")
        return False
    except Exception as e:
        print(f"ERROR: Failed to create BigQuery client: {e}")
        return False

    # Table definitions with new schema
    audit_table_id = f"{project}.{dataset}.audit_log"
    audit_schema = [
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

    kpi_table_id = f"{project}.{dataset}.job_kpis"
    kpi_schema = [
        # Core identifiers
        bq.SchemaField("request_id", "STRING", mode="REQUIRED"),
        bq.SchemaField("job_name", "STRING", mode="REQUIRED"),

        # Job classification
        bq.SchemaField("job_type", "STRING"),  # modification, validation, query, sync, health_check, metric
        bq.SchemaField("status", "STRING", mode="REQUIRED"),  # success, partial, failure, dry_run, skipped

        # Execution context
        bq.SchemaField("triggered_by", "STRING"),  # cli, http, scheduler, n8n, mcp
        bq.SchemaField("environment", "STRING"),   # development, staging, production
        bq.SchemaField("dry_run", "BOOLEAN"),

        # Timing
        bq.SchemaField("started_at", "TIMESTAMP", mode="REQUIRED"),
        bq.SchemaField("completed_at", "TIMESTAMP"),
        bq.SchemaField("duration_seconds", "FLOAT"),

        # Counters (modification jobs)
        bq.SchemaField("records_checked", "INTEGER"),
        bq.SchemaField("records_updated", "INTEGER"),
        bq.SchemaField("records_skipped", "INTEGER"),
        bq.SchemaField("error_count", "INTEGER"),

        # Counters (validation jobs)
        bq.SchemaField("tests_passed", "INTEGER"),
        bq.SchemaField("tests_failed", "INTEGER"),

        # Flexible data (JSON)
        bq.SchemaField("parameters", "JSON"),       # Job input parameters
        bq.SchemaField("result_data", "JSON"),      # Query/sync results, metrics, validation details
        bq.SchemaField("errors", "JSON"),           # Error messages list
        bq.SchemaField("modified_records", "JSON"), # Records modified (modification jobs)
        bq.SchemaField("action_summary", "JSON"),   # Summary by action type
        bq.SchemaField("extra_kpis", "JSON"),       # Job-specific KPIs
    ]

    # Execution Plans table
    plans_table_id = f"{project}.{dataset}.execution_plans"
    plans_schema = [
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
        bq.SchemaField("changes", "JSON"),
        bq.SchemaField("approved_ids", "JSON"),
        bq.SchemaField("rejected_ids", "JSON"),
    ]

    # Execution Feedback table (for learning)
    feedback_table_id = f"{project}.{dataset}.execution_feedback"
    feedback_schema = [
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

    # Intervention Tasks table
    tasks_table_id = f"{project}.{dataset}.intervention_tasks"
    tasks_schema = [
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

    tables = [
        ("audit_log", audit_table_id, audit_schema, "timestamp", None),
        ("job_kpis", kpi_table_id, kpi_schema, "started_at", None),
        ("execution_plans", plans_table_id, plans_schema, "created_at", None),
        ("execution_feedback", feedback_table_id, feedback_schema, "feedback_at", None),
        ("intervention_tasks", tasks_table_id, tasks_schema, "created_at", ["status", "assignee_type", "department"]),
    ]

    for name, table_id, schema, partition_field, clustering_fields in tables:
        print(f"\n--- {name} ---")

        # Check if table exists
        try:
            existing = client.get_table(table_id)
            print(f"  Existing table found with {len(existing.schema)} columns")

            if dry_run:
                print(f"  [DRY-RUN] Would delete table: {table_id}")
            else:
                print(f"  Deleting table: {table_id}")
                client.delete_table(table_id)
                print(f"  Table deleted")
        except Exception:
            print(f"  Table does not exist (will create)")

        # Create table with new schema
        if dry_run:
            print(f"  [DRY-RUN] Would create table with {len(schema)} columns:")
            for field in schema:
                print(f"    - {field.name}: {field.field_type} ({field.mode})")
            if clustering_fields:
                print(f"  [DRY-RUN] Clustering fields: {clustering_fields}")
        else:
            print(f"  Creating table with {len(schema)} columns...")
            table = bq.Table(table_id, schema=schema)
            table.time_partitioning = bq.TimePartitioning(
                type_=bq.TimePartitioningType.DAY,
                field=partition_field,
            )
            if clustering_fields:
                table.clustering_fields = clustering_fields
            client.create_table(table)
            print(f"  Table created successfully")

    print(f"\n{'='*60}")
    if dry_run:
        print("DRY-RUN complete. Run without --dry-run to apply changes.")
    else:
        print("Tables recreated successfully!")
    print(f"{'='*60}\n")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Recreate BigQuery tables with new schema",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )

    args = parser.parse_args()

    if not args.dry_run:
        print("\nWARNING: This will DELETE existing tables and all their data!")
        confirm = input("Type 'yes' to continue: ")
        if confirm.lower() != "yes":
            print("Aborted.")
            sys.exit(0)

    success = recreate_tables(dry_run=args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
