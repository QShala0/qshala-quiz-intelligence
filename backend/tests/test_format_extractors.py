"""GATE 3: format detection and extraction (the test table), confidence routing, and the single source of truth."""
import asyncio
import json
import os
from pathlib import Path

import pytest

from backend.app.services.deduplication.deduplicator import Deduplicator
from backend.app.services.ingestion import format_extractors as fe
from backend.app.services.ingestion.deck_metadata import infer_formats, annotate_questions
from backend.app.services.ingestion.question_extractor import QuestionExtractor
from backend.app.services.ingestion.question_formats import detect_and_structure_format
from backend.app.services.tagging.format_classifier import classify_and_extract

FIX = Path(__file__).resolve().parent / "fixtures"


def fx(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def extract(slides):
    return QuestionExtractor().extract_from_slides(slides, "t", 2026)


def by_slide(qs):
    return {q["slide_number"]: q for q in qs}


def norm(s):
    return s.replace("\u2019", "'").strip()


# ------------------------------------------------------------------------------------------ value parsing

@pytest.mark.parametrize("text,expected", [
    ("\u20b93,604 crore", 3604e7), ("2019", 2019.0), ("18%", 18.0), ("12 lakh", 12e5), ("$4.5 billion", 4.5e9),
    ("3.2 million", 3.2e6), ("1990-1995", 1992.5), ("20 to 30", 25.0), ("(Malappuram)", None), ("", None),
    ("12 March 1950", None), ("many", None),
])
def test_parse_value(text, expected):
    got = fe.parse_value(text)
    assert (got is None and expected is None) or got == pytest.approx(expected)


@pytest.mark.parametrize("title,expected", [
    ("Budget FY26-27 (Highest to Lowest).", "desc"), ("Year of unicorn status (First to latest).", "asc"),
    ("GI tagged products (North to South)", "asc"), ("Oldest to newest films", "asc"), ("Largest city first", "desc"),
    ("Put these in order", None), ("Steps (from first to last)", "asc"),
])
def test_direction_from_title(title, expected):
    assert fe.direction_from_title(title) == expected


# ------------------------------------------------------------------------------------------ synthetic fixtures

def test_synthetic_dsu_formats():
    qs = by_slide(extract(fx("dsu_mini")))
    assert qs[84]["question_format"] == "LIST_IT" and qs[84]["play_mode"] == "DIRECTED"
    assert qs[84]["format_payload"]["options"][0].keys() >= {"text", "correct"}
    assert qs[114]["question_format"] == "IMAGE_REVEAL"
    steps = qs[114]["format_payload"]["reveal_steps"]
    assert [s["position"] for s in steps] == ["bottom-left", "bottom-right", "top-right", "top-left"]
    assert qs[140]["question_format"] == "CONNECT" and len(qs[140]["format_payload"]["items"]) == 9
    assert len(qs[148]["format_payload"]["items"]) == 5 and qs[148]["format_payload"]["text_hint"].startswith("Unit of")
    assert qs[156]["question_videos"] and qs[156]["answer"] == "MRF"
    assert qs[15]["question_format"] == "STANDARD_QA" and qs[15]["topic_label"] == "Sustainability 1"
    assert qs[72]["_ingest"]["needs_review"]


def test_synthetic_reserve_formats():
    three = by_slide(extract(fx("three_clues_reserve")))
    assert three[3]["question_format"] == "STEPPED_CLUE" and three[3]["format_payload"]["scoring_ladder_ok"] is True
    assert three[13]["format_payload"]["scoring_ladder_ok"] is False
    assert three[313]["slot"] == "RESERVE"
    arr = by_slide(extract(fx("arrange_reserve")))
    assert arr[3]["format_payload"]["correct_order"] == [3, 1, 4, 2] and arr[3]["format_payload"]["order_source"] == "computed"
    assert arr[6]["format_payload"]["correct_order"] == [3, 1, 4, 2]
    assert arr[132]["format_payload"]["correct_order"] == [] and arr[132]["format_payload"]["order_source"] == "none"
    assert arr[132]["_ingest"]["needs_review"]
    assert arr[141]["_ingest"]["needs_review"] and arr[183]["_ingest"]["needs_review"]
    mcq = by_slide(extract(fx("mcq_reserve")))
    assert mcq[17]["format_payload"]["correct_letter"] == "B"
    assert [o["text"] for o in mcq[17]["format_payload"]["options"]] == ["1024", "1930", "100", "786"]
    assert mcq[14]["format_payload"]["star"] is True
    assert mcq[47]["answer"] == "A. Joint Accounts" and mcq[47]["question_images"]       # picture slide 48 joined


# ------------------------------------------------------------------------------------------ source of truth + routing

def test_ingestion_result_is_the_single_source_of_truth():
    qs = extract(fx("dsu_mini"))
    annotate_questions(qs, fx("dsu_mini"))
    for q in qs:
        assert q["_meta"]["question_format"] == q["_ingest"]["question_format"] == q["question_format"]
        assert q["question_type"] == q["question_format"]
    # the legacy detector hands back the structured result untouched
    q = next(q for q in qs if q["question_format"] == "IMAGE_REVEAL")
    assert detect_and_structure_format("x", "y", structured=q)[1] is q["format_payload"]


def test_format_classifier_reads_the_ingestion_result():
    stored = {"type": "LIST_IT", "extraction": {"confidence": 0.97}}
    # question text full of Connect/Stepped words must not override what ingestion decided
    fmt, data = classify_and_extract("Clue 1: what connects these? arrange", "answer", stored_format="LIST_IT", stored_payload=stored)
    assert fmt == "LIST_IT" and data is None
    # older rows with no ingestion result keep the old behaviour
    fmt, _ = classify_and_extract("Arrange these from lowest to highest", "x")
    assert fmt == "ARRANGE_ORDER"


def test_confidence_routes():
    qs = extract(fx("dsu_mini"))
    for q in qs:
        c, r = q["_ingest"]["confidence"], q["_ingest"]["route"]
        assert r == ("review" if q["_ingest"]["needs_review"] else ("accept" if c >= 0.9 else "vision"))
        if c < 0.7:
            assert q["_ingest"]["needs_review"]


def test_vision_band_only_and_only_when_enabled(monkeypatch):
    from backend.app.services.ingestion import format_vision as fv

    class Fake:
        def __init__(self, verdict):
            self.verdict, self.calls = verdict, 0

        async def generate_multimodal_ex(self, prompt, media, **kw):
            self.calls += 1
            return json.dumps(self.verdict), {}

    async def fetch(url):
        return b"x", "image/jpeg"

    def q(route, conf):
        return {"_ingest": {"route": route, "confidence": conf, "needs_review": route == "review", "question_format": "CONNECT",
                            "reasons": []}, "format_payload": {"extraction": {}}, "question_images": ["/a.png"], "image_refs": []}

    qs = [q("accept", 0.97), q("vision", 0.8), q("review", 0.5)]
    prov = Fake({"ok": False, "reason": "looks like a plain question"})
    stats = asyncio.run(fv.verify_band(qs, prov, fetch=fetch))
    assert prov.calls == 1 and stats["disagreed"] == 1                    # only the 0.7-0.9 question was sent
    assert qs[1]["_ingest"]["route"] == "review" and qs[0]["_ingest"]["route"] == "accept" and qs[2]["_ingest"]["route"] == "review"
    qs = [q("vision", 0.8)]
    asyncio.run(fv.verify_band(qs, Fake({"ok": True, "reason": ""}), fetch=fetch))
    assert qs[0]["_ingest"]["route"] == "accept" and not qs[0]["_ingest"]["needs_review"]
    monkeypatch.delenv("FORMAT_VISION_CHECK", raising=False)
    assert fv.vision_check_enabled() is False
    monkeypatch.setenv("FORMAT_VISION_CHECK", "1")
    assert fv.vision_check_enabled() is True


def test_call_cap(monkeypatch):
    from backend.app.services.ingestion import format_vision as fv

    class Fake:
        calls = 0

        async def generate_multimodal_ex(self, *a, **k):
            Fake.calls += 1
            return '{"ok": true}', {}

    async def fetch(url):
        return b"x", "image/jpeg"

    qs = [{"_ingest": {"route": "vision", "confidence": 0.8, "needs_review": False, "question_format": "MCQ", "reasons": []},
           "format_payload": {}, "question_images": ["/a.png"], "image_refs": []} for _ in range(5)]
    monkeypatch.setenv("FORMAT_VISION_MAX_CALLS", "2")
    stats = asyncio.run(fv.verify_band(qs, Fake(), fetch=fetch))
    assert Fake.calls == 2 and stats["skipped"] == 3


def test_infer_formats_resets_at_audience_buffer_tiebreak():
    slides = [
        {"slide_number": 1, "slide_type": "SECTION_MARKER", "title": "CONNECT ROUND 4", "extracted_text": "CONNECT\nROUND 4"},
        {"slide_number": 2, "slide_type": "QUESTION", "title": "q", "extracted_text": "q"},
        {"slide_number": 3, "slide_type": "CONTENT", "title": "AUDIENCE QUESTION", "extracted_text": "AUDIENCE QUESTION"},
        {"slide_number": 4, "slide_type": "QUESTION", "title": "q2", "extracted_text": "q2"},
        {"slide_number": 5, "slide_type": "CONTENT", "title": "BUFFER", "extracted_text": "BUFFER"},
        {"slide_number": 6, "slide_type": "QUESTION", "title": "q3", "extracted_text": "q3"},
    ]
    got = infer_formats(slides)
    assert got[2] == "CONNECT_4" and got[4] == "QA" and got[6] == "QA"


def test_decks_without_answer_markers_use_the_legacy_extractor():
    slides = [{"id": "a", "slide_number": 1, "slide_type": "QUESTION", "title": "q", "extracted_text": "Q?\nAnswer: yes",
               "speaker_notes": "Answer: yes", "image_paths": [], "all_media_items": []}]
    qs = extract(slides)
    assert len(qs) == 1 and "_ingest" not in qs[0]


def test_env_switch_restores_legacy(monkeypatch):
    monkeypatch.setenv("FORMAT_AWARE_INGESTION", "0")
    qs = extract(fx("dsu_mini"))
    assert not any("_ingest" in q for q in qs)


# ------------------------------------------------------------------------------------------ real decks (optional)

DECK_DIRS = [Path(p) for p in os.environ.get(
    "QSHALA_DECK_DIRS",
    r"C:\Users\bragg\Desktop\Projects\QShala\PPT\PPT - QS 1\PPTs for Akshay & Glenn;C:\Users\bragg\Desktop\Projects\QShala\PPT\oo",
).split(";")]
_CACHE = {}


def real(name):
    if name in _CACHE:
        return _CACHE[name]
    path = next((d / name for d in DECK_DIRS if (d / name).exists()), None)
    if path is None:
        pytest.skip(f"real deck {name!r} not available")
    import backend.app.services.storage.r2_storage as r2
    from backend.app.services.ingestion.pptx_parser import PPTXParser
    old = r2.get_media_storage
    r2.get_media_storage = lambda: (_ for _ in ()).throw(RuntimeError("storage disabled in tests"))
    try:
        slides = PPTXParser(str(path), "memtest-extract", write_to_disk=False).parse()
    finally:
        r2.get_media_storage = old
    qs = extract(slides)
    _CACHE[name] = (slides, qs, by_slide(qs))
    return _CACHE[name]


DSU = "DSU Business Quiz Finals.pptx"


def test_real_dsu_table():
    slides, all_qs, qs = real(DSU)
    assert len(all_qs) == 74
    q = qs[15]
    assert (q["question_format"], q["slot"], q["play_mode"], q["topic_label"], q["answer"]) == (
        "STANDARD_QA", "LIVE", "PASS", "Sustainability 1", "Plastic Waste") and q["answer_slide_number"] == 17

    q = qs[84]
    p = q["format_payload"]
    assert (q["question_format"], q["slot"], q["play_mode"], p["team"]) == ("LIST_IT", "LIVE", "DIRECTED", 8)
    assert sorted(norm(o["text"]) for o in p["options"] if o["correct"]) == sorted(
        ["SBI", "Reliance Industries Ltd.", "Larsen & Toubro Ltd.", "ITC", "Bajaj Auto", "Tata Steel"])
    assert sorted(norm(o["text"]) for o in p["options"] if not o["correct"]) == sorted(["Nestle", "Bharti Airtel", "Sun Pharma"])
    assert qs[84]["_ingest"]["route"] == "accept"
    assert sorted(norm(o["text"]) for o in qs[93]["format_payload"]["options"] if not o["correct"]) == sorted(
        ["General Motors", "McDonald's", "Nvidia"])

    q = qs[114]
    p = q["format_payload"]
    assert q["question_format"] == "IMAGE_REVEAL" and q["play_mode"] == "BUZZER" and q["answer"] == "Ratan Tata"
    assert [s["position"] for s in p["reveal_steps"]] == ["bottom-left", "bottom-right", "top-right", "top-left"]
    assert [(s["correct"], s["wrong"]) for s in p["reveal_steps"]] == [(20, -15), (15, -10), (10, -5), (5, 0)]
    assert p["full_image"] and q["answer_slide_number"] == 116

    assert qs[140]["question_format"] == "CONNECT" and len(qs[140]["format_payload"]["items"]) == 9
    assert qs[140]["answer"] == "Online Streaming Services / OTT" and qs[140]["answer_slide_number"] == 142
    p = qs[148]["format_payload"]
    assert len(p["items"]) == 5 and p["text_hint"] == "Unit of measuring light intensity" and qs[148]["answer"] == "Soap Brands"
    assert qs[156]["question_format"] == "CONNECT" and qs[156]["question_videos"] and qs[156]["answer"] == "MRF"

    a, b = qs[23], qs[26]
    assert a["topic_label"] == b["topic_label"] == "Entrepreneurship 1"
    assert (a["slot"], b["slot"]) == ("RESERVE", "LIVE") and "Lehman" in a["answer"] and b["answer"] == "Alia Bhatt"
    h = Deduplicator.compute_hash
    assert h(a["question_text"], a["answer"]) != h(b["question_text"], b["answer"])

    q = qs[72]
    assert q["question_format"] == "STANDARD_QA" and q["_ingest"]["needs_review"] and q["topic_label"] is None
    assert q["answer"] == "Pav Bhaji" and q["answer_slide_number"] == 74
    for n, ans in ((102, "IPL"), (130, "Coldplay")):
        assert (qs[n]["question_format"], qs[n]["play_mode"], qs[n]["answer"]) == ("STANDARD_QA", "AUDIENCE", ans)
    q = qs[224]
    assert (q["question_format"], q["play_mode"], q["topic_label"]) == ("STANDARD_QA", "BUFFER", None) and q["answer_slide_number"] == 226
    for n in (82, 110, 136):
        assert qs[n]["slot"] == "EXAMPLE"
    q = qs[163]
    assert (q["question_format"], q["slot"], q["answer"]) == ("CONNECT", "RESERVE", "Tetra Pak") and q["answer_slide_number"] == 165

    used = set()
    for q in all_qs:
        used |= {q["slide_number"], q["answer_slide_number"]}
    for n in (3, 4, 5, 9, 83, 105, 170, 207, 208, 209, 210, 211):
        assert n not in used


def test_real_list_it_reserve():
    _, _, qs = real("ZVQ - List It - Reserve.pptx")
    p = qs[3]["format_payload"]
    assert qs[3]["question_format"] == "LIST_IT"
    assert sum(o["correct"] is True for o in p["options"]) == 6 and sum(o["correct"] is False for o in p["options"]) == 3
    assert qs[68]["_ingest"]["needs_review"] and len(qs[68]["format_payload"]["options"]) == 8


def test_real_three_clues_reserve():
    _, _, qs = real("ZVQ - Three Clues - Reserve.pptx")
    p = qs[3]["format_payload"]
    assert qs[3]["question_format"] == "STEPPED_CLUE" and qs[3]["answer"] == "T. Nagar" and qs[3]["answer_slide_number"] == 7
    assert [(c["correct"], c["wrong"]) for c in p["clues"]] == [(20, -15), (15, -10), (10, -5)] and p["scoring_ladder_ok"] is True
    assert qs[13]["format_payload"]["scoring_ladder_ok"] is False and qs[13]["answer"] == "Cashew"
    q = qs[313]
    assert q["slot"] == "RESERVE" and q["answer"] == "Punjab National Bank (PNB)" and q["answer_slide_number"] == 317
    assert q["format_payload"].get("points_missing") is True


def test_real_arrange_reserve():
    _, _, qs = real("Arrange the Order - 2026 Aug to Dec ZVQ College - Reserve.pptx")
    for n, direction in ((3, "desc"), (6, "asc")):
        p = qs[n]["format_payload"]
        assert qs[n]["question_format"] == "ARRANGE_ORDER" and p["direction"] == direction
        assert p["correct_order"] == [3, 1, 4, 2] and p["order_source"] == "computed"
    assert qs[3]["answer_slide_number"] == 5 and qs[6]["answer_slide_number"] == 8
    p = qs[132]["format_payload"]
    assert qs[132]["answer_slide_number"] == 134 and p["correct_order"] == [] and p["order_source"] == "none"
    assert qs[132]["_ingest"]["needs_review"] and len(p["items"]) == 4
    assert qs[141]["_ingest"]["needs_review"] and qs[183]["_ingest"]["needs_review"]
    assert [it["label"] for it in qs[3]["format_payload"]["items"]] == [1, 2, 3, 4]


def test_real_mcq_reserve():
    _, _, qs = real("Prelim MCQs - 2026 Aug to Dec ZVQ College - Reserve.pptx")
    p = qs[17]["format_payload"]
    assert qs[17]["question_format"] == "MCQ" and qs[17]["answer_slide_number"] == 19
    assert [o["text"] for o in p["options"]] == ["1024", "1930", "100", "786"] and p["correct_letter"] == "B"
    assert qs[14]["format_payload"]["star"] is True and qs[14]["answer_slide_number"] == 16
    q = qs[47]
    assert q["answer"] == "A. Joint Accounts" and q["answer_slide_number"] == 50 and q["source_slide_range"] == "Slide 47-50"
    assert q["format_payload"]["correct_letter"] == "A" and len(q["question_images"]) >= 1


def test_real_hotspot_reserve():
    _, all_qs, qs = real("ZVQ - Hotspot Buzzer - Reserve.pptx")
    q = qs[3]
    assert (q["question_format"], q["format_payload"]["media_kind"], q["play_mode"], q["answer"]) == (
        "STANDARD_QA", "IMAGE", "BUZZER", "Ebenezer Scrooge") and q["answer_slide_number"] == 5
    for n in (348, 352, 356):
        assert n not in qs
        assert all(q["slide_number"] != n and q["answer_slide_number"] != n for q in all_qs)
