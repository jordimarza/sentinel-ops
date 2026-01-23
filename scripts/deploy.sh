#!/bin/bash
# =============================================================================
# Sentinel-Ops Deployment Script
# =============================================================================
# Usage:
#   ./scripts/deploy.sh              # Deploy to production
#   ./scripts/deploy.sh --dry-run    # Show what would be deployed
# =============================================================================

set -e

# Configuration
PROJECT_ID="alohas-automation"
REGION="europe-west1"
FUNCTION_NAME="sentinel"
SERVICE_ACCOUNT="sentinel-ops@${PROJECT_ID}.iam.gserviceaccount.com"
RUNTIME="python312"
MEMORY="512MB"
TIMEOUT="540s"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}=== Sentinel-Ops Deployment ===${NC}"
echo "Project: ${PROJECT_ID}"
echo "Region: ${REGION}"
echo "Service Account: ${SERVICE_ACCOUNT}"
echo ""

# Check if dry-run
DRY_RUN=false
if [[ "$1" == "--dry-run" ]]; then
    echo -e "${YELLOW}DRY RUN - Not actually deploying${NC}"
    DRY_RUN=true
fi

# Change to project directory
cd "$(dirname "$0")/.."
echo "Deploying from: $(pwd)"
echo ""

# Set project
if [[ "$DRY_RUN" == false ]]; then
    gcloud config set project "$PROJECT_ID"
fi

# Deploy function
echo -e "${GREEN}Deploying Cloud Function...${NC}"
DEPLOY_CMD="gcloud functions deploy $FUNCTION_NAME \
    --gen2 \
    --runtime=$RUNTIME \
    --region=$REGION \
    --source=. \
    --entry-point=sentinel \
    --trigger-http \
    --service-account=$SERVICE_ACCOUNT \
    --set-env-vars=ENVIRONMENT=production,GCP_PROJECT=$PROJECT_ID \
    --memory=$MEMORY \
    --timeout=$TIMEOUT \
    --min-instances=0 \
    --max-instances=10"

echo "$DEPLOY_CMD"
echo ""

if [[ "$DRY_RUN" == false ]]; then
    eval "$DEPLOY_CMD"

    # Get function URL
    FUNCTION_URL=$(gcloud functions describe "$FUNCTION_NAME" --gen2 --region="$REGION" --format='value(serviceConfig.uri)')

    echo ""
    echo -e "${GREEN}=== Deployment Complete ===${NC}"
    echo ""
    echo "Function URL: $FUNCTION_URL"
    echo ""
    echo "Test commands:"
    echo "  curl $FUNCTION_URL/health"
    echo "  curl $FUNCTION_URL/jobs"
    echo "  curl -X POST $FUNCTION_URL/execute -H 'Content-Type: application/json' -d '{\"job\": \"clean_old_orders\", \"dry_run\": true}'"
else
    echo -e "${YELLOW}Would deploy with above command${NC}"
fi
