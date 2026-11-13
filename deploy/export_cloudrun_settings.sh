#!/usr/bin/env bash
# Writes the LIVE Cloud Run settings (service and worker job) into deploy/ so they are versioned and reviewable.
# Run it from a machine logged in to gcloud, review the files for secrets, then commit them.
# Secrets configured through Secret Manager appear only as references; plain-text env vars appear as values, so
# check the output before committing and move anything sensitive to Secret Manager.
set -euo pipefail
SERVICE="${SERVICE:-qshala-quiz-intelligence}"
JOB="${JOB:-qshala-ingestion-worker}"
REGION="${REGION:-asia-south1}"
cd "$(dirname "$0")"
gcloud run services describe "$SERVICE" --region="$REGION" --format=export > cloudrun-service.live.yaml
gcloud run jobs describe "$JOB" --region="$REGION" --format=export > cloudrun-job.live.yaml
echo "Wrote deploy/cloudrun-service.live.yaml and deploy/cloudrun-job.live.yaml - review for secrets, then commit."
grep -nE "maxScale|minScale|memory:|cpu:|containerConcurrency|timeoutSeconds" cloudrun-service.live.yaml || true
