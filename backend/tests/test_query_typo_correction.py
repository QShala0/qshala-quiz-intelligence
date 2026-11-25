"""Query typo-correction must not rewrite real words into other topics.

"history" used to be fuzzy-"corrected" to "chemistry" (and "rocket" to "cricket", "finance" to "insurance", "good" to
"food"), so "bangalore history for school kids" returned Chemistry, Festivals and Plants. A word that exists in the bank
is a real word; only unknown words are treated as typos.
"""
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.prompt_quiz import engine
from backend.app.services.prompt_quiz.retrieval import SYNONYMS, Index, expand, tokenize

# Real words that the old fuzzy matcher rewrote into an unrelated synonym key.
REAL_WORDS = ["history", "rocket", "finance", "good", "port", "exports", "imports", "future", "agriculture",
              "geopolitics", "hollywood", "credit", "making", "asking", "header", "oscar", "author", "brain"]


@pytest.mark.parametrize("word", REAL_WORDS)
def test_a_word_in_the_bank_is_never_rewritten_to_another_topic(word):
    assert word not in SYNONYMS
    bag = expand(tokenize(word), known={word})
    assert set(bag) == {word}, f"{word!r} was expanded to {sorted(set(bag) - {word})}"


@pytest.mark.parametrize("typo,intended", [("bnak", "bank"), ("epople", "people"), ("crickt", "cricket"), ("bankng", "banking")])
def test_unknown_words_are_still_corrected_as_typos(typo, intended):
    bag = expand(tokenize(typo), known={"unrelated"})
    assert intended in bag, f"{typo!r} should still be corrected to {intended!r}"


def test_callers_without_a_vocabulary_keep_the_old_behaviour():
    assert "chemistry" in expand(tokenize("history"))


def _row(i, **kw):
    base = {"secondary_l2": [], "keywords": [], "shelf_life": "EVERGREEN", "sensitivity": "NONE", "image_dependent": False,
            "question_images": [], "answer_images": [], "image_refs": [], "search_summary": None, "difficulty": "Easy",
            "difficulty_score": 0.2, "tagged": True, "question_format": "STANDARD_QA", "format_payload": None,
            "city_name": None, "geo_region": None, "india_international": "IN", "document_id": kw.get("document_title")}
    return {**base, "id": f"q{i}", **kw}


def _school_bank():
    rows = [dict(question_text=f"The Haber process {i} in chemistry makes ammonia from nitrogen", answer=f"Ammonia {i}",
                 topic_l1="Science", subtopic_l2="Chemistry", document_title="Agastya Regionals Set 2 Prelims",
                 audience_suitability=["middle_school"], primary_audience="school") for i in range(12)]
    rows += [dict(question_text="Which ruler founded Bengaluru in 1537?", answer="Kempegowda", topic_l1="History",
                  subtopic_l2="Modern History", document_title="City Trivia Night", audience_suitability=["middle_school"]),
             dict(question_text="Which Bengaluru fort did Tipu Sultan use as a summer palace?", answer="Tipu Sultan's Palace",
                  topic_l1="History", subtopic_l2="Modern History", document_title="City Trivia Night",
                  audience_suitability=["middle_school"])]
    rows += [dict(question_text=f"Filler cricket {i}", answer="x", topic_l1="Sports", document_title="d") for i in range(15)]
    return [_row(i, **r) for i, r in enumerate(rows)]


def test_history_prompt_for_school_kids_returns_no_chemistry():
    bank = _school_bank()
    with patch.object(engine, "_load_bank", lambda db, *a, **k: [dict(r) for r in bank]), \
         patch("backend.app.config.settings.GEMINI_API_KEY", ""):
        draft = engine.build_draft(MagicMock(), "bangalore history for school kids")
    texts = [q["question_text"] for q in draft["questions"]]
    assert texts, "expected the genuine history questions"
    assert not any("Haber" in t for t in texts), texts


def test_index_search_for_history_does_not_score_chemistry_questions():
    bank = _school_bank()
    hits = Index(bank).search("history", {}, top_k=50)
    assert hits and all("Haber" not in h["question_text"] for h in hits)
