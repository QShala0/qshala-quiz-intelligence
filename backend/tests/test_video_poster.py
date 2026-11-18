"""A video's poster picture (the camera icon a clip is drawn with) is part of the clip, not a picture of the question."""
import os

import pytest
from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from backend.app.services.ingestion.pptx_parser import PPTXParser


def _png(path, seed):
    """A noisy 200x200 PNG: big enough that no size filter drops it, and unlike the known camera icon."""
    import random
    im = Image.frombytes("RGB", (200, 200), random.Random(seed).randbytes(200 * 200 * 3))
    im.save(path)
    return str(path)


def _deck(tmp_path):
    poster, real = _png(tmp_path / "poster.png", 7), _png(tmp_path / "real.png", 13)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096)
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[6])
    s.shapes.add_movie(str(clip), Inches(1), Inches(1), Inches(3), Inches(2), poster_frame_image=poster, mime_type="video/mp4")
    s.shapes.add_picture(real, Inches(5), Inches(1), Inches(2), Inches(2))
    path = tmp_path / "deck.pptx"
    prs.save(path)
    return str(path)


def _parse(path, tmp_path, monkeypatch):
    import backend.app.services.storage.r2_storage as r2mod
    monkeypatch.setattr(r2mod, "get_media_storage", lambda: None)
    from backend.app.config import settings
    monkeypatch.setattr(settings, "SLIDES_DIR", tmp_path / "slides")
    return PPTXParser(path, "poster-test", write_to_disk=False).parse()


def test_poster_image_of_a_video_is_not_a_picture(tmp_path, monkeypatch):
    slides = _parse(_deck(tmp_path), tmp_path, monkeypatch)
    s = slides[0]
    assert len(s["video_paths"]) == 1
    names = [os.path.basename(p) for p in s["image_paths"]]
    assert len(names) == 1, names                      # only the real picture
    kinds = {sh["media_kind"] for sh in s["shapes"] if sh["type"] in ("PICTURE", "MEDIA")}
    assert kinds == {"video", "image"}


def test_switch_off_restores_old_behaviour(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ONLY_QUESTIONS_ENABLED", "0")
    slides = _parse(_deck(tmp_path), tmp_path, monkeypatch)
    assert len(slides[0]["image_paths"]) == 2          # the poster leaks again, as before


# ------------------------------------------------------------------------------------------ the backfill's planning

class _Q:
    def __init__(self, id, qi, ai, ir, video=(), audio=()):
        self.id, self.document_id = id, "d"
        self.question_images, self.answer_images, self.image_refs = qi, ai, ir
        self.video_refs, self.audio_refs = list(video), list(audio)


def test_backfill_removes_only_posters_from_clip_questions():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "strip_video_posters", Path(__file__).resolve().parents[1] / "scripts" / "strip_video_posters.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    is_poster = lambda u: u.startswith("icon")
    qs = [
        _Q("a", ["icon1", "pic1"], ["ans1"], ["icon1", "pic1", "ans1"], video=["v.mp4"]),   # poster removed, rest kept
        _Q("b", ["icon2"], [], ["icon2"], audio=["a.mp3"]),                                   # audio poster removed
        _Q("c", ["icon3"], [], ["icon3"]),                                                    # no clip: untouched
        _Q("d", ["pic2"], [], ["pic2"], video=["v.mp4"]),                                     # nothing to remove
        _Q("e", ["icon4"], [], ["icon4"], video=["v.mp4"]),                                   # a person edited it
    ]
    rows, counts = mod.plan_changes(qs, edited_ids={"e"}, is_poster=is_poster)
    got = {r["question_id"]: r["new"] for r in rows}
    assert got == {"a": {"question_images": ["pic1"], "answer_images": ["ans1"], "image_refs": ["pic1", "ans1"]},
                   "b": {"question_images": [], "answer_images": [], "image_refs": []}}
    assert counts["skipped_human_edited"] == 1 and counts["to_change"] == 2 and counts["clip_questions"] == 4
