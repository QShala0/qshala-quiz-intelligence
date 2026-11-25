import pytest
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.app.database import Base
from backend.app.models.document import Document
from backend.app.models.question import Question
from backend.app.services.deduplication.deduplicator import Deduplicator
from backend.app.routers.questions import resolve_question_duplicate
from backend.app.schemas.question import DuplicateResolveRequest

@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSession()
    try:
        # Create a test document
        doc = Document(
            id="doc_test_100",
            filename="Test_Quiz.pptx",
            title="Test Quiz Presentation",
            file_type="pptx",
            storage_path="/tmp/test.pptx"
        )
        db.add(doc)
        db.commit()
        yield db
    finally:
        db.close()

def generate_normalized_vector(base_seed: int, noise_scale: float = 0.0, dim: int = 768):
    np.random.seed(base_seed)
    v = np.random.randn(dim)
    if noise_scale > 0:
        np.random.seed(base_seed + 999)
        noise = np.random.randn(dim) * noise_scale
        v = v + noise
    norm = np.linalg.norm(v)
    return (v / norm).tolist()

def test_tier1_exact_hash_bypass_and_provenance(test_db):
    """
    Tier 1 tests:
    - Normalized text hash matches identical or punctuation-varied questions O(1).
    - Updates provenance and occurrence_count on existing Question.
    - Skips Tier 2 vector computation.
    """
    dedup = Deduplicator()
    doc_id = "doc_test_100"

    existing_q = Question(
        id="q_existing_001",
        document_id=doc_id,
        question_text="What is the capital city of Australia?",
        answer="Canberra",
        content_hash=Deduplicator.compute_hash("What is the capital city of Australia?", "Canberra"),
        topic="Geography",
        occurrence_count=1,
        provenance_decks=[{"document_id": doc_id, "slide_id": "slide_1"}]
    )
    test_db.add(existing_q)
    test_db.commit()

    # Candidate with slight punctuation / case variance (same canonical hash)
    candidate = {
        "question_text": "What is the capital city of Australia!?",
        "answer": "canberra.",
        "primary_topic": "Geography",
        "slide_id": "slide_new"
    }

    final_candidates, exact_duplicates, _ = dedup.deduplicate_batch(
        db=test_db,
        candidates=[candidate],
        candidate_embeddings=[generate_normalized_vector(42)],
        doc_id="doc_second_tournament"
    )

    # Candidate was caught by Tier 1 exact hash
    assert len(exact_duplicates) == 1
    assert exact_duplicates[0]["duplicate_type"] == "EXACT_HASH"
    assert exact_duplicates[0]["similarity"] == 1.0
    assert exact_duplicates[0]["matched_id"] == "q_existing_001"
    assert len(final_candidates) == 0

    # Provenance and occurrences updated
    test_db.refresh(existing_q)
    assert existing_q.occurrence_count == 2
    prov_doc_ids = [p["document_id"] for p in existing_q.provenance_decks]
    assert "doc_second_tournament" in prov_doc_ids

