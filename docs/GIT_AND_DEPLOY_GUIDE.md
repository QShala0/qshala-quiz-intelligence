# QShala: Git, GitHub and Deploy Guide (for beginners)

Read this once end to end, then keep it open whenever you push or ask an AI to commit.
Every step explains **what** happens and **why**.

---

## 1. The big picture (3 places your code lives)

Think of writing an essay with a friend:

| Place | College analogy | What it is |
|---|---|---|
| **Your laptop folder** (`Downloads\qshala`) | Your private notebook | Where you and the AI edit files. Nobody else sees it. |
| **GitHub** (`QShala0/qshala-quiz-intelligence`) | The shared Google Drive | The official copy. The team and the deploy robots read from here. |
| **Live website** (Vercel + Google Cloud Run) | The printed, published essay | What real users see. Updated *automatically* from GitHub. |

**Key idea:** You never upload to the website by hand. You send code to GitHub, and robots publish it.
So "the site is old" always means: *the robots did not publish*, or *the code never reached GitHub*.

### Who publishes what

| Part | Robot | Where it runs | Triggered when |
|---|---|---|---|
| Frontend (the pages you click) | **Vercel** | vercel.com | Code lands on `main` |
| Backend (API, ingestion, export) | **Google Cloud Build -> Cloud Run** | GCP project `qshala-quiz`, Singapore | Code lands on `main` **and all tests pass** |

> The second row is the trap that bit you. If even one test fails, the backend is **not** published,
> and nothing shouts about it. The frontend still updates, so the site looks half-new, half-old.

---

## 2. Words you must know

- **Repository (repo):** the project folder plus its full history.
- **Commit:** a saved snapshot with a message. Like "Save As" with a note, but you can go back to any one.
- **Branch:** a separate copy of the timeline to experiment on safely. Like a draft of your essay.
- **`main`:** the official branch. Whatever is on `main` goes live. Treat it as sacred.
- **Push:** send your commits from the laptop to GitHub.
- **Pull / fetch:** bring GitHub's newer commits down to your laptop.
- **Pull Request (PR):** "please review my draft and merge it into `main`". Tests run automatically on it.
- **Merge:** accept the draft into `main`. **This is the moment it goes live.**
- **CI:** the test robot on GitHub. Green tick = tests passed. Red cross = something broke.
- **Untracked file:** a file on your laptop that Git has never been told about. It is **not** in any commit.

---

## 3. The golden rules

1. **Never push straight to `main`.** Always: branch -> push branch -> PR -> green checks -> merge.
2. **Green tick before merge.** Red = do not merge. Ask the AI to fix it first.
3. **Check `git status` before every commit.** Any file marked `??` (untracked) that your code uses must be added.
   *This exact mistake broke your deploy:* `pipeline.py` imported `question_normalizer.py`, but that file was
   never committed. It worked on your laptop and crashed everywhere else.
4. **After merging, confirm it actually deployed** (section 6). Merged is not the same as live.
5. **One topic per branch/PR.** Small PRs are easy to review and easy to undo.
6. **Never commit secrets:** `.env`, passwords, API keys. (`.env` is already ignored; keep it that way.)
7. **Do not commit junk:** the loose `.pptx` test files, `.log` files, `OneDrive_*.zip` in the project root. Keep them out of the repo.

---

## 4. The normal workflow, step by step

Replace `my-change` with a short name, like `fix/export-notes`.

### Step 0: Start from the latest official code
```
git checkout main
git pull
```
*Why:* you must start from what is on GitHub, or your branch will clash with other people's work.

### Step 1: Make a branch
```
git checkout -b fix/my-change
```
*Why:* your experiments cannot hurt `main` or the live site.

### Step 2: Make changes (you or the AI edit files)
Just work. Nothing is saved to Git yet.

### Step 3: Look at what changed
```
git status
git diff
```
- `git status` lists changed files (`M`), new untracked files (`??`) and deleted files.
- Ask yourself: **is every file I need listed? Is there anything that should NOT go in (secrets, test pptx files)?**

### Step 4: Run the tests (see section 5)

### Step 5: Stage and commit
```
git add path/to/file1.py path/to/file2.py
git commit -m "fix(export): short clear message"
```
- `git add` = "put these files in the box". **Name files explicitly**, avoid `git add .` (it grabs junk).
- `git commit` = "seal the box with a label". The message should say *what and why* in one line.

### Step 6: Push the branch (not main)
```
git push -u origin fix/my-change
```
*Why:* this uploads your branch to GitHub. The live site is **not** touched yet.

### Step 7: Open a Pull Request
```
gh pr create --base main --title "..." --body "..."
```
(or click the green button GitHub shows you). The test robot starts automatically.

### Step 8: Wait for green
On the PR page you will see checks: **frontend**, **backend**, and a Vercel preview.
- All green: continue.
- Backend red: click "Details", read the failed test name, give it to the AI to fix, commit, push again. The PR updates itself.

### Step 9: Merge
Click **Merge pull request** on GitHub. **Now it goes live.**

### Step 10: Confirm it deployed (section 6)

### Step 11: Clean up
```
git checkout main
git pull
git branch -d fix/my-change
```

---

## 5. Running tests on your laptop first

