import os
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app.database import SessionLocal, Base, engine
from backend.app.main import app
from backend.app.models.sharepoint_index import (
    SharePointIndexItem,
    SharePointIndexState,
    SharePointHiddenFolder,
)
from backend.app.services.sharepoint import relevance
from backend.app.services.sharepoint import index as sp_index

client = TestClient(app)


@pytest.fixture()
def db():
    Base.metadata.create_all(bind=engine)
    s = SessionLocal()
    prefix = f"reltest-{uuid.uuid4().hex[:8]}"
    s._test_prefix = prefix
    s._test_index_ids = []
    s._test_drive_ids = []
    s._test_hidden_folder_ids = []

    try:
        yield s
    finally:
        s.rollback()
        if s._test_hidden_folder_ids:
            s.query(SharePointHiddenFolder).filter(
                SharePointHiddenFolder.folder_id.in_(s._test_hidden_folder_ids)
            ).delete(synchronize_session=False)
        if s._test_index_ids:
            s.query(SharePointIndexItem).filter(
                SharePointIndexItem.id.in_(s._test_index_ids)
            ).delete(synchronize_session=False)
        if s._test_drive_ids:
            s.query(SharePointIndexState).filter(
                SharePointIndexState.drive_id.in_(s._test_drive_ids)
            ).delete(synchronize_session=False)
            s.query(SharePointIndexItem).filter(
                SharePointIndexItem.drive_id.in_(s._test_drive_ids)
            ).delete(synchronize_session=False)

        # Safety prefix cleanup
        pfx = f"{prefix}%"
        s.query(SharePointHiddenFolder).filter(
            SharePointHiddenFolder.folder_id.like(pfx) | SharePointHiddenFolder.drive_id.like(pfx)
        ).delete(synchronize_session=False)
        s.query(SharePointIndexItem).filter(
            SharePointIndexItem.id.like(pfx) | SharePointIndexItem.drive_id.like(pfx)
        ).delete(synchronize_session=False)
        s.query(SharePointIndexState).filter(
            SharePointIndexState.drive_id.like(pfx)
        ).delete(synchronize_session=False)
        s.commit()
        s.close()


def test_relevance_cbse_pdf_and_other_pdf_excluded_when_not_include_pdf():
    # 1. CBSE PDF
    bucket = relevance.classify_file(
        name="Grade 10 Math.pdf",
        ext=".pdf",
        size=50000,
        folder_path="QShala School Program/CBSE Books/Grade 10",
        ancestor_folder_ids=[],
        folders_with_ingested_decks=set(),
        hidden_folder_ids=set(),
        include_pdf=False,
    )
    assert bucket == relevance.BUCKET_EXCLUDED

    # 2. Standard PDF with include_pdf=False -> excluded
    bucket_std_pdf = relevance.classify_file(
        name="School_Handout.pdf",
        ext=".pdf",
        size=50000,
        folder_path="QShala School Program/Handouts",
        ancestor_folder_ids=[],
        folders_with_ingested_decks=set(),
        hidden_folder_ids=set(),
        include_pdf=False,
    )
    assert bucket_std_pdf == relevance.BUCKET_EXCLUDED

    # 3. Standard PDF with include_pdf=True -> other (no quiz words)
    bucket_std_pdf_inc = relevance.classify_file(
        name="School_Handout.pdf",
        ext=".pdf",
        size=50000,
        folder_path="QShala School Program/Handouts",
        ancestor_folder_ids=[],
        folders_with_ingested_decks=set(),
        hidden_folder_ids=set(),
        include_pdf=True,
    )
    assert bucket_std_pdf_inc == relevance.BUCKET_OTHER

    # 4. CBSE PDF with include_pdf=True -> still excluded by exclude words (cbse, books)
    bucket_cbse_inc = relevance.classify_file(
        name="Grade 10 Math.pdf",
        ext=".pdf",
        size=50000,
        folder_path="QShala School Program/CBSE Books/Grade 10",
        ancestor_folder_ids=[],
        folders_with_ingested_decks=set(),
        hidden_folder_ids=set(),
        include_pdf=True,
    )
    assert bucket_cbse_inc == relevance.BUCKET_EXCLUDED


