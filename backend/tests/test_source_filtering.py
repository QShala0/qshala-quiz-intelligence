import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base, get_db
from backend.app.main import app
from backend.app.models.document import Document
from backend.app.models.question import Question
from backend.app.services.ai.local_provider import LocalEmbeddingProvider
from backend.app.services.retrieval.hybrid_retriever import HybridRetriever
from backend.app.services.source_grouping import (
    build_source_catalog,
    derive_source_series,
    resolve_deck_query,
)


def _seed_source_fixture(db):
    emb_prov = LocalEmbeddingProvider()
    emb_radio = asyncio.run(emb_prov.get_embedding("radio wireless transmission eiffel tower"))
    emb_sci = asyncio.run(emb_prov.get_embedding("science chemistry ph indicator banana radioactive"))
    emb_other = asyncio.run(emb_prov.get_embedding("world geography capital history"))

    docs = [
        Document(
            id="doc-ag-s2p",
            filename="Agastya Regionals Set 2 Prelims 2026.pptx",
            title="Agastya Regionals Set 2 Prelims 2026",
            year=2026,
            file_type="pptx",
            storage_path="ag_s2p.pptx",
            question_count=2,
            processing_status="COMPLETED",
        ),
        Document(
            id="doc-ag-s2f",
            filename="Agastya Regionals Set 2 Finals 2026.pptx",
            title="Agastya Regionals Set 2 Finals 2026",
            year=2026,
            file_type="pptx",
            storage_path="ag_s2f.pptx",
            question_count=2,
            processing_status="COMPLETED",
        ),
        Document(
            id="doc-ag-s1f",
            filename="Agastya Regionals Set 1 Finals 2026.pptx",
            title="Agastya Regionals Set 1 Finals 2026",
            year=2026,
            file_type="pptx",
            storage_path="ag_s1f.pptx",
            question_count=1,
            processing_status="COMPLETED",
        ),
        Document(
            id="doc-ag-s3p",
            filename="Agastya Regionals Set 3 Prelims 2026.pptx",
            title="Agastya Regionals Set 3 Prelims 2026",
            year=2026,
            file_type="pptx",
            storage_path="ag_s3p.pptx",
            question_count=1,
            processing_status="COMPLETED",
        ),
        Document(
            id="doc-world-1",
            filename="World_Trivia_General.pptx",
            title="World Trivia General",
            year=2025,
            file_type="pptx",
            storage_path="world.pptx",
            question_count=3,
            processing_status="COMPLETED",
        ),
    ]
    for d in docs:
        db.add(d)

    questions = [
        Question(
            id="q-ag-radio",
            document_id="doc-ag-s2p",
            question_text="The Eiffel Tower serves a purpose in wireless communication. Which form of broadcasting sound uses radio waves?",
            answer="Radio Transmission",
            topic="Science",
            subtopic="Physics",
            difficulty="Medium",
            difficulty_score=0.5,
            tags=["radio", "wireless", "physics"],
            grade_min=5,
            grade_max=10,
            slot="LIVE",
            embedding=emb_radio,
        ),
        Question(
            id="q-ag-banana",
            document_id="doc-ag-s2p",
            question_text="Bananas are mildly radioactive because they contain which element?",
            answer="Potassium-40",
            topic="Science",
            subtopic="Chemistry",
            difficulty="Easy",
            difficulty_score=0.3,
            tags=["science", "chemistry"],
            grade_min=5,
            grade_max=10,
            slot="LIVE",
            embedding=emb_sci,
        ),
        Question(
            id="q-ag-ph",
            document_id="doc-ag-s2f",
            question_text="Shankhapushpa butterfly pea flower acts as a natural pH indicator changing colour in acid and base.",
            answer="Natural pH indicator",
            topic="Science",
            subtopic="Chemistry",
            difficulty="Medium",
            difficulty_score=0.5,
            tags=["chemistry", "plants"],
            grade_min=6,
            grade_max=10,
            slot="LIVE",
            embedding=emb_sci,
        ),
        Question(
            id="q-ag-kangri",
            document_id="doc-ag-s2f",
            question_text="In Kashmir locals use a clay pot with hot coal wrapped in wicker called Kangri.",
            answer="Kangri",
            topic="Culture",
            subtopic="India",
            difficulty="Hard",
            difficulty_score=0.75,
            tags=["kashmir", "heritage"],
            grade_min=6,
            grade_max=12,
            slot="LIVE",
            embedding=emb_other,
        ),
        Question(
            id="q-ag-bones",
            document_id="doc-ag-s1f",
            question_text="Humans are born with 300 connective tissues that fuse into 206 by adulthood. Name them.",
            answer="Bones",
            topic="Science",
            subtopic="Biology",
            difficulty="Easy",
            difficulty_score=0.25,
            tags=["biology", "anatomy"],
            grade_min=4,
            grade_max=8,
            slot="LIVE",
            embedding=emb_sci,
        ),
        Question(
            id="q-ag-ngc",
            document_id="doc-ag-s3p",
            question_text="NGC 2264 is nicknamed the Christmas Tree Cluster in astronomy.",
            answer="Christmas Tree Cluster",
            topic="Science",
            subtopic="Astronomy",
            difficulty="Hard",
            difficulty_score=0.7,
            tags=["astronomy", "space"],
            grade_min=6,
            grade_max=12,
            slot="LIVE",
            embedding=emb_sci,
        ),
        Question(
            id="q-world-radio",
            document_id="doc-world-1",
            question_text="Who invented the long-range radio telegraph system in Italy?",
            answer="Guglielmo Marconi",
            topic="History",
            subtopic="Inventions",
            difficulty="Medium",
            difficulty_score=0.5,
            tags=["radio", "history"],
            grade_min=5,
            grade_max=10,
            slot="LIVE",
            embedding=emb_radio,
        ),
        Question(
            id="q-world-sci",
            document_id="doc-world-1",
            question_text="What is the chemical symbol for Sodium on the periodic table?",
            answer="Na",
            topic="Science",
            subtopic="Chemistry",
            difficulty="Easy",
            difficulty_score=0.2,
            tags=["science", "chemistry"],
            grade_min=4,
            grade_max=8,
            slot="LIVE",
            embedding=emb_sci,
        ),
        Question(
            id="q-world-geo",
            document_id="doc-world-1",
            question_text="What is the capital city of Australia?",
            answer="Canberra",
            topic="Geography",
            subtopic="World Capitals",
            difficulty="Easy",
            difficulty_score=0.2,
            tags=["geography"],
            grade_min=3,
            grade_max=8,
            slot="LIVE",
            embedding=emb_other,
        ),
    ]
    for q in questions:
        db.add(q)
    db.commit()


