"""GATE 6: format-aware selection counts and the static PPTX export (opens, right slide counts, round trip)."""
import io
import json
import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pptx import Presentation

from backend.app.config import settings
from backend.app.database import SessionLocal
from backend.app.main import app
from backend.app.models.document import Document
from backend.app.models.question import Question
from backend.app.services.export.exporter import QuizExporter
from backend.app.services.export import format_slides as fs
from backend.app.services.ingestion.pptx_parser import PPTXParser
from backend.app.services.ingestion.question_extractor import QuestionExtractor
from backend.app.services.prompt_quiz import engine

client = TestClient(app)
API = "/api/v1"
FIX = Path(__file__).resolve().parent / "fixtures"


def fx(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def extract(name):
    return {q["slide_number"]: q for q in QuestionExtractor().extract_from_slides(fx(name), "t", 2026)}


# ------------------------------------------------------------------------------------------ selection

def _seed(slot, fmt, text):
    doc_id, q_id = str(uuid.uuid4()), str(uuid.uuid4())
    db = SessionLocal()
    db.add(Document(id=doc_id, filename="s.pptx", title="s", year=2025, file_type="pptx", storage_path="x",
                    file_size_bytes=1, processing_status="COMPLETED"))
    db.add(Question(id=q_id, document_id=doc_id, question_text=text, answer="Answer " + text[:12], topic="Zebra Quiz Topic", slot=slot,
                    question_format=fmt, format_payload={"type": fmt}))
    db.commit()
    db.close()
    return doc_id, q_id


def _drop(doc_id, q_id):
    db = SessionLocal()
    try:
        db.query(Question).filter(Question.id == q_id).delete(synchronize_session=False)
        db.query(Document).filter(Document.id == doc_id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_format_counts_exclude_reserve_and_example_by_default(monkeypatch):
    monkeypatch.setenv("PROMPT_QUIZ_BANK_TTL_S", "0")
    rows = [_seed("LIVE", "LIST_IT", "live list it question one two"), _seed("RESERVE", "LIST_IT", "reserve list it question"),
            _seed("EXAMPLE", "CONNECT", "example connect question number"), _seed("LIVE", "CONNECT_4", "old style connect question")]
    try:
        base = client.get(f"{API}/prompt-quiz/format-counts").json()
        withres = client.get(f"{API}/prompt-quiz/format-counts", params={"include_reserve": True}).json()
        both = client.get(f"{API}/prompt-quiz/format-counts", params={"include_reserve": True, "include_example": True}).json()
        assert base["included_slots"] == ["LIVE"] and withres["included_slots"] == ["LIVE", "RESERVE"]
        assert withres["counts"]["LIST_IT"] == base["counts"]["LIST_IT"] + 1
        assert both["counts"]["CONNECT"] == base["counts"]["CONNECT"] + 1                 # the example, and CONNECT_4 folds into CONNECT
        assert base["counts"]["CONNECT"] >= 1 and "CONNECT_4" not in base["counts"]
        assert base["slots"]["RESERVE"] >= 1 and base["slots"]["EXAMPLE"] >= 1 and base["total"] == sum(base["counts"].values())
    finally:
        for r in rows:
            _drop(*r)


def test_quiz_draft_never_picks_reserve_unless_asked(monkeypatch):
    monkeypatch.setenv("PROMPT_QUIZ_BANK_TTL_S", "0")
    live, spare = _seed("LIVE", "STANDARD_QA", "The quagga, a zebra relative, was hunted to extinction in which century?"), _seed("RESERVE", "STANDARD_QA", "Name the continent where wild zebra herds still migrate across the Serengeti plains")
    try:
        def ids(options):
            r = client.post(f"{API}/prompt-quiz/draft", json={"prompt": "zebra", "count": 5, "options": options}).json()
            return {q["id"] for q in r["questions"]}
        assert live[1] in ids({}) and spare[1] not in ids({})
        assert spare[1] in ids({"include_reserve": True})
        # saving a quiz that holds a reserve question works (lookups see every slot)
        r = client.post(f"{API}/prompt-quiz/save", json={"prompt": "zebra", "question_ids": [live[1], spare[1]]})
        assert r.status_code == 200, r.text
    finally:
        _drop(*live)
        _drop(*spare)


# ------------------------------------------------------------------------------------------ export

@pytest.fixture()
def media_dir():
    doc = f"memtest-rt-{uuid.uuid4().hex[:8]}"
    d = settings.SLIDES_DIR / doc / "media"
    d.mkdir(parents=True, exist_ok=True)
    yield doc, d
    shutil.rmtree(settings.SLIDES_DIR / doc, ignore_errors=True)


def _png(path: Path, seed: int):
    import random
    rnd = random.Random(seed)
    im = Image.new("RGB", (160, 120))
    im.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(160 * 120)])
    im.save(path, "PNG")


