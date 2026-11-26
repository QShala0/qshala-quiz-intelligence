# Question Bank Anomaly Audit & Fix Plan

## Goal

The production question bank (3,551 questions in Neon PostgreSQL) has accumulated several classes of formatting, storage, and validation anomalies from ingestion. This plan describes what each anomaly is, its root cause, what will be fixed (backfill migration), and what guard-rails will prevent it from re-occurring in the future.

---

## What Was Found (Production Audit)

| Anomaly | Count | Root Cause |
|---|---|---|
| Non-canonical `question_format` values (`VISUAL_CLUE`, `BUZZER`, `VIDEO_CLUE`, `AUDIO`, `POTPOURRI`, `QA`) | **557** | Old ingestion code set these labels; they are now mapped but orphaned |
| Long underscore blanks in question text (`____`) | **307** | PPT "fill in the blank" slides OCR'd verbatim — not stripped |
| Format header leaked into `question_text` (`[Portfolio Builder · Stepped Clues]`, `[Connect · Common Link]`) | **129** | `heal_specialized.py` writes these headers into `question_text` for display — harmless but redundant since `format_payload` carries the structured data |
| `leading_Ans_prefix` in `answer` (`A. Net Asset Value (NAV)`, `A. Joint Accounts`) | **14** | MCQ slide OCR kept the MCQ letter prefix on the correct answer |
| `points_or_round_in_question_text` artifacts (`Points: 10`, `Round 2:`) | **33** | Slide scoring metadata leaked into the question body during extraction |
| `empty_options_list_on_non_mcq` | **25** | Leftover `options: []` from old extractor path that set options on every question |
| `empty_answer` | **4** | Extractor found no answer text; inserted blank string instead of null |
| `empty_question_text` | **2** | Cover/junk slides incorrectly classified as QA |
| `very_short_question_text` (<15 chars: `?`, `WHO`, `8.`, `Which movie?`) | **22** | Junk/transitional slides not filtered |
| `QA` format (9) | **9** | Alias for `STANDARD_QA` — different string but same meaning |

---

## Open Questions

> [!IMPORTANT]
> **Non-canonical formats (`VISUAL_CLUE`, `BUZZER`, `VIDEO_CLUE`, `AUDIO`, `POTPOURRI`)** — these are **not** errors in the data; they represent real question types that the new extractor now calls `STANDARD_QA` with media refs. The 557 rows using these values work fine in the UI because the renderer falls back gracefully. Two options:
>
> **Option A**: Remap them all to `STANDARD_QA` (clean but lossy — the `VISUAL_CLUE` semantics are gone).
> **Option B**: Make them **canonical aliases** — add them to the allowed-format list in the app, validator, and UI so they render perfectly without any data change.
>
> **Recommended: Option B for display formats (`VISUAL_CLUE`, `VIDEO_CLUE`, `AUDIO`, `POTPOURRI`, `BUZZER`), and remap `QA → STANDARD_QA`.**

> [!NOTE]
> The **`[Portfolio Builder · Stepped Clues]` header prefix** in `question_text` is intentionally written there by `heal_specialized.py` for legibility. It's not an anomaly — it's how stepped-clue questions are displayed in the question bank. We will leave these unchanged.

---

## Proposed Changes

### 1. New: `backend/app/services/ingestion/question_normalizer.py` [NEW]

A single, idempotent normalizer called at ingest time and as a backfill. Fixes all text-level issues.

```python
def normalize_answer(answer: str, question_format: str) -> str:
    """Strip MCQ letter prefix from answer (A. → '') for MCQ and STANDARD_QA."""
    if not answer:
        return answer
    # Strip leading "A. " / "A) " style MCQ prefix
    cleaned = re.sub(r'^[A-D][.)]\s+', '', answer.strip())
    return cleaned

def normalize_question_text(text: str) -> str:
    """Remove scoring/round artifacts and normalize whitespace."""
    if not text:
        return text
    # Remove standalone "Points: N" / "Bonus: N" lines
    text = re.sub(r'(?m)^(Points?|Bonus)\s*:\s*\d+\s*$', '', text)
    # Remove standalone "Round N:" lines
    text = re.sub(r'(?m)^Round\s+\d+\s*[:\-]?\s*$', '', text)
    # Collapse 3+ blank lines to 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Collapse excessive spaces (not newlines)
    text = re.sub(r'[ \t]{3,}', ' ', text)
    return text.strip()

def normalize_options(options, question_format: str):
    """Return None (not []) for non-MCQ questions that have empty options."""
    if question_format != 'MCQ' and isinstance(options, list) and len(options) == 0:
        return None
    return options
```

---

### 2. Modify: `backend/app/services/ingestion/question_extractor.py` [MODIFY]

#### Apply normalizer at save time

Add a call to `normalize_question_text`, `normalize_answer`, and `normalize_options` before every question is committed to the DB (in `_save_question` or the equivalent write path).

```python
# Before saving:
from backend.app.services.ingestion.question_normalizer import normalize_question_text, normalize_answer, normalize_options
q_dict["question_text"] = normalize_question_text(q_dict.get("question_text", ""))
q_dict["answer"] = normalize_answer(q_dict.get("answer", ""), q_dict.get("question_format", ""))
q_dict["options"] = normalize_options(q_dict.get("options"), q_dict.get("question_format", ""))
```

---

