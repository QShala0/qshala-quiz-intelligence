"""Edit images on a question: upload, add / remove / reorder through PATCH, deleting unused files, Connect pictures."""
import copy
import json
import shutil
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.main import app
from backend.app.config import settings
from backend.app.database import SessionLocal, engine
from backend.app.models.document import Document, Slide
from backend.app.models.question import Question
from backend.app.models.quiz import GeneratedQuestion, Quiz
from backend.app.routers import questions as questions_router
from backend.app.services import media_cleanup
from backend.app.services.storage import r2_storage

client = TestClient(app)
API = "/api/v1"
BASE = "https://cdn.example.test"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 64
# Unique names per run, so a left-over row from some other test can never mention these files.
TAG = uuid.uuid4().hex[:8]
A_KEY, B_KEY, ANS_KEY, C2_KEY = f"{TAG}-a.png", f"{TAG}-b.png", f"{TAG}-ans.png", f"{TAG}-c2.png"
A, B, ANS = f"{BASE}/{A_KEY}", f"{BASE}/{B_KEY}", f"{BASE}/{ANS_KEY}"
C1, C2, C3 = f"{BASE}/{TAG}-c1.png", f"{BASE}/{C2_KEY}", f"{BASE}/{TAG}-c3.png"


def connect_payload():
    return {"type": "CONNECT",
            "items": [{"order": 1, "image": C1, "note": "keep me"}, {"order": 2, "image": C2}, {"order": 3, "image": C3}],
            "text_hint": "Famous alumni", "item_labels": ["Satya", "Menaka"],
            "scoring": {"correct": 10, "wrong": -5}, "answer": "Hyderabad Public School"}


class FakeR2:
    public_base_url = BASE

    def __init__(self):
        self.uploaded, self.deleted, self.fail_delete = [], [], False

    def upload_bytes(self, key, data, content_type=None):
        self.uploaded.append((key, content_type, len(data)))
        return f"{BASE}/{key}"

    def delete_object(self, key):
        if self.fail_delete:
            raise RuntimeError("R2 is down")
        self.deleted.append(key)


@pytest.fixture()
def fake_r2(monkeypatch):
    fake = FakeR2()
    monkeypatch.setattr(r2_storage, "get_media_storage", lambda: fake)
    return fake


@pytest.fixture()
def make_question():
    created_docs = []

    def factory(connect=False, **extra):
        db = SessionLocal()
        doc_id = str(uuid.uuid4())
        db.add(Document(id=doc_id, filename="img.pptx", title="img", file_type="pptx", storage_path="x",
                        file_size_bytes=1, processing_status="COMPLETED"))
        db.flush()
        if connect:
            fields = dict(question_images=[C1, C2, C3], answer_images=[ANS], image_refs=[C1, C2, C3, ANS],
                          format_payload=connect_payload(), question_format="CONNECT")
        else:
            fields = dict(question_images=[A, B], answer_images=[ANS], image_refs=[A, B, ANS])
        fields.update(extra)
        qid = str(uuid.uuid4())
        db.add(Question(id=qid, document_id=doc_id, question_text=f"q {uuid.uuid4()}?", answer="HPS",
                        topic="General", content_hash=uuid.uuid4().hex, **fields))
        db.commit()
        db.close()
        created_docs.append(doc_id)
        return qid

    yield factory
    db = SessionLocal()
    for did in created_docs:
        # SQLite does not cascade, and a left-over row that mentions a picture would (rightly) keep its file alive
        db.query(Question).filter(Question.document_id == did).delete()
        db.query(Slide).filter(Slide.document_id == did).delete()
        db.query(Document).filter(Document.id == did).delete()
    db.commit()
    db.close()


@pytest.fixture()
def question(make_question):
    return make_question()


def _row(qid):
    db = SessionLocal()
    try:
        q = db.query(Question).filter(Question.id == qid).one()
        return {"q": list(q.question_images or []), "a": list(q.answer_images or []), "refs": list(q.image_refs or []),
                "payload": json.dumps(q.format_payload, sort_keys=True), "text": q.question_text,
                "items": [dict(i) for i in ((q.format_payload or {}).get("items") or [])]}
    finally:
        db.close()