def test_tier2_threshold_flagging_possible_duplicate(test_db):
    """
    Tier 2 tests:
    - Candidate with cosine similarity >= 0.92 is FLAGGED as 'POSSIBLE_DUPLICATE'.
    - Crucially: NOT auto-rejected or discarded; retained in final candidates for UI human review.
    - Candidate with similarity < 0.92 is flagged as 'UNIQUE'.
    """
    dedup = Deduplicator()
    doc_id = "doc_test_100"

    # Seed base vector
    base_vector = generate_normalized_vector(101, noise_scale=0.0)

    existing_q = Question(
        id="q_existing_vector",
        document_id=doc_id,
        question_text="Which Australian marsupial subsists primarily on eucalyptus leaves?",
        answer="Koala",
        content_hash=Deduplicator.compute_hash("Which Australian marsupial subsists primarily on eucalyptus leaves?", "Koala"),
        topic="Science & Nature",
        embedding=base_vector
    )
    test_db.add(existing_q)
    test_db.commit()

    # Vector 1: Near duplicate vector (slight noise -> cosine similarity ~0.95 >= 0.92)
    near_dup_vector = generate_normalized_vector(101, noise_scale=0.30)
    sim_high = float(np.dot(base_vector, near_dup_vector))
    assert sim_high >= 0.92, f"Expected high similarity >= 0.92, got {sim_high}"

    candidate_high = {
        "question_text": "Name the native Australian marsupial that feeds almost exclusively on eucalyptus.",
        "answer": "The Koala bear",
        "primary_topic": "Science & Nature",
        "slide_id": "slide_2"
    }

    # Vector 2: Different question vector (noise_scale 1.5 -> cosine similarity ~0.70 < 0.92)
    diff_vector = generate_normalized_vector(101, noise_scale=1.5)
    sim_low = float(np.dot(base_vector, diff_vector))
    assert sim_low < 0.92, f"Expected low similarity < 0.92, got {sim_low}"

    candidate_low = {
        "question_text": "What is the largest living reptile found in Northern Australia?",
        "answer": "Saltwater Crocodile",
        "primary_topic": "Science & Nature",
        "slide_id": "slide_3"
    }

    final_candidates, exact_duplicates, _ = dedup.deduplicate_batch(
        db=test_db,
        candidates=[candidate_high, candidate_low],
        candidate_embeddings=[near_dup_vector, diff_vector],
        doc_id="doc_second_tournament",
        threshold=0.92
    )

    assert len(exact_duplicates) == 0
    # BOTH candidates must be retained for storage/insertion!
    assert len(final_candidates) == 2

    # High similarity candidate must be flagged as POSSIBLE_DUPLICATE
    c_high_res = next(c for c in final_candidates if "Koala" in c["answer"])
    assert c_high_res["duplicate_status"] == "POSSIBLE_DUPLICATE"
    assert c_high_res["duplicate_of_id"] == "q_existing_vector"
    assert c_high_res["duplicate_similarity"] >= 0.92

    # Low similarity candidate must be flagged as UNIQUE
    c_low_res = next(c for c in final_candidates if "Crocodile" in c["answer"])
    assert c_low_res["duplicate_status"] == "UNIQUE"
    assert c_low_res["duplicate_of_id"] is None

def test_duplicate_resolution_workflow(test_db):
    """
    Tests human review resolution actions:
    1. CONFIRM_DUPLICATE: changes status to CONFIRMED_DUPLICATE and updates origin provenance.
    2. DISMISS_UNIQUE: changes status to RESOLVED.
    """
    doc_id = "doc_test_100"

    orig_q = Question(
        id="orig_q_01",
        document_id=doc_id,
        question_text="Who was the first Prime Minister of Australia?",
        answer="Sir Edmund Barton",
        topic="Australian History",
        occurrence_count=1,
        provenance_decks=[{"document_id": doc_id, "slide_id": "s1"}]
    )
    dup_candidate = Question(
        id="dup_candidate_01",
        document_id=doc_id,
        question_text="Name the first Australian Prime Minister who took office in 1901.",
        answer="Edmund Barton",
        topic="Australian History",
        duplicate_status="POSSIBLE_DUPLICATE",
        duplicate_similarity=0.945,
        duplicate_of_id="orig_q_01"
    )
    test_db.add_all([orig_q, dup_candidate])
    test_db.commit()

    # Action 1: Confirm duplicate (merges candidate into existing question and cleans up candidate)
    req_confirm = DuplicateResolveRequest(action="CONFIRM_DUPLICATE")
    res1 = resolve_question_duplicate(dup_candidate.id, req_confirm, test_db)

    assert res1.id == orig_q.id
    test_db.refresh(orig_q)
    assert orig_q.occurrence_count == 2
    assert test_db.query(Question).filter(Question.id == dup_candidate.id).first() is None

    # Action 2: Dismiss as unique
    dup_candidate_2 = Question(
        id="dup_candidate_02",
        document_id=doc_id,
        question_text="Name another Prime Minister.",
        answer="Barton",
        topic="Australian History",
        duplicate_status="POSSIBLE_DUPLICATE",
        duplicate_similarity=0.93,
        duplicate_of_id="orig_q_01"
    )
    test_db.add(dup_candidate_2)
    test_db.commit()

    req_dismiss = DuplicateResolveRequest(action="DISMISS_UNIQUE")
    res2 = resolve_question_duplicate(dup_candidate_2.id, req_dismiss, test_db)

    assert res2.duplicate_status == "RESOLVED"

