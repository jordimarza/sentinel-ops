#!/usr/bin/env python3
"""
Local Run Script

Convenience script for running sentinel-ops jobs locally.

Usage:
    python scripts/local_run.py --job clean_old_orders --dry-run
    python scripts/local_run.py --job clean_old_orders --days=60
    python scripts/local_run.py --list
"""

import argparse
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment from .env.local
try:
    from dotenv import load_dotenv
    env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local")
    if os.path.exists(env_file):
        load_dotenv(env_file)
        print(f"Loaded environment from {env_file}")
except ImportError:
    print("python-dotenv not installed, using system environment")


def main():
    parser = argparse.ArgumentParser(
        description="Run sentinel-ops jobs locally",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--job",
        help="Job name to run",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Run in dry-run mode (default: True)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in live mode (actually make changes)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available jobs",
    )

    # Allow arbitrary job parameters
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Days parameter for jobs that use it",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of records to process",
    )

    args = parser.parse_args()

    # Import after path setup
    from core.context import RequestContext
    from core.jobs import get_job, list_jobs

    if args.list:
        print("\nAvailable Jobs:")
        print("-" * 60)
        for job in list_jobs():
            print(f"  {job['name']}")
            print(f"    {job.get('description', 'No description')}")
            print(f"    Tags: {', '.join(job.get('tags', []))}")
            print()
        return

    if not args.job:
        parser.print_help()
        return

    job_class = get_job(args.job)
    if not job_class:
        print(f"Error: Unknown job '{args.job}'")
        print(f"Available: {[j['name'] for j in list_jobs()]}")
        sys.exit(1)

    # Determine dry-run mode
    dry_run = not args.live

    # Build parameters
    params = {"days": args.days}
    if args.limit:
        params["limit"] = args.limit

    # Create context
    ctx = RequestContext.for_cli(
        job_name=args.job,
        dry_run=dry_run,
    )

    print(f"\nRunning: {args.job}")
    print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"  Parameters: {params}")
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

        if result.kpis:
            print("\nKPIs:")
            for key, value in result.kpis.items():
                print(f"  {key}: {value}")

        if result.errors:
            print("\nErrors (first 5):")
            for error in result.errors[:5]:
                print(f"  - {error}")

    except Exception as e:
        print(f"\nJob failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
