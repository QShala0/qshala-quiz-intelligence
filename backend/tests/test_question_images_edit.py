"""Edit images on a question: upload, add / remove / reorder through PATCH, no deletion, Connect clues untouched."""
import copy
import json
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
from backend.app.models.document import Document
from backend.app.models.question import Question
from backend.app.routers import questions as questions_router
from backend.app.services.storage import r2_storage

client = TestClient(app)
API = "/api/v1"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 64
CONNECT_PAYLOAD = {"kind": "CONNECT", "clues": [{"label": i, "image": f"https://cdn.example.test/c{i}.png"} for i in range(1, 7)],
                   "common_link": "Hyderabad Public School"}
A, B, ANS = "https://cdn.example.test/a.png", "https://cdn.example.test/b.png", "https://cdn.example.test/ans.png"


class FakeR2:
    def __init__(self):
        self.uploaded, self.deleted = [], []

    def upload_bytes(self, key, data, content_type=None):
        self.uploaded.append((key, content_type, len(data)))
        return f"https://cdn.example.test/{key}"

    def delete(self, keys):
        self.deleted.append(keys)

    def delete_object(self, key):
        self.deleted.append(key)

    def delete_prefix(self, prefix):
        self.deleted.append(prefix)


@pytest.fixture()
def fake_r2(monkeypatch):
    fake = FakeR2()
    monkeypatch.setattr(r2_storage, "get_media_storage", lambda: fake)
    return fake


@pytest.fixture()
def question():
    db = SessionLocal()
    doc = Document(id=str(uuid.uuid4()), filename="img.pptx", title="img", file_type="pptx", storage_path="x",
                   file_size_bytes=1, processing_status="COMPLETED")
    db.add(doc)
    db.flush()
    q = Question(id=str(uuid.uuid4()), document_id=doc.id, question_text=f"connect {uuid.uuid4()}?", answer="HPS",
                 topic="General", content_hash=uuid.uuid4().hex,
                 question_images=[A, B], answer_images=[ANS], image_refs=[A, B, ANS],
                 format_payload=copy.deepcopy(CONNECT_PAYLOAD), question_format="CONNECT")
    db.add(q)
    db.commit()
    qid, did = q.id, doc.id
    db.close()
    yield qid
    db = SessionLocal()
    db.query(Document).filter(Document.id == did).delete()
    db.commit()
    db.close()


def _row(qid):
    db = SessionLocal()
    try:
        q = db.query(Question).filter(Question.id == qid).one()
        return {"q": list(q.question_images or []), "a": list(q.answer_images or []), "refs": list(q.image_refs or []),
                "payload": json.dumps(q.format_payload, sort_keys=True), "text": q.question_text}
    finally:
        db.close()


def test_upload_stores_valid_images_and_returns_urls(question, fake_r2):
    r = client.post(f"{API}/questions/{question}/images",
                    files=[("files", ("a.png", PNG, "image/png")), ("files", ("b.jpg", JPG, "image/jpeg")),
                           ("files", ("c.webp", WEBP, "image/webp"))])
    assert r.status_code == 200, r.text
    urls = r.json()["urls"]
    assert len(urls) == 3 and all(u.startswith(f"https://cdn.example.test/questions/{question}/") for u in urls)
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


def test_patch_add_remove_reorder_and_keep_catch_all_in_step(question, fake_r2):
    before = _row(question)
    new = "https://cdn.example.test/questions/x/new.png"
    r = client.patch(f"{API}/questions/{question}", json={
        "question_images": [new, B],                      # add one, drop a.png, reorder
        "answer_images": [ANS, new]})                     # `new` is already a question picture
    assert r.status_code == 200, r.text
    row = _row(question)
    assert row["q"] == [new, B]
    assert row["a"] == [ANS]                              # one role per picture
    assert row["refs"] == [new, B, ANS]
    assert A not in row["refs"]                           # a removed picture cannot return through image_refs
    body = r.json()
    assert body["question_images"] == row["q"] and body["answer_images"] == row["a"]
    assert row["text"] == before["text"]                  # nothing else changed
    assert not fake_r2.deleted                            # detaching never deletes a stored file


def test_removing_every_question_picture_leaves_no_ghost_in_image_refs(question):
    r = client.patch(f"{API}/questions/{question}", json={"question_images": [], "answer_images": [ANS]})
    assert r.status_code == 200
    row = _row(question)
    assert row["q"] == [] and row["a"] == [ANS] and row["refs"] == []
    r = client.patch(f"{API}/questions/{question}", json={"question_images": [], "answer_images": []})
    assert _row(question)["refs"] == [] and r.json()["image_refs"] == []


def test_patch_validates_urls_and_dedupes(question):
    for bad in ("javascript:alert(1)", "ftp://x/y.png", "//evil.example/x.png", "/static/../../etc/passwd", "relative/path.png"):
        assert client.patch(f"{API}/questions/{question}", json={"question_images": [bad]}).status_code == 400
    ok = client.patch(f"{API}/questions/{question}", json={"question_images": [f" {A} ", A, "/static/slides/d/media/b.png", ""]})
    assert ok.status_code == 200
    assert _row(question)["q"] == [A, "/static/slides/d/media/b.png"]


def test_connect_clue_payload_is_byte_identical_after_image_edits(question, fake_r2):
    before = _row(question)["payload"]
    assert json.loads(before)["clues"][0]["image"] == "https://cdn.example.test/c1.png"
    client.post(f"{API}/questions/{question}/images", files=[("files", ("a.png", PNG, "image/png"))])
    client.patch(f"{API}/questions/{question}", json={"question_images": ["https://cdn.example.test/z.png"], "answer_images": []})
    client.patch(f"{API}/questions/{question}", json={"question_images": [], "answer_images": []})
    assert _row(question)["payload"] == before


def test_patch_without_image_fields_leaves_images_alone(question):
    before = _row(question)
    assert client.patch(f"{API}/questions/{question}", json={"difficulty": "Hard"}).status_code == 200
    after = _row(question)
    assert {k: after[k] for k in ("q", "a", "refs", "payload")} == {k: before[k] for k in ("q", "a", "refs", "payload")}


def test_image_code_never_touches_ai_or_embeddings():
    import ast
    tree = ast.parse(Path(questions_router.__file__).read_text(encoding="utf-8"))
    wanted = {"_clean_image_urls", "_sniff_image", "_max_image_bytes", "upload_question_images", "update_question"}
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
    # the update handler only ever reads format_payload nowhere: image edits cannot reach the clue data
    update = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "update_question")
    assert not any(isinstance(n, ast.Attribute) and n.attr in ("format_payload", "format_data") for n in ast.walk(update))
