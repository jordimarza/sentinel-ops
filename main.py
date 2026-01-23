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
Examples:
    python main.py list
    python main.py run clean_old_orders --dry-run
    python main.py run clean_old_orders --days=60 --limit=100
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
    # Allow arbitrary parameters
    run_parser.add_argument(
        "params",
        nargs="*",
        help="Job parameters as key=value pairs",
    )

    args = parser.parse_args()

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

            print("\nResult:")
            print("-" * 60)
            print(f"  Status: {result.status.value}")
            print(f"  Records checked: {result.records_checked}")
            print(f"  Records updated: {result.records_updated}")
            print(f"  Records skipped: {result.records_skipped}")
            print(f"  Errors: {len(result.errors)}")
            if result.duration_seconds:
                print(f"  Duration: {result.duration_seconds:.2f}s")

            # Print job-specific KPIs
            if result.kpis:
                print("\nKPIs:")
                for key, value in result.kpis.items():
                    print(f"  {key}: {value}")

            if result.errors:
                print("\nErrors:")
                for error in result.errors[:5]:
                    print(f"  - {error}")
                if len(result.errors) > 5:
                    print(f"  ... and {len(result.errors) - 5} more")

            # Output result dict for programmatic use
            print("\nResult Dict (for BigQuery):")
            import json
            print(json.dumps(result.to_dict(), indent=2, default=str))

        except Exception as e:
            logger.exception(f"Job {job_name} failed")
            print(f"\nJob failed: {e}")
            sys.exit(1)


if __name__ == "__main__":
    cli()
