#!/usr/bin/env bash
# Remote job runner - calls Cloud Function and formats output
# Usage: ./scripts/remote.sh <job_name> [--dry-run] [--limit N] [param=value ...]
#
# Examples:
#   ./scripts/remote.sh check_ar_hold_violations --dry-run --limit 5
#   ./scripts/remote.sh sync_so_picking_dates --dry-run --limit 10
#   ./scripts/remote.sh sync_po_picking_dates --dry-run
#   ./scripts/remote.sh date_compliance_all --dry-run --limit 3
#   ./scripts/remote.sh clean_old_orders --dry-run days=60 --limit 10
#   ./scripts/remote.sh adjust_closed_order_quantities --dry-run --limit 10
#   ./scripts/remote.sh complete_shipping_only_orders --dry-run --limit 10  # B2B (S%) by default
#   ./scripts/remote.sh complete_shipping_only_orders --dry-run --limit 10 order_name_pattern=%  # All orders
#   ./scripts/remote.sh clean_empty_draft_transfers --dry-run --limit 10
#   ./scripts/remote.sh check_ar_hold_violations --dry-run order_ids=745296
#   ./scripts/remote.sh check_ar_hold_violations order_ids=745296  # LIVE

set -euo pipefail

URL="https://sentinel-ops-659945993606.europe-west1.run.app/execute"

# Load API key from .env.local
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../.env.local"
if [[ -f "$ENV_FILE" ]]; then
    API_KEY=$(grep SENTINEL_API_KEY_SCHEDULER "$ENV_FILE" | cut -d= -f2)
else
    echo "Error: .env.local not found" >&2
    exit 1
fi

if [[ $# -lt 1 ]]; then
    echo "Usage: ./scripts/remote.sh <job_name> [--dry-run] [--limit N] [param=value ...]"
    echo ""
    echo "Jobs:"
    echo "  check_ar_hold_violations    sync_so_picking_dates"
    echo "  sync_po_picking_dates       date_compliance_all"
    echo "  clean_old_orders            adjust_closed_order_quantities"
    echo "  complete_shipping_only_orders  clean_empty_draft_transfers"
    exit 0
fi

JOB="$1"
shift

DRY_RUN=false
LIMIT=""
PARAMS=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true ;;
        --limit) LIMIT="$2"; shift ;;
        --limit=*) LIMIT="${1#--limit=}" ;;
        *=*)
            KEY="${1%%=*}"
            VAL="${1#*=}"
            # Detect lists (comma-separated numbers)
            if [[ "$VAL" =~ ^[0-9]+(,[0-9]+)+$ ]]; then
                VAL="[${VAL}]"
            elif [[ "$VAL" =~ ^[0-9]+$ ]]; then
                # Single number - check if key expects a list
                if [[ "$KEY" == *_ids ]]; then
                    VAL="[${VAL}]"
                fi
                # else keep as number
            elif [[ "$VAL" == "true" || "$VAL" == "True" ]]; then
                VAL="true"
            elif [[ "$VAL" == "false" || "$VAL" == "False" ]]; then
                VAL="false"
            else
                VAL="\"${VAL}\""
            fi
            if [[ -n "$PARAMS" ]]; then
                PARAMS="$PARAMS, \"$KEY\": $VAL"
            else
                PARAMS="\"$KEY\": $VAL"
            fi
            ;;
    esac
    shift
done

# Add limit to params
if [[ -n "$LIMIT" ]]; then
    if [[ -n "$PARAMS" ]]; then
        PARAMS="$PARAMS, \"limit\": $LIMIT"
    else
        PARAMS="\"limit\": $LIMIT"
    fi
fi

# Build JSON body
if [[ -n "$PARAMS" ]]; then
    BODY="{\"job\": \"$JOB\", \"dry_run\": $DRY_RUN, \"params\": {$PARAMS}}"
else
    BODY="{\"job\": \"$JOB\", \"dry_run\": $DRY_RUN}"
fi

echo "Running: $JOB (dry_run=$DRY_RUN)"
echo "Body: $BODY"
echo "------------------------------------------------------------"

curl -s -X POST "$URL" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: $API_KEY" \
    -d "$BODY" | python -m json.tool

echo ""