def _patch(qid, **body):
    return client.patch(f"{API}/questions/{qid}", json=body)


# ------------------------------------------------------------------------------------------------ upload

def test_upload_stores_valid_images_and_returns_urls(question, fake_r2):
    r = client.post(f"{API}/questions/{question}/images",
                    files=[("files", ("a.png", PNG, "image/png")), ("files", ("b.jpg", JPG, "image/jpeg")),
                           ("files", ("c.webp", WEBP, "image/webp"))])
    assert r.status_code == 200, r.text
    urls = r.json()["urls"]
    assert len(urls) == 3 and all(u.startswith(f"{BASE}/questions/{question}/") for u in urls)
    assert [u.rsplit(".", 1)[1] for u in urls] == ["png", "jpg", "webp"]
    assert not fake_r2.deleted
    assert _row(question)["q"] == [A, B]                       # upload alone attaches nothing


def test_upload_rejects_bad_type_too_large_unknown_question_and_stores_nothing(question, fake_r2, monkeypatch):
    bad = client.post(f"{API}/questions/{question}/images", files=[("files", ("x.png", b"not an image at all", "image/png"))])
    assert bad.status_code == 400
    mixed = client.post(f"{API}/questions/{question}/images",
                        files=[("files", ("a.png", PNG, "image/png")), ("files", ("x.txt", b"hello", "text/plain"))])
    assert mixed.status_code == 400 and fake_r2.uploaded == []           # one bad file: nothing stored
    monkeypatch.setenv("QUESTION_IMAGE_MAX_BYTES", "100")
    big = client.post(f"{API}/questions/{question}/images", files=[("files", ("big.png", PNG + b"\x00" * 200, "image/png"))])
    assert big.status_code == 413
    assert client.post(f"{API}/questions/{uuid.uuid4()}/images", files=[("files", ("a.png", PNG, "image/png"))]).status_code == 404
    monkeypatch.setenv("QUESTION_IMAGE_MAX_BYTES", str(4 * 1024 * 1024))
    monkeypatch.setenv("QUESTION_IMAGE_MAX_FILES", "1")
    two = client.post(f"{API}/questions/{question}/images",
                      files=[("files", ("a.png", PNG, "image/png")), ("files", ("b.png", PNG, "image/png"))])
    assert two.status_code == 400
    assert fake_r2.uploaded == []


def test_upload_falls_back_to_local_disk_without_r2(question, monkeypatch):
    monkeypatch.setattr(r2_storage, "get_media_storage", lambda: None)
    r = client.post(f"{API}/questions/{question}/images", files=[("files", ("a.png", PNG, "image/png"))])
    assert r.status_code == 200, r.text
    url = r.json()["urls"][0]
    assert url.startswith(f"/static/slides/questions/{question}/")
    folder = settings.SLIDES_DIR / "questions" / question
    try:
        assert (folder / url.rsplit("/", 1)[1]).read_bytes() == PNG
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# ------------------------------------------------------------------------------------------------ PATCH, flat lists

def test_patch_add_remove_reorder_and_keep_catch_all_in_step(question, fake_r2):
    before = _row(question)
    new = f"{BASE}/questions/x/new.png"
    r = _patch(question, question_images=[new, B], answer_images=[ANS, new])   # add one, drop a.png, reorder
    assert r.status_code == 200, r.text
    row = _row(question)
    assert row["q"] == [new, B]
    assert row["a"] == [ANS]                              # one role per picture
    assert row["refs"] == [new, B, ANS]
    assert A not in row["refs"]                           # a removed picture cannot return through image_refs
    body = r.json()
    assert body["question_images"] == row["q"] and body["answer_images"] == row["a"]
    assert row["text"] == before["text"]                  # nothing else changed
    assert fake_r2.deleted == [A_KEY]                     # the dropped picture is deleted from storage too


