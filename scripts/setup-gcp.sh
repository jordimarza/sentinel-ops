#!/bin/bash
# =============================================================================
# Sentinel-Ops GCP Setup Script
# =============================================================================
# Run this ONCE to set up service account, secrets, and permissions.
#
# Usage:
#   ./scripts/setup-gcp.sh              # Run setup
#   ./scripts/setup-gcp.sh --dry-run    # Show what would be created
# =============================================================================

set -e

# Configuration
CF_PROJECT="alohas-automation"      # Cloud Functions project
BQ_PROJECTS=("alohas-data" "alohas-analytics")  # BigQuery projects (read/write access)
SERVICE_ACCOUNT_NAME="sentinel-ops"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT_NAME}@${CF_PROJECT}.iam.gserviceaccount.com"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}=== Sentinel-Ops GCP Setup ===${NC}"
echo "Cloud Functions Project: ${CF_PROJECT}"
echo "BigQuery Projects: ${BQ_PROJECTS[*]}"
echo "Service Account: ${SERVICE_ACCOUNT}"
echo ""

# Check if dry-run
DRY_RUN=false
if [[ "$1" == "--dry-run" ]]; then
    echo -e "${YELLOW}DRY RUN - Not actually creating resources${NC}"
    echo ""
    DRY_RUN=true
fi

# =============================================================================
# Step 1: Create Service Account
# =============================================================================
echo -e "${GREEN}Step 1: Creating service account...${NC}"
if [[ "$DRY_RUN" == false ]]; then
    gcloud config set project "$CF_PROJECT"

    if gcloud iam service-accounts describe "$SERVICE_ACCOUNT" &>/dev/null; then
        echo "Service account already exists"
    else
        gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" \
            --display-name="Sentinel-Ops Cloud Function"
        echo "Created service account"
    fi
else
    echo "Would create: $SERVICE_ACCOUNT"
fi
echo ""

# =============================================================================
# Step 2: Grant Permissions
# =============================================================================
echo -e "${GREEN}Step 2: Granting permissions...${NC}"

# Secret Manager access (in CF project)
echo "  - Secret Manager access in ${CF_PROJECT}"
if [[ "$DRY_RUN" == false ]]; then
    gcloud projects add-iam-policy-binding "$CF_PROJECT" \
        --member="serviceAccount:${SERVICE_ACCOUNT}" \
        --role="roles/secretmanager.secretAccessor" \
        --quiet
fi

# BigQuery access (in each BQ project)
for BQ_PROJECT in "${BQ_PROJECTS[@]}"; do
    echo "  - BigQuery dataEditor in ${BQ_PROJECT}"
    if [[ "$DRY_RUN" == false ]]; then
        gcloud projects add-iam-policy-binding "$BQ_PROJECT" \
            --member="serviceAccount:${SERVICE_ACCOUNT}" \
            --role="roles/bigquery.dataEditor" \
            --quiet
    fi

    echo "  - BigQuery jobUser in ${BQ_PROJECT}"
    if [[ "$DRY_RUN" == false ]]; then
        gcloud projects add-iam-policy-binding "$BQ_PROJECT" \
            --member="serviceAccount:${SERVICE_ACCOUNT}" \
            --role="roles/bigquery.jobUser" \
            --quiet
    fi
done
echo ""

# =============================================================================
# Step 3: Create Secrets
# =============================================================================
echo -e "${GREEN}Step 3: Creating secrets...${NC}"
echo -e "${YELLOW}You will be prompted to enter values for each secret.${NC}"
echo ""

create_secret() {
    local name=$1
    local description=$2

    if [[ "$DRY_RUN" == true ]]; then
        echo "Would create secret: $name"
        return
    fi

    if gcloud secrets describe "$name" --project="$CF_PROJECT" &>/dev/null; then
        echo "Secret $name already exists (skipping)"
    else
        echo -n "Enter value for $name ($description): "
        read -s value
        echo ""
        echo -n "$value" | gcloud secrets create "$name" --data-file=- --project="$CF_PROJECT"
        echo "Created secret: $name"
    fi
}

create_secret "sentinel-ops-odoo-url" "Odoo URL (e.g., https://odoo.alohas.com)"
create_secret "sentinel-ops-odoo-db" "Odoo database name"
create_secret "sentinel-ops-odoo-username" "Odoo username/email"
create_secret "sentinel-ops-odoo-password" "Odoo API password"
create_secret "sentinel-ops-bq-project" "BigQuery project ID"
create_secret "sentinel-ops-bq-dataset" "BigQuery dataset name"

echo ""

# =============================================================================
# Step 4: Enable APIs
# =============================================================================
echo -e "${GREEN}Step 4: Enabling required APIs...${NC}"
if [[ "$DRY_RUN" == false ]]; then
    gcloud services enable cloudfunctions.googleapis.com --project="$CF_PROJECT"
    gcloud services enable cloudbuild.googleapis.com --project="$CF_PROJECT"
    gcloud services enable secretmanager.googleapis.com --project="$CF_PROJECT"
    gcloud services enable run.googleapis.com --project="$CF_PROJECT"
    echo "APIs enabled"
else
    echo "Would enable: cloudfunctions, cloudbuild, secretmanager, run"
fi
echo ""

# =============================================================================
# Done
# =============================================================================
echo -e "${GREEN}=== Setup Complete ===${NC}"
echo ""
echo "Next steps:"
echo "  1. Deploy: ./scripts/deploy.sh"
echo "  2. Or set up GitHub auto-deploy (see below)"
echo ""
echo "To connect GitHub for auto-deploy:"
echo "  1. Go to: https://console.cloud.google.com/run?project=${CF_PROJECT}"
echo "  2. Click 'Create Service' or 'Edit' on sentinel"
echo "  3. Click 'Set up Continuous Deployment'"
echo "  4. Connect your GitHub repo"
echo "  5. Select branch: main"
echo "  6. Build type: Dockerfile or Buildpacks"
