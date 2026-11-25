from backend.app.services.ingestion.question_normalizer import (
    normalize_answer, normalize_options, normalize_question_dict, normalize_question_format,
    normalize_question_text, validate_before_save)


def test_mcq_prefix_stripped():
    assert normalize_answer("A. Net Asset Value (NAV)", "MCQ") == "Net Asset Value (NAV)"
    assert normalize_answer("A) Joint Accounts", "STANDARD_QA") == "Joint Accounts"


def test_initials_in_names_kept():
    assert normalize_answer("A. R. Rahman", "STANDARD_QA") == "A. R. Rahman"
    assert normalize_answer("Apple", "STANDARD_QA") == "Apple"


def test_text_artifacts_removed():
    out = normalize_question_text("Points: 10\nRound 2:\nWho wrote Hamlet?\n\n\n\nExtra")
    assert out == "Who wrote Hamlet?\n\nExtra"


def test_options_and_format():
    assert normalize_options([], "STANDARD_QA") is None
    assert normalize_options([], "MCQ") == []
    assert normalize_question_format("QA") == "STANDARD_QA"


def test_dict_flags_and_idempotent():
    q = {"question_format": "QA", "question_text": "?", "answer": "", "options": []}
    normalize_question_dict(q)
    assert q["question_format"] == "STANDARD_QA" and q["options"] is None
    assert q["_ingest"]["needs_review"]
    snap = dict(q)
    normalize_question_dict(q)
    assert q["question_text"] == snap["question_text"]


def test_validate_before_save():
    errs = validate_before_save({"question_text": "x", "answer": "", "question_format": "NOPE", "options": []})
    assert len(errs) == 4