def test_relevance_temp_and_lock_files_excluded():
    bucket = relevance.classify_file(
        name="~$Agastya Quiz.pptx",
        ext=".pptx",
        size=50000,
        folder_path="Quizzes",
        ancestor_folder_ids=[],
        folders_with_ingested_decks=set(),
        hidden_folder_ids=set(),
    )
    assert bucket == relevance.BUCKET_EXCLUDED

    bucket_lock = relevance.classify_file(
        name=".~lock.presentation.pptx#",
        ext=".pptx",
        size=50000,
        folder_path="Quizzes",
        ancestor_folder_ids=[],
        folders_with_ingested_decks=set(),
        hidden_folder_ids=set(),
    )
    assert bucket_lock == relevance.BUCKET_EXCLUDED


def test_relevance_tiny_files_excluded():
    # 10 KB file (< 30 KB)
    bucket_tiny = relevance.classify_file(
        name="Quiz Finals 2026.pptx",
        ext=".pptx",
        size=10 * 1024,
        folder_path="Quizzes",
        ancestor_folder_ids=[],
        folders_with_ingested_decks=set(),
        hidden_folder_ids=set(),
    )
    assert bucket_tiny == relevance.BUCKET_EXCLUDED

    # 50 KB file (>= 30 KB)
    bucket_normal = relevance.classify_file(
        name="Quiz Finals 2026.pptx",
        ext=".pptx",
        size=50 * 1024,
        folder_path="Quizzes",
        ancestor_folder_ids=[],
        folders_with_ingested_decks=set(),
        hidden_folder_ids=set(),
    )
    assert bucket_normal == relevance.BUCKET_LIKELY


def test_relevance_agastya_regionals_set_5_finals_2026():
    bucket = relevance.classify_file(
        name="Agastya Regionals Set 5 Finals 2026.pptx",
        ext=".pptx",
        size=50000,
        folder_path="Quizzes/Agastya",
        ancestor_folder_ids=[],
        folders_with_ingested_decks=set(),
        hidden_folder_ids=set(),
    )
    assert bucket == relevance.BUCKET_LIKELY


def test_relevance_working_doc_and_exclude_words():
    # Exclude phrase "working doc"
    bucket = relevance.classify_file(
        name="Mint Money Festival Nov 28_Working Doc.pptx",
        ext=".pptx",
        size=50000,
        folder_path="Events/Mint",
        ancestor_folder_ids=[],
        folders_with_ingested_decks=set(),
        hidden_folder_ids=set(),
    )
    assert bucket == relevance.BUCKET_EXCLUDED

    # Exclude word in folder path e.g. "marketing"
    bucket_folder = relevance.classify_file(
        name="Festival Deck.pptx",
        ext=".pptx",
        size=50000,
        folder_path="Company/Marketing/Collaterals",
        ancestor_folder_ids=[],
        folders_with_ingested_decks=set(),
        hidden_folder_ids=set(),
    )
    assert bucket_folder == relevance.BUCKET_EXCLUDED


def test_relevance_folder_affinity():
    # Deck has no quiz keywords in its name or path
    # Parent folder f1 has an ingested deck
    bucket_direct = relevance.classify_file(
        name="Science Topic Review.pptx",
        ext=".pptx",
        size=50000,
        folder_path="School Program/Grade 7",
        ancestor_folder_ids=["f1"],
        folders_with_ingested_decks={"f1"},
        hidden_folder_ids=set(),
    )
    assert bucket_direct == relevance.BUCKET_LIKELY

    # Grandparent folder f0 has an ingested deck (within 2 levels)
    bucket_grandparent = relevance.classify_file(
        name="Science Topic Review.pptx",
        ext=".pptx",
        size=50000,
        folder_path="School Program/Grade 7/Term 1",
        ancestor_folder_ids=["f1", "f0"],
        folders_with_ingested_decks={"f0"},
        hidden_folder_ids=set(),
    )
    assert bucket_grandparent == relevance.BUCKET_LIKELY

    # Ancestor > 2 levels away does not count
    bucket_far = relevance.classify_file(
        name="Science Topic Review.pptx",
        ext=".pptx",
        size=50000,
        folder_path="School Program/Grade 7/Term 1/Sub",
        ancestor_folder_ids=["f2", "f1", "f0"],
        folders_with_ingested_decks={"f0"},
        hidden_folder_ids=set(),
    )
    assert bucket_far == relevance.BUCKET_OTHER