def test_removing_every_question_picture_leaves_no_ghost_in_image_refs(question, fake_r2):
    r = _patch(question, question_images=[], answer_images=[ANS])
    assert r.status_code == 200
    row = _row(question)
    assert row["q"] == [] and row["a"] == [ANS] and row["refs"] == []
    r = _patch(question, question_images=[], answer_images=[])
    assert _row(question)["refs"] == [] and r.json()["image_refs"] == []
    assert sorted(fake_r2.deleted) == sorted([A_KEY, ANS_KEY, B_KEY])


def test_patch_validates_urls_and_dedupes(question, fake_r2):
    for bad in ("javascript:alert(1)", "ftp://x/y.png", "//evil.example/x.png", "/static/../../etc/passwd", "relative/path.png"):
        assert _patch(question, question_images=[bad]).status_code == 400
    ok = _patch(question, question_images=[f" {A} ", A, "/static/slides/d/media/b.png", ""])
    assert ok.status_code == 200
    assert _row(question)["q"] == [A, "/static/slides/d/media/b.png"]


def test_patch_without_image_fields_leaves_images_alone(question, fake_r2):
    before = _row(question)
    assert _patch(question, difficulty="Hard").status_code == 200
    after = _row(question)
    assert {k: after[k] for k in ("q", "a", "refs", "payload")} == {k: before[k] for k in ("q", "a", "refs", "payload")}
    assert fake_r2.deleted == []


# ------------------------------------------------------------------------------------------------ Connect pictures

def test_clue_images_rewrite_the_numbered_connect_pictures_and_nothing_else(make_question, fake_r2):
    qid = make_question(connect=True)
    before = json.loads(_row(qid)["payload"])
    new = f"{BASE}/questions/{qid}/new.png"
    r = _patch(qid, clue_images=[C3, new, C1], answer_images=[ANS])      # reorder, add one, drop C2
    assert r.status_code == 200, r.text
    row = _row(qid)
    assert [(i["order"], i["image"]) for i in row["items"]] == [(1, C3), (2, new), (3, C1)]
    assert [i for i in row["items"] if i["image"] == C1][0]["note"] == "keep me"      # extra fields of an item survive
    after = json.loads(row["payload"])
    for key in ("type", "text_hint", "item_labels", "scoring", "answer"):             # the rest is untouched
        assert after[key] == before[key]
    assert row["q"] == [C3, new, C1] and row["a"] == [ANS] and row["refs"] == [C3, new, C1, ANS]
    served = r.json()["format_payload"]["items"]
    assert [i["image"] for i in served] == [C3, new, C1]                              # the card shows the new grid
    assert fake_r2.deleted == [C2_KEY]


def test_clue_images_win_over_question_images_and_need_a_connect_question(make_question, fake_r2):
    qid = make_question(connect=True)
    r = _patch(qid, clue_images=[C2], question_images=[A, B])
    assert r.status_code == 200
    assert _row(qid)["q"] == [C2] and [i["image"] for i in _row(qid)["items"]] == [C2]
    plain = make_question()
    before = _row(plain)
    bad = _patch(plain, clue_images=[A])
    assert bad.status_code == 400
    assert _row(plain) == before                                                      # nothing was changed


def test_strip_only_edit_leaves_the_connect_payload_byte_identical(make_question, fake_r2):
    qid = make_question(connect=True)
    before = _row(qid)["payload"]
    _patch(qid, question_images=[f"{BASE}/z.png"], answer_images=[])
    _patch(qid, question_images=[], answer_images=[])
    assert _row(qid)["payload"] == before


# ------------------------------------------------------------------------------------------------ never delete a file still in use

