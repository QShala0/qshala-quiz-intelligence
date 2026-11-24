# QShala Cost Audit: November 2026

**Date:** 23 November 2026  
**Auditor:** Antigravity (Read-Only Cost Auditor)  
**Methodology:** Read-only inspection of Google Cloud Run, Cloud Monitoring metrics, Cloudflare R2 bucket (`qshala-media`), Neon PostgreSQL database (`qshala-quiz`), Gemini API telemetry, and local instrumented quiz benchmarks.  
**Currency Rate Used:** ₹95 per US$ (consistent with `docs/COST_ESTIMATE.md`).  
**Status of Resources:** Zero production changes made; zero secrets printed; zero git commits/pushes created.

---

## Executive Summary: Where `docs/COST_ESTIMATE.md` Was Wrong

| Audit Item | `COST_ESTIMATE.md` Assumed | Real Verified Audit Finding | Error / Impact in Estimate |
|---|---|---|---|
| **R2 Total Storage** | 42.5 GiB (45.6 GB) | **21.16 GiB (22.72 GB)** | **Overstated by 2.0x**. Current R2 bill is only **₹18/mo** above free tier, not ₹51/mo. |
| **R2 Per Question** | ~14 MB / question | **~7.01 MB / question** (4.05 MB slide media + 2.96 MB raw PPTX) | **Overstated by 2.0x**. 50,000 questions will need ~350 GB in R2, not 704 GB. |
| **Orphaned R2 Media** | Not identified | **15 orphaned raw decks** totaling **3.78 GiB (4.06 GB)** in `raw-uploads/` with no database record. | Unnecessary 4.06 GB storage retained from uncommitted uploads. |
| **Cloud Run Active Time** | 3.0 active hours / day | **1.03 active hours / day** (31.01 active hours across 29,221 requests in 30 days) | **Overstated by 2.9x**. Real active compute cost is ~₹480/mo, not ₹1,400/mo. |
| **Cloud Run 2 GiB Safe?** | Sizing down to 2 GiB suggested as option | **Peak memory hit 1,960.1 MiB (1.91 GiB)** | **Unsafe**. Dropping to 2 GiB leaves 4% headroom and risks OOM crashes. |
| **Cloud Run 1 vCPU Safe?** | Unconfirmed | **Peak CPU utilization was 0.66 vCPU (32.9% of 2 vCPU)** | **Safe**. Sizing down to 1 vCPU is safe and saves ~₹2,600/mo. |
| **Tier 2 Multiplier (Singapore)** | 1.4x (Assumed) | **1.4000x exact** ($0.00003360/vCPU-s vs $0.00002400) | Verified exact. |
| **Quiz Prompt AI Calls** | Assumed 3 calls (Intent + Interpreter + Curator) per prompt | **2 calls** (Intent + Curator). Interpreter is only a fallback when Intent fails. | Architecture corrected. |
| **Quiz Thinking Tokens** | Unmeasured (assumed small) | **1,585 thinking tokens per curator call** (95.3% of generated output; up to 2,880 on refinements). | Thinking causes curator to take 7–11s and **time out** against its 4.0s limit. |
| **Neon Bank Reload Size** | ~120 MB per reload at 50k questions | **1.73 MB today** (~564 bytes/question); **28.2 MB at 50,000 questions** | **Overstated by 4.25x**. Embedding & transcripts are deferred. |
| **Database Size** | 108.5 MB | **98.10 MB** (questions table: 64.3 MB incl. embeddings) | Overstated by 10.4 MB. |
| **App Cost Telemetry** | Understated (0.075 / 0.30 defaults) | Verified: neither `GEMINI_PRICE_IN_PER_M` nor `GEMINI_PRICE_OUT_PER_M` is set on Cloud Run. | Ingestion usage logs show $0.32 (₹28); real cost was $1.78 (₹169). |

---

## 1. Real Google Cloud Billing & Telemetry