def test_relevance_no_signal_no_affinity_is_other():
    bucket = relevance.classify_file(
        name="Annual Overview.pptx",
        ext=".pptx",
        size=50000,
        folder_path="Operations/General",
        ancestor_folder_ids=["f_ops"],
        folders_with_ingested_decks=set(),
        hidden_folder_ids=set(),
    )
    assert bucket == relevance.BUCKET_OTHER


def test_relevance_hidden_folder_excludes_children():
    bucket = relevance.classify_file(
        name="Agastya Finals Quiz.pptx",
        ext=".pptx",
        size=50000,
        folder_path="Quizzes/Agastya",
        ancestor_folder_ids=["f_agastya", "f_quizzes"],
        folders_with_ingested_decks=set(),
        hidden_folder_ids={"f_agastya"},
    )
    assert bucket == relevance.BUCKET_EXCLUDED


def test_relevance_custom_exclude_words_env(monkeypatch):
    monkeypatch.setenv("SHAREPOINT_EXCLUDE_WORDS", "junktest, custombad")
    bucket = relevance.classify_file(
        name="junktest_overview.pptx",
        ext=".pptx",
        size=50000,
        folder_path="Quizzes",
        ancestor_folder_ids=[],
        folders_with_ingested_decks=set(),
        hidden_folder_ids=set(),
    )
    assert bucket == relevance.BUCKET_EXCLUDED


# ==========================================
# Endpoint Tests
# ==========================================

