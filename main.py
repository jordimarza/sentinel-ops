"""
Sentinel-Ops - Cloud Function Entry Point

Main entry point for Google Cloud Functions.

Usage:
    # Deploy as Cloud Function
    gcloud functions deploy sentinel --runtime python312 --trigger-http

    # Local development
    functions-framework --target=sentinel --debug

    # Test with curl
    curl -X POST http://localhost:8080/health
    curl -X POST http://localhost:8080/jobs
    curl -X POST http://localhost:8080/execute -d '{"job": "clean_old_orders", "dry_run": true}'
"""

import logging
import os
import sys

# Configure logging before imports
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import functions_framework
    from flask import Request
    HAS_FUNCTIONS_FRAMEWORK = True
except ImportError:
    HAS_FUNCTIONS_FRAMEWORK = False
    logger.warning("functions-framework not installed, HTTP handler unavailable")


if HAS_FUNCTIONS_FRAMEWORK:
    @functions_framework.http
    def sentinel(request: Request):
        """
        Cloud Function HTTP entry point.

        Args:
            request: Flask Request object

        Returns:
            Tuple of (response_body, status_code, headers)
        """
        from adapters.http import handle_request

        try:
            response, status_code = handle_request(request)

            return (
                response,
                status_code,
                {"Content-Type": "application/json"},
            )

        except Exception as e:
            logger.exception("Unhandled exception in sentinel function")
            return (
                {"error": "Internal server error", "message": str(e)},
                500,
                {"Content-Type": "application/json"},
            )


def _print_kpis(kpis: dict, indent: int = 2) -> None:
    """
    Print KPIs with nice formatting, handling nested structures.

    Supports both flat KPIs and structured funnel KPIs.
    """
    prefix = " " * indent

    # Check if this is a structured funnel KPI (has discovery, orders, lines keys)
    if "discovery" in kpis or "orders" in kpis or "lines" in kpis:
        # Structured funnel format
        if "discovery" in kpis:
            d = kpis["discovery"]
            print(f"\n{prefix}=== DISCOVERY ===")
            print(f"{prefix}  Lines from query: {d.get('lines_from_query', 0):,}")
            print(f"{prefix}  Lines with mismatch: {d.get('lines_with_mismatch', 0):,}")
            print(f"{prefix}  Orders with mismatch: {d.get('orders_with_mismatch', 0):,}")
            limit = d.get('limit_param')
            reached = d.get('limit_reached', False)
            if limit:
                status = "REACHED" if reached else "not reached"
                print(f"{prefix}  Limit: {limit} ({status})")

        if "orders" in kpis:
            o = kpis["orders"]
            print(f"\n{prefix}=== ORDERS ===")
            print(f"{prefix}  Processed: {o.get('processed', 0)}")
            print(f"{prefix}    → Adjusted: {o.get('adjusted', 0)}")
            print(f"{prefix}    → Skipped (all lines correct): {o.get('skipped_all_lines_correct', 0)}")
            if o.get('with_errors', 0) > 0:
                print(f"{prefix}    → With errors: {o.get('with_errors', 0)}")

        if "lines" in kpis:
            ln = kpis["lines"]
            print(f"\n{prefix}=== LINES ===")
            print(f"{prefix}  Processed: {ln.get('processed', 0)}")
            print(f"{prefix}    → Adjusted: {ln.get('adjusted', 0)}")
            print(f"{prefix}    → Skipped (already correct): {ln.get('skipped_already_correct', 0)}")
            if ln.get('skipped_negative_qty', 0) > 0:
                print(f"{prefix}    → Skipped (negative qty): {ln.get('skipped_negative_qty', 0)}")
            if ln.get('with_errors', 0) > 0:
                print(f"{prefix}    → With errors: {ln.get('with_errors', 0)}")

        if "open_moves" in kpis:
            om = kpis["open_moves"]
            print(f"\n{prefix}=== CONTEXT ===")
            print(f"{prefix}  Lines with open moves: {om.get('lines_with_moves', 0)}")
            print(f"{prefix}  Lines without open moves: {om.get('lines_without_moves', 0)}")
    else:
        # Flat KPI format (legacy or simple jobs)
        for key, value in kpis.items():
            if isinstance(value, dict):
                print(f"{prefix}{key}:")
                for k, v in value.items():
                    print(f"{prefix}  {k}: {v}")
            else:
                print(f"{prefix}{key}: {value}")