### 3. Modify: `backend/app/services/ingestion/heal_specialized.py` [MODIFY]

Extend the existing `heal_specialized_questions()` to also run the new normalizer across all rows — called at app startup (already happens) and via the new migration endpoint.

---

### 4. New: `backend/app/routers/admin.py` — `/admin/backfill-normalize` endpoint [MODIFY or NEW]

A one-shot admin endpoint (protected by admin auth or a secret header) that:
1. Runs `normalize_question_text` + `normalize_answer` + `normalize_options` across all rows.
2. Remaps `question_format = 'QA'` → `'STANDARD_QA'`.
3. Flags the 4 empty-answer questions and 2 empty-question-text rows with `format_needs_review = True`.
4. Flags the 22 very-short (<15 char) question-text rows with `format_needs_review = True`.
5. Returns a JSON summary of all changes.

---

### 5. Modify: `backend/app/models/question.py` [MODIFY]

Add the display-only formats to the canonical allowed list (comment update only — no DB migration needed):

```python
question_format = Column(
    String(20),
    ...
    comment="STANDARD_QA | CONNECT | LIST_IT | IMAGE_REVEAL | STEPPED_CLUE | ARRANGE_ORDER | MCQ "
            "| VISUAL_CLUE | VIDEO_CLUE | AUDIO | BUZZER | POTPOURRI"  # display aliases
)
```

Also add a DB-level `CHECK` constraint in an Alembic migration:

```sql
ALTER TABLE questions
  ADD CONSTRAINT ck_question_format CHECK (
    question_format IN (
      'STANDARD_QA','CONNECT','CONNECT_4','LIST_IT','IMAGE_REVEAL',
      'STEPPED_CLUE','ARRANGE_ORDER','MCQ',
      'VISUAL_CLUE','VIDEO_CLUE','AUDIO','BUZZER','POTPOURRI'
    )
  );
```

---

### 6. New: `backend/alembic/versions/xxxx_normalize_question_formats.py` [NEW]

Alembic migration that:
1. Runs `UPDATE questions SET question_format = 'STANDARD_QA' WHERE question_format = 'QA'` (9 rows).
2. Sets `format_needs_review = TRUE` where `answer = '' OR answer IS NULL` (4 rows).
3. Sets `format_needs_review = TRUE` where `length(question_text) < 15` (22 rows).
4. Runs `normalize_answer` SQL for MCQ-style `A. ` prefixes: `UPDATE questions SET answer = regexp_replace(answer, '^[A-D][.)]\s+', '') WHERE answer ~ '^[A-D][.)]\s'` (14 rows).
5. Removes `options = '[]'` for non-MCQ rows: `UPDATE questions SET options = NULL WHERE question_format != 'MCQ' AND options::text = '[]'` (25 rows).
6. Strips `Points:` / `Bonus:` / `Round N:` artifacts from `question_text` via a PL/pgSQL function (33 rows).
7. Adds the CHECK constraint on `question_format`.

---

### 7. Modify: `backend/app/services/validation/validator.py` [MODIFY]

Add pre-save validation guards:

```python
# In validate_question() or a new validate_before_save():
ALLOWED_FORMATS = {
    'STANDARD_QA','CONNECT','CONNECT_4','LIST_IT','IMAGE_REVEAL',
    'STEPPED_CLUE','ARRANGE_ORDER','MCQ',
    'VISUAL_CLUE','VIDEO_CLUE','AUDIO','BUZZER','POTPOURRI'
}

def validate_before_save(q_dict: dict) -> List[str]:
    errors = []
    qt = (q_dict.get("question_text") or "").strip()
    ans = (q_dict.get("answer") or "").strip()
    fmt = q_dict.get("question_format", "STANDARD_QA")

    if not qt:
        errors.append("question_text is empty")
    elif len(qt) < 10:
        errors.append(f"question_text too short ({len(qt)} chars)")
    if not ans:
        errors.append("answer is empty")
    if fmt not in ALLOWED_FORMATS:
        errors.append(f"unknown question_format: {fmt!r}")
    if fmt != 'MCQ' and isinstance(q_dict.get("options"), list) and len(q_dict.get("options", [])) == 0:
        errors.append("empty options list on non-MCQ question")
    return errors
```

---

## Verification Plan

### Automated Tests

```bash
# After migration:
pytest backend/tests/test_question_normalizer.py -v

# Full suite:
pytest backend/tests/ -v --timeout=60
```

A new `test_question_normalizer.py` will cover:
- `normalize_answer('A. Net Asset Value (NAV)', 'MCQ')` → `'Net Asset Value (NAV)'`
- `normalize_question_text` strips `Points: 10` / `Round 2:` lines
- `normalize_options([], 'STANDARD_QA')` → `None`
- `validate_before_save` catches empty answers, short questions, bad formats

### Manual Verification

After the migration runs on production:
1. Open the question bank — confirm **no "A. Net Asset Value (NAV)"** style answers visible
2. Search for any question with `format_needs_review = True` — confirm they are flagged in the UI for human review
3. Confirm `question_format = 'QA'` returns 0 rows: `SELECT COUNT(*) FROM questions WHERE question_format = 'QA'`
4. Confirm 0 `options = '[]'` on non-MCQ rows

### Deployment

1. Run Alembic migration on production Neon DB
2. Trigger `/admin/backfill-normalize` endpoint once
3. Verify counts in the response match expectations