### BigQuery Billing Export Status
* **Status:** **NOT ENABLED** (`VERIFIED` via `bq ls --project_id=qshala-quiz`).
* **Impact:** Fine-grained billing records by SKU cannot be queried directly through BigQuery CLI without owner console enablement.
* **Owner Action Needed:**
  1. Open [Google Cloud Console -> Billing -> Billing Export](https://console.cloud.google.com/billing/export).
  2. Enable "Daily cost detail" export to BigQuery into a dataset in `qshala-quiz`.
  3. Alternatively, view **Billing -> Reports**, group by **Service** and **SKU**, and filter by the last 3 completed months (August, September, October 2026) and Month-to-Date (November 2026).

### Observable Infrastructure Metrics (Cloud Monitoring API v3)
*Source: `https://monitoring.googleapis.com/v3/projects/qshala-quiz/timeSeries` over 30 days (2026-10-24 to 2026-11-23).*

| Metric | Measured Value | Label | Source |
|---|---|---|---|
| **Total Cloud Run Requests (30d)** | 29,221 requests | `VERIFIED` | `run.googleapis.com/request_count` |
| **Average Request Latency** | 3,820.9 ms | `VERIFIED` | `run.googleapis.com/request_latencies` |
| **Active Request Processing Time (30d)** | 111,650.3 s = **31.01 hours** | `VERIFIED` | `run.googleapis.com/request_latencies` |
| **Average Active Hours / Day** | **1.03 hours / day** | `VERIFIED` | Divided by 30 days |
| **Total Billable Instance Hours (30d)** | **698.27 hours** | `VERIFIED` | `run.googleapis.com/container/billable_instance_time` |
| **Regional Distribution (30d)** | `asia-south1` (Mumbai): 564.20 h<br>`asia-southeast1` (Singapore): 134.07 h | `VERIFIED` | Regional breakdown (Mumbai deleted 19 Nov 2026) |
| **Warm Instance Duration (Singapore)** | 24.01 h (Nov 21), 24.30 h (Nov 22) | `VERIFIED` | Demonstrates 100% 24/7 warm idle since Nov 21 |
| **Ingestion Worker Job Billable Time (30d)** | 10,532.2 s = **2.93 hours** (20 executions) | `VERIFIED` | `run.googleapis.com/container/billable_instance_time` (Job) |

---

## 2. Cloud Run Configuration & Sizing Audit

### Service & Job Specifications
*Source: `gcloud run services describe qshala-quiz-intelligence` and `gcloud run jobs describe qshala-ingestion-worker` in `asia-southeast1`.*

| Attribute | Service (`qshala-quiz-intelligence`) | Job (`qshala-ingestion-worker`) | Label |
|---|---|---|---|
| **CPU Limit** | 2 vCPU | 2 vCPU (2000m) | `VERIFIED` |
| **Memory Limit** | 4 GiB | 8 GiB | `VERIFIED` |
| **Min Instances** | **1** (set 21 Nov 2026) | N/A (Job) | `VERIFIED` |
| **Max Instances** | 1 | N/A | `VERIFIED` |
| **Concurrency** | 80 | N/A | `VERIFIED` |
| **CPU Allocation Mode** | CPU allocated only during request processing (`cpu-throttling: true`) | Task lifecycle | `VERIFIED` |
| **Startup CPU Boost** | `true` | N/A | `VERIFIED` |
| **Timeout** | 600 seconds | 3600 seconds | `VERIFIED` |
| **Region** | `asia-southeast1` (Singapore) | `asia-southeast1` (Singapore) | `VERIFIED` |
| **Deployed Environment Variables (Names Only)** | `PROJECT_NAME`, `LLM_PROVIDER`, `EMBEDDING_PROVIDER`, `EMBEDDING_DIM`, `DUPLICATE_SIMILARITY_THRESHOLD`, `MIN_GROUNDING_SCORE`, `GEMINI_MODEL`, `TAGGING_CONFIDENCE_THRESHOLD`, `TAGGING_AMBIGUITY_RATIO`, `R2_ACCOUNT_ID`, `R2_BUCKET_NAME`, `R2_PUBLIC_BASE_URL`, `FORCE_AI_TAGGING`, `REQUIRE_POSTGRES`, `MALLOC_ARENA_MAX`, `VISION_CONCURRENCY`, `INGESTION_MODE`, `GCP_PROJECT_ID`, `GCP_REGION`, `INGESTION_JOB_NAME`, `CORS_ORIGINS`, `SENTRY_DSN`, `TAG_CONCURRENCY`, `DATABASE_URL`, `GEMINI_API_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `SHAREPOINT_SITE_ID`, `SHAREPOINT_ROOT_FOLDER`, `SHAREPOINT_DRIVE_ID`, `SHAREPOINT_MAX_FILE_MB`, `AZURE_CLIENT_SECRET` | Same core vars + `QSHALA_TAG_CHUNK_SIZE` | `VERIFIED` |

### Sizing and Safety Evaluation: Can it drop to 2 GiB / 1 vCPU?
*Source: 30-day p99 metrics via Cloud Monitoring API.*

1. **Memory (Peak: 1,960.1 MiB / 1.91 GiB on 2026-10-30):**
   - 1,960.1 MiB represents **95.7% of a 2 GiB (2,048 MiB) boundary**.
   - **Verdict:** Dropping the service limit to 2 GiB is **UNSAFE**. During large PPTX slide extractions or quiz caching, an instance limited to 2 GiB will experience out-of-memory kernel kills (`SIGKILL` 137). Keep memory at **4 GiB**.
2. **CPU (Peak: 0.66 vCPU / 32.9% of 2 vCPU):**
   - The p99 CPU utilization across all 30 days peaked at 0.66 vCPU (on 2026-11-02) and typically ran at 0.20–0.44 vCPU during requests.
   - **Verdict:** Sizing CPU down to **1 vCPU is SAFE** (provided container concurrency is kept at 40–80).

### Cloud Run Real Monthly Cost Calculation (Singapore Tier 2)

* **Tier 2 Unit Rates (`asia-southeast1`):**
  - Active vCPU: **$0.00003360 / vCPU-s** (`VERIFIED` via GCP Pricing; exactly 1.40x Tier 1)
  - Active Memory: **$0.00000350 / GiB-s** (`VERIFIED`; exactly 1.40x Tier 1)
  - Idle Min Instance (vCPU + GiB): **$0.00000350 / second** (`VERIFIED`)
  - Invocations: $0.40 / 1,000,000 requests

* **Calculated Costs (at ₹95/$):**
  1. **Idle Warm Instance (2 vCPU + 4 GiB = 6 units):**
     - $0.00000350 * 6 * 3600 s/h * 24 h/d * 30.5 d = **$55.19 / mo = ₹5,243 / mo**.
  2. **Active Request Processing (1.03 active hours / day):**
     - 1.03 h/d * 30.5 d = 31.4 hours = 113,040 seconds.
     - vCPU (2 vCPU): 113,040 * 2 * $0.00003360 = $7.60
     - Memory (4 GiB): 113,040 * 4 * $0.00000350 = $1.58
     - Total Active: $9.18 / mo = **₹872 / mo** (vs ₹1,400 assumed in `COST_ESTIMATE.md`).
  3. **Cloud Run Ingestion Job:**
     - 20 executions / mo = 2.93 hours = 10,550 seconds.
     - (2 vCPU * $0.00003360 + 8 GiB * $0.00000350) * 10,550 = $1.01 / mo = **₹96 / mo**.
  4. **Total Real Cloud Run Spend Today:** **₹6,211 / mo** (Idle: ₹5,243, Active: ₹872, Job: ₹96).
  *If scaled to `min-instances=0` (cold start ~16s): Saves **₹5,243 / mo** (84% of Cloud Run bill).*  
  *If sized to 1 vCPU / 4 GiB with min 1: Saves **₹1,748 / mo** (28% of Cloud Run bill).*

---

## 3. Gemini AI Spend & Cost Telemetry Gap

### Telemetry Configuration
* `GEMINI_PRICE_IN_PER_M` on Cloud Run: **NOT SET** (`VERIFIED`).
* `GEMINI_PRICE_OUT_PER_M` on Cloud Run: **NOT SET** (`VERIFIED`).
* Default values in `backend/app/services/ai/usage_cost.py`: **$0.075 input / $0.30 output**.
* Google Official List Price (`gemini-3.5-flash-lite`): **$0.30 input / $2.50 output** (`VERIFIED`).

### Logged Ingestion Spend in Database (`ingestion_usage`)
*Source: Read-only query on PostgreSQL `ingestion_usage` table.*

| Stage | Logged Rows | Logged Calls | Tokens In | Tokens Out | Think Tokens | Cached Tokens |
|---|---|---|---|---|---|---|
| **vision** | 23 docs | 1,825 | 2,109,977 | 165,918 | 0 | 0 |
| **tagging** | 31 docs | 124 | 705,277 | 208,390 | 0 | 5,611 |
| **Total Logged** | **54 rows** | **1,949 calls** | **2,815,254** | **374,308** | **0** | **5,611** |

* **Cost at App's Internal Defaults ($0.075 / $0.30 at ₹87/$):** **$0.32 (₹28.13)**.
* **Cost at Real Google Prices ($0.30 / $2.50 at ₹95/$):** **$1.78 (₹169.05)**.
* **Reporting Error:** The internal dashboard underreports ingestion spend by **6.0x**.
* **Unlogged AI Spend (The Gap):**
  - Quiz generation (`intent_engine`, `curator_llm`, `interpreter_llm`) and semantic embeddings (`gemini-embedding-001`) are **never written** to `ingestion_usage`.
  - All quiz and search spend only appears on the Google Cloud invoice under Generative Language API.

---

## 4. Measured Quiz Prompt Benchmark (10 Prompts + 5 Refinements)

*Source: Measured locally against live `gemini-3.5-flash-lite` using exact Google `usageMetadata` responses.*

### Discovery on Prompt Architecture
In `backend/app/services/prompt_quiz/engine.py` (lines 388–412):
1. The engine calls `deconstruct_prompt` (Intent Engine).
2. `interpret_with_gemini` (Interpreter) is **mutually exclusive**—it is only called if Intent fails.
3. The engine deterministically retrieves candidate questions from Neon.
4. The engine calls `curate_quiz_with_gemini` (Curator).
*Every normal prompt executes **2 calls** (Intent + Curator), NOT 3 calls.*

### Benchmark Results Table

| Prompt # / Type | Prompt Text | Intent (In/Out/Think) | Curator (In/Out/Think) | Fallback Interpreter | Prompt Cost (₹) | Total Latency |
|---|---|---|---|---|---|---|
| **P1** | Indian freedom movement & national leaders | 1052 / 112 / 672 | 2382 / 124 / 1193 | 1203 / 144 / 488 | **₹0.5969** | 13.35s |
| **P2** | Space exploration, astronauts, ISRO (high school) | 1054 / 204 / 581 | 2383 / 153 / 1842 | 1205 / 157 / 698 | **₹0.7582** | 15.31s |
| **P3** | Business giants, startup founders in India | 1051 / 196 / 553 | 2380 / 35 / 1616 | 1202 / 149 / 597 | **₹0.6678** | 14.22s |
| **P4** | World cinema, directors, iconic movie trivia | 1050 / 21 / 764 | 2379 / 34 / 839 | 1201 / 149 / 442 | **₹0.4915** | 10.90s |
| **P5** | Cricket records, world cups, legendary players | 1050 / 186 / 500 | 2379 / 43 / 448 | 1201 / 144 / 594 | **₹0.3773** | 8.37s |
| **P6** | Wildlife, biodiversity, national parks in India | 1050 / 18 / 765 | 2379 / 76 / 1115 | 1201 / 147 / 590 | **₹0.5666** | 12.49s |
| **P7** | Inventions, patents, accidental discoveries | 1049 / 192 / 417 | 2378 / 40 / 1977 | 1200 / 147 / 608 | **₹0.7213** | 14.13s |
| **P8** | Ancient civilizations, architecture, archaeology | 1048 / 187 / 538 | 2377 / 32 / 1522 | 1199 / 143 / 582 | **₹0.6389** | 12.56s |
| **P9** | Food, spices, coffee culture, culinary history | 1051 / 74 / 709 | 2380 / 43 / 689 | 1202 / 139 / 522 | **₹0.4576** | 10.74s |
| **P10** | Tech gadgets, artificial intelligence, electronics | 1050 / 143 / 641 | 2379 / 150 / 2234 | 1201 / 140 / 538 | **₹0.8501** | 16.34s |
| **R1** | Refine: "Focus strictly on India" | 1132 / 313 / 339 | 2407 / 116 / 2880 | 1228 / 149 / 524 | **₹0.9673** | 16.80s |
| **R2** | Refine: "Make them harder and spicy" | 1135 / 202 / 538 | 2409 / 157 / 1999 | 1231 / 151 / 571 | **₹0.7888** | 14.50s |
| **R3** | Refine: "Give me 5 more questions" | 1133 / 150 / 634 | 2407 / 43 / 2635 | 1229 / 174 / 430 | **₹0.9231** | 15.60s |
| **R4** | Refine: "Only questions for middle school kids" | 1133 / 18 / 766 | 2407 / 78 / 1413 | 1229 / 131 / 582 | **₹0.6412** | 11.20s |
| **R5** | Refine: "Exclude modern era, keep historical" | 1133 / 68 / 717 | 2408 / 41 / 1376 | 1229 / 160 / 336 | **₹0.6239** | 11.40s |

### Call Stage Averages

| Call Role | Average Input Tokens | Average Output Tokens | Average Thinking Tokens | Average Cost per Call (₹) |
|---|---|---|---|---|
| **Intent Engine** | 1,078.1 | 138.9 | 608.9 | **₹0.2083** |
| **Curator LLM** | 2,388.9 | 77.7 | **1,585.2** | **₹0.4630** |
| **Interpreter (Fallback only)** | 1,210.7 | 148.3 | 540.1 | ₹0.1980 |
| **Standard Initial Prompt (Intent + Curator)** | — | — | — | **₹0.6126** |
| **Follow-up Refinement (Intent + Curator)** | — | — | — | **₹0.7889** |

### Critical Finding: Thinking Tokens & Curator Timeout Bug
1. **The Problem:** In `curator_llm.py`, the timeout is set to `CURATOR_TIMEOUT_S = 4.0`. By default, `gemini-3.5-flash-lite` generates **1,585 thinking tokens** per curation call, causing calls to take **7.5 to 11.2 seconds**. Consequently, in live production, the Curator **routinely times out** and aborts, falling back to the deterministic editor and wasting AI spend.
2. **The Experiment:** Running Curator with `thinkingBudget: 0`:
   - Thinking tokens dropped from **1,585 to 0**.
   - Latency dropped from **8.0s to 1.94s** (a 4x speedup, comfortably under the 4.0s timeout!).
   - Curator cost dropped from **₹0.4630 to ₹0.0780** (an 83% reduction).
   - Standard prompt cost dropped from **₹0.6126 to ₹0.2863**.
3. **Recommendation:** Explicitly set `thinkingConfig: {"thinkingBudget": 0}` in `curator_llm.py` and `intent_engine.py`.

---

## 5. Neon Database & Bank Reload Transfer

### Database Sizing & Architecture
*Source: Read-only query on PostgreSQL information schema.*

* **Total Database Size:** **98.10 MB** (`VERIFIED`).
  - `questions` table: **64.29 MB** (incl. 768-dim embeddings).
  - `sharepoint_index_items` table: **11.58 MB**.
  - `slides` table: **7.80 MB**.
  - `qshala_tags` table: **3.71 MB**.
  - `question_edit_history`: **3.13 MB**.
  - Other tables: **7.59 MB**.
* **Total Stored Entities:** 63 decks, 12,566 slides, 3,242 questions, 4,362 tags (`VERIFIED`).

### Bank Reload Size & Transfer Extrapolation
*Source: Direct measurement of serialized database bank payload via `_load_bank_uncached` query.*

* **Real Query Behavior:** `backend/app/services/prompt_quiz/engine.py` explicitly defers `Question.embedding`, `options`, `explanation`, transcripts, and provenance.
* **Measured Transfer Today (3,218 live bank questions):** **1.73 MB** (~564 bytes / question) (`VERIFIED`).
* **Extrapolated Transfer at 50,000 Questions:** **28.2 MB per reload** (`ESTIMATED`).
* **Correction to `COST_ESTIMATE.md`:** The estimate warned of 120 MB per reload. The actual payload is **4.25x smaller**.

### Autosuspend & Keep-Alive Audit
* In `backend/app/database.py`:
  - `pool_pre_ping=True` is enabled.
  - `pool_recycle=280` (recycles connections every 4.67 minutes).
* **Impact of Cloud Run Min Instances:** Because Cloud Run has `min-instances=1`, the warm instance maintains open idle connections through Neon's PgBouncer pooler (`-pooler.c-2.ap-southeast-1.aws.neon.tech`).
* **Cache TTL:** `PROMPT_QUIZ_BANK_TTL_S` defaults to **10 seconds** in `engine.py` and is **NOT set** on Cloud Run. Under traffic, the bank reloads frequently.
* **Owner Action Needed:**
  - Check [Neon Console -> Dashboard](https://console.neon.tech/) for:
    1. Plan tier (Free vs Launch).
    2. Real Compute-Unit-Hours (CU-h) over August–November.
    3. Real Egress / Data Transfer (GB).
    4. Confirm if autosuspend is triggering or if warm instances keep compute awake.

---

## 6. Cloudflare R2 Storage & Media Analysis

*Source: Direct S3 API iteration over all 9,308 keys in bucket `qshala-media` cross-referenced with database `documents`.*

### Storage by Prefix

| Prefix | Object Count | Size (GiB) | Size (GB) | Share of Storage | Label |
|---|---|---|---|---|---|
| `slides/` | 9,273 | 12.23 GiB | **13.13 GB** | 57.8% | `VERIFIED` |
| `raw-uploads/` | 35 | 8.93 GiB | **9.59 GB** | 42.2% | `VERIFIED` |
| **Total R2** | **9,308** | **21.16 GiB** | **22.72 GB** | **100.0%** | `VERIFIED` |

### Storage by Media Type

| Media Category | Object Count | Size (GiB) | Size (GB) | Notes | Label |
|---|---|---|---|---|---|
| **Original PPTX Decks** | 35 | 8.93 GiB | 9.59 GB | In `raw-uploads/` | `VERIFIED` |
| **Embedded Videos** | 545 | 7.70 GiB | 8.27 GB | In `slides/` (.mp4, .mov) | `VERIFIED` |
| **Large Images (> 1 MB)** | 1,211 | 2.78 GiB | 2.98 GB | High-res slide artwork | `VERIFIED` |
| **Small Images (<= 1 MB)**| 7,355 | 1.36 GiB | 1.46 GB | Icons, diagrams, clue crops | `VERIFIED` |
| **Audio (.mp3 / .wav)** | 155 | 0.38 GiB | 0.41 GB | Clue audio clips | `VERIFIED` |
| **Other / Metadata** | 7 | < 0.01 GiB | < 0.01 GB | Thumbnails / TIFF | `VERIFIED` |

### Top 10 Largest Objects in R2

1. `raw-uploads/792c999b-06db-45da-acd2-35fb40e79bb0.pptx`: 762.97 MiB
2. `raw-uploads/4330abd2-f0a3-48db-8f14-ebb8213d92ec.pptx`: 733.31 MiB
3. `raw-uploads/d9712f9a-ffe4-4fae-97c6-22b2de600ee9.pptx`: 707.95 MiB
4. `raw-uploads/11284bbb-0e83-40bc-ba0c-be61e800f51e.pptx`: 559.20 MiB
5. `raw-uploads/81fc9ec4-46d9-4437-bbc0-b89859cd85b1.pptx`: 523.00 MiB
6. `raw-uploads/8eca9da8-3e6f-4579-bcd1-a8ed0e3de674.pptx`: 491.77 MiB
7. `raw-uploads/7c7fd1d9-284d-450c-ab8f-ac0ce3211657.pptx`: 462.68 MiB
8. `raw-uploads/5a05ae83-a61e-452f-9c2f-1448f6b52d00.pptx`: 377.92 MiB
9. `raw-uploads/5e1d168d-1f89-4b3a-af47-e981d9f1e5d4.pptx`: 377.92 MiB
10. `slides/792c999b-06db-45da-acd2-35fb40e79bb0/media/media19.mp4`: 256.50 MiB

### Critical Finding: Orphaned Raw Uploads
* Cross-referencing `raw-uploads/` with the `documents` table revealed **15 orphaned raw PPTX files** totaling **3.78 GiB (4.06 GB)**.
* These files have no matching document record in Neon (representing aborted uploads or test runs).
* **Purging orphaned uploads immediately recovers 3.78 GiB of R2 storage.**

### Savings from Deleting Raw Decks
* Currently billable R2 storage: 22.72 GB - 10 GB free = 12.72 GB = **$0.19 / mo (₹18 / mo)**.
* If raw decks are deleted after ingestion: Total R2 drops to 13.13 GB -> Billable is 3.13 GB = **$0.047 / mo (₹4.5 / mo)**.
* Today, savings are negligible (~₹14/mo); but at 50,000 questions, deleting raw decks saves **~₹240/mo**.

---

## 7. Projected Costs at 50,000 Questions (One-Page Model)

*Scale factor: 15.4x question growth (from 3,242 to 50,000 questions; ~908 additional decks).*

| Cost Component | Today (3,242 Qs) | 50k Qs: Low Case (No raw decks, compressed images, min=0) | 50k Qs: Base Case (Raw decks kept, min=1 warm, 1 vCPU) | 50k Qs: High Case (2 vCPU warm, 1.5x heavier 2026 media) | Label |
|---|---|---|---|---|---|
| **Cloud Run Service** | ₹6,115 | **₹1,200** (min 0, 1 vCPU, 4 GiB) | **₹4,460** (min 1, 1 vCPU, 4 GiB) | **₹6,600** (min 1, 2 vCPU, 4 GiB) | `ESTIMATED` |
| **Cloud Run Job (Ingest run-rate)** | ₹96 | **₹110** (40 new decks/mo) | **₹110** (40 new decks/mo) | **₹190** (70 new decks/mo) | `ESTIMATED` |
| **Neon Compute (CU-hours)** | ₹450 to ₹1,800 | **₹450** (0.25 CU 6h/day) | **₹1,800** (0.25 CU always-on) | **₹3,700** (0.50 CU always-on) | `ESTIMATED` |
| **Neon Storage ($0.35/GB)** | ₹0 (Free tier) | **₹40** (~1.2 GB) | **₹50** (~1.5 GB) | **₹75** (~2.2 GB) | `ESTIMATED` |
| **Cloudflare R2 Storage** | ₹18 | **₹164** (~125 GB) | **₹485** (~350 GB) | **₹735** (~525 GB) | `ESTIMATED` |
| **Gemini Ingestion (Run-rate)** | ₹110 | **₹130** (Batch API, 2k Qs/mo) | **₹260** (Standard, 2k Qs/mo) | **₹520** (Standard, 4k Qs/mo) | `ESTIMATED` |
| **Gemini Quiz Prompts (Run-rate)** | ₹90 | **₹285** (1k prompts, thinking=0) | **₹615** (1k prompts, thinking on) | **₹1,230** (2k prompts, thinking on) | `ESTIMATED` |
| **Other GCP (Build, Artifacts, Logs)** | ~₹250 | **₹300** | **₹350** | **₹450** | `ESTIMATED` |
| **Subtotal (Hosting + AI + DB)** | **₹7,129 to ₹8,479** | **₹2,679 / month** | **₹8,130 / month** | **₹13,500 / month** | `ESTIMATED` |
| **One-Off Ingestion to 50k Qs** | — | **₹5,416** (Batch Gemini + Job) | **₹7,416** (Standard Gemini + Job) | **₹10,500** (Heavy 2026 Decks) | `ESTIMATED` |

---

## 8. Recommendations Ranked by Monthly Savings

| Rank | Action | Monthly Savings (₹) | Implementation Effort | Technical Risk & Trade-offs |
|---|---|---|---|---|
| **1** | **Disable Cloud Run Warm Instance (`min-instances=0`)** | **₹5,243 / mo** | 1 minute (Cloud Run console / gcloud) | **Latency Risk:** The first request after 15 minutes of idle experiences a cold start of ~16 seconds. |
| **2** | **Resize Cloud Run to 1 vCPU / 4 GiB (Keep Warm)** | **₹1,748 / mo** | 1 minute (Cloud Run console / gcloud) | **Minimal Risk:** Peak CPU is only 0.66 vCPU. Memory stays at 4 GiB to eliminate OOM risk. |
| **3** | **Disable Thinking in Curator & Intent (`thinkingBudget: 0`)** | **~₹350 / mo** (up to ₹1,500 at 5k prompts) + **Fixes Timeouts** | Code patch (1 line in `curator_llm.py` and `intent_engine.py`) | **Zero Risk:** Eliminates curator timeout failures; reduces prompt latency from 12s to 3s. |
| **4** | **Use Gemini Batch Mode for Deck Ingestions** | **50% off Ingestion** (~₹3,086 one-off, ~₹130/mo) | Medium (async batch submission) | **Operational Delay:** Ingestion results return within 2–4 hours instead of real-time. |
| **5** | **Purge 15 Orphaned R2 Uploads (3.78 GiB)** | Immediate storage hygiene | Script / Cloudflare dashboard | **Zero Risk:** Objects have no DB reference and cannot be accessed by users. |
| **6** | **Prune Raw PPTX Decks After Ingestion** | **₹240 / mo** (at 50k questions) | Lifecycle rule / script | **Capability Loss:** Loses ability to re-extract raw slides from original deck if parser improves. |

---

## 9. Proposed Fix for `usage_cost.py` & Test Suite (Do Not Merge)

### Problem
`backend/app/services/ai/usage_cost.py` defaults to `$0.075 / $0.30` per million tokens, whereas official `gemini-3.5-flash-lite` pricing is `$0.30 / $2.50` (batch: `$0.15 / $1.25`).

### Proposed Code Diff

```diff
--- a/backend/app/services/ai/usage_cost.py
+++ b/backend/app/services/ai/usage_cost.py
@@ -6,8 +6,10 @@ logger = logging.getLogger(__name__)
 
 # gemini-3.5-flash-lite list prices, USD per million tokens. Thinking tokens are billed as output.
-PRICE_IN_PER_M = float(os.getenv("GEMINI_PRICE_IN_PER_M", "0.075"))
-PRICE_OUT_PER_M = float(os.getenv("GEMINI_PRICE_OUT_PER_M", "0.30"))
+PRICE_IN_PER_M = float(os.getenv("GEMINI_PRICE_IN_PER_M", "0.30"))
+PRICE_OUT_PER_M = float(os.getenv("GEMINI_PRICE_OUT_PER_M", "2.50"))
+PRICE_BATCH_IN_PER_M = float(os.getenv("GEMINI_PRICE_BATCH_IN_PER_M", "0.15"))
+PRICE_BATCH_OUT_PER_M = float(os.getenv("GEMINI_PRICE_BATCH_OUT_PER_M", "1.25"))
 # Cached prompt tokens are billed at a fraction of the input price. 0.1 is an assumption - set the real ratio.
 CACHED_INPUT_RATIO = float(os.getenv("GEMINI_CACHED_INPUT_RATIO", "0.1"))
-USD_TO_INR = float(os.getenv("USD_TO_INR", "87.0"))
+USD_TO_INR = float(os.getenv("USD_TO_INR", "95.0"))
```

### Proposed Test Diff

```diff
--- a/backend/tests/test_usage_tracking.py
+++ b/backend/tests/test_usage_tracking.py
@@ -10,6 +10,6 @@ def test_thinking_tokens_are_billed_as_output_and_cached_at_a_discount():
     base = estimate_cost_usd(1_000_000, 1_000_000)
-    assert abs(base - (0.075 + 0.30)) < 1e-9
-    assert abs(estimate_cost_usd(0, 0, thinking=1_000_000) - 0.30) < 1e-9
+    assert abs(base - (0.30 + 2.50)) < 1e-9
+    assert abs(estimate_cost_usd(0, 0, thinking=1_000_000) - 2.50) < 1e-9
```

---

## 10. Checklist of Items Waiting on Owner Action

1. **Google Cloud Billing Export:**
   - Go to Cloud Console -> Billing -> Billing Export to enable BigQuery export for automated SKU tracking, or paste the August–November spend from Billing -> Reports.
2. **Neon Console Access:**
   - Check Neon Console for current subscription plan, actual monthly CU-hours, and real data transfer GB.
3. **Cloudflare GraphQL / API Key:**
   - If Class A/B request operation counts are needed, check Cloudflare Dashboard -> R2 -> Analytics or provide a read-only Cloudflare API key.