@pytest.fixture
def source_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ENABLE_INTENT_ENGINE", "false")
    monkeypatch.setenv("ENABLE_AI_CURATOR", "false")
    monkeypatch.setenv("PROMPT_QUIZ_BANK_TTL_S", "0")

    db_file = tmp_path / "source_filter_test.db"
    engine = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    seed_db = TestingSession()
    try:
        _seed_source_fixture(seed_db)
    finally:
        seed_db.close()

    def _override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as client:
            yield client, TestingSession
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_source_grouping_and_fuzzy_resolution():
    assert derive_source_series("Agastya Regionals Set 2 Prelims 2026") == "Agastya Regionals"
    assert derive_source_series("Agastya Regionals Set 1 Finals 2026.pptx") == "Agastya Regionals"

    docs = [
        {"id": "d1", "title": "Agastya Regionals Set 2 Prelims 2026", "filename": "s2p.pptx", "question_count": 20},
        {"id": "d2", "title": "Agastya Regionals Set 2 Finals 2026", "filename": "s2f.pptx", "question_count": 31},
        {"id": "d3", "title": "Agastya Regionals Set 1 Finals 2026", "filename": "s1f.pptx", "question_count": 33},
        {"id": "d4", "title": "Agastya Regionals Set 3 Prelims 2026", "filename": "s3p.pptx", "question_count": 20},
        {"id": "d5", "title": "World Trivia General", "filename": "world.pptx", "question_count": 50},
    ]
    catalog = build_source_catalog(docs)
    ag_series = [s for s in catalog["series"] if s["label"] == "Agastya Regionals"]
    assert len(ag_series) == 1
    assert ag_series[0]["deck_count"] == 4
    assert ag_series[0]["question_count"] == 104
    assert set(ag_series[0]["document_ids"]) == {"d1", "d2", "d3", "d4"}

    # Typo tolerance: "aghtya" and "ashtya" -> all 4 Agastya decks
    r_typo = resolve_deck_query("10 questions from aghtya", docs, is_prompt=True)
    assert r_typo["tokens"] == ["agastya"]
    assert set(r_typo["doc_ids"]) == {"d1", "d2", "d3", "d4"}

    r_typo2 = resolve_deck_query("ashtya radio", docs, is_prompt=False)
    assert r_typo2["tokens"] == ["agastya"]
    assert set(r_typo2["doc_ids"]) == {"d1", "d2", "d3", "d4"}
    assert r_typo2["remaining_query"] == "radio"

    # Narrowing to specific set/prelims
    r_set2p = resolve_deck_query("aghtya set 2 prelims", docs, is_prompt=True)
    assert r_set2p["doc_ids"] == ["d1"]

    # "A tough finals round from Agastya" should NOT lock out Prelims (difficulty word, not deck-bound)
    r_tough = resolve_deck_query("A tough finals round from Agastya", docs, is_prompt=True)
    assert set(r_tough["doc_ids"]) == {"d1", "d2", "d3", "d4"}

    # "Agastya Set 2 Finals" SHOULD narrow specifically to d2
    r_set2f = resolve_deck_query("Agastya Set 2 Finals", docs, is_prompt=True)
    assert r_set2f["doc_ids"] == ["d2"]


