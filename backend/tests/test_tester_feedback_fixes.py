"""Comprehensive regression tests for:
1. Vercel chunked upload (< 5 MB R2 staging + ceiling + cancellation)
2. Bilingual Kannada+English ('ಉತ್ತರ!\\nAnswer!') & Finals classification/extraction
3. Idempotent startup DB auto-repair (repair_broken_deck_extractions & repair_kannada_only_answers)
4. Prompt Quiz Engine retrieval (Karnataka STEM vs Focus on India, Agastya quizkarnataka, School friendly Q1 continuity, placeholder dedup)
"""
from __future__ import annotations

import asyncio
import importlib
import io
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.datastructures import UploadFile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_memory_db():
    from backend.app.database import Base
    # Ensure all models are imported before create_all
    import backend.app.models.document  # noqa: F401
    import backend.app.models.question  # noqa: F401
    import backend.app.models.question_meta  # noqa: F401
    import backend.app.models.qshala_tag  # noqa: F401
    import backend.app.models.quiz  # noqa: F401
    import backend.app.models.upload_session  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


class _FakeR2Service:
    """In-memory Cloudflare R2 stand-in enforcing S3/R2's 5 MiB non-final part minimum."""

    MIN_PART_BYTES = 5 * 1024 * 1024

    def __init__(self):
        self.is_configured = True
        self.objects: Dict[str, bytes] = {}
        self.multiparts: Dict[str, Dict[str, Any]] = {}

    def create_multipart_upload(self, key: str) -> str:
        uid = f"mp-{uuid.uuid4()}"
        self.multiparts[uid] = {"key": key, "parts": {}, "aborted": False, "completed": False}
        return uid

    def upload_part(self, key: str, upload_id: str, part_number: int, data: bytes) -> str:
        mp = self.multiparts[upload_id]
        assert not mp["aborted"]
        mp["parts"][part_number] = data
        return f'"etag-{part_number}"'

    def complete_multipart_upload(self, key: str, upload_id: str, parts: List[Dict[str, Any]]) -> str:
        mp = self.multiparts[upload_id]
        ordered = sorted(parts, key=lambda p: int(p["PartNumber"]))
        # Enforce R2/S3 rule: EVERY part except the last MUST be >= 5 MiB
        for p in ordered[:-1]:
            pn = int(p["PartNumber"])
            assert len(mp["parts"][pn]) >= self.MIN_PART_BYTES, (
                f"EntityTooSmall: non-final part {pn} is {len(mp['parts'][pn])} bytes (< 5 MiB)"
            )
        assembled = b"".join(mp["parts"][int(p["PartNumber"])] for p in ordered)
        self.objects[key] = assembled
        mp["completed"] = True
        return f"https://r2.example.com/{key}"

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        if upload_id in self.multiparts:
            self.multiparts[upload_id]["aborted"] = True

    def upload_bytes(self, key: str, data: bytes, content_type: str = None) -> str:
        self.objects[key] = data
        return f"https://r2.example.com/{key}"

    def download(self, key: str) -> bytes:
        return self.objects[key]

    def delete(self, keys: List[str]) -> None:
        for k in keys:
            self.objects.pop(k, None)

    def delete_prefix(self, prefix: str) -> int:
        gone = [k for k in list(self.objects.keys()) if k.startswith(prefix)]
        for k in gone:
            del self.objects[k]
        return len(gone)


# ---------------------------------------------------------------------------
# 1. Vercel < 5 MB Chunked R2 Staging + Ceiling + Cancel Tests
# ---------------------------------------------------------------------------