def _referrers(qid):
    """Each way another row can still point at A; every one of them must stop the file being deleted."""
    def other_question(db, did):
        db.add(Question(id=str(uuid.uuid4()), document_id=did, question_text=f"other {uuid.uuid4()}?", answer="x", topic="General",
                        content_hash=uuid.uuid4().hex, question_images=[A]))

    def other_connect(db, did):
        db.add(Question(id=str(uuid.uuid4()), document_id=did, question_text=f"conn {uuid.uuid4()}?", answer="x", topic="General",
                        content_hash=uuid.uuid4().hex, question_format="CONNECT",
                        format_payload={"type": "CONNECT", "items": [{"order": 1, "image": A}]}))

    def saved_quiz(db, did):
        quiz = Quiz(id=str(uuid.uuid4()), title="t", topic="General")
        db.add(quiz)
        db.flush()
        db.add(GeneratedQuestion(id=str(uuid.uuid4()), quiz_id=quiz.id, order_index=1, question_text="g?", answer="x", image_refs=[A]))

    def slide(db, did):
        db.add(Slide(id=str(uuid.uuid4()), document_id=did, slide_number=1, slide_type="QUESTION", image_paths=[A]))

    return {"other question": other_question, "other connect payload": other_connect, "saved quiz": saved_quiz,
            "slide": slide}


@pytest.mark.parametrize("who", ["other question", "other connect payload", "saved quiz", "slide"])
def test_a_file_still_used_elsewhere_is_detached_but_not_deleted(make_question, fake_r2, who):
    qid = make_question()
    db = SessionLocal()
    did = db.query(Question.document_id).filter(Question.id == qid).scalar()
    _referrers(qid)[who](db, did)
    db.commit()
    db.close()
    try:
        assert _patch(qid, question_images=[B], answer_images=[ANS]).status_code == 200
        assert A not in _row(qid)["q"]                      # it is off this question ...
        assert A_KEY not in fake_r2.deleted                 # ... but the file stays, something else uses it
    finally:
        db = SessionLocal()
        db.query(GeneratedQuestion).filter(GeneratedQuestion.question_text == "g?").delete()
        db.query(Quiz).filter(Quiz.title == "t").delete()
        db.commit()
        db.close()


def test_a_file_held_by_a_trash_snapshot_is_kept(question, fake_r2):
    table = f"trash_{uuid.uuid4().hex[:8]}_questions"
    with engine.begin() as conn:
        conn.execute(text(f'CREATE TABLE "{table}" (id TEXT, question_images TEXT)'))
        conn.execute(text(f'INSERT INTO "{table}" VALUES (:i, :j)'), {"i": "t1", "j": json.dumps([A])})
    try:
        assert _patch(question, question_images=[B], answer_images=[ANS]).status_code == 200
        assert fake_r2.deleted == []
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP TABLE "{table}"'))


def test_only_files_the_app_owns_are_deleted(make_question, fake_r2):
    placeholder = f"{BASE}/slides/d/media/slide_41_img_1.png"
    foreign = "https://other.example.com/photo.png"
    nonascii = f"{BASE}/slides/d/media/café.png"
    qid = make_question(question_images=[A, placeholder, foreign, nonascii], answer_images=[],
                        image_refs=[A, placeholder, foreign, nonascii])
    assert _patch(qid, question_images=[], answer_images=[]).status_code == 200
    assert fake_r2.deleted == [A_KEY]                       # not the shared placeholder, not another host, not an unsafe name


def test_the_switch_turns_deleting_off(question, fake_r2, monkeypatch):
    monkeypatch.setenv("DELETE_REMOVED_IMAGES", "0")
    assert _patch(question, question_images=[B], answer_images=[ANS]).status_code == 200
    assert A not in _row(question)["q"] and fake_r2.deleted == []


def test_a_failing_delete_never_fails_the_save(question, fake_r2):
    fake_r2.fail_delete = True
    r = _patch(question, question_images=[B], answer_images=[ANS])
    assert r.status_code == 200
    assert _row(question)["q"] == [B]                       # saved; the file just stays


