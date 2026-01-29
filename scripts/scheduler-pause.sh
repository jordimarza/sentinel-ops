#!/usr/bin/env bash
# Pause or resume Cloud Scheduler jobs
#
# Usage:
#   ./scripts/scheduler-pause.sh pause sentinel-date-compliance-all
#   ./scripts/scheduler-pause.sh resume sentinel-date-compliance-all
#   ./scripts/scheduler-pause.sh pause-all    # Pause all sentinel jobs
#   ./scripts/scheduler-pause.sh resume-all   # Resume all sentinel jobs
#   ./scripts/scheduler-pause.sh status       # Show status of all jobs

set -euo pipefail

if ! command -v yq &> /dev/null; then
    echo "Error: yq is required. Install with: brew install yq"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/scheduler-config.yaml"

PROJECT=$(yq '.project' "$CONFIG_FILE")
REGION=$(yq '.region' "$CONFIG_FILE")

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <action> [job-name]"
    echo ""
    echo "Actions:"
    echo "  pause <job>    Pause a specific job"
    echo "  resume <job>   Resume a specific job"
    echo "  pause-all      Pause all sentinel jobs"
    echo "  resume-all     Resume all sentinel jobs"
    echo "  status         Show status of all jobs"
    exit 0
fi

ACTION="$1"
JOB_NAME="${2:-}"

case "$ACTION" in
    pause)
        if [[ -z "$JOB_NAME" ]]; then
            echo "Error: Job name required"
            exit 1
        fi
        echo "Pausing: $JOB_NAME"
        gcloud scheduler jobs pause "$JOB_NAME" \
            --project="$PROJECT" \
            --location="$REGION"
        echo "Paused!"
        ;;

    resume)
        if [[ -z "$JOB_NAME" ]]; then
            echo "Error: Job name required"
            exit 1
        fi
        echo "Resuming: $JOB_NAME"
        gcloud scheduler jobs resume "$JOB_NAME" \
            --project="$PROJECT" \
            --location="$REGION"
        echo "Resumed!"
        ;;

    pause-all)
        echo "Pausing all sentinel jobs..."
        gcloud scheduler jobs list --project="$PROJECT" --location="$REGION" \
            --format="value(name.basename())" \
            --filter="name~sentinel-" | while read -r job; do
            echo "  Pausing: $job"
            gcloud scheduler jobs pause "$job" \
                --project="$PROJECT" \
                --location="$REGION" 2>/dev/null || true
        done
        echo "All jobs paused!"
        ;;

    resume-all)
        echo "Resuming all sentinel jobs..."
        gcloud scheduler jobs list --project="$PROJECT" --location="$REGION" \
            --format="value(name.basename())" \
            --filter="name~sentinel-" | while read -r job; do
            echo "  Resuming: $job"
            gcloud scheduler jobs resume "$job" \
                --project="$PROJECT" \
                --location="$REGION" 2>/dev/null || true
        done
        echo "All jobs resumed!"
        ;;

    status)
        echo "Cloud Scheduler job status:"
        echo "------------------------------------------------------------"
        gcloud scheduler jobs list --project="$PROJECT" --location="$REGION" \
            --format="table(name.basename(), state, schedule, lastAttemptTime.date(), httpTarget.uri:label=TARGET)" \
            --filter="name~sentinel-"
        ;;

    *)
        echo "Unknown action: $ACTION"
        exit 1
        ;;
esac