def cli():
    """
    Command-line interface for local execution.

    Usage:
        python main.py list                    # List jobs
        python main.py run <job> [--dry-run]   # Run a job
        python main.py run <job> --days=60     # Run with params
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Sentinel-Ops - ERP Operations Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples (CLI):
    python main.py list
    python main.py run clean_old_orders --dry-run
    python main.py run check_ar_hold_violations --dry-run --limit 5
    python main.py run date_compliance_all --dry-run --limit 3

Examples (HTTP / Cloud Function):
    # Health check
    curl http://localhost:8080/health

    # List jobs
    curl http://localhost:8080/jobs

    # Run a job (dry-run, BQ discovery)
    curl -X POST http://localhost:8080/execute \\
      -H "Content-Type: application/json" \\
      -d '{"job": "check_ar_hold_violations", "dry_run": true}'

    # Run with limit
    curl -X POST http://localhost:8080/execute \\
      -H "Content-Type: application/json" \\
      -d '{"job": "check_ar_hold_violations", "dry_run": true, "params": {"limit": 5}}'

    # Run with specific IDs
    curl -X POST http://localhost:8080/execute \\
      -H "Content-Type: application/json" \\
      -d '{"job": "check_ar_hold_violations", "dry_run": true, "params": {"order_ids": [745296]}}'

    # Run all date compliance (BQ discovery)
    curl -X POST http://localhost:8080/execute \\
      -H "Content-Type: application/json" \\
      -d '{"job": "date_compliance_all", "dry_run": true, "params": {"limit": 3}}'
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # List command
    list_parser = subparsers.add_parser("list", help="List available jobs")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run a job")
    run_parser.add_argument("job", help="Job name to run")
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in dry-run mode (no mutations)",
    )
    run_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode with verbose output",
    )
    run_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of records to process",
    )
    args, extra_params = parser.parse_known_args()
    # Capture extra key=value params that argparse doesn't know about
    args.params = extra_params

    if not args.command:
        parser.print_help()
        return

    # Import here to avoid circular imports
    from core.context import RequestContext
    from core.jobs import get_job, list_jobs

    if args.command == "list":
        jobs = list_jobs()
        print("\nAvailable Jobs:")
        print("-" * 60)
        for job in jobs:
            tags = ", ".join(job.get("tags", [])) or "none"
            print(f"  {job['name']}")
            print(f"    Description: {job.get('description', 'No description')}")
            print(f"    Tags: {tags}")
            print()
        print(f"Total: {len(jobs)} jobs")

    elif args.command == "run":
        from core.config import get_settings

        job_name = args.job
        debug = args.debug

        # Enable debug logging if requested
        if debug:
            logging.getLogger().setLevel(logging.DEBUG)
            print("DEBUG MODE: Verbose output enabled\n")

        # Validate configuration before attempting to run
        settings = get_settings()
        try:
            settings.validate_for_job()
        except ValueError as e:
            print(str(e))
            sys.exit(1)

        # Show config summary in debug mode
        if debug:
            print("Configuration:")
            print(f"  ODOO_URL: {settings.odoo_url}")
            print(f"  ODOO_DB: {settings.odoo_db}")
            print(f"  ENVIRONMENT: {settings.environment}")
            if settings.is_bq_configured():
                print(f"  BigQuery: {settings.bq_project}/{settings.bq_dataset}")
            else:
                print("  BigQuery: NoOp (not configured)")
            if settings.slack_webhook_url:
                print(f"  Slack: {settings.slack_channel}")
            else:
                print("  Slack: Disabled")
            print()

        job_class = get_job(job_name)

        if not job_class:
            available = [j["name"] for j in list_jobs()]
            print(f"Error: Unknown job '{job_name}'")
            print(f"Available jobs: {', '.join(available)}")
            sys.exit(1)

        # Parse parameters
        params = {}
        for param in args.params or []:
            if "=" in param:
                key, value = param.split("=", 1)
                # Remove leading dashes
                key = key.lstrip("-")
                # Convert underscores to match Python parameter names
                key = key.replace("-", "_")
                # Handle comma-separated lists (e.g., order_ids=455346,455347)
                if "," in value:
                    try:
                        value = [int(x.strip()) for x in value.split(",")]
                    except ValueError:
                        value = [x.strip() for x in value.split(",")]
                else:
                    # Try to parse as number
                    try:
                        value = int(value)
                    except ValueError:
                        try:
                            value = float(value)
                        except ValueError:
                            # Handle boolean strings
                            if value.lower() in ("true", "yes"):
                                value = True
                            elif value.lower() in ("false", "no"):
                                value = False
                params[key] = value

        # Inject --limit flag into params (if provided and not already set)
        if args.limit is not None and "limit" not in params:
            params["limit"] = args.limit

        # Create context
        ctx = RequestContext.for_cli(
            job_name=job_name,
            dry_run=args.dry_run,
            debug=debug,
        )

        print(f"Running job: {job_name}")
        print(f"  Dry run: {args.dry_run}")
        print(f"  Parameters: {params}")
        if debug:
            print(f"  Debug: True")
        print("-" * 60)

        try:
            job = job_class(ctx)
            result = job.execute(**params)

            # Format result output
            print(f"\n{'='*60}")
            print(f"  Status: {result.status.value}")
            if result.duration_seconds:
                print(f"  Duration: {result.duration_seconds:.2f}s")

            # Show job-specific KPIs with nice formatting
            if result.kpis:
                _print_kpis(result.kpis)

            if result.errors:
                print(f"\n  Errors ({len(result.errors)}):")
                for error in result.errors[:5]:
                    print(f"    - {error}")
                if len(result.errors) > 5:
                    print(f"    ... and {len(result.errors) - 5} more")

            print(f"{'='*60}")

            # Full result dict only in debug mode
            if debug:
                import json
                print("\n[DEBUG] Full result:")
                print(json.dumps(result.to_kpi_dict(), indent=2, default=str))

        except Exception as e:
            logger.exception(f"Job {job_name} failed")
            print(f"\nJob failed: {e}")
            sys.exit(1)


if __name__ == "__main__":
    cli()