def test_documents_sources_endpoint(source_client):
    client, _ = source_client
    res = client.get("/api/v1/documents/sources")
    assert res.status_code == 200
    data = res.json()
    ag = [s for s in data["series"] if s["label"] == "Agastya Regionals"]
    assert len(ag) == 1
    assert ag[0]["deck_count"] == 4
    assert ag[0]["question_count"] == 6
    assert len(data["decks"]) == 5


def test_questions_bank_source_filtering_and_scoped_search(source_client):
    client, _ = source_client

    # 1. Multi-deck document_ids filter
    res_multi = client.get("/api/v1/questions", params={"document_ids": "doc-ag-s2p,doc-ag-s1f", "limit": 50})
    assert res_multi.status_code == 200
    ids_multi = {q["id"] for q in res_multi.json()}
    assert ids_multi == {"q-ag-radio", "q-ag-banana", "q-ag-bones"}

    # 2. Text search WITH document_ids stays strictly inside selected deck (no q-world-radio leak!)
    res_scoped_q = client.get("/api/v1/questions", params={"document_ids": "doc-ag-s2p", "query": "radio", "limit": 50})
    assert res_scoped_q.status_code == 200
    items_scoped = res_scoped_q.json()
    assert len(items_scoped) > 0
    assert all(q["document_id"] == "doc-ag-s2p" for q in items_scoped)
    assert "q-world-radio" not in {q["id"] for q in items_scoped}
    assert items_scoped[0]["id"] == "q-ag-radio"

    # 3. Fuzzy pure-source query "aghtya" -> strictly Agastya questions, zero World Trivia bleed
    res_typo = client.get("/api/v1/questions", params={"query": "aghtya", "limit": 50})
    assert res_typo.status_code == 200
    items_typo = res_typo.json()
    assert {q["id"] for q in items_typo} == {
        "q-ag-radio", "q-ag-banana", "q-ag-ph", "q-ag-kangri", "q-ag-bones", "q-ag-ngc"
    }

    # 4. Compound typo + topic query "aghtya radio" -> strictly Agastya radio hits
    res_compound = client.get("/api/v1/questions", params={"query": "aghtya radio", "limit": 50})
    assert res_compound.status_code == 200
    items_compound = res_compound.json()
    assert len(items_compound) > 0
    assert all(q["document_id"].startswith("doc-ag-") for q in items_compound)
    assert items_compound[0]["id"] == "q-ag-radio"
    assert "q-world-radio" not in {q["id"] for q in items_compound}

    # 5. Specific deck search "Agastya Set 2 Prelims" -> strictly doc-ag-s2p
    res_set2p = client.get("/api/v1/questions", params={"query": "Agastya Set 2 Prelims", "limit": 50})
    assert res_set2p.status_code == 200
    assert {q["id"] for q in res_set2p.json()} == {"q-ag-radio", "q-ag-banana"}