def test_endpoint_not_imported_classification_and_filters(db):
    prefix = db._test_prefix
    drive_id = f"{prefix}-drive"
    db._test_drive_ids.append(drive_id)

    # Folders
    f_root = SharePointIndexItem(
        id=f"{prefix}-f-root",
        drive_id=drive_id,
        parent_id=None,
        name="Quizzes",
        is_folder=True,
    )
    f_sub = SharePointIndexItem(
        id=f"{prefix}-f-sub",
        drive_id=drive_id,
        parent_id=f_root.id,
        name="2026 Finals",
        is_folder=True,
    )
    f_other = SharePointIndexItem(
        id=f"{prefix}-f-other",
        drive_id=drive_id,
        parent_id=None,
        name="Operations",
        is_folder=True,
    )

    now = datetime.now(timezone.utc)

    # 1. Likely quiz deck (pptx, quiz keyword, > 30KB)
    f_likely = SharePointIndexItem(
        id=f"{prefix}-item-likely",
        drive_id=drive_id,
        parent_id=f_sub.id,
        name=f"{prefix}_Agastya_Regionals_Finals_Quiz.pptx",
        is_folder=False,
        size=55000,
        ext=".pptx",
        modified=now - timedelta(minutes=5),
    )
    # 2. Other PPTX (pptx, no quiz signals, > 30KB, in Operations)
    f_other_deck = SharePointIndexItem(
        id=f"{prefix}-item-other",
        drive_id=drive_id,
        parent_id=f_other.id,
        name=f"{prefix}_General_Business_Presentation.pptx",
        is_folder=False,
        size=55000,
        ext=".pptx",
        modified=now - timedelta(minutes=10),
    )
    # 3. Temp file (excluded)
    f_temp = SharePointIndexItem(
        id=f"{prefix}-item-temp",
        drive_id=drive_id,
        parent_id=f_sub.id,
        name=f"~${prefix}_Quiz_Lock.pptx",
        is_folder=False,
        size=55000,
        ext=".pptx",
        modified=now - timedelta(minutes=15),
    )
    # 4. Tiny file (excluded)
    f_tiny = SharePointIndexItem(
        id=f"{prefix}-item-tiny",
        drive_id=drive_id,
        parent_id=f_sub.id,
        name=f"{prefix}_Tiny_Finals_Quiz.pptx",
        is_folder=False,
        size=5000,
        ext=".pptx",
        modified=now - timedelta(minutes=20),
    )
    # 5. Standard PDF (excluded by default, other when include_pdf=1)
    f_pdf = SharePointIndexItem(
        id=f"{prefix}-item-pdf",
        drive_id=drive_id,
        parent_id=f_other.id,
        name=f"{prefix}_Resource_Guide.pdf",
        is_folder=False,
        size=55000,
        ext=".pdf",
        modified=now - timedelta(minutes=25),
    )

    items = [f_root, f_sub, f_other, f_likely, f_other_deck, f_temp, f_tiny, f_pdf]
    for it in items:
        db.add(it)
        db._test_index_ids.append(it.id)

    # Add index state
    state = SharePointIndexState(
        drive_id=drive_id,
        status="IDLE",
        items_indexed=len(items),
        last_full_at=now,
    )
    db.add(state)
    db.commit()

    # 1. Default request (view=likely, include_pdf=0)
    res = client.get(f"/api/v1/sharepoint/not-imported?drive_id={drive_id}")
    assert res.status_code == 200, res.text
    data = res.json()

    assert "buckets" in data
    # Buckets:
    # likely: f_likely (1)
    # other: f_other_deck (1)
    # excluded: f_temp, f_tiny, f_pdf (3)
    assert data["buckets"]["likely"] == 1
    assert data["buckets"]["other"] == 1
    assert data["buckets"]["excluded"] == 3
    assert data["total"] == 1
    assert len(data["files"]) == 1
    assert data["files"][0]["id"] == f_likely.id
    assert data["files"][0]["bucket"] == "likely"

    # 2. View = other
    res_other = client.get(f"/api/v1/sharepoint/not-imported?drive_id={drive_id}&view=other")
    assert res_other.status_code == 200
    data_other = res_other.json()
    assert data_other["total"] == 1
    assert len(data_other["files"]) == 1
    assert data_other["files"][0]["id"] == f_other_deck.id

    # 3. View = all
    res_all = client.get(f"/api/v1/sharepoint/not-imported?drive_id={drive_id}&view=all")
    assert res_all.status_code == 200
    data_all = res_all.json()
    assert data_all["total"] == 2
    assert len(data_all["files"]) == 2
    file_ids = [f["id"] for f in data_all["files"]]
    assert f_likely.id in file_ids
    assert f_other_deck.id in file_ids

    # 4. include_pdf = 1
    # When include_pdf=1, f_pdf is no longer excluded (it becomes 'other')
    res_pdf = client.get(f"/api/v1/sharepoint/not-imported?drive_id={drive_id}&include_pdf=1&view=other")
    assert res_pdf.status_code == 200
    data_pdf = res_pdf.json()
    assert data_pdf["buckets"]["excluded"] == 2  # temp, tiny
    assert data_pdf["buckets"]["other"] == 2     # f_other_deck, f_pdf
    pdf_ids = [f["id"] for f in data_pdf["files"]]
    assert f_pdf.id in pdf_ids

    # 5. Search query q
    res_q = client.get(f"/api/v1/sharepoint/not-imported?drive_id={drive_id}&view=all&q=Agastya")
    assert res_q.status_code == 200
    data_q = res_q.json()
    assert len(data_q["files"]) == 1
    assert data_q["files"][0]["id"] == f_likely.id

    # 6. Root folder filter
    res_folder = client.get(f"/api/v1/sharepoint/not-imported?drive_id={drive_id}&view=all&folder=Quizzes")
    assert res_folder.status_code == 200
    data_folder = res_folder.json()
    assert len(data_folder["files"]) == 1
    assert data_folder["files"][0]["id"] == f_likely.id

    # 7. Paging and ordering (newest first)
    res_p1 = client.get(f"/api/v1/sharepoint/not-imported?drive_id={drive_id}&view=all&limit=1&offset=0")
    assert res_p1.status_code == 200
    data_p1 = res_p1.json()
    assert len(data_p1["files"]) == 1
    assert data_p1["files"][0]["id"] == f_likely.id  # modified 5 mins ago vs 10 mins ago

    res_p2 = client.get(f"/api/v1/sharepoint/not-imported?drive_id={drive_id}&view=all&limit=1&offset=1")
    assert res_p2.status_code == 200
    data_p2 = res_p2.json()
    assert len(data_p2["files"]) == 1
    assert data_p2["files"][0]["id"] == f_other_deck.id

    # 8. Hide folder escape valve
    hide_res = client.post(
        "/api/v1/sharepoint/not-imported/hide-folder",
        json={"folder_id": f_sub.id, "drive_id": drive_id, "path": "Quizzes/2026 Finals"},
    )
    assert hide_res.status_code == 200
    db._test_hidden_folder_ids.append(f_sub.id)

    # After hiding f_sub, f_likely is excluded!
    res_after_hide = client.get(f"/api/v1/sharepoint/not-imported?drive_id={drive_id}&view=likely")
    assert res_after_hide.status_code == 200
    data_after_hide = res_after_hide.json()
    assert data_after_hide["buckets"]["likely"] == 0
    assert data_after_hide["buckets"]["excluded"] == 4
    assert len(data_after_hide["files"]) == 0

    # 9. Unhide folder
    unhide_res = client.delete(f"/api/v1/sharepoint/not-imported/hide-folder/{f_sub.id}")
    assert unhide_res.status_code == 200
    res_after_unhide = client.get(f"/api/v1/sharepoint/not-imported?drive_id={drive_id}&view=likely")
    assert res_after_unhide.status_code == 200
    data_after_unhide = res_after_unhide.json()
    assert data_after_unhide["buckets"]["likely"] == 1
    assert len(data_after_unhide["files"]) == 1


