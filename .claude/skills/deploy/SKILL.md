---
name: deploy
description: Deploy sentinel-ops to Cloud Functions. Use when user says "deploy", "push to cloud", or "deploy to GCP".
allowed-tools: Bash(gcloud *), Bash(./scripts/deploy.sh)
---

# Deploy to Cloud Functions

> Deploy sentinel-ops to Google Cloud Functions.

## Prerequisites

- gcloud CLI installed and authenticated
- GCP Project with Cloud Functions enabled
- Secrets configured in Secret Manager
- Service Account with required permissions

## Steps

1. **Set project**
   ```bash
   gcloud config set project $ARGUMENTS
   ```

2. **Deploy function**
   ```bash
   ./scripts/deploy.sh
   ```

   Or manually:
   ```bash
   gcloud functions deploy sentinel \
     --gen2 \
     --runtime=python312 \
     --region=us-central1 \
     --source=. \
     --entry-point=sentinel \
     --trigger-http \
     --allow-unauthenticated \
     --set-env-vars="ENVIRONMENT=production,GCP_PROJECT=$ARGUMENTS" \
     --memory=256MB \
     --timeout=540s
   ```

3. **Verify deployment**
   ```bash
   FUNCTION_URL=$(gcloud functions describe sentinel --gen2 --region=us-central1 --format='value(serviceConfig.uri)')
   curl $FUNCTION_URL/health
   curl $FUNCTION_URL/jobs
   ```

## Arguments

- `$ARGUMENTS` - GCP Project ID

## First Time Setup: Secrets

```bash
echo -n "https://your-odoo.com" | gcloud secrets create sentinel-ops-odoo-url --data-file=-
echo -n "your_database" | gcloud secrets create sentinel-ops-odoo-db --data-file=-
echo -n "api_user" | gcloud secrets create sentinel-ops-odoo-username --data-file=-
echo -n "secret_password" | gcloud secrets create sentinel-ops-odoo-password --data-file=-
```

## Service Account Permissions

```bash
gcloud projects add-iam-policy-binding $ARGUMENTS \
  --member="serviceAccount:$ARGUMENTS@appspot.gserviceaccount.com" \
  --role="roles/bigquery.dataEditor"

gcloud projects add-iam-policy-binding $ARGUMENTS \
  --member="serviceAccount:$ARGUMENTS@appspot.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

## Scheduling Jobs

```bash
gcloud scheduler jobs create http sentinel-daily-cleanup \
  --location=us-central1 \
  --schedule="0 2 * * *" \
  --uri="$FUNCTION_URL/execute" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"job": "clean_old_orders", "dry_run": false}'
```

## Monitoring

```bash
# View logs
gcloud functions logs read sentinel --gen2 --region=us-central1 --limit=50
```

---

**Version**: 1.1.0
**Updated**: 2025-01-22 - Converted to SKILL.md format
