"""Picture editor support: serving a question's own picture to the editor, and cleaning up pictures when a quiz or one
of its questions is deleted."""
import shutil
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.main import app
from backend.app.config import settings
from backend.app.database import SessionLocal
from backend.app.models.document import Document, Slide
from backend.app.models.question import Question
from backend.app.models.quiz import GeneratedQuestion, Quiz
from backend.app.services.storage import r2_storage

client = TestClient(app)
API = "/api/v1"
BASE = "https://cdn.example.test"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x02" * 64
TAG = uuid.uuid4().hex[:8]


class FakeR2:
    public_base_url = BASE

    def __init__(self, objects=None):
        self.objects, self.deleted = dict(objects or {}), []

    def download(self, key):
        if key not in self.objects:
            raise KeyError(key)
        return self.objects[key]

    def delete_object(self, key):
        self.deleted.append(key)


@pytest.fixture()
def no_r2(monkeypatch):
    monkeypatch.setattr(r2_storage, "get_media_storage", lambda: None)


@pytest.fixture()
def made():
    """Rows created by a test, removed afterwards (SQLite does not cascade)."""
    rec = {"docs": [], "quizzes": [], "dirs": []}
    yield rec
    db = SessionLocal()
    for qz in rec["quizzes"]:
        db.query(GeneratedQuestion).filter(GeneratedQuestion.quiz_id == qz).delete()
        db.query(Quiz).filter(Quiz.id == qz).delete()
    for did in rec["docs"]:
        db.query(Question).filter(Question.document_id == did).delete()
        db.query(Slide).filter(Slide.document_id == did).delete()
        db.query(Document).filter(Document.id == did).delete()
    db.commit()
    db.close()
    for d in rec["dirs"]:
        shutil.rmtree(d, ignore_errors=True)


def _question(made, **images):
    db = SessionLocal()
    did, qid = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(Document(id=did, filename="e.pptx", title="e", file_type="pptx", storage_path="x", file_size_bytes=1,
                    processing_status="COMPLETED"))
    db.flush()
    db.add(Question(id=qid, document_id=did, question_text=f"q {uuid.uuid4()}?", answer="a", topic="General",
                    content_hash=uuid.uuid4().hex, **images))
    db.commit()
    db.close()
    made["docs"].append(did)
    return qid


def _local_file(made, qid, name, data=PNG):
    folder = settings.SLIDES_DIR / "questions" / qid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    made["dirs"].append(folder)
    return folder / name, f"/static/slides/questions/{qid}/{name}"


# ------------------------------------------------------------------------------------------------ image source

def test_serves_a_local_picture_that_is_on_the_question(made, no_r2):
    qid = _question(made)
    path, url = _local_file(made, qid, f"{TAG}-pic.png")
    db = SessionLocal()
    db.query(Question).filter(Question.id == qid).update({"question_images": [url]})
    db.commit()
    db.close()
    r = client.get(f"{API}/questions/{qid}/image-source", params={"url": url})
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"


def test_serves_an_r2_picture_through_the_api(made, monkeypatch):
    key = f"questions/{TAG}/r2pic.jpg"
    monkeypatch.setattr(r2_storage, "get_media_storage", lambda: FakeR2({key: JPG}))
    qid = _question(made, answer_images=[f"{BASE}/{key}"])
    r = client.get(f"{API}/questions/{qid}/image-source", params={"url": f"{BASE}/{key}"})
    assert r.status_code == 200 and r.content == JPG and r.headers["content-type"] == "image/jpeg"


def test_serves_a_connect_clue_picture(made, monkeypatch):
    key = f"questions/{TAG}/clue.png"
    monkeypatch.setattr(r2_storage, "get_media_storage", lambda: FakeR2({key: PNG}))
    qid = _question(made, question_format="CONNECT",
                    format_payload={"type": "CONNECT", "items": [{"order": 1, "image": f"{BASE}/{key}"}]})
    assert client.get(f"{API}/questions/{qid}/image-source", params={"url": f"{BASE}/{key}"}).status_code == 200


def test_refuses_a_url_the_question_does_not_list(made, monkeypatch):
    monkeypatch.setattr(r2_storage, "get_media_storage", lambda: FakeR2({"secret.png": PNG}))
    qid = _question(made, question_images=[f"{BASE}/mine.png"])
    r = client.get(f"{API}/questions/{qid}/image-source", params={"url": f"{BASE}/secret.png"})
    assert r.status_code == 400
    r = client.get(f"{API}/questions/{qid}/image-source", params={"url": "http://169.254.169.254/latest"})
    assert r.status_code == 400


