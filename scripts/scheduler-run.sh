#!/usr/bin/env bash
# Manually trigger a Cloud Scheduler job (run now)
#
# Usage:
#   ./scripts/scheduler-run.sh sentinel-date-compliance-all
#   ./scripts/scheduler-run.sh sentinel-clean-empty-drafts

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
    echo "Usage: $0 <scheduler-job-name>"
    echo ""
    echo "Available jobs:"
    gcloud scheduler jobs list --project="$PROJECT" --location="$REGION" \
        --format="value(name.basename())" \
        --filter="name~sentinel-" | while read -r job; do
        echo "  $job"
    done
    exit 0
fi

JOB_NAME="$1"

echo "Triggering: $JOB_NAME"
echo "------------------------------------------------------------"

gcloud scheduler jobs run "$JOB_NAME" \
    --project="$PROJECT" \
    --location="$REGION"

echo ""
echo "Job triggered! Check logs:"
echo "  gcloud functions logs read sentinel-ops --project=$PROJECT --region=$REGION --limit=50"
