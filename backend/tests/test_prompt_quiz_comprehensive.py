import pytest
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.database import SessionLocal
from backend.app.services.prompt_quiz.engine import interpret, build_draft, swap_one
from backend.app.services.prompt_quiz.retrieval import parse_prompt

client = TestClient(app)


# ----------------------------------------------------------------------
# 1. PARSER & INTERPRETER TESTS (Including ambiguous, word numbers, typos)
# ----------------------------------------------------------------------

def test_interpret_quantity_numbers():
    assert interpret("5 questions on history").count == 5
    assert interpret("1 question on history").count == 1
    assert interpret("50 questions on history").count == 50
    assert interpret("100 questions on history").count == 50  # clamped to max 50


def test_interpret_word_numbers():
    """Ambiguous: users type 'five questions' or 'twenty questions'."""
    assert interpret("five questions on cricket").count == 5
    assert interpret("ten questions on movies").count == 10
    assert interpret("fifteen questions on geography").count == 15
    assert interpret("twenty questions on banking").count == 20
    assert interpret("three tough questions").count == 3


def test_interpret_school_audience():
    """Ambiguous: 'school', 'kids', 'children', 'family' should trigger school_safe filter."""
    for p in ["5 questions for school", "quiz for kids", "family trivia night", "children quiz"]:
        b = interpret(p)
        assert b.filters.get("school_safe") is True, f"Failed for prompt: {p}"


def test_interpret_difficulty_bias():
    assert interpret("tough finals quiz on banking").hard_bias == "hard"
    assert interpret("difficult questions on science").hard_bias == "hard"
    assert interpret("easy warm-up questions").hard_bias == "easy"
    assert interpret("gentle questions on food").hard_bias == "easy"
    assert interpret("balanced questions on history").hard_bias == "medium"


def test_interpret_media_constraints():
    """Ambiguous: variants of no image."""
    for p in ["5 questions without images", "text only quiz", "no image questions", "non-image trivia"]:
        b = interpret(p)
        assert b.filters.get("exclude_image") is True, f"Failed for prompt: {p}"


def test_interpret_geography_constraints():
    b_in = interpret("10 questions on India")
    assert b_in.filters.get("india_international") == "IN"

    b_intl = interpret("10 international questions")
    assert b_intl.filters.get("india_international") == "INTL"


# ----------------------------------------------------------------------
# 2. RETRIEVAL & BANK TESTS (Broad, zero-keyword, and domain queries)
# ----------------------------------------------------------------------

def test_broad_and_zero_keyword_prompts():
    """CRITICAL: User asks for a general quiz without specific entity keywords.
    The engine MUST NOT return 0 questions when the bank has questions!"""
    db = SessionLocal()
    try:
        from backend.app.services.prompt_quiz.engine import _load_bank
        bank = _load_bank(db)
        safe_count = sum(1 for q in bank if q.get("tagged") and (q.get("sensitivity") or "").strip().upper() == "NONE")

        prompts = [
            "give me 5 questions",
            "5 questions for school",
            "10 questions",
            "quiz for kids",
            "family quiz",
            "5 questions without images",
            "5 questions on India",
            "5 easy questions",
            "5 tough questions",
        ]
        for p in prompts:
            res = build_draft(db, p)
            if res["brief"]["filters"].get("school_safe") and safe_count < res["brief"]["count"]:
                assert len(res["questions"]) <= safe_count
            else:
                assert len(res["questions"]) > 0, (
                    f"Prompt {p!r} returned 0 questions! "
                    f"Brief query={res['brief']['query']!r}, filters={res['brief']['filters']}"
                )
            assert len(res["questions"]) <= res["brief"]["count"]
    finally:
        db.close()


def test_corporate_and_typo_retrieval():
    """Ambiguous & typos: corporate queries and common typos like 'cooprate'."""
    db = SessionLocal()
    try:
        res_corp = build_draft(db, "5 questions for corporate")
        assert len(res_corp["questions"]) > 0, "Corporate query returned 0 questions"

        res_typo = build_draft(db, "5 questions for cooprate")
        assert len(res_typo["questions"]) > 0, "Typo 'cooprate' query returned 0 questions"
    finally:
        db.close()


def test_negative_constraint_effective():
    """If exclude_image is set, NO returned question should be image_dependent."""
    db = SessionLocal()
    try:
        res = build_draft(db, "10 questions without images")
        for q in res["questions"]:
            assert q.get("image_dependent") is not True
    finally:
        db.close()


# ----------------------------------------------------------------------
# 3. SWAP, GUARDIAN & EDITOR TESTS
# ----------------------------------------------------------------------

def test_swap_excludes_current_and_keeps():
    db = SessionLocal()
    try:
        res = build_draft(db, "5 questions on company")
        qs = res["questions"]
        if len(qs) >= 2:
            target = qs[0]
            keep_ids = [q["id"] for q in qs]
            swapped = swap_one(db, "5 questions on company", target["id"], keep_ids)
            if swapped:
                assert swapped["id"] != target["id"]
                assert swapped["id"] not in keep_ids
    finally:
        db.close()


def test_no_duplicate_questions_in_draft():
    """Draft must never contain the same question twice."""
    db = SessionLocal()
    try:
        for p in ["10 questions on Australia", "10 questions on company", "10 questions"]:
            res = build_draft(db, p)
            ids = [q["id"] for q in res["questions"]]
            assert len(ids) == len(set(ids)), f"Duplicate question IDs found in draft for {p!r}"
    finally:
        db.close()


# ----------------------------------------------------------------------
# 4. API END-TO-END TESTS
# ----------------------------------------------------------------------

def test_api_draft_and_save_lifecycle():
    r = client.post("/api/v1/prompt-quiz/draft", json={"prompt": "5 questions on banking"})
    assert r.status_code == 200
    data = r.json()
    assert len(data["questions"]) > 0
    ids = [q["id"] for q in data["questions"]]

    # Save
    save_res = client.post("/api/v1/prompt-quiz/save", json={
        "prompt": "5 questions on banking",
        "title": "Banking Quick Quiz",
        "question_ids": ids
    })
    assert save_res.status_code == 200
    quiz_id = save_res.json()["quiz_id"]

    # Verify quiz exists
    get_res = client.get(f"/api/v1/quizzes/{quiz_id}")
    assert get_res.status_code == 200
    quiz_data = get_res.json()
    assert quiz_data["title"] == "Banking Quick Quiz"
    assert len(quiz_data["questions"]) == len(ids)