def test_unknown_question_is_404():
    assert client.get(f"{API}/questions/{uuid.uuid4()}/image-source", params={"url": "/x.png"}).status_code == 404


def test_a_path_outside_the_media_folders_is_never_read(made, no_r2):
    qid = _question(made, question_images=["/static/slides/../../../etc/passwd"])
    r = client.get(f"{API}/questions/{qid}/image-source", params={"url": "/static/slides/../../../etc/passwd"})
    assert r.status_code == 404


def test_a_file_that_is_not_a_picture_is_refused(made, no_r2):
    qid = _question(made)
    _, url = _local_file(made, qid, f"{TAG}-fake.png", data=b"<html>not an image</html>")
    db = SessionLocal()
    db.query(Question).filter(Question.id == qid).update({"question_images": [url]})
    db.commit()
    db.close()
    assert client.get(f"{API}/questions/{qid}/image-source", params={"url": url}).status_code == 415


def test_a_missing_file_is_404(made, no_r2):
    qid = _question(made, question_images=[f"/static/slides/questions/{TAG}/gone.png"])
    r = client.get(f"{API}/questions/{qid}/image-source", params={"url": f"/static/slides/questions/{TAG}/gone.png"})
    assert r.status_code == 404


# ------------------------------------------------------------------------------------------------ quiz delete cleanup

def _quiz(made, *question_image_lists):
    db = SessionLocal()
    quiz = Quiz(id=str(uuid.uuid4()), title=f"quiz {TAG}", topic="General")
    db.add(quiz)
    db.flush()
    gids = []
    for i, imgs in enumerate(question_image_lists, start=1):
        g = GeneratedQuestion(id=str(uuid.uuid4()), quiz_id=quiz.id, order_index=i, question_text=f"g{i}?", answer="x",
                              question_images=imgs, image_refs=imgs)
        db.add(g)
        gids.append(g.id)
    db.commit()
    qz = quiz.id
    db.close()
    made["quizzes"].append(qz)
    return qz, gids


def test_deleting_a_quiz_removes_pictures_only_it_used_and_keeps_shared_ones(made, no_r2):
    owner = str(uuid.uuid4())
    orphan_path, orphan = _local_file(made, owner, f"{TAG}-orphan.png")
    shared_path, shared = _local_file(made, owner, f"{TAG}-shared.png")
    _question(made, question_images=[shared])                 # a bank question still shows this one
    qz, _ = _quiz(made, [orphan, shared])
    assert client.delete(f"{API}/quizzes/{qz}").status_code == 200
    assert not orphan_path.exists()                           # the quiz was its last user
    assert shared_path.exists()                               # the bank question still needs it


def test_deleting_one_quiz_question_keeps_pictures_its_quiz_still_shows(made, no_r2):
    owner = str(uuid.uuid4())
    path, url = _local_file(made, owner, f"{TAG}-q.png")
    qz, (g1, g2) = _quiz(made, [url], [url])
    assert client.delete(f"{API}/quizzes/questions/{g1}").status_code == 200
    assert path.exists()                                      # the other question in the quiz still shows it
    assert client.delete(f"{API}/quizzes/questions/{g2}").status_code == 200
    assert not path.exists()


def test_deck_media_is_never_deleted_by_a_quiz_delete(made, no_r2):
    deck_dir = settings.SLIDES_DIR / f"deck-{TAG}" / "media"
    deck_dir.mkdir(parents=True, exist_ok=True)
    (deck_dir / "slide.png").write_bytes(PNG)
    made["dirs"].append(deck_dir.parent)
    qz, _ = _quiz(made, [f"/static/slides/deck-{TAG}/media/slide.png"])
    assert client.delete(f"{API}/quizzes/{qz}").status_code == 200
    assert (deck_dir / "slide.png").exists()                  # only the editor's own uploads are ever removed


def test_a_quiz_delete_with_r2_pictures_only_deletes_unused_ones(made, monkeypatch):
    fake = FakeR2()
    monkeypatch.setattr(r2_storage, "get_media_storage", lambda: fake)
    used, unused = f"questions/{TAG}/used.png", f"questions/{TAG}/unused.png"
    _question(made, answer_images=[f"{BASE}/{used}"])
    qz, _ = _quiz(made, [f"{BASE}/{used}", f"{BASE}/{unused}"])
    assert client.delete(f"{API}/quizzes/{qz}").status_code == 200
    assert fake.deleted == [unused]
