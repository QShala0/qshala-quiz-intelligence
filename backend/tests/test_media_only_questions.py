"""Audio-only and text+audio questions are kept: the clip is attached, a text-less slide gets a placeholder, round
music and timers are ignored, and a slide with a real clip that produced no question is reported."""
from backend.app.services.ingestion.question_extractor import QuestionExtractor
from backend.app.services.tagging.qshala_rules import is_answer_led


def shape(sid, type_, text="", kind=None, md5=None, url=None, name=None, y=0):
    return {"id": sid, "name": name or f"S{sid}", "type": type_, "text": text, "x": 0, "y": y, "w": 100, "h": 50,
            "groups": [], "path": [], "path_ids": [], "media_md5": md5, "media_kind": kind, "overlay_class": None,
            "zip_name": None, "media_url": url}


def slide(no, text="", media=(), kind="CONTENT", title=None):
    shapes = []
    if text:
        shapes.append(shape(1, "TEXT_BOX", text))
    for i, (k, md5, url) in enumerate(media):
        shapes.append(shape(10 + i, "MEDIA", kind=k, md5=md5, url=url, y=100 + i))
    return {"slide_number": no, "slide_type": kind, "title": title or (text.split("\n")[0] if text else ""),
            "extracted_text": text, "speaker_notes": "", "image_paths": [], "video_paths": [], "has_images": False,
            "metadata_json": {}, "shapes": shapes}


def qa(first_no, question_slide, answer_text, answer_media=()):
    return [question_slide,
            slide(first_no + 1, "ANSWER", kind="ANSWER_TRANSITION"),
            slide(first_no + 2, answer_text, media=answer_media, kind="ANSWER")]


def extract(slides):
    ex = QuestionExtractor()
    qs = ex.extract_from_slides(slides, "t", 2026)
    return qs, ex


def test_audio_only_slide_becomes_a_question_with_the_clip_and_goes_to_review():
    qs, ex = extract(qa(1, slide(1, media=[("audio", "crcA", "/m/a.mp3")], kind="QUESTION"), "Yesterday"))
    assert len(qs) == 1
    q = qs[0]
    assert q["question_audio"] == ["/m/a.mp3"] and q["audio_refs"] == ["/m/a.mp3"]
    assert q["question_text"] == "(listen to the clip)" and q["answer"] == "Yesterday"
    assert q["format_payload"]["media_kind"] == "AUDIO"
    assert q["_ingest"]["needs_review"] is True


def test_short_text_plus_audio_keeps_its_text_and_the_clip():
    qs, _ = extract(qa(1, slide(1, "1.\n\nName this song", media=[("audio", "crcA", "/m/a.mp3")], kind="QUESTION"), "Yesterday"))
    q = qs[0]
    assert "Name this song" in q["question_text"]
    assert q["question_audio"] == ["/m/a.mp3"]
    assert q["_ingest"]["needs_review"] is False


def test_audio_on_the_answer_slide_is_an_answer_clip():
    qs, _ = extract(qa(1, slide(1, "1.\n\nWho sang this line?", kind="QUESTION"), "Lata", answer_media=[("audio", "crcB", "/m/b.mp3")]))
    q = qs[0]
    assert q["answer_audio"] == ["/m/b.mp3"] and q["question_audio"] == []


def test_video_only_slide_is_still_a_clip_question():
    qs, _ = extract(qa(1, slide(1, media=[("video", "crcV", "/m/v.mp4")], kind="QUESTION"), "Basil"))
    q = qs[0]
    assert q["question_videos"] == ["/m/v.mp4"] and q["question_text"] == "(see the clip)"


def test_music_used_on_three_slides_is_furniture_not_a_clue():
    deck = []
    for i in range(3):
        deck += qa(1 + 3 * i, slide(1 + 3 * i, f"{i + 1}.\n\nWhich city is this?", media=[("audio", "crcM", "/m/music.mp3")], kind="QUESTION"), f"City{i}")
    qs, _ = extract(deck)
    assert len(qs) == 3 and all(q["question_audio"] == [] for q in qs)


def test_a_timer_is_never_a_clue():
    s = slide(1, "1.\n\nWhich city is this?", kind="QUESTION")
    s["shapes"].append(shape(20, "MEDIA", kind="audio", md5="crcT", url="/m/timer.mp3", name="Timer 60"))
    qs, _ = extract(qa(1, s, "Pune"))
    assert qs[0]["question_audio"] == []


def test_a_slide_with_a_real_clip_and_no_question_is_reported():
    deck = qa(1, slide(1, "1.\n\nWho is this?", kind="QUESTION"), "Ana")
    deck.append(slide(4, "Listen to this clip carefully and tell us the singer of the song", media=[("audio", "crcZ", "/m/z.mp3")], kind="QUESTION"))
    deck.append(slide(5, "Round 2", media=[("audio", "crcY", "/m/round.mp3")], kind="CONTENT"))    # round music: not reported
    qs, ex = extract(deck)
    assert ex.skipped_media_slides == [4]


def test_switch_off_restores_the_old_behaviour(monkeypatch):
    monkeypatch.setenv("MEDIA_ONLY_QUESTIONS_ENABLED", "0")
    qs, ex = extract(qa(1, slide(1, "1.\n\nName this song", media=[("audio", "crcA", "/m/a.mp3")], kind="QUESTION"), "Yesterday"))
    assert qs[0]["question_audio"] == [] and qs[0]["audio_refs"] == []
    assert ex.skipped_media_slides == []


def test_a_media_only_question_is_tagged_from_its_answer():
    assert is_answer_led("(listen to the clip)", "STANDARD_QA", media_count=1) is True
    assert is_answer_led("Name this song", "STANDARD_QA", media_count=1) is True
    assert is_answer_led("Which river flows through the city where the 1857 revolt began and which later gave its name to a famous treaty?",
                         "STANDARD_QA", media_count=1) is False