def test_relevance_cbse_tightened_rules(monkeypatch):
    # 1. a .pptx in QShala School Program/CBSE Books/Grade 7 is excluded
    bucket_cbse_books = relevance.classify_file(
        name="Chapter_4_Notes.pptx",
        ext=".pptx",
        size=50000,
        folder_path="QShala School Program/CBSE Books/Grade 7",
    )
    assert bucket_cbse_books == relevance.BUCKET_EXCLUDED

    # 2. a .pptx in Quizzes/School Quizzes/Kalaanjali CBSE Lit Fest Quiz 2026/Prelims is not excluded (it is likely)
    bucket_lit_fest = relevance.classify_file(
        name="Prelims.pptx",
        ext=".pptx",
        size=50000,
        folder_path="Quizzes/School Quizzes/Kalaanjali CBSE Lit Fest Quiz 2026/Prelims",
    )
    assert bucket_lit_fest == relevance.BUCKET_LIKELY

    # 3. Grade 6 _CBSE_S7 Light, Shadows and Reflections.pptx in Discover - Curriculum/CBSE/Klorofeel is excluded
    bucket_disc_cbse = relevance.classify_file(
        name="Grade 6 _CBSE_S7 Light, Shadows and Reflections.pptx",
        ext=".pptx",
        size=50000,
        folder_path="Discover - Curriculum/CBSE/Klorofeel",
    )
    assert bucket_disc_cbse == relevance.BUCKET_EXCLUDED

    # 4. a file named CBSE Class 10 Quiz Prelims.pptx is not excluded (it is likely)
    bucket_cbse_quiz = relevance.classify_file(
        name="CBSE Class 10 Quiz Prelims.pptx",
        ext=".pptx",
        size=50000,
        folder_path="Quizzes/General",
    )
    assert bucket_cbse_quiz == relevance.BUCKET_LIKELY

    # 5. the env var still adds an exclude word anywhere in the path
    monkeypatch.setenv("SHAREPOINT_EXCLUDE_WORDS", "envjunkword")
    bucket_env = relevance.classify_file(
        name="Normal_Quiz_Finals.pptx",
        ext=".pptx",
        size=50000,
        folder_path="Quizzes/envjunkword/Events",
    )
    assert bucket_env == relevance.BUCKET_EXCLUDED


