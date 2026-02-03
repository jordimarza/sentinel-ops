#!/usr/bin/env bash
# Deploy/update Cloud Scheduler jobs for Sentinel-Ops
#
# Requirements: yq (brew install yq), gcloud CLI
#
# Usage:
#   ./scripts/scheduler-deploy.sh              # Deploy all enabled schedules
#   ./scripts/scheduler-deploy.sh --dry-run    # Show what would be done
#   ./scripts/scheduler-deploy.sh --job NAME   # Deploy specific job only
#   ./scripts/scheduler-deploy.sh --delete     # Delete disabled schedules
#   ./scripts/scheduler-deploy.sh --list       # List current schedules

set -euo pipefail

# Check dependencies
if ! command -v yq &> /dev/null; then
    echo "Error: yq is required. Install with: brew install yq"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/scheduler-config.yaml"
ENV_FILE="$SCRIPT_DIR/../.env.local"

# Load API key
if [[ -f "$ENV_FILE" ]]; then
    API_KEY=$(grep SENTINEL_API_KEY_SCHEDULER "$ENV_FILE" | cut -d= -f2)
else
    echo "Error: .env.local not found" >&2
    exit 1
fi

# Parse config
PROJECT=$(yq '.project' "$CONFIG_FILE")
REGION=$(yq '.region' "$CONFIG_FILE")
TIMEZONE=$(yq '.timezone' "$CONFIG_FILE")
FUNCTION_URL=$(yq '.function_url' "$CONFIG_FILE")

# Parse arguments
DRY_RUN=false
DELETE_DISABLED=false
LIST_ONLY=false
SPECIFIC_JOB=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true ;;
        --delete) DELETE_DISABLED=true ;;
        --list) LIST_ONLY=true ;;
        --job) SPECIFIC_JOB="$2"; shift ;;
        --job=*) SPECIFIC_JOB="${1#--job=}" ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --dry-run     Show what would be done without making changes"
            echo "  --delete      Also delete disabled schedules"
            echo "  --list        List current Cloud Scheduler jobs"
            echo "  --job NAME    Deploy only the specified job"
            echo "  -h, --help    Show this help"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
    shift
done

# List current schedules
if [[ "$LIST_ONLY" == true ]]; then
    echo "Current Cloud Scheduler jobs for project $PROJECT:"
    echo "------------------------------------------------------------"
    gcloud scheduler jobs list --project="$PROJECT" --location="$REGION" \
        --format="table(name.basename(), schedule, state, httpTarget.uri)" \
        --filter="name~sentinel-"
    exit 0
fi

echo "Deploying Cloud Scheduler jobs"
echo "Project: $PROJECT"
echo "Region: $REGION"
echo "Timezone: $TIMEZONE"
echo "Function URL: $FUNCTION_URL"
echo "------------------------------------------------------------"

# Get number of schedules
NUM_SCHEDULES=$(yq '.schedules | length' "$CONFIG_FILE")

for i in $(seq 0 $((NUM_SCHEDULES - 1))); do
    NAME=$(yq ".schedules[$i].name" "$CONFIG_FILE")
    SCHEDULE=$(yq ".schedules[$i].schedule" "$CONFIG_FILE")
    JOB=$(yq ".schedules[$i].job" "$CONFIG_FILE")
    ENABLED=$(yq ".schedules[$i].enabled" "$CONFIG_FILE")
    DESCRIPTION=$(yq ".schedules[$i].description" "$CONFIG_FILE")
    PARAMS=$(yq -o=json ".schedules[$i].params // {}" "$CONFIG_FILE")

    # Skip if specific job requested and this isn't it
    if [[ -n "$SPECIFIC_JOB" && "$NAME" != "$SPECIFIC_JOB" ]]; then
        continue
    fi

    echo ""
    echo "Job: $NAME"
    echo "  Schedule: $SCHEDULE"
    echo "  Sentinel job: $JOB"
    echo "  Enabled: $ENABLED"

    # Build request body
    BODY=$(cat <<EOF
{
  "job": "$JOB",
  "dry_run": false,
  "params": $PARAMS
}
EOF
)

    if [[ "$ENABLED" == "true" ]]; then
        echo "  Action: CREATE/UPDATE"
        echo "  Body: $BODY"

        if [[ "$DRY_RUN" == true ]]; then
            echo "  [DRY-RUN] Would create/update scheduler job"
        else
            # Check if job exists
            if gcloud scheduler jobs describe "$NAME" --project="$PROJECT" --location="$REGION" &>/dev/null; then
                echo "  Updating existing job..."
                gcloud scheduler jobs update http "$NAME" \
                    --project="$PROJECT" \
                    --location="$REGION" \
                    --schedule="$SCHEDULE" \
                    --time-zone="$TIMEZONE" \
                    --uri="$FUNCTION_URL" \
                    --http-method=POST \
                    --update-headers="Content-Type=application/json,X-API-Key=$API_KEY" \
                    --message-body="$BODY" \
                    --description="$DESCRIPTION" \
                    --attempt-deadline=1200s \
                    --quiet
                echo "  Updated: $NAME"
            else
                echo "  Creating new job..."
                gcloud scheduler jobs create http "$NAME" \
                    --project="$PROJECT" \
                    --location="$REGION" \
                    --schedule="$SCHEDULE" \
                    --time-zone="$TIMEZONE" \
                    --uri="$FUNCTION_URL" \
                    --http-method=POST \
                    --headers="Content-Type=application/json,X-API-Key=$API_KEY" \
                    --message-body="$BODY" \
                    --description="$DESCRIPTION" \
                    --attempt-deadline=1200s \
                    --quiet
                echo "  Created: $NAME"
            fi
        fi
    else
        if [[ "$DELETE_DISABLED" == true ]]; then
            echo "  Action: DELETE (disabled)"
            if [[ "$DRY_RUN" == true ]]; then
                echo "  [DRY-RUN] Would delete scheduler job"
            else
                if gcloud scheduler jobs describe "$NAME" --project="$PROJECT" --location="$REGION" &>/dev/null; then
                    gcloud scheduler jobs delete "$NAME" \
                        --project="$PROJECT" \
                        --location="$REGION" \
                        --quiet
                    echo "  Deleted: $NAME"
                else
                    echo "  Skipped (doesn't exist)"
                fi
            fi
        else
            echo "  Action: SKIP (disabled, use --delete to remove)"
        fi
    fi
done

echo ""
echo "------------------------------------------------------------"
echo "Done!"
if [[ "$DRY_RUN" == true ]]; then
    echo "(Dry-run mode - no changes made)"
fi