def materialise(q, doc, d):
    """Give the question's picture URLs real files (noisy PNGs, one per distinct URL) and point its payload at them."""
    tok, urls = fs.tokenize_payload({k: v for k, v in q["format_payload"].items()})
    new = []
    for i, _u in enumerate(urls):
        name = f"p{uuid.uuid4().hex[:6]}_{i}.png"
        _png(d / name, seed=hash((doc, name)) & 0xFFFF)
        new.append(f"/static/slides/{doc}/media/{name}")
    out = dict(q)
    out["format_payload"] = fs.detokenize_payload(tok, new)
    return out


def quiz_of(questions):
    return {"id": "qz", "title": "Round trip", "topic": "Mixed", "difficulty": "Balanced",
            "questions": [dict(id=f"g{i}", is_approved=True, question_text=q["question_text"], answer=q["answer"],
                               explanation=q.get("explanation") or "", options=q.get("options"), format_payload=q["format_payload"],
                               question_type=q["question_type"]) for i, q in enumerate(questions)]}


def export(questions):
    return QuizExporter.to_pptx(quiz_of(questions), quiz_type="minimalist", include_social_slides=False)


def reingest(data, tmp_path, monkeypatch):
    import backend.app.services.storage.r2_storage as r2
    monkeypatch.setattr(r2, "get_media_storage", lambda: None)
    f = tmp_path / "export.pptx"
    f.write_bytes(data)
    slides = PPTXParser(str(f), f"memtest-back-{uuid.uuid4().hex[:6]}", write_to_disk=False).parse()
    return QuestionExtractor().extract_from_slides(slides, "t", 2026), slides


def core(q):
    """What must survive a round trip: format, texts, options and the payload with picture URLs reduced to their pattern."""
    p = {k: v for k, v in q["format_payload"].items() if k != "extraction"}
    tok, urls = fs.tokenize_payload(p)
    return {"format": q["question_format"], "payload": tok, "n_pictures": len(urls), "answer": q["answer"],
            "question_text": q["question_text"], "options": q.get("options")}


def sample(name_slides):
    out = []
    for name, nums in name_slides:
        ex = extract(name)
        out.extend(ex[n] for n in nums)
    return out


def test_slide_counts_per_format_and_the_file_opens(media_dir):
    doc, d = media_dir
    qs = [materialise(q, doc, d) for q in sample([("dsu_mini", [84, 114, 140]), ("three_clues_reserve", [3]),
                                                 ("arrange_reserve", [3]), ("mcq_reserve", [17])])]
    prs = Presentation(io.BytesIO(export(qs)))                       # the exported file opens
    texts = [" | ".join(sh.text_frame.text for sh in s.shapes if sh.has_text_frame) for s in prs.slides]
    n_clues = len(qs[3]["format_payload"]["clues"])
    # cover + reveal(4 + ANSWER + answer) + list it(3) + connect(3) + stepped(clues + 2) + arrange(3) + mcq(3)
    assert len(prs.slides) == 1 + 6 + 3 + 3 + (n_clues + 2) + 3 + 3
    assert sum(t.strip().startswith("ANSWER") for t in texts) == 6
    reveal = [s for s in prs.slides if any(sh.name.startswith("Cover") for sh in s.shapes)]
    assert [sum(sh.name.startswith("Cover") for sh in s.shapes) for s in reveal] == [4, 3, 2, 1]     # progressive: one more lifted each slide
    arrange = next(s for s in prs.slides if any(sh.shape_type == 6 for sh in s.shapes))
    assert sum(sh.shape_type == 6 for sh in arrange.shapes) == 4                                     # four grouped items
    li = next(s for s in prs.slides if "Correct answers are ticked" in " ".join(sh.text_frame.text for sh in s.shapes if sh.has_text_frame))
    marks = " ".join(sh.text_frame.text for sh in li.shapes if sh.has_text_frame)
    assert marks.count("✓") == 6 and marks.count("✗") == 3                                 # 6 ticks, 3 crosses


