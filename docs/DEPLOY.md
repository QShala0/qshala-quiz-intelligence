# Deploying and rolling back (API on Cloud Run)

## How a deploy works now

`deploy/cloudbuild.yaml` runs on every push to `main`: **tests -> build -> push -> deploy -> update the worker job**.
If the tests fail nothing is built or deployed. The image is tagged with the commit SHA, so every deploy is an
addressable, rollback-able version. Only the image changes; env vars, secrets, memory and instance settings stay as
configured on the service.

The frontend deploys separately on Vercel and is not affected.

## One-time setup (needs the GCP console or `gcloud`)

1. Cloud Build -> Triggers -> create a trigger on push to `main`, configuration type **Cloud Build configuration file**,
   location `deploy/cloudbuild.yaml`. Check the substitution defaults at the top of that file (service, job, region,
   Artifact Registry repo).
2. Give the Cloud Build service account: `Cloud Run Admin`, `Service Account User`, `Artifact Registry Writer`.
3. **Disable** the old "Continuously deploy from source repository" trigger and, if it exists, the separate
   `sync-ingestion-job` trigger (this file already updates the worker job), so a commit is not deployed twice.
4. Run `deploy/export_cloudrun_settings.sh`, review the two `.live.yaml` files for secrets, and commit them. That records
   the real memory, CPU, concurrency, timeout and instance limits in git instead of only in the console.

## Rolling back

Fastest (seconds, no rebuild) - send traffic back to the previous revision:

```bash
gcloud run revisions list --service qshala-quiz-intelligence --region asia-south1
gcloud run services update-traffic qshala-quiz-intelligence --region asia-south1 --to-revisions=<PREVIOUS_REVISION>=100
```

Also point the worker back, or uploads will run newer code than the API:

```bash
gcloud run jobs update qshala-ingestion-worker --region asia-south1 \
  --image asia-south1-docker.pkg.dev/<PROJECT>/cloud-run-source-deploy/qshala-quiz-intelligence:<GOOD_COMMIT_SHA>
```

Then fix forward on a branch; do not leave `main` on the bad commit.

## If a database migration was part of the bad deploy

Migrations in this repo only add things (columns, indexes, tables), so rolling the code back is safe. Do not drop the
new column or table during an incident; the old code ignores them.

## Undoing a deleted deck or question bank

Deletes keep a recoverable copy for 30 days: `GET /api/v1/trash` lists them and
`POST /api/v1/trash/<id>/restore` puts one back.