def test_endpoint_not_imported_modified_filter(db):
    prefix = db._test_prefix
    drive_id = f"{prefix}-drive-mod"
    db._test_drive_ids.append(drive_id)

    f_root = SharePointIndexItem(
        id=f"{prefix}-mod-f-root",
        drive_id=drive_id,
        parent_id=None,
        name="Quizzes",
        is_folder=True,
    )
    db.add(f_root)
    db._test_index_ids.append(f_root.id)

    now = datetime.now(timezone.utc)

    # 1. 5 days ago (in 30d, in 90d, in 1y)
    item_30d = SharePointIndexItem(
        id=f"{prefix}-item-30d",
        drive_id=drive_id,
        parent_id=f_root.id,
        name=f"{prefix}_Recent_Finals_Quiz.pptx",
        is_folder=False,
        size=50000,
        ext=".pptx",
        modified=now - timedelta(days=5),
    )
    # 2. 60 days ago (not in 30d, in 90d, in 1y)
    item_90d = SharePointIndexItem(
        id=f"{prefix}-item-90d",
        drive_id=drive_id,
        parent_id=f_root.id,
        name=f"{prefix}_Quarter_Finals_Quiz.pptx",
        is_folder=False,
        size=50000,
        ext=".pptx",
        modified=now - timedelta(days=60),
    )
    # 3. 200 days ago (not in 30d, not in 90d, in 1y)
    item_1y = SharePointIndexItem(
        id=f"{prefix}-item-1y",
        drive_id=drive_id,
        parent_id=f_root.id,
        name=f"{prefix}_Year_Finals_Quiz.pptx",
        is_folder=False,
        size=50000,
        ext=".pptx",
        modified=now - timedelta(days=200),
    )
    # 4. 500 days ago (more than 365 days ago -> older)
    item_older = SharePointIndexItem(
        id=f"{prefix}-item-older",
        drive_id=drive_id,
        parent_id=f_root.id,
        name=f"{prefix}_Ancient_Finals_Quiz.pptx",
        is_folder=False,
        size=50000,
        ext=".pptx",
        modified=now - timedelta(days=500),
    )
    # 5. Undated (None) -> appears only in all
    item_undated = SharePointIndexItem(
        id=f"{prefix}-item-undated",
        drive_id=drive_id,
        parent_id=f_root.id,
        name=f"{prefix}_Undated_Finals_Quiz.pptx",
        is_folder=False,
        size=50000,
        ext=".pptx",
        modified=None,
    )

    items = [item_30d, item_90d, item_1y, item_older, item_undated]
    for it in items:
        db.add(it)
        db._test_index_ids.append(it.id)

    state = SharePointIndexState(
        drive_id=drive_id,
        status="IDLE",
        items_indexed=len(items) + 1,
        last_full_at=now,
    )
    db.add(state)
    db.commit()

    # Test 30d: only item_30d
    res_30d = client.get(f"/api/v1/sharepoint/not-imported?drive_id={drive_id}&modified=30d&view=all")
    assert res_30d.status_code == 200
    data_30d = res_30d.json()
    assert len(data_30d["files"]) == 1
    assert data_30d["files"][0]["id"] == item_30d.id
    assert data_30d["buckets"]["likely"] == 1

    # Test 90d: item_30d and item_90d
    res_90d = client.get(f"/api/v1/sharepoint/not-imported?drive_id={drive_id}&modified=90d&view=all")
    assert res_90d.status_code == 200
    data_90d = res_90d.json()
    assert len(data_90d["files"]) == 2
    ids_90d = [f["id"] for f in data_90d["files"]]
    assert item_30d.id in ids_90d
    assert item_90d.id in ids_90d
    assert data_90d["buckets"]["likely"] == 2

    # Test 1y: item_30d, item_90d, item_1y
    res_1y = client.get(f"/api/v1/sharepoint/not-imported?drive_id={drive_id}&modified=1y&view=all")
    assert res_1y.status_code == 200
    data_1y = res_1y.json()
    assert len(data_1y["files"]) == 3
    ids_1y = [f["id"] for f in data_1y["files"]]
    assert item_30d.id in ids_1y
    assert item_90d.id in ids_1y
    assert item_1y.id in ids_1y
    assert data_1y["buckets"]["likely"] == 3

    # Test older: only item_older
    res_older = client.get(f"/api/v1/sharepoint/not-imported?drive_id={drive_id}&modified=older&view=all")
    assert res_older.status_code == 200
    data_older = res_older.json()
    assert len(data_older["files"]) == 1
    assert data_older["files"][0]["id"] == item_older.id
    assert data_older["buckets"]["likely"] == 1

    # Test all: all 5 items, including undated
    res_all = client.get(f"/api/v1/sharepoint/not-imported?drive_id={drive_id}&modified=all&view=all")
    assert res_all.status_code == 200
    data_all = res_all.json()
    assert len(data_all["files"]) == 5
    ids_all = [f["id"] for f in data_all["files"]]
    assert item_undated.id in ids_all
    assert data_all["buckets"]["likely"] == 5
