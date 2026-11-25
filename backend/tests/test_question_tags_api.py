"""The Edit Tags API: every tag editable, history recorded, deck edits flow down, duplicates become Repeated."""
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.main import app
from backend.app.database import SessionLocal
from backend.app.models.document import Document
from backend.app.models.qshala_tag import QshalaQuestionMetadata
from backend.app.models.question import Question
from backend.app.models.question_meta import QuestionRepeat
from backend.app.services.ingestion.cancellation import purge_document
from test_v6_pipeline import _ingest

client = TestClient(app)
API = "/api/v1"


def _first_questions(doc_id):
    db = SessionLocal()
    try:
        rows = (db.query(Question.id).join(QshalaQuestionMetadata, QshalaQuestionMetadata.question_id == Question.id)
                .filter(Question.document_id == doc_id).order_by(QshalaQuestionMetadata.question_number).all())
        return [r[0] for r in rows]
    finally:
        db.close()


def test_detail_edit_history_and_deck_propagation():
    tag = uuid.uuid4().hex[:8]
    doc = _ingest("2022-05-10 PES University Bengaluru Prelims ZVQ History Quiz English.pptx", tag)
    try:
        q1, q2 = _first_questions(doc)[:2]
        d = client.get(f"{API}/questions/{q1}/detail").json()
        assert d["deck"]["college"] == "PES University" and d["deck"]["is_varsity"] is True
        assert d["meta"]["is_varsity"] is True and d["meta"]["question_format"] in ("CONNECT", "CONNECT_4")
        assert d["meta"]["question_label"].startswith("Round 1 Q")
        assert [p["l2"] for p in d["topic_paths"]] == ["Modern History", "Indian Politics"]
        assert d["hashtags"] == [] and d["repeated"] == []
        assert d["question_images"] == [] and d["document"]["id"] == doc

        # edit traits: stored, normalised, remembered as manual, and written to history
        r = client.put(f"{API}/questions/{q1}/meta", json={"changes": {
            "secondary_audience": "School", "relevant_year": 2016, "question_format": "buzzer",
            "star_marked": False, "india_international": "in", "geo_region": "Mumbai, India"}})
        assert r.status_code == 200, r.text
        m = r.json()["meta"]
        assert m["secondary_audience"] == "School" and m["relevant_year"] == 2016
        assert m["question_format"] == "BUZZER" and m["star_marked"] is False
        assert m["india_international"] == "IN" and m["geo_region"] == "Mumbai, India"
        assert {"relevant_year", "question_format", "star_marked"} <= set(m["manual_fields"])
        hist = client.get(f"{API}/questions/{q1}/history").json()
        assert any(h["field"] == "relevant_year" and h["new_value"] == "2016" and h["kind"] == "EDIT" for h in hist)

        # invalid values are rejected with a clear error
        assert client.put(f"{API}/questions/{q1}/meta", json={"changes": {"question_format": "quiz-ish"}}).status_code == 422
        assert client.put(f"{API}/questions/{q1}/meta", json={"changes": {"india_international": "MIXED"}}).status_code == 422

        # a hand edit on one question survives a deck edit; the other question follows the deck
        client.put(f"{API}/questions/{q1}/meta", json={"changes": {"city_name": "Mysuru"}})
        out = client.put(f"{API}/documents/{doc}/deck-meta", json={"changes": {"city": "Pune", "college": "IIM Bangalore"}})
        assert out.status_code == 200 and out.json()["city"] == "Pune"
        assert out.json()["questions_updated"] >= 1
        assert client.get(f"{API}/questions/{q1}/detail").json()["meta"]["city_name"] == "Mysuru"
        assert client.get(f"{API}/questions/{q2}/detail").json()["meta"]["city_name"] == "Pune"
        assert client.get(f"{API}/questions/{q2}/detail").json()["meta"]["college_institution"] == "IIM Bangalore"
        assert client.get(f"{API}/documents/{doc}/deck-meta").json()["city"] == "Pune"
        assert client.put(f"{API}/documents/nope/deck-meta", json={"changes": {"city": "x"}}).status_code == 404
    finally:
        db = SessionLocal()
        purge_document(db, doc)
        db.close()


