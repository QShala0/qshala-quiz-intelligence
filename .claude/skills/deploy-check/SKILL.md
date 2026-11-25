---
name: deploy-check
description: Check whether the latest merge to main actually deployed (Cloud Build gate, live Cloud Run revision and job image). Use after merging, or when the owner asks if something is live.
---

A failing test blocks the backend deploy silently, so always verify. Run:

```
gcloud builds list --project qshala-quiz --limit 4 --format="table(id,status,createTime,substitutions.COMMIT_SHA)"
gcloud run services describe qshala-quiz-intelligence --project qshala-quiz --region asia-southeast1 --format="value(status.latestReadyRevisionName,status.conditions[0].status)"
gcloud run jobs describe qshala-ingestion-worker --project qshala-quiz --region asia-southeast1 --format="value(spec.template.spec.template.spec.containers[0].image)"
git fetch -q origin main && git log origin/main --oneline | head -5
```

If a build is WORKING, poll it (45 s steps, up to about 10 minutes) with `gcloud builds describe <id> --format="value(status)"`.

Report: the build status for the merged commit, whether the live image tag is that commit (or a newer one that includes it), and any build still running. If a build FAILED, fetch the log (`gcloud builds log <id>`), name the failing test, and say the deploy did NOT happen. Remember the service and job share one image and the frontend deploys separately on Vercel. Never run `gcloud builds submit`.
