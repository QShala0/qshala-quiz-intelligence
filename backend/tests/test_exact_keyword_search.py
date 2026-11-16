import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database import Base
from backend.app.models.document import Document
from backend.app.models.question import Question
from backend.app.routers.questions import search_questions
from backend.app.services.retrieval.hybrid_retriever import keyword_terms


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Document(id="d1", filename="space.pptx", title="Space Deck", file_type="pptx", storage_path="/tmp/s.pptx"))
    rows = [
        ("q1", "Which planet is known as the Red Planet?", "Mars", "Astronomy"),
        ("q2", "Name the first Indian in space.", "Rakesh Sharma", "Astronomy"),
        ("q3", "Which bank issues banknotes in India?", "RBI", "Finance"),
    ]
    for qid, text, ans, topic in rows:
        s.add(Question(id=qid, document_id="d1", topic=topic, question_text=text, answer=ans))
    s.commit()
    yield s
    s.close()


def _ids(db, **kw):
    res = asyncio.run(search_questions(db=db, **kw))
    return {r.id for r in res}


def test_terms_split_words_and_quoted_phrases():
    assert keyword_terms('radio "mint road"') == [
        {"kind": "word", "text": "radio"}, {"kind": "phrase", "text": "mint road"}]


def test_only_questions_containing_the_word_are_returned(db):
    assert _ids(db, query="planet") == {"q1"}
    assert _ids(db, query="india") == {"q2", "q3"}


def test_every_typed_word_must_match(db):
    assert _ids(db, query="india space") == {"q2"}
    assert _ids(db, query="india mars") == set()


def test_matches_answer_and_topic_too(db):
    assert _ids(db, query="rakesh") == {"q2"}
    assert _ids(db, query="finance") == {"q3"}


def test_quoted_phrase_matches_as_phrase(db):
    assert _ids(db, query='"red planet"') == {"q1"}
    assert _ids(db, query='"planet red"') == set()