def test_topic_paths_hierarchy_custom_subtopics_and_hashtags():
    doc = _ingest("2022 Pune Prelims Quiz.pptx", uuid.uuid4().hex[:8])
    try:
        q1 = _first_questions(doc)[0]
        r = client.put(f"{API}/questions/{q1}/topic-paths", json={"paths": [
            {"l1": "Sports", "l2": "cricket", "keywords": ["IPL", "ipl", "Auction", "#Mumbai Indians"]},
            {"l1": "Business", "l2": "Sports Business", "keywords": ["Franchise"]},
            {"l1": "Science", "l2": "Quantum Cooking", "keywords": ["Spherification"]},   # custom subtopic
            {"l1": "Cooking", "l2": "Knife Skills", "keywords": []},                        # custom theme
        ]})
        assert r.status_code == 200, r.text
        d = r.json()
        paths = d["topic_paths"]
        assert [(p["l1"], p["l2"]) for p in paths] == [
            ("Sports", "Cricket"), ("Sports", "Sports Business"),     # the real theme wins for a real subtopic
            ("Science", "Quantum Cooking"), ("Cooking", "Knife Skills")]
        assert paths[0]["keywords"] == ["IPL", "Auction", "Mumbai Indians"]      # cleaned and de-duplicated
        assert [p["is_custom"] for p in paths] == [False, False, True, True]
        assert all(p["source"] == "HUMAN" for p in paths)
        assert d["topic"] == "Sports" and d["subtopic"] == "Cricket"

        tax = client.get(f"{API}/taxonomy").json()
        assert len(tax["themes"]) == 11 and sum(len(t["subtopics"]) for t in tax["themes"]) == 143
        assert {"l1": "Cooking", "l2": "Knife Skills", "count": 1} in tax["custom"]
        assert "BUZZER" in tax["formats"] and "NA" in tax["geographies"]

        # hashtags are kept apart from keywords and survive a topic edit
        h = client.put(f"{API}/questions/{q1}/hashtags", json={"hashtags": ["#Throwback", "Finals Special"]}).json()
        assert h["hashtags"] == ["Throwback", "Finals Special"]
        d2 = client.put(f"{API}/questions/{q1}/topic-paths", json={"paths": [
            {"l1": "Sports", "l2": "Cricket", "keywords": ["Wankhede"]}]}).json()
        assert d2["hashtags"] == ["Throwback", "Finals Special"]
        assert [p["l2"] for p in d2["topic_paths"]] == ["Cricket"]

        hist = client.get(f"{API}/questions/{q1}/history").json()
        assert any(x["field"] == "topic_paths" for x in hist) and any(x["field"] == "hashtags" for x in hist)
        assert client.put(f"{API}/questions/{q1}/topic-paths", json={"paths": [{"l1": "", "l2": ""}]}).status_code == 422

        # the list rows carry the new summary fields
        rows = client.get(f"{API}/questions", params={"document_id": doc, "limit": 50}).json()
        row = next(x for x in rows if x["id"] == q1)
        assert row["topic_paths"][0]["l2"] == "Cricket" and row["question_label"] and row["repeated_count"] == 0
        assert row["star_marked"] is True
    finally:
        db = SessionLocal()
        purge_document(db, doc)
        db.close()