def test_intra_batch_duplicate_vector_matching(test_db):
    """
    Tests intra-batch near-duplicate detection:
    - When two questions in the SAME incoming batch exceed the 0.92 semantic threshold,
      the second question is flagged as POSSIBLE_DUPLICATE.
    - Crucially: duplicate_of_id must be the valid UUID of the first candidate (not a raw hash),
      ensuring foreign key integrity and UI side-by-side comparison support.
    """
    dedup = Deduplicator()
    v1 = generate_normalized_vector(202, noise_scale=0.0)
    v2 = generate_normalized_vector(202, noise_scale=0.25) # similarity ~0.96 >= 0.92

    cand1 = {
        "question_text": "What is the largest living bird species in the world?",
        "answer": "Common Ostrich",
        "primary_topic": "Science & Nature"
    }
    cand2 = {
        "question_text": "Which flightless bird native to Africa is the world's largest living bird?",
        "answer": "Ostrich",
        "primary_topic": "Science & Nature"
    }

    final_candidates, exact_duplicates, _ = dedup.deduplicate_batch(
        db=test_db,
        candidates=[cand1, cand2],
        candidate_embeddings=[v1, v2],
        doc_id="doc_intra_batch_test",
        threshold=0.92
    )

    assert len(final_candidates) == 2
    assert final_candidates[0]["duplicate_status"] == "UNIQUE"
    assert final_candidates[1]["duplicate_status"] == "POSSIBLE_DUPLICATE"
    assert final_candidates[1]["duplicate_of_id"] == final_candidates[0]["id"]
    assert len(final_candidates[1]["duplicate_of_id"]) == 36 # Valid UUID length, not 64-char hash
    assert final_candidates[1]["duplicate_similarity"] >= 0.92


def test_sparse_symbolic_and_disjoint_answers_not_flagged(test_db):
    """
    Guards against false positives on sparse symbolic/math slides and disjoint answers:
    - Candidate '_ _ _ _ L+2 L-2 A+1 F+2' (Answer: SKIN) matched against an existing visual
      slide with empty question text and answer 'Zohran Mamdani'.
      Even with high vector similarity, this must NOT be flagged as duplicate.
    - Candidate '_ _ _ _ R+1 G+2 P-4 C-2' (Answer: CUBE) matched against a Google question.
      Must NOT be flagged as duplicate.
    """
    dedup = Deduplicator()
    doc_id = "doc_test_100"
    v = generate_normalized_vector(303, noise_scale=0.0)

    # Existing question 1: visual slide with no question text and unrelated answer
    existing_visual = Question(
        id="q_visual_001",
        document_id=doc_id,
        question_text="",
        answer="Zohran Mamdani - New York City's mayor",
        content_hash="dummy_hash_001",
        topic="General Knowledge",
        embedding=v
    )
    # Existing question 2: Google products question
    existing_google = Question(
        id="q_google_002",
        document_id=doc_id,
        question_text="Which of these are products owned by Google?",
        answer="Which of these logos belong to products owned by Google?",
        content_hash="dummy_hash_002",
        topic="Technology",
        embedding=v
    )
    test_db.add_all([existing_visual, existing_google])
    test_db.commit()

    cand_skin = {
        "question_text": "_ _ _ _ L+2 L-2 A+1 F+2",
        "answer": "SKIN",
        "primary_topic": "Mathematics"
    }
    cand_cube = {
        "question_text": "_ _ _ _ R+1 G+2 P-4 C-2",
        "answer": "CUBE",
        "primary_topic": "Mathematics"
    }

    final_candidates, _, _ = dedup.deduplicate_batch(
        db=test_db,
        candidates=[cand_skin, cand_cube],
        candidate_embeddings=[v, v],
        doc_id="doc_math_deck",
        threshold=0.85 # Even with a low threshold!
    )

    assert len(final_candidates) == 2
    # Both must be UNIQUE!
    assert final_candidates[0]["duplicate_status"] == "UNIQUE"
    assert final_candidates[0]["duplicate_of_id"] is None
    assert final_candidates[1]["duplicate_status"] == "UNIQUE"
    assert final_candidates[1]["duplicate_of_id"] is None