def test_vercel_chunked_upload_stages_small_slices_into_r2_5mb_parts(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    from backend.app.routers import documents as docs_router

    importlib.reload(docs_router)
    try:
        assert docs_router.MAX_FILE_SIZE_BYTES < 5 * 1024 * 1024
        assert docs_router.MAX_CHUNKED_FILE_BYTES >= 2048 * 1024 * 1024

        db = _make_memory_db()
        r2 = _FakeR2Service()

        # 7.8 MB payload sliced into Vercel-safe 3.5 MB + 3.5 MB + 0.8 MB slices
        c0 = b"A" * int(3.5 * 1024 * 1024)
        c1 = b"B" * int(3.5 * 1024 * 1024)
        c2 = b"C" * int(0.8 * 1024 * 1024)
        full_payload = c0 + c1 + c2
        upload_id = str(uuid.uuid4())

        class _BG:
            def add_task(self, *a, **kw):
                pass

        monkeypatch.setattr(docs_router, "_schedule_ingestion", lambda *a, **kw: None)

        async def _run():
            r0 = await docs_router._upload_chunk_direct_to_r2(
                Path("unused"), UploadFile(file=io.BytesIO(c0), filename="c0"), upload_id,
                0, 3, ".pptx", "Agastya Regionals Set 1 Prelims 2026.pptx",
                "Agastya Regionals Set 1 Prelims 2026", 2026, r2, db, _BG(),
            )
            assert r0["status"] == "CHUNK_RECEIVED"

            # Idempotent retry of chunk 0 must NOT double-count or corrupt state
            r0_retry = await docs_router._upload_chunk_direct_to_r2(
                Path("unused"), UploadFile(file=io.BytesIO(c0), filename="c0"), upload_id,
                0, 3, ".pptx", "Agastya Regionals Set 1 Prelims 2026.pptx",
                "Agastya Regionals Set 1 Prelims 2026", 2026, r2, db, _BG(),
            )
            assert r0_retry["status"] == "CHUNK_RECEIVED"

            r1 = await docs_router._upload_chunk_direct_to_r2(
                Path("unused"), UploadFile(file=io.BytesIO(c1), filename="c1"), upload_id,
                1, 3, ".pptx", "Agastya Regionals Set 1 Prelims 2026.pptx",
                "Agastya Regionals Set 1 Prelims 2026", 2026, r2, db, _BG(),
            )
            assert r1["status"] == "CHUNK_RECEIVED"

            r2_res = await docs_router._upload_chunk_direct_to_r2(
                Path("unused"), UploadFile(file=io.BytesIO(c2), filename="c2"), upload_id,
                2, 3, ".pptx", "Agastya Regionals Set 1 Prelims 2026.pptx",
                "Agastya Regionals Set 1 Prelims 2026", 2026, r2, db, _BG(),
            )
            return r2_res

        res = asyncio.run(_run())
        assert res["status"] == "COMPLETED"
        assert res["document"]["file_size_bytes"] == len(full_payload)

        # Verify R2 assembled object matches byte-for-byte and zero temp staged objects remain
        mp = next(iter(r2.multiparts.values()))
        assert mp["completed"] is True
        assert r2.objects[mp["key"]] == full_payload
        assert not [k for k in r2.objects if k.startswith(f"raw-chunks/{upload_id}/")]
    finally:
        monkeypatch.delenv("VERCEL", raising=False)
        importlib.reload(docs_router)


def test_vercel_chunked_cancel_cleans_staged_r2_prefix(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    from backend.app.routers import documents as docs_router

    importlib.reload(docs_router)
    try:
        db = _make_memory_db()
        r2 = _FakeR2Service()
        monkeypatch.setattr(docs_router, "get_r2_service", lambda: r2, raising=False)
        import backend.app.services.storage.r2_storage as r2_mod
        monkeypatch.setattr(r2_mod, "get_r2_service", lambda: r2)

        upload_id = str(uuid.uuid4())
        c0 = b"Z" * int(3.5 * 1024 * 1024)

        class _BG:
            def add_task(self, *a, **kw):
                pass

        async def _run():
            await docs_router._upload_chunk_direct_to_r2(
                Path("unused"), UploadFile(file=io.BytesIO(c0), filename="c0"), upload_id,
                0, 3, ".pptx", "Agastya.pptx", "Agastya", 2026, r2, db, _BG(),
            )
            assert any(k.startswith(f"raw-chunks/{upload_id}/") for k in r2.objects)
            res = docs_router.cancel_chunked_upload(upload_id, db=db)
            assert res["status"] == "CANCELLED"

        asyncio.run(_run())
        assert not [k for k in r2.objects if k.startswith(f"raw-chunks/{upload_id}/")]
    finally:
        monkeypatch.delenv("VERCEL", raising=False)
        importlib.reload(docs_router)


# ---------------------------------------------------------------------------
# 2. Classification + Extraction Fixes (Agastya Prelims + Finals)
# ---------------------------------------------------------------------------

def test_agastya_bilingual_classification_and_extraction():
    from backend.app.services.ingestion.slide_classifier import SlideClassifier
    from backend.app.services.ingestion.question_extractor import QuestionExtractor
    from backend.app.services.ingestion.deck_metadata import annotate_questions

    slides = [
        {"id": "s1", "slide_number": 1, "title": "Agastya", "extracted_text": "Agastya Science Quiz 2026", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s2", "slide_number": 2, "title": "Round 1", "extracted_text": "Round 1: List-It!", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s3", "slide_number": 3, "title": "", "extracted_text": "Ready for Q1.", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s4", "slide_number": 4, "title": "List-It! Friction", "extracted_text": "List-It!\nWhich daily actions rely on friction?\n| | | |\n| | | |", "speaker_notes": "A. Walking without slipping\nB. Writing on paper\nC. Lighting a matchstick\nD. Braking a bicycle", "image_paths": ["i1.png", "i2.png", "i3.png", "i4.png"], "metadata_json": {}},
        {"id": "s5", "slide_number": 5, "title": "", "extracted_text": "Topics: Biology Space\nQ1 Q2 Next round", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s6", "slide_number": 6, "title": "", "extracted_text": "*15. Stupa is a sports-tech company using AI analytics. Which sport's rules were modernized in 2025?", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s7", "slide_number": 7, "title": "", "extracted_text": "ಉತ್ತರ!\nAnswer!", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s8", "slide_number": 8, "title": "", "extracted_text": "ಟೇಬಲ್ ಟೆನ್ನಿಸ್\nTable Tennis\nBack", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s9", "slide_number": 9, "title": "", "extracted_text": "Question for Audience", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s10", "slide_number": 10, "title": "", "extracted_text": "Which natural phenomenon bends light when entering water?", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s11", "slide_number": 11, "title": "", "extracted_text": "ಉತ್ತರ!\nAnswer!", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s12", "slide_number": 12, "title": "", "extracted_text": "What is refraction?", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s13", "slide_number": 13, "title": "", "extracted_text": "Which wireless method did J.C. Bose pioneer in 1895?", "speaker_notes": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQ==", "image_paths": [], "metadata_json": {}},
        {"id": "s14", "slide_number": 14, "title": "", "extracted_text": "ಉತ್ತರ!\nAnswer!", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s15", "slide_number": 15, "title": "", "extracted_text": "Radio Transmission", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
    ]

    clf = SlideClassifier()
    types = clf.classify_deck(slides)
    for s, st in zip(slides, types):
        s["slide_type"] = st

    assert types[2] == "ANNOUNCEMENT"       # Ready for Q1.
    assert types[3] == "QUESTION"           # List-It!
    assert types[4] == "ANNOUNCEMENT"       # Topics menu board
    assert types[5] == "QUESTION"           # *15. Stupa (not RULES!)
    assert types[6] == "ANSWER_TRANSITION"  # ಉತ್ತರ!\nAnswer!
    assert types[7] == "ANSWER"             # Table Tennis
    assert types[8] == "ANNOUNCEMENT"       # Question for Audience
    assert types[11] == "ANSWER"            # "What is refraction?" stays ANSWER!

    qs = QuestionExtractor().extract_from_slides(slides, "Agastya Regionals Set 2 Finals 2026", 2026, use_structured=False)
    annotate_questions(qs, slides)

    assert len(qs) == 4
    by_q = {q["slide_number"]: q for q in qs}

    # Q on slide 4: List-It! (NOT CONNECT_4, NOT answer = "| |")
    assert by_q[4]["_meta"]["question_format"] == "LIST_IT"
    assert "Walking without slipping" in by_q[4]["answer"]
    assert "| |" not in by_q[4]["answer"]

    # Q on slide 6: *15 Stupa rescued + bilingual Kannada line -> English answer + no "Back"
    assert "Stupa" in by_q[6]["question_text"]
    assert by_q[6]["answer"] == "Table Tennis"
    assert "Back" not in by_q[6]["explanation"]
    assert "ಟೇಬಲ್" in by_q[6]["explanation"]

    # Q on slide 10: Guess-the-Question ("What is refraction?") preserved as answer & marked Audience Question without clobbering round
    assert by_q[10]["answer"] == "What is refraction?"
    assert by_q[10].get("is_audience_question") is True
    assert "Audience Question" in (by_q[10].get("tags") or [])
    assert by_q[10]["_meta"]["round_number"] == 1

    # Q on slide 13: data:image/jpeg stripped cleanly from notes, real answer extracted
    assert by_q[13]["answer"] == "Radio Transmission"
    assert "data:image/" not in by_q[13]["answer"]
    assert "data:image/" not in by_q[13]["explanation"]


# ---------------------------------------------------------------------------
# 3. Startup DB Repair Pass Tests
# ---------------------------------------------------------------------------

def test_repair_broken_deck_extractions_and_kannada_fallback_idempotent():
    from backend.app.models.document import Document, Slide
    from backend.app.models.question import Question
    from backend.app.models.qshala_tag import QshalaQuestionMetadata, QshalaTag
    from backend.app.models.quiz import Quiz, GeneratedQuestion, RetrievalSource
    from backend.app.services.ingestion.repair_bilingual import (
        repair_broken_deck_extractions,
        repair_kannada_only_answers,
    )

    db = _make_memory_db()

    doc = Document(
        id="doc-agastya-broken",
        filename="Agastya Regionals Set 2 Prelims 2026.pptx",
        title="Agastya Regionals Set 2 Prelims",
        year=2026,
        file_type="pptx",
        storage_path="/tmp/ag.pptx",
        processing_status="COMPLETED",
        slide_count=5,
        question_count=3,
    )
    db.add(doc)
    db.flush()

    s1 = Slide(id="sl-1", document_id=doc.id, slide_number=1, slide_type="QUESTION", title="", extracted_text="Which fruit has an enzyme called bromelain?", speaker_notes="", image_paths=[], has_images=False)
    s2 = Slide(id="sl-2", document_id=doc.id, slide_number=2, slide_type="ANSWER", title="", extracted_text="ಉತ್ತರ!\nAnswer!", speaker_notes="", image_paths=[], has_images=False)
    s3 = Slide(id="sl-3", document_id=doc.id, slide_number=3, slide_type="QUESTION", title="", extracted_text="ಅನಾನಸ್\nPineapple", speaker_notes="", image_paths=[], has_images=False)
    s4 = Slide(id="sl-4", document_id=doc.id, slide_number=4, slide_type="QUESTION", title="", extracted_text="Ready for Q2.", speaker_notes="", image_paths=[], has_images=False)
    s5 = Slide(id="sl-5", document_id=doc.id, slide_number=5, slide_type="QUESTION", title="", extracted_text="Radio waves pioneered by J.C. Bose?", speaker_notes="data:image/jpeg;base64,AAAA", image_paths=[], has_images=False)
    s6 = Slide(id="sl-6", document_id=doc.id, slide_number=6, slide_type="ANSWER", title="", extracted_text="ಉತ್ತರ!\nAnswer!", speaker_notes="", image_paths=[], has_images=False)
    s7 = Slide(id="sl-7", document_id=doc.id, slide_number=7, slide_type="CONTENT", title="", extracted_text="Radio Transmission", speaker_notes="", image_paths=[], has_images=False)
    db.add_all([s1, s2, s3, s4, s5, s6, s7])
    db.flush()

    # Broken pre-existing Question rows mirroring live DB state
    q_real = Question(
        id="q-real-1", document_id=doc.id, slide_id="sl-1", answer_slide_id="sl-2",
        question_text="Which fruit has an enzyme called bromelain?", answer="ಉತ್ತರ!",
        explanation="Answer!", topic="General Knowledge", subtopic="Biology", content_hash="old1",
    )
    q_orphan = Question(
        id="q-orphan-bumper", document_id=doc.id, slide_id="sl-4", answer_slide_id=None,
        question_text="Ready for Q2.", answer="Radio", explanation="",
        topic="General Knowledge", subtopic="Science", content_hash="old2",
    )
    q_b64 = Question(
        id="q-b64-1", document_id=doc.id, slide_id="sl-5", answer_slide_id=None,
        question_text="Radio waves pioneered by J.C. Bose?", answer="data:image/jpeg;base64,AAAA",
        explanation="", topic="General Knowledge", subtopic="Physics", content_hash="old3",
    )
    db.add_all([q_real, q_orphan, q_b64])
    db.flush()

    db.add(QshalaTag(id="tag-1", question_id="q-real-1", source="LLM", status="confirmed", topic_l1="Science", sensitivity="NONE"))
    db.add(QshalaTag(id="tag-orphan", question_id="q-orphan-bumper", source="RULE", status="draft", topic_l1="Science", sensitivity="NONE"))
    db.add(QshalaQuestionMetadata(question_id="q-real-1", language="English"))
    db.add(QshalaQuestionMetadata(question_id="q-orphan-bumper", language="English"))

    # Simulate RetrievalSource pointing at orphan question (must be SET NULL cleanly without IntegrityError)
    qz = Quiz(id="qz-1", title="Test", topic="Science")
    db.add(qz)
    db.flush()
    gq = GeneratedQuestion(id="gq-1", quiz_id="qz-1", order_index=0, question_text="x", answer="y")
    db.add(gq)
    db.flush()
    db.add(RetrievalSource(id="rs-1", generated_question_id="gq-1", historical_question_id="q-orphan-bumper"))
    db.commit()

    res1 = repair_broken_deck_extractions(db)
    assert res1["repaired_docs"] == 1
    assert res1["removed_orphans"] >= 1

    db.expire_all()
    fixed_real = db.query(Question).filter(Question.id == "q-real-1").one()
    assert fixed_real.answer == "Pineapple"
    # Existing confirmed tag must survive intact
    assert db.query(QshalaTag).filter(QshalaTag.question_id == "q-real-1").count() == 1
    assert db.query(Question).filter(Question.id == "q-orphan-bumper").first() is None
    assert db.query(RetrievalSource).filter(RetrievalSource.id == "rs-1").one().historical_question_id is None

    fixed_b64 = db.query(Question).filter(Question.id == "q-b64-1").one()
    assert fixed_b64.answer == "Radio Transmission"
    assert not fixed_b64.answer.startswith("data:image/")

    # Second run must be completely idempotent (0 work)
    res2 = repair_broken_deck_extractions(db)
    assert res2["repaired_docs"] == 0

    # Standalone repair_kannada_only_answers test
    doc2 = Document(id="doc-kan-only", filename="k.pptx", title="K", year=2026, file_type="pptx", storage_path="/tmp/k.pptx", processing_status="COMPLETED")
    db.add(doc2)
    db.flush()
    ks = Slide(id="sl-k-ans", document_id=doc2.id, slide_number=2, slide_type="ANSWER", extracted_text="ಪೊಟ್ಯಾಸಿಯಮ್\nPotassium", speaker_notes="", image_paths=[], has_images=False)
    db.add(ks)
    db.flush()
    kq = Question(id="q-kan-1", document_id=doc2.id, answer_slide_id="sl-k-ans", question_text="Which element symbol is K?", answer="ಪೊಟ್ಯಾಸಿಯಮ್", topic="Science", content_hash="hk")
    db.add(kq)
    db.commit()

    kres = repair_kannada_only_answers(db)
    assert kres["repaired"] == 1
    assert db.query(Question).filter(Question.id == "q-kan-1").one().answer == "Potassium"


# ---------------------------------------------------------------------------
# 4. Prompt Quiz Retrieval Tests (Tester Scenarios)
# ---------------------------------------------------------------------------

def _seed_retrieval_bank(db):
    from backend.app.models.document import Document
    from backend.app.models.question import Question
    from backend.app.models.qshala_tag import QshalaQuestionMetadata, QshalaTag

    ag_doc1 = Document(id="d-ag1", filename="Agastya Regionals Set 1 Finals 2026.pptx", title="Agastya Regionals Set 1 Finals 2026", year=2026, file_type="pptx", storage_path="/tmp/1", processing_status="COMPLETED")
    ag_doc2 = Document(id="d-ag2", filename="Agastya Regionals Set 2 Prelims 2026.pptx", title="Agastya Regionals Set 2 Prelims 2026", year=2026, file_type="pptx", storage_path="/tmp/2", processing_status="COMPLETED")
    zvq_doc = Document(id="d-zvq", filename="ZVQ Finals 2025.pptx", title="ZVQ Finals 2025", year=2025, file_type="pptx", storage_path="/tmp/3", processing_status="COMPLETED")
    db.add_all([ag_doc1, ag_doc2, zvq_doc])
    db.flush()

    rows = [
        # (id, doc, q_text, ans, topic, subtopic, geo, diff, diff_score, kws, aud_suit)
        ("q-irr", "d-ag1", "Which everyday Indian science experiment demonstrates drip irrigation for saving water in farms?", "Drip Irrigation", "Technology", "Agriculture", "IN", "Easy", 0.2, ["irrigation", "everyday", "science", "experiment", "water"], ["primary", "middle_school"]),
        ("q-ref", "d-ag2", "A pencil appears bent in a glass of water during an everyday science experiment. What phenomenon explains this?", "Refraction", "General Knowledge", "Science", "NA", "Easy", 0.25, ["science", "experiment", "refraction", "light", "water"], ["middle_school"]),
        ("q-pop", "d-ag2", "Which quick-setting gypsum compound is used to cast fractured bones and moulds?", "Plaster of Paris", "General Knowledge", "Science", "INTL", "Easy", 0.3, ["chemistry", "science", "experiment", "gypsum"], ["middle_school"]),
        ("q-pot", "d-ag1", "Why does a small piece of this reactive metal catch fire in a water science experiment? Name the element.", "Potassium", "General Knowledge", "Science", "NA", "Medium", 0.45, ["science", "experiment", "chemistry", "water"], ["middle_school"]),
        ("q-isro", "d-ag1", "Which Indian lunar science experiment landed near the Moon's south pole in 2023?", "Chandrayaan-3", "General Knowledge", "Space and Astronomy", "IN", "Easy", 0.2, ["isro", "science", "experiment", "moon", "india"], ["middle_school"]),
        ("q-fric", "d-ag2", "In this everyday science experiment on motion, what force slows a rolling marble across sand?", "Friction", "General Knowledge", "Science", "NA", "Easy", 0.2, ["physics", "science", "experiment", "force"], ["middle_school"]),
        ("q-lit", "d-ag1", "Why does turmeric turn reddish-brown with soap in a kitchen science experiment?", "Natural pH indicator", "General Knowledge", "Science", "IN", "Easy", 0.25, ["kitchen", "everyday", "science", "experiment", "acid", "base"], ["middle_school"]),
        ("q-sol", "d-ag2", "Which everyday Indian solar experiment cooks food using a concave mirrorbox?", "Solar Cooker", "Technology", "Energy", "IN", "Easy", 0.25, ["solar", "everyday", "science", "experiment", "india"], ["middle_school"]),
        ("q-pho", "d-ag1", "Which plant process converts sunlight into glucose in a classic leaf starch science experiment?", "Photosynthesis", "General Knowledge", "Biology", "NA", "Easy", 0.3, ["biology", "science", "experiment", "plant"], ["middle_school"]),
        ("q-mag", "d-ag2", "Why does a freely suspended magnet align north-south in an everyday Earth science experiment?", "Earth Magnetism", "General Knowledge", "Science", "NA", "Easy", 0.3, ["magnet", "science", "experiment", "physics"], ["middle_school"]),
        ("q-raman", "d-zvq", "Which Indian physicist won the 1930 Nobel Prize for an experiment scattering light in liquids?", "C. V. Raman", "General Knowledge", "Science", "IN", "Easy", 0.25, ["raman", "science", "experiment", "india", "physics"], ["middle_school"]),
        # Non-STEM / distraction items that MUST NOT leak on "Focus on India"
        ("q-bank", "d-zvq", "The Bank was founded on 2 June 1806 in Calcutta as one of India's earliest state banks.", "Bank of Bengal", "Finance", "Banks and Banking", "IN", "Medium", 0.5, ["india", "calcutta", "bank", "1806", "state", "one"], ["high_school"]),
        ("q-const", "d-zvq", "Which article of the Constitution of India establishes the Finance Commission for states?", "Article 280", "Politics", "Governance and Constitution", "IN", "Medium", 0.5, ["india", "constitution", "state", "government"], ["college"]),
        ("q-glogo", "d-zvq", "The logo of which organization controlled and managed by the Government of India features a Lion Capital?", "SEBI / Government Emblem", "Business", "Brands", "IN", "Medium", 0.5, ["india", "government", "logo", "state"], ["college"]),
    ]

    for idx, (qid, did, qt, ans, top, sub, geo, diff, dscore, kws, aud) in enumerate(rows, 1):
        db.add(Question(
            id=qid, document_id=did, question_text=qt, answer=ans, topic=top, subtopic=sub,
            tags=kws, difficulty=diff, difficulty_score=dscore, audience_suitability=aud,
            content_hash=f"h-{idx}", temporal_nature="EVERGREEN",
        ))
        db.add(QshalaTag(
            id=f"t-{idx}", question_id=qid, source="LLM", status="confirmed",
            topic_l1=top, subtopic_l2=sub, keywords_l3=kws, india_international=geo,
            shelf_life="EVERGREEN", sensitivity="NONE",
        ))
        db.add(QshalaQuestionMetadata(
            question_id=qid, primary_audience="School" if did.startswith("d-ag") else "College",
            language="English", india_international=geo,
        ))
    db.commit()


def test_prompt_quiz_tester_scenarios_without_llm(monkeypatch):
    monkeypatch.setenv("ENABLE_INTENT_ENGINE", "false")
    monkeypatch.setenv("ENABLE_AI_CURATOR", "false")
    import backend.app.services.prompt_quiz.interpreter_llm as interp_mod
    monkeypatch.setattr(interp_mod, "interpret_with_gemini", lambda *a, **kw: None)
    from backend.app.services.prompt_quiz.engine import (
        build_draft,
        guardian,
        Brief,
        _normalize_answer_entity,
    )

    # 1. Placeholder normalization must return "" so distinct placeholder rows never collapse
    assert _normalize_answer_entity("ಉತ್ತರ!") == ""
    assert _normalize_answer_entity("Answer!") == ""
    assert _normalize_answer_entity("See image") == ""
    assert _normalize_answer_entity("data:image/jpeg;base64,/9j/4AAQ") == ""

    hits = [
        {"id": "x1", "question_text": "What solar eclipse visual question is shown first?", "answer": "ಉತ್ತರ!"},
        {"id": "x2", "question_text": "Which comet orbits near Jupiter with green glow?", "answer": "ಉತ್ತರ!"},
        {"id": "x3", "question_text": "Identify the constellation containing Polaris.", "answer": "See image"},
        {"id": "x4", "question_text": "What optical illusion uses curved mirrors?", "answer": "See image"},
    ]
    kept = guardian(hits, Brief(prompt="science", query="science", count=10))
    assert len(kept) == 4

    db = _make_memory_db()
    _seed_retrieval_bank(db)

    # 2. Prompt 1: Grade 6 & 7 Karnataka state board everyday science & experiments
    res1 = build_draft(
        db,
        "give me 10 questions for grade 6 & 7 karnataka state board students relevant, with everyday science and science experiments around one.",
    )
    ids1 = {q["id"] for q in res1["questions"]}
    # Must NOT exclude universal NA/INTL science questions just because Karnataka was mentioned
    assert len(ids1) == 10
    assert {"q-bank", "q-const", "q-glogo"}.isdisjoint(ids1)
    assert "q-ref" in ids1 and "q-fric" in ids1 and "q-mag" in ids1
    # Verify INTL questions are not hard-excluded either (available when count=12)
    res1_all = build_draft(
        db,
        "give me 12 questions for grade 6 & 7 karnataka state board students relevant, with everyday science and science experiments.",
    )
    assert "q-pop" in {q["id"] for q in res1_all["questions"]}

    # 3. Click 'India Focus' ("Focus on India"): Bank 1806 / Constitution / Govt Logo must NEVER sneak in
    res1_in = build_draft(
        db,
        "Focus on India",
        previous_brief=res1["brief"],
        current_questions=res1["questions"],
    )
    ids_in = {q["id"] for q in res1_in["questions"]}
    assert {"q-bank", "q-const", "q-glogo"}.isdisjoint(ids_in)
    assert {"q-irr", "q-isro", "q-lit", "q-sol"}.issubset(ids_in)

    # 4. Prompt 2: "Agastya quizkarnataka ..." -> deck_filter = ['agastya'], all results from Agastya
    res2 = build_draft(
        db,
        "give me 10 questions for Agastya quizkarnataka state board students relevant, with everyday science and science experiments around one.",
    )
    assert "agastya" in (res2["brief"]["filters"].get("deck_filter") or [])
    assert len(res2["questions"]) == 10
    assert all(q["document_id"] in ("d-ag1", "d-ag2") for q in res2["questions"])
    assert {"q-bank", "q-const", "q-glogo", "q-raman"}.isdisjoint({q["id"] for q in res2["questions"]})

    # 5. Click 'School friendly' ("Make it for middle school kids"): Q1 must NOT be dropped
    first_q_id = res2["questions"][0]["id"]
    res2_school = build_draft(
        db,
        "Make it for middle school kids",
        previous_brief=res2["brief"],
        current_questions=res2["questions"],
    )
    school_ids = [q["id"] for q in res2_school["questions"]]
    assert first_q_id in school_ids
    assert school_ids[0] == first_q_id


def test_prompt_quiz_gemini_interpreter_subtopic_expansion_and_india_flag(monkeypatch):
    monkeypatch.setenv("ENABLE_INTENT_ENGINE", "false")
    monkeypatch.setenv("ENABLE_AI_CURATOR", "false")
    import backend.app.services.prompt_quiz.interpreter_llm as interp_mod
    from backend.app.services.prompt_quiz.engine import build_draft

    def _fake_gem(prompt, previous_brief=None, current_questions=None):
        p = (prompt or "").lower()
        if "focus on india" in p:
            return {
                "count": 10,
                "query": "Focus on India",
                "topics_l1": [],
                "subtopics_l2": [],
                "india_focused": True,
                "framing": "Indian science focus",
            }
        return {
            "count": 10,
            "query": "everyday science experiments",
            "topics_l1": ["Science"],
            "subtopics_l2": ["Everyday Science", "Physics", "Chemistry", "Biology"],
            "search_terms": ["science experiments", "everyday science", "physics", "chemistry", "biology"],
            "audience": "school",
            "school_safe": True,
            "evergreen": True,
            "hard_bias": "easy",
            "locality": "Karnataka",
            "india_focused": True,
            "framing": "Science experiments for grade 6 & 7 Karnataka students",
        }

    monkeypatch.setattr(interp_mod, "interpret_with_gemini", _fake_gem)

    db = _make_memory_db()
    _seed_retrieval_bank(db)

    res = build_draft(
        db,
        "give me 10 questions for grade 6 & 7 karnataka state board students relevant, with everyday science and science experiments around one.",
    )
    ids = {q["id"] for q in res["questions"]}
    assert len(ids) == 10
    assert {"q-bank", "q-const", "q-glogo"}.isdisjoint(ids)
    assert "q-ref" in ids and "q-fric" in ids and "q-mag" in ids

    res_in = build_draft(
        db,
        "Focus on India",
        previous_brief=res["brief"],
        current_questions=res["questions"],
    )
    ids_in = {q["id"] for q in res_in["questions"]}
    assert {"q-bank", "q-const", "q-glogo"}.isdisjoint(ids_in)
    assert {"q-irr", "q-isro", "q-lit", "q-sol"}.issubset(ids_in)


def test_agastya_finals_list_it_image_reveal_example_skip_and_connect_dedup():
    """Verify Set-1/Set-2 Finals extraction: empty-notes List-It, Round 2 subtopics,
    Image Reveal (+20/-15 & grid digits & 'Nobel prize winner'), Example! skip,
    generic Connect prompt no-collapse dedup, and Tie Breakers round reset."""
    from backend.app.services.ingestion.slide_classifier import SlideClassifier
    from backend.app.services.ingestion.question_extractor import QuestionExtractor
    from backend.app.services.ingestion.deck_metadata import annotate_questions

    slides = [
        {"id": "s1", "slide_number": 1, "title": "Round 1: List it!", "extracted_text": "Round 1: List it!\n8 Questions", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s2", "slide_number": 2, "title": "Simple Machines", "extracted_text": "Simple Machines\n| | |\n| | |\nSpeedometer\nPulley\nLever\nScrew\nWheel and Axle\nBattery\nWedge\nInclined plane\nPendulum", "speaker_notes": "", "image_paths": ["i1.png", "i2.png", "i3.png", "i4.png"], "metadata_json": {}},
        {"id": "s3", "slide_number": 3, "title": "Round 2: Topic", "extracted_text": "Round 2: Topic\n10 topics. Teams to choose", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s4", "slide_number": 4, "title": "Biology: Q1", "extracted_text": "Biology: Q1\nIn his 1875 publication, which trickster plant shares its name with a planet?", "speaker_notes": "", "image_paths": ["q_bio.png"], "metadata_json": {}},
        {"id": "s5", "slide_number": 5, "title": "?????!\nAnswer!", "extracted_text": "?????!\nAnswer!", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s6", "slide_number": 6, "title": "Venus Flytrap", "extracted_text": "Venus Flytrap\nBack", "speaker_notes": "", "image_paths": ["a_bio.png"], "metadata_json": {}},
        {"id": "s7", "slide_number": 7, "title": "Question for Audience", "extracted_text": "Question for Audience", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s8", "slide_number": 8, "title": "Bird", "extracted_text": "Bird named after the sound it makes when feeding", "speaker_notes": "", "image_paths": ["q_aud.png"], "metadata_json": {}},
        {"id": "s9", "slide_number": 9, "title": "?????!\nAnswer!", "extracted_text": "?????!\nAnswer!", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s10", "slide_number": 10, "title": "Woodpecker", "extracted_text": "Woodpecker", "speaker_notes": "", "image_paths": ["a_aud.png"], "metadata_json": {}},
        {"id": "s11", "slide_number": 11, "title": "Round 3: Image Reveal", "extracted_text": "Round 3: Image Reveal\n+20/-15 style visual clues", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s12", "slide_number": 12, "title": "4", "extracted_text": "4\n3\n1\n2\n4. A Nobel prize winner chemist", "speaker_notes": "", "image_paths": ["ir1.png", "ir2.png"], "metadata_json": {}},
        {"id": "s13", "slide_number": 13, "title": "?????!\nAnswer!", "extracted_text": "?????!\nAnswer!", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s14", "slide_number": 14, "title": "Marie Curie", "extracted_text": "Marie Curie", "speaker_notes": "", "image_paths": ["ir_ans.png"], "metadata_json": {}},
        {"id": "s15", "slide_number": 15, "title": "Round 4: Connect", "extracted_text": "Round 4: Connect\nAll elements have a common link", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s16", "slide_number": 16, "title": "Example!", "extracted_text": "Example!", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s17", "slide_number": 17, "title": "Connect", "extracted_text": "Connect", "speaker_notes": "Nitrogen Oxygen cylinder", "image_paths": ["ex1.png", "ex2.png", "ex3.png", "ex4.png"], "metadata_json": {}},
        {"id": "s18", "slide_number": 18, "title": "?????!\nAnswer!", "extracted_text": "?????!\nAnswer!", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s19", "slide_number": 19, "title": "Composition of Air", "extracted_text": "Composition of Air", "speaker_notes": "", "image_paths": ["ex_ans.png"], "metadata_json": {}},
        {"id": "s20", "slide_number": 20, "title": "1. Connect", "extracted_text": "1. Connect", "speaker_notes": "Clue set 1", "image_paths": ["c1_1.png", "c1_2.png", "c1_3.png", "c1_4.png"], "metadata_json": {}},
        {"id": "s21", "slide_number": 21, "title": "?????!\nAnswer!", "extracted_text": "?????!\nAnswer!", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s22", "slide_number": 22, "title": "Cartilage", "extracted_text": "Cartilage", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s23", "slide_number": 23, "title": "2.Connect", "extracted_text": "2.Connect", "speaker_notes": "Clue set 2", "image_paths": ["c2_1.png", "c2_2.png", "c2_3.png", "c2_4.png", "c2_5.png", "c2_6.png"], "metadata_json": {}},
        {"id": "s24", "slide_number": 24, "title": "?????!\nAnswer!", "extracted_text": "?????!\nAnswer!", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s25", "slide_number": 25, "title": "Natural Electricity", "extracted_text": "Natural Electricity", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s26", "slide_number": 26, "title": "Tie Breakers Questions", "extracted_text": "Tie Breakers Questions", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s27", "slide_number": 27, "title": "1.", "extracted_text": "1. Melatonin is produced in the pineal gland.", "speaker_notes": "", "image_paths": ["tb1.png"], "metadata_json": {}},
        {"id": "s28", "slide_number": 28, "title": "?????!\nAnswer!", "extracted_text": "?????!\nAnswer!", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
        {"id": "s29", "slide_number": 29, "title": "Sleep", "extracted_text": "Sleep", "speaker_notes": "", "image_paths": [], "metadata_json": {}},
    ]

    clf = SlideClassifier()
    ext = QuestionExtractor()
    for s, st in zip(slides, clf.classify_deck(slides)):
        s["slide_type"] = st

    assert slides[1]["slide_type"] == "QUESTION"  # Empty-notes pipe-table List-It

    extracted = ext.extract_from_slides(slides, "Agastya Regionals Set 1 Finals", 2026, use_structured=False)
    annotate_questions(extracted, slides)

    answers = [q["answer"] for q in extracted]
    assert "Composition of Air" not in answers  # Example! skipped even though classified ANNOUNCEMENT
    assert answers == [
        "Select matching items from the 9 options on slide (answer key not in slide notes)",
        "Venus Flytrap",
        "Woodpecker",
        "Marie Curie",
        "Cartilage",
        "Natural Electricity",
        "Sleep",
    ]

    q_list, q_bio, q_aud, q_ir, q_c1, q_c2, q_tb = extracted
    assert q_list["_meta"]["question_format"] == "LIST_IT"
    assert q_list["options"] and len(q_list["options"]) == 9
    assert q_bio["subtopic"] == "Biology" and q_bio["_meta"]["round_number"] == 2
    assert q_aud["_meta"]["round_number"] == 2  # Inline singular 'Question for Audience' preserves Round 2
    assert q_ir["_meta"]["question_format"] == "IMAGE_REVEAL"
    assert q_ir["_meta"]["round_number"] == 3  # 'Nobel prize winner' must NOT reset round
    assert "4 3 1 2" not in q_ir["question_text"]
    assert q_c1["_meta"]["question_format"] == "CONNECT_4" and q_c2["_meta"]["question_format"] == "CONNECT_4"
    assert len(q_c2["question_images"]) == 6
    assert q_tb["_meta"]["round_number"] is None
    assert q_tb["subtopic"] == "Tie Breakers"


def test_heal_specialized_preserves_specific_connect_prompts_and_six_images():
    """Verify heal_specialized_questions keeps rich question prompts intact and stores all 6 Connect images."""
    from backend.app.models.document import Document
    from backend.app.models.question import Question
    from backend.app.services.ingestion.heal_specialized import heal_specialized_questions

    db = _make_memory_db()
    doc = Document(id="d-h1", filename="Finals.pptx", title="Finals", storage_path="/tmp/f.pptx", file_type="pptx", file_size_bytes=100, slide_count=10, question_count=2, processing_status="COMPLETED", year=2026)
    db.add(doc)
    specific_prompt = "Due to extreme heat in the US, which plant with leaves modified into spines adapts to heat?"
    q_spec = Question(
        id="q-c-spec", content_hash="h1", document_id="d-h1",
        question_text=specific_prompt, answer="Cactus",
        question_format="CONNECT_4", question_type="CONNECT_4",
        question_images=["img1.png"], image_refs=["img1.png"],
        topic="Science", subtopic="Plants", topics=["Science"], tags=[],
    )
    q_six = Question(
        id="q-c-six", content_hash="h2", document_id="d-h1",
        question_text="Look at the 4 elements. What is the common link?", answer="Elephant",
        question_format="CONNECT_4", question_type="CONNECT_4",
        question_images=[f"e{i}.png" for i in range(1, 7)],
        image_refs=[f"e{i}.png" for i in range(1, 7)],
        topic="Science", subtopic="Animals and Wildlife", topics=["Science"], tags=[],
    )
    db.add_all([q_spec, q_six])
    db.commit()

    heal_specialized_questions(db)
    db.refresh(q_spec)
    db.refresh(q_six)
    assert specific_prompt in q_spec.question_text
    assert "Look at the 4 elements" not in q_spec.question_text
    assert len(q_six.format_data["elements"]) == 6
    assert [el["image_url"] for el in q_six.format_data["elements"]] == [f"e{i}.png" for i in range(1, 7)]


def test_science_vs_finance_taxonomy_precision():
    """Verify scientific terms never misroute into Finance, Derivatives, Tech Inventions, or Biology."""
    from backend.app.services.tagging.qshala_rules import classify_taxonomy

    cases = [
        ("Which trickster plant shares its name with a planet in the solar system?", "Venus Flytrap", "Biology", "Science"),
        ("Scientists developed materials containing gold particles to remove unwanted heat energy.", "Heat Energy", "", "Science"),
        ("Tennis balls are used to make safe nests away from predators for a tiny rodent.", "Mice", "", "Science"),
        ("Earth's rotation around Earth's axis deflects wind in an atmospheric pressure cyclone.", "Cyclone", "Physics", "Science"),
        ("Solar energy imagines a future with green technology and renewable energy.", "Solar Energy", "", "Science"),
        ("A security program designed to remove viruses, Trojans, and spyware from your computer.", "What is Anti-virus software?", "", "Technology"),
    ]
    for q_text, ans_text, rnd, expected_l1 in cases:
        l1, _l2, _ = classify_taxonomy(q_text, ans_text, rnd, "QA")
        assert l1 == expected_l1, f"Expected {expected_l1} for {ans_text!r}, got {l1}"


def test_repair_broken_deck_extractions_end_to_end_and_idempotent():
    """Verify repair_broken_deck_extractions fixes bumpers/Back/Stepped-in-IR/missing List-It,
    syncs QshalaQuestionMetadata & QshalaTag columns, and is strictly idempotent on pass 2."""
    from backend.app.models.document import Document, Slide
    from backend.app.models.question import Question
    from backend.app.models.qshala_tag import QshalaQuestionMetadata, QshalaTag
    from backend.app.services.ingestion.repair_bilingual import repair_broken_deck_extractions

    db = _make_memory_db()
    doc = Document(
        id="doc-rep", filename="Agastya Regionals Set 1 Finals 2026.pptx",
        title="Agastya Regionals Set 1 Finals", storage_path="/tmp/a.pptx",
        file_type="pptx", file_size_bytes=100, slide_count=7, question_count=3,
        processing_status="COMPLETED", year=2026,
    )
    db.add(doc)
    slides = [
        Slide(id="sl-1", document_id="doc-rep", slide_number=1, slide_type="SECTION_MARKER", title="Round 1: List-It!", extracted_text="Round 1: List-It!\n8 Questions", image_paths=[]),
        Slide(id="sl-2", document_id="doc-rep", slide_number=2, slide_type="ANSWER", title="Simple Machines", extracted_text="Simple Machines\n| | |\nSpeedometer\nPulley\nLever\nScrew\nWheel and Axle\nBattery\nWedge\nInclined plane\nPendulum", image_paths=["a1.png", "a2.png", "a3.png", "a4.png"]),
        Slide(id="sl-3", document_id="doc-rep", slide_number=3, slide_type="SECTION_MARKER", title="Round 3: Image Reveal", extracted_text="Round 3: Image Reveal", image_paths=[]),
        Slide(id="sl-4", document_id="doc-rep", slide_number=4, slide_type="QUESTION", title="1. Scientific instrument", extracted_text="1. Scientific instrument 1 2 4 3", image_paths=["ir1.png", "ir2.png"]),
        Slide(id="sl-5", document_id="doc-rep", slide_number=5, slide_type="ANSWER", title="?????!\nAnswer!", extracted_text="?????!\nAnswer!", image_paths=[]),
        Slide(id="sl-6", document_id="doc-rep", slide_number=6, slide_type="QUESTION", title="Mercury Barometer", extracted_text="Mercury Barometer\nBack", image_paths=["ir_ans.png"]),
        Slide(id="sl-7", document_id="doc-rep", slide_number=7, slide_type="QUESTION", title="Ready for Q1.", extracted_text="Ready for Q1.", image_paths=[]),
    ]
    db.add_all(slides)
    q_broken_ir = Question(
        id="q-ir-old", content_hash="h-old1", document_id="doc-rep", slide_id="sl-4",
        question_text="Clue 1 (+20/-15): ...", answer="Answer!",
        question_format="STEPPED_CLUE", question_type="STEPPED_CLUE",
        topic="General Knowledge", subtopic="Image Reveal", topics=["General Knowledge"], tags=[],
    )
    q_leaked_back = Question(
        id="q-back-old", content_hash="h-old2", document_id="doc-rep", slide_id="sl-6",
        question_text="Mercury Barometer\nBack", answer="See explanation",
        question_format="QA", question_type="QA",
        topic="General Knowledge", subtopic="Image Reveal", topics=["General Knowledge"], tags=[],
    )
    q_bumper = Question(
        id="q-bmp-old", content_hash="h-old3", document_id="doc-rep", slide_id="sl-7",
        question_text="Ready for Q1.", answer="data:image/png;base64,AAA",
        question_format="QA", question_type="QA",
        topic="General Knowledge", subtopic="General Trivia", topics=["General Knowledge"], tags=[],
    )
    db.add_all([q_broken_ir, q_leaked_back, q_bumper])
    db.add(QshalaTag(question_id="q-ir-old", source="RULE", status="confirmed", topic_l1="General Knowledge", subtopic_l2="Image Reveal", keywords_l3=[]))
    db.commit()

    res1 = repair_broken_deck_extractions(db)
    assert res1["repaired_docs"] == 1
    assert res1["rescued_questions"] >= 1
    assert res1["removed_orphans"] >= 2

    repaired_qs = db.query(Question).filter(Question.document_id == "doc-rep").order_by(Question.source_slide_range).all()
    assert len(repaired_qs) == 2
    assert {q.question_format for q in repaired_qs} == {"LIST_IT", "IMAGE_REVEAL"}
    assert all(q.topic == "Science" for q in repaired_qs)
    ir_q = next(q for q in repaired_qs if q.question_format == "IMAGE_REVEAL")
    assert ir_q.answer == "Mercury Barometer"
    qt = db.query(QshalaTag).filter(QshalaTag.question_id == ir_q.id).first()
    assert qt is not None and qt.topic_l1 == "Science"
    assert db.query(QshalaQuestionMetadata).filter(QshalaQuestionMetadata.question_id == ir_q.id).count() == 1

    res2 = repair_broken_deck_extractions(db)
    assert res2 == {
        "repaired_docs": 0,
        "updated_questions": 0,
        "rescued_questions": 0,
        "removed_orphans": 0,
        "kannada_fallback": {"repaired": 0, "sanitized": 0},
    }


def test_slide_roles_kannada_answer_marker_and_audience_not_connect():
    """Verify slide_roles.analyze_deck handles Kannada 'ಉತ್ತರ!\\nAnswer!', strips base64 URIs,
    skips Agastya bumpers/winners, and keeps Question-for-Audience out of forced CONNECT."""
    from backend.app.services.ingestion.slide_roles import analyze_deck
    from backend.app.services.ingestion.format_extractors import (
        FMT_CONNECT,
        extract_deck,
    )
    from backend.app.services.ingestion.question_extractor import QuestionExtractor

    slides = [
        {"slide_number": 1, "title": "ಸುಸ್ವಾಗತ \nWelcome!", "extracted_text": "ಸುಸ್ವಾಗತ \nWelcome!\nAgastya Quiz", "speaker_notes": "", "image_paths": []},
        {"slide_number": 2, "title": "Round 4: Connect", "extracted_text": "Round 4: Connect\nFind the link", "speaker_notes": "", "image_paths": []},
        {
            "slide_number": 3,
            "title": "1. Connect",
            "extracted_text": "1. Connect",
            "speaker_notes": "Clues <data:image/jpeg;base64,/9j/4AAQSkZJRgABAQ> linked to photosynthesis",
            "image_paths": ["c1.png", "c2.png", "c3.png", "c4.png"],
            "shapes": [
                {"type": "TEXT_BOX", "text": "1. Connect", "left": 50, "top": 20, "width": 500, "height": 50},
                {"type": "PICTURE", "media_url": "c1.png", "left": 50, "top": 100, "width": 200, "height": 150},
                {"type": "PICTURE", "media_url": "c2.png", "left": 300, "top": 100, "width": 200, "height": 150},
                {"type": "PICTURE", "media_url": "c3.png", "left": 50, "top": 280, "width": 200, "height": 150},
                {"type": "PICTURE", "media_url": "c4.png", "left": 300, "top": 280, "width": 200, "height": 150},
            ],
        },
        {"slide_number": 4, "title": "ಉತ್ತರ!\nAnswer!", "extracted_text": "ಉತ್ತರ!\nAnswer!", "speaker_notes": "", "image_paths": []},
        {"slide_number": 5, "title": "Chlorophyll", "extracted_text": "Chlorophyll", "speaker_notes": "<data:image/png;base64,AAA>", "image_paths": ["ans1.png"]},
        {"slide_number": 6, "title": "Question for Audience", "extracted_text": "Question for Audience", "speaker_notes": "", "image_paths": []},
        {
            "slide_number": 7,
            "title": "",
            "extracted_text": "Due to extreme heat, this plant's leaves modified into spines. Identify it.",
            "speaker_notes": "",
            "image_paths": ["cac.png"],
            "shapes": [
                {"type": "TEXT_BOX", "text": "Due to extreme heat, this plant's leaves modified into spines. Identify it.", "left": 40, "top": 60, "width": 700, "height": 120},
                {"type": "PICTURE", "media_url": "cac.png", "left": 200, "top": 220, "width": 300, "height": 200},
            ],
        },
        {"slide_number": 8, "title": "ಉತ್ತರ!\nAnswer!", "extracted_text": "ಉತ್ತರ!\nAnswer!", "speaker_notes": "", "image_paths": []},
        {"slide_number": 9, "title": "Cactus", "extracted_text": "Cactus", "speaker_notes": "", "image_paths": ["cac_ans.png"]},
        {"slide_number": 10, "title": "ಅಂಕಗಳು \nSCORES", "extracted_text": "ಅಂಕಗಳು \nSCORES", "speaker_notes": "", "image_paths": []},
        {"slide_number": 11, "title": "WINNERS!", "extracted_text": "WINNERS!", "speaker_notes": "", "image_paths": []},
    ]
    deck = analyze_deck(slides)
    assert len(deck.units) == 2

    u_connect, u_aud = deck.units
    assert u_connect.content_slides == [3] and u_connect.answer_slide == 5
    assert u_aud.content_slides == [7] and u_aud.answer_slide == 9
    assert u_aud.play_mode == "AUDIENCE"

    extracted = extract_deck(slides, deck, QuestionExtractor())
    assert len(extracted) == 2
    q_conn, q_aud = extracted
    assert q_conn["question_format"] == FMT_CONNECT
    assert "data:image/" not in (q_conn.get("explanation") or "")
    assert q_aud["question_format"] != FMT_CONNECT
    assert q_aud["answer"] == "Cactus"
    assert q_aud["play_mode"] == "AUDIENCE"


def test_repair_salvages_video_media_and_strips_furniture_audio():
    """Verify repair_broken_deck_extractions rescues answer-slide videos (Slide 43 -> answer_videos),
    promotes question videos to VIDEO_CLUE, and strips >=3-slide furniture audio & timer videos."""
    from backend.app.models.document import Document, Slide
    from backend.app.models.question import Question
    from backend.app.services.ingestion.repair_bilingual import repair_broken_deck_extractions

    db = _make_memory_db()
    doc = Document(
        id="doc-vid", filename="Agastya Regionals Set 2 Prelims 2026.pptx",
        title="Agastya Regionals Set 2 Prelims", storage_path="/tmp/s2p.pptx",
        file_type="pptx", file_size_bytes=100, slide_count=9, question_count=4,
        processing_status="COMPLETED", year=2026,
    )
    db.add(doc)
    slides = [
        Slide(id="vs-1", document_id="doc-vid", slide_number=1, slide_type="QUESTION", title="10.", extracted_text="10. NGC 2264 is nicknamed Christmas Tree Cluster by NASA.", image_paths=["ngc.png"]),
        Slide(id="vs-2", document_id="doc-vid", slide_number=2, slide_type="ANSWER", title="ಉತ್ತರ!\nAnswer!", extracted_text="ಉತ್ತರ!\nAnswer!", image_paths=[]),
        Slide(id="vs-3", document_id="doc-vid", slide_number=3, slide_type="ANSWER", title="Christmas Tree Cluster Nebula", extracted_text="Christmas Tree Cluster Nebula", image_paths=[]),
        Slide(id="vs-4", document_id="doc-vid", slide_number=4, slide_type="QUESTION", title="13.", extracted_text="13. Shankhapushpa petals contain anthocyanin pigments.", image_paths=["flower.png"]),
        Slide(id="vs-5", document_id="doc-vid", slide_number=5, slide_type="ANSWER", title="ಉತ್ತರ!\nAnswer!", extracted_text="ಉತ್ತರ!\nAnswer!", image_paths=[]),
        Slide(id="vs-6", document_id="doc-vid", slide_number=6, slide_type="QUESTION", title="Natural pH indicator", extracted_text="Natural pH indicator", image_paths=[]),
        Slide(id="vs-7", document_id="doc-vid", slide_number=7, slide_type="QUESTION", title="14.", extracted_text="14. Which gas turns limewater milky?", image_paths=[]),
        Slide(id="vs-8", document_id="doc-vid", slide_number=8, slide_type="ANSWER", title="ಉತ್ತರ!\nAnswer!", extracted_text="ಉತ್ತರ!\nAnswer!", image_paths=[]),
        Slide(id="vs-9", document_id="doc-vid", slide_number=9, slide_type="ANSWER", title="Carbon dioxide", extracted_text="Carbon dioxide", image_paths=[]),
    ]
    db.add_all(slides)

    # Simulate live Neon state: broken Kannada answers, orphan question on answer slide vs-6 with media5.mp4,
    # question video media3.mp4 on vs-1, timer video, and furniture audio1.wav reused across 3 slides.
    q1 = Question(
        id="vq-1", content_hash="vh1", document_id="doc-vid", slide_id="vs-1",
        question_text="NGC 2264 is nicknamed Christmas Tree Cluster by NASA.", answer="ಉತ್ತರ!",
        question_format="VIDEO_CLUE", question_type="VIDEO_CLUE",
        question_images=["ngc.png"], image_refs=["ngc.png"],
        question_videos=["https://cdn.test/media3.mp4", "https://cdn.test/1min_timer.mp4"],
        video_refs=["https://cdn.test/media3.mp4", "https://cdn.test/1min_timer.mp4"],
        audio_refs=["https://cdn.test/audio1.wav"],
        question_audio=["https://cdn.test/audio1.wav"],
        topic="Science", subtopic="Astronomy", topics=["Science"], tags=[],
    )
    q2 = Question(
        id="vq-2", content_hash="vh2", document_id="doc-vid", slide_id="vs-4",
        question_text="Shankhapushpa petals contain anthocyanin pigments.", answer="ಉತ್ತರ!",
        question_format="VISUAL_CLUE", question_type="VISUAL_CLUE",
        question_images=["flower.png"], image_refs=["flower.png"],
        audio_refs=["https://cdn.test/audio1.wav"],
        question_audio=["https://cdn.test/audio1.wav"],
        topic="Science", subtopic="Biology", topics=["Science"], tags=[],
    )
    q_orphan_ans = Question(
        id="vq-orphan", content_hash="vh3", document_id="doc-vid", slide_id="vs-6",
        question_text="Natural pH indicator", answer="See explanation",
        question_format="VIDEO_CLUE", question_type="VIDEO_CLUE",
        question_videos=["https://cdn.test/media5.mp4"],
        video_refs=["https://cdn.test/media5.mp4"],
        topic="Science", subtopic="Chemistry", topics=["Science"], tags=[],
    )
    q3 = Question(
        id="vq-3", content_hash="vh4", document_id="doc-vid", slide_id="vs-7",
        question_text="Which gas turns limewater milky?", answer="ಉತ್ತರ!",
        question_format="QA", question_type="QA",
        audio_refs=["https://cdn.test/audio1.wav"],
        question_audio=["https://cdn.test/audio1.wav"],
        topic="Science", subtopic="Chemistry", topics=["Science"], tags=[],
    )
    db.add_all([q1, q2, q_orphan_ans, q3])
    db.commit()

    res1 = repair_broken_deck_extractions(db)
    assert res1["repaired_docs"] == 1
    assert res1["removed_orphans"] >= 1

    qs = db.query(Question).filter(Question.document_id == "doc-vid").order_by(Question.source_slide_range).all()
    assert len(qs) == 3
    by_ans = {q.answer: q for q in qs}
    assert "Christmas Tree Cluster Nebula" in by_ans
    assert "Natural pH indicator" in by_ans
    assert "Carbon dioxide" in by_ans

    ngc_q = by_ans["Christmas Tree Cluster Nebula"]
    assert ngc_q.question_format == "VIDEO_CLUE"
    assert ngc_q.question_videos == ["https://cdn.test/media3.mp4"]
    assert ngc_q.audio_refs == []
    assert ngc_q.slot == "LIVE"
    assert ngc_q.format_needs_review is False

    flower_q = by_ans["Natural pH indicator"]
    assert flower_q.question_videos == []
    assert flower_q.answer_videos == ["https://cdn.test/media5.mp4"]
    assert flower_q.video_refs == ["https://cdn.test/media5.mp4"]
    assert flower_q.question_format != "VIDEO_CLUE"

    co2_q = by_ans["Carbon dioxide"]
    assert co2_q.audio_refs == []
    assert co2_q.question_format != "AUDIO"

    res2 = repair_broken_deck_extractions(db)
    assert res2 == {
        "repaired_docs": 0,
        "updated_questions": 0,
        "rescued_questions": 0,
        "removed_orphans": 0,
        "kannada_fallback": {"repaired": 0, "sanitized": 0},
    }