def test_repeated_entries_can_be_fixed_and_removed_and_confirmed_duplicates_become_repeats():
    tag = uuid.uuid4().hex[:8]
    doc1 = _ingest("2022-05-10 PES University Bengaluru Prelims ZVQ History Quiz.pptx", tag)
    doc2 = _ingest("2023-02-11 IIM Bangalore Bengaluru Finals Open Quiz.pptx", tag)
    extra_doc = None
    try:
        q1 = _first_questions(doc1)[0]
        d = client.get(f"{API}/questions/{q1}/detail").json()
        assert len(d["repeated"]) == 1
        rid = d["repeated"][0]["id"]
        assert d["repeated"][0]["label"].startswith("Open Quiz · Round")
        rows = client.get(f"{API}/questions", params={"document_id": doc1}).json()
        assert next(x for x in rows if x["id"] == q1)["repeated_count"] == 1

        p = client.patch(f"{API}/questions/{q1}/repeats/{rid}", json={"quiz_name": "Open Quiz 2023", "question_number": 9})
        assert p.status_code == 200 and p.json()["label"].startswith("Open Quiz 2023 · ")
        assert client.delete(f"{API}/questions/{q1}/repeats/{rid}").json()["repeated"] == []
        db = SessionLocal()
        assert db.query(Question).filter(Question.id == q1).one().occurrence_count == 1
        # a possible duplicate sitting in a third deck, confirmed by a person
        extra_doc = str(uuid.uuid4())
        db.add(Document(id=extra_doc, filename="2024-01-01 Delhi Finals Quiz.pptx", title="Quiz 2024", year=2024,
                        file_type="pptx", storage_path="x", processing_status="COMPLETED"))
        db.flush()
        dup = Question(document_id=extra_doc, question_text="near copy?", answer="a", topic="History",
                       duplicate_status="POSSIBLE_DUPLICATE", duplicate_of_id=q1, source_slide_range="Slide 3-4")
        db.add(dup)
        db.commit()
        dup_id = dup.id
        db.close()

        r = client.post(f"{API}/questions/{dup_id}/resolve-duplicate", json={"action": "CONFIRM_DUPLICATE"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == q1 and body["repeated_count"] == 1
        assert body["repeated"][0]["match_type"] == "CONFIRMED" and body["occurrence_count"] == 2
        db = SessionLocal()
        assert db.query(Question).filter(Question.id == dup_id).first() is None     # stored once
        db.close()
    finally:
        db = SessionLocal()
        for d in (doc1, doc2, extra_doc):
            if d:
                purge_document(db, d)
        db.close()


def test_duplicate_resolve_keep_candidate():
    tag = uuid.uuid4().hex[:8]
    doc1 = _ingest("2022-05-10 PES University Bengaluru Prelims ZVQ History Quiz.pptx", tag)
    extra_doc = None
    try:
        q1 = _first_questions(doc1)[0]
        db = SessionLocal()
        orig_obj = db.query(Question).filter(Question.id == q1).one()
        orig_text = orig_obj.question_text
        orig_answer = orig_obj.answer

        extra_doc = str(uuid.uuid4())
        db.add(Document(id=extra_doc, filename="2024-05-20 Candidate Deck.pptx", title="Candidate Deck 2024", year=2024,
                        file_type="pptx", storage_path="y", processing_status="COMPLETED"))
        db.flush()
        cand = Question(
            document_id=extra_doc,
            question_text="New Candidate Question Text (Better formatted)?",
            answer="Better Candidate Answer",
            topic="Finance",
            duplicate_status="POSSIBLE_DUPLICATE",
            duplicate_of_id=q1,
            source_slide_range="Slide 5-6"
        )
        db.add(cand)
        db.commit()
        cand_id = cand.id
        db.close()

        # Resolve with keep="CANDIDATE"
        r = client.post(f"{API}/questions/{cand_id}/resolve-duplicate", json={
            "action": "CONFIRM_DUPLICATE",
            "keep": "CANDIDATE"
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == q1  # ID is preserved
        assert body["question_text"] == "New Candidate Question Text (Better formatted)?"
        assert body["answer"] == "Better Candidate Answer"
        assert body["document_id"] == extra_doc

        db = SessionLocal()
        # Candidate row should be deleted
        assert db.query(Question).filter(Question.id == cand_id).first() is None
        # Original row updated in place
        updated_orig = db.query(Question).filter(Question.id == q1).one()
        assert updated_orig.question_text == "New Candidate Question Text (Better formatted)?"
        assert updated_orig.answer == "Better Candidate Answer"
        assert updated_orig.document_id == extra_doc
        assert updated_orig.occurrence_count >= 2
        # Previous deck recorded as repeat
        from backend.app.models.question_meta import QuestionRepeat
        assert db.query(QuestionRepeat).filter(QuestionRepeat.question_id == q1, QuestionRepeat.document_id == doc1).first() is not None
        db.close()
    finally:
        db = SessionLocal()
        for d in (doc1, extra_doc):
            if d:
                purge_document(db, d)
        db.close()