def test_pictures_the_editor_saved_to_local_disk_are_removed_from_disk_but_deck_media_is_not(make_question, monkeypatch):
    monkeypatch.setattr(r2_storage, "get_media_storage", lambda: None)
    qid = make_question()
    mine = settings.SLIDES_DIR / "questions" / qid / "mine.png"
    deck = settings.SLIDES_DIR / f"deck-{qid}" / "media" / "deck.png"
    for p in (mine, deck):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(PNG)
    try:
        db = SessionLocal()
        db.query(Question).filter(Question.id == qid).update({
            "question_images": [f"/static/slides/questions/{qid}/mine.png", f"/static/slides/deck-{qid}/media/deck.png"],
            "answer_images": [], "image_refs": []})
        db.commit()
        db.close()
        assert _patch(qid, question_images=[], answer_images=[]).status_code == 200
        assert not mine.exists()                            # the editor's own upload is cleaned up
        assert deck.exists()                                # original deck media on disk is never touched
    finally:
        shutil.rmtree(settings.SLIDES_DIR / "questions" / qid, ignore_errors=True)
        shutil.rmtree(settings.SLIDES_DIR / f"deck-{qid}", ignore_errors=True)


# ------------------------------------------------------------------------------------------------ the cleanup helpers

def test_storage_key_is_the_same_for_a_cdn_url_and_a_static_path():
    assert media_cleanup.storage_key(f"{BASE}/slides/d1/media/x.png") == "slides/d1/media/x.png"
    assert media_cleanup.storage_key("/static/slides/d1/media/x.png") == "slides/d1/media/x.png"
    assert media_cleanup.storage_key("/api/v1/static/slides/d1/media/x.png?v=2") == "slides/d1/media/x.png"
    assert media_cleanup.storage_key("") is None


def test_like_wildcards_in_a_file_name_do_not_over_match(make_question, fake_r2):
    lookalike, mine = f"{BASE}/{TAG}-aXb.png", f"{BASE}/{TAG}-a_b.png"
    other = make_question(question_images=[lookalike], answer_images=[], image_refs=[lookalike])
    qid = make_question(question_images=[mine], answer_images=[], image_refs=[mine])
    assert _patch(qid, question_images=[], answer_images=[]).status_code == 200
    assert fake_r2.deleted == [f"{TAG}-a_b.png"]            # "_" is not treated as "any character"
    assert _row(other)["q"] == [lookalike]


def test_a_longer_file_name_that_merely_ends_the_same_does_not_protect_a_file(make_question, fake_r2):
    # "<tag>-a.png" appears inside "xx<tag>-a.png": that is a different file, so it must not keep this one alive
    other = make_question(question_images=[f"{BASE}/xx{A_KEY}"], answer_images=[], image_refs=[f"{BASE}/xx{A_KEY}"])
    qid = make_question(question_images=[A], answer_images=[], image_refs=[A])
    assert _patch(qid, question_images=[], answer_images=[]).status_code == 200
    assert fake_r2.deleted == [A_KEY]
    assert _row(other)["q"] == [f"{BASE}/xx{A_KEY}"]


def test_image_code_never_touches_ai_or_embeddings():
    import ast
    tree = ast.parse(Path(questions_router.__file__).read_text(encoding="utf-8"))
    wanted = {"_clean_image_urls", "_sniff_image", "_max_image_bytes", "upload_question_images", "update_question",
              "_picture_urls", "_set_connect_items"}
    used = set()
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in wanted):
        for n in ast.walk(fn):
            if isinstance(n, ast.Name):
                used.add(n.id.lower())
            elif isinstance(n, ast.Attribute):
                used.add(n.attr.lower())
            elif isinstance(n, ast.ImportFrom):
                used.add((n.module or "").lower())
                used.update(a.name.lower() for a in n.names)
    for word in ("gemini", "embed", "tagger", "llm", "vision"):
        assert not any(word in u for u in used), word
    cleanup = (Path(media_cleanup.__file__)).read_text(encoding="utf-8").lower()
    for word in ("gemini", "embedding", "tagger", "llm"):
        assert word not in cleanup, word