def test_prompt_quiz_source_selection_fuzzy_and_swap_continuity(source_client):
    client, _ = source_client

    # 1. Typo in prompt: "4 questions from aghtya" -> detects agastya + excludes World Trivia
    r1 = client.post("/api/v1/prompt-quiz/draft", json={"prompt": "4 questions from aghtya"})
    assert r1.status_code == 200
    d1 = r1.json()
    assert "agastya" in (d1["brief"]["filters"].get("deck_filter") or [])
    assert len(d1["questions"]) == 4
    assert all(q["document_id"].startswith("doc-ag-") for q in d1["questions"])
    assert d1["brief"]["filters"].get("source_labels")

    # 2. Specific set in prompt: "2 questions from aghtya set 2 prelims"
    r_set2p = client.post("/api/v1/prompt-quiz/draft", json={"prompt": "2 questions from aghtya set 2 prelims"})
    assert r_set2p.status_code == 200
    d_set2p = r_set2p.json()
    assert len(d_set2p["questions"]) == 2
    assert {q["document_id"] for q in d_set2p["questions"]} == {"doc-ag-s2p"}

    # 3. Explicit UI options.document_ids with generic prompt
    r_ui = client.post(
        "/api/v1/prompt-quiz/draft",
        json={
            "prompt": "3 questions",
            "options": {"document_ids": ["doc-ag-s2f", "doc-ag-s1f"]},
        },
    )
    assert r_ui.status_code == 200
    d_ui = r_ui.json()
    assert len(d_ui["questions"]) == 3
    assert all(q["document_id"] in {"doc-ag-s2f", "doc-ag-s1f"} for q in d_ui["questions"])

    # 4. Follow-up single-slot swap preserves source filter from previous_brief
    r_swap = client.post(
        "/api/v1/prompt-quiz/draft",
        json={
            "prompt": "replace question 1 with science",
            "previous_brief": d1["brief"],
            "current_questions": [
                {"index": i + 1, "id": q["id"], "question_text": q["question_text"]}
                for i, q in enumerate(d1["questions"])
            ],
        },
    )
    assert r_swap.status_code == 200
    d_swap = r_swap.json()
    assert all(q["document_id"].startswith("doc-ag-") for q in d_swap["questions"])


@pytest.mark.anyio
async def test_hybrid_retriever_strict_scope_never_relaxes_across_decks(source_client):
    _, TestingSession = source_client
    db = TestingSession()
    try:
        retriever = HybridRetriever(db)
        retriever.embedding_provider = LocalEmbeddingProvider()
        # Even searching a topic ("geography") that only exists in doc-world-1 must NOT leak when scoped to doc-ag-s2p
        results = await retriever.search(
            query="australia canberra",
            topic="Geography",
            document_ids=["doc-ag-s2p"],
            allow_relax=False,
            limit=10,
        )
        assert results == []
    finally:
        db.close()
