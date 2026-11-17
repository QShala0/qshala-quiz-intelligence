# SharePoint import

Quizmasters pick decks (or whole folders) in **Settings > SharePoint**, preview what is new, and import. Files are read
through Microsoft Graph with an app-only, read-only grant to **one** SharePoint site (`Sites.Selected`).

## How it works
1. **Browse** (`GET /sharepoint/browse`): folders and `.pptx` / `.pdf` decks, each marked New, Changed, Already imported,
   In progress or Possible duplicate (same file name and size as a deck uploaded by hand).
2. **Preview** (`POST /sharepoint/plan`): dry run for a selection (files, folders, sub-folders, name / date filters).
3. **Import** (`POST /sharepoint/import`): marks the wanted files SELECTED as one batch. Nothing is downloaded yet.
4. **Transfer** (`POST /sharepoint/transfer-next`): the browser calls this repeatedly; each call moves up to 3 files.
   A file is downloaded (size-capped), hashed, skipped if a byte-identical deck is already in QShala, otherwise stored in
   blob storage and queued as the same ingestion job the older delta sync creates. A paused or interrupted import is
   resumed with the Resume button; files stuck mid-download for 10 minutes are handed back automatically.

The older `POST /sharepoint/sync` (delta sync of a whole library) is unchanged.

## One-time setup (an admin does this)
1. **Entra admin center > App registrations > New registration** (single tenant). Note the *Tenant ID* and *Client ID*.
2. **Certificates & secrets > New client secret.** Copy the value once. Secrets expire (max 24 months): add a reminder.
3. **API permissions > Add > Microsoft Graph > Application permissions > `Sites.Selected` > Grant admin consent.**
4. **Grant the app read access to the one site** (needs a SharePoint/Global admin). Either PnP PowerShell:
   `Grant-PnPAzureADAppSitePermission -AppId <client-id> -DisplayName "QShala" -Site https://<tenant>.sharepoint.com/sites/<site> -Permissions Read`
   or Graph `POST /sites/{site-id}/permissions` with `{"roles":["read"],"grantedToIdentities":[{"application":{"id":"<client-id>","displayName":"QShala"}}]}`.
5. **Find the site id:** `GET https://graph.microsoft.com/v1.0/sites/<tenant>.sharepoint.com:/sites/<site>`; use the full `id`
   (`hostname,siteCollectionId,siteId`).
6. **Cloud Run settings** (service `qshala-quiz-intelligence`): `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`
   (from Secret Manager), `SHAREPOINT_SITE_ID`. Optional: `SHAREPOINT_DRIVE_ID` (pin one library), `SHAREPOINT_MAX_FILE_MB`
   (default 200), `SHAREPOINT_MAX_PLAN_ITEMS` (default 2000), `SHAREPOINT_MAX_DEPTH` (default 8). `BLOB_READ_WRITE_TOKEN`
   must already be set. The ingestion worker does not need the Azure values.

## Troubleshooting
| Message | Cause |
|---|---|
| "SharePoint refused access" | Step 3 consent or step 4 site grant is missing, or the secret expired. |
| "not found" | Wrong `SHAREPOINT_SITE_ID` / drive, or the item was deleted. |
| "SharePoint is throttling requests" | Graph rate limit; the importer already waits and retries, try again in a minute. |
| "over the N MB limit" | Raise `SHAREPOINT_MAX_FILE_MB` or import that file by hand. |
| "Identical file already in QShala" | Byte-identical deck exists (same sha256); it was skipped on purpose. |