def test_round_trip_recovers_the_same_payloads(media_dir, tmp_path, monkeypatch):
    doc, d = media_dir
    originals = [materialise(q, doc, d) for q in sample([
        ("dsu_mini", [84, 114, 140, 148]), ("three_clues_reserve", [3, 13]), ("arrange_reserve", [3, 6, 132]),
        ("mcq_reserve", [14, 17, 47])])]
    back, _slides = reingest(export(originals), tmp_path, monkeypatch)
    assert len(back) == len(originals)
    for orig, got in zip(originals, back):
        assert core(got) == core(orig), orig["question_format"]
        assert got["format_payload"]["extraction"]["source"] == "qshala_export"
        assert got["_ingest"]["route"] == "accept" and got["question_format"] == orig["question_format"]
    # the picture URLs are the NEW upload's, not the old ones
    old = {u for o in originals for u in fs.tokenize_payload(o["format_payload"])[1]}
    new = {u for g in back for u in fs.tokenize_payload(g["format_payload"])[1]}
    assert new and not (old & new)


def test_arrange_order_entered_by_hand_survives_the_round_trip(media_dir, tmp_path, monkeypatch):
    doc, d = media_dir
    q = materialise(extract("arrange_reserve")[132], doc, d)
    q["format_payload"] = {**q["format_payload"], "correct_order": [2, 1, 4, 3], "order_source": "human"}
    back, _ = reingest(export([q]), tmp_path, monkeypatch)
    assert back[0]["format_payload"]["correct_order"] == [2, 1, 4, 3] and back[0]["format_payload"]["order_source"] == "human"


def test_plain_questions_and_unexpandable_payloads_use_the_template_path(media_dir, tmp_path):
    doc, d = media_dir
    plain = {"id": "p", "is_approved": True, "question_text": "Which planet is the red planet?", "answer": "Mars",
             "explanation": "", "options": None, "format_payload": {"type": "STANDARD_QA"}, "question_type": "SLIDE_QA"}
    broken = {"id": "b", "is_approved": True, "question_text": "Odd one", "answer": "X", "explanation": "", "options": None,
              "format_payload": {"type": "LIST_IT", "options": [], "extraction": {"confidence": 1}}, "question_type": "LIST_IT"}
    prs = Presentation(io.BytesIO(QuizExporter.to_pptx({"id": "q", "title": "t", "topic": "x", "questions": [plain, broken]},
                                                       quiz_type="minimalist", include_social_slides=False)))
    all_text = " ".join(sh.text_frame.text for s in prs.slides for sh in s.shapes if sh.has_text_frame)
    assert "Mars" in all_text and "Odd one" in all_text
    assert fs.export_format_of(broken["format_payload"]) is None and fs.export_format_of({"type": "LIST_IT"}) is None


def test_notes_blob_helpers():
    p = {"type": "CONNECT", "items": [{"order": 1, "image": "/a.png"}, {"order": 2, "image": "/b.png"}, {"order": 3, "image": "/a.png"}],
         "extraction": {"confidence": 1}}
    blob, urls = fs.build_blob("CONNECT", p, "q", "a", "", None, {})
    assert urls == ["/a.png", "/b.png"] and "extraction" not in blob
    data = fs.parse_blob("host notes\n" + blob)
    assert data and data["payload"]["items"][2]["image"] == {"$img": 0}
    assert fs.detokenize_payload(data["payload"], ["/x", "/y"])["items"][1]["image"] == "/y"
    with pytest.raises(IndexError):
        fs.detokenize_payload(data["payload"], ["/x"])
    assert fs.parse_blob("no marker here") is None and fs.parse_blob(fs.MARKER + "\n{not json") is None