Backend (only the files relevant to your change; the full suite is slow):
```
python -m pytest backend/tests/test_something.py -q
```
Frontend:
```
cd frontend
npx tsc --noEmit
npx eslint <changed files>
```
Notes:
- A few tests fail **only on your laptop** because of your local `.env` (for example
  `test_qa_tournament_overhaul`). GitHub's result is the real judge.
- Passing locally does **not** guarantee passing on GitHub (missing committed file, different OS). That is why the PR step exists.

---

## 6. "Is my change actually live?" checklist

Do this after every merge that touches backend code.

**1. What is on GitHub main?**
```
git fetch
git log origin/main --oneline -3
```
Note the first 7-character code (the "SHA"), e.g. `1696af0`.

**2. Did the tests pass on main?**
```
gh run list --branch main --limit 3
```
Look for `CI ... success`. `failure` = backend will not deploy.

**3. Did Google Cloud build it?**
```
gcloud builds list --project qshala-quiz --limit 3
```
Find your SHA. Status must be `SUCCESS`. `FAILURE` = the website's backend is still the old version.

**4. Is the new backend running?**
```
gcloud run services describe qshala-quiz-intelligence --project qshala-quiz --region asia-southeast1 --format="value(status.latestReadyRevisionName)"
```
The revision name should have a new number after your merge. Also open `<your site>/health` and check it responds.

**5. Frontend:** Vercel dashboard -> project -> Deployments. The newest **Production** row should show your SHA.

### Quick diagnosis table

| Symptom | Likely cause | Fix |
|---|---|---|
| New buttons visible but old behaviour (exports, ingestion) | Backend build failed on tests | Check step 2 and 3, fix the failing test |
| Nothing changed anywhere | Change never merged to `main` (still on a branch/PR) | Merge the PR |
| Works on my laptop, fails on GitHub | A file was never committed (`??` in `git status`) | `git add` it |
| Vercel Preview shows it, main site does not | You are looking at a preview URL, or not merged | Use the production domain; merge |
| Build `SUCCESS` but site old | Browser cache | Hard refresh (Ctrl+Shift+R) |

---

## 7. Mistakes to avoid (and how to undo safely)

| Mistake | Safe response |
|---|---|
| Committed on `main` by accident (not pushed) | Ask the AI to move the commit to a branch. Do **not** push. |
| Pushed a bad commit to a branch | Just push a fix; branches are disposable. |
| Merged something broken into `main` | Use **Revert** on the PR page on GitHub (creates an "undo" PR). Do not rewrite history. |
| Committed a secret | Tell the team immediately: the key must be **rotated** (changed). Deleting the file is not enough. |
| `git push --force` | **Never**, especially on shared branches. It can erase other people's work. |
| `git reset --hard`, `git clean -fd` | These delete work permanently. Do not run them unless you understand exactly what they remove. |

---

## 8. Working with AI assistants (Claude Code and others)

The AI is fast, so *you* must be the safety check. Tell it these rules at the start of any session, or point it at this file:

> "Follow docs/GIT_AND_DEPLOY_GUIDE.md. Work on a branch, never push to main, never push without me saying 'push it',
> show me `git status` and the file list before committing, and tell me what each command does."

**Before you let the AI commit**, ask it:
1. "Show me `git status` and `git diff --stat`." (What files? Anything surprising?)
2. "Are there untracked files my code depends on?"
3. "Which tests did you run, and did they pass?"
4. "What branch are we on?" (Must not be `main`.)

**Before you say "push it"**, confirm:
- It is a branch, not `main`.
- Tests were run.
- You understand what the PR contains.

**After a merge**, ask it: "Run the section 6 checklist and tell me if the backend deployed."

Red flags from an AI: it wants `--force`, `--no-verify`, `git add .` on a messy folder, deploying with
`gcloud builds submit` from your laptop (skips tests and ships untracked files), or editing `main` directly.

---

## 9. Housekeeping habits

- **Weekly:** `git branch` and delete branches that are merged. `git branch --merged main` lists them.
- **Keep the project folder clean:** move the loose `.pptx`, `.zip`, `.log` files out of the repo folder. Only code and docs belong.
- **One clone only:** the nested `qshala-quiz-intelligence/` folder inside the project is a second copy. Two copies cause "which one is current?" confusion.
- **Branch protection (do once on GitHub):** Settings -> Branches -> add rule for `main` -> require a PR and require the `backend` and `frontend` checks to pass. Then GitHub itself stops you from merging red code.
- **Vercel check (do once):** Project -> Settings -> Git: production branch must be `main`, repo `QShala0/qshala-quiz-intelligence`.
- **Never create anything in `asia-south1` (Mumbai).** Everything lives in `asia-southeast1` (Singapore).

---

## 10. One-page cheat sheet

```
git checkout main && git pull          # start fresh
git checkout -b fix/my-change          # new branch
# ...edit...
git status                             # what changed? any ?? files?
python -m pytest backend/tests/<file> -q
git add <specific files>
git commit -m "fix(area): what and why"
git push -u origin fix/my-change       # push BRANCH
gh pr create --base main               # open PR, wait for green
# merge on GitHub  -> goes live
gh run list --branch main --limit 3    # tests ok?
gcloud builds list --project qshala-quiz --limit 3   # deployed?
```

If unsure at any step: **stop and ask "what does this command do and can it be undone?"** before pressing Enter.
