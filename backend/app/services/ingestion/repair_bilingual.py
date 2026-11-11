"""Idempotent repair pass for broken bilingual / Agastya-style deck extractions.

Heals existing databases where decks using multi-line Kannada+English transition
dividers ("ಉತ್ತರ!\\nAnswer!"), base64 data URIs in speaker notes, Finals bumper
slides, or List-It tables were mis-extracted into the vault prior to the classifier
and extractor fixes.

Safe to run repeatedly at startup inside `ensure_schema_columns()`: exits early in
O(1) query time when no broken artifacts are present, makes zero network/LLM calls,
and preserves foreign keys (`QshalaTag`, `QshalaQuestionMetadata`, `RetrievalSource`).
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_BROKEN_PLACEHOLDER_ANSWERS = {
    "",
    "ಉತ್ತರ",
    "answer",
    "answers",
    "ans",
    "solution",
    "solutions",
    "see explanation",
    "answer indicated on slide",
}

_BUMPER_QUESTION_RE = re.compile(
    r"^(?:ready\s*(?:for\s+q\d*[\.\!]?|[\?\!]?)|(?:question\s+)?for\s+audience|audience\s+questions?|tie\s*breakers?\s+questions?|example\s*!?|buffer\s*(?:questions?)?)$",
    re.IGNORECASE,
)

_TIMER_MEDIA_TOKENS = ("timer", "1min", "countdown")
_FURNITURE_MEDIA_THRESHOLD = 3


def _is_timer_media_url(url: Optional[str]) -> bool:
    low = (url or "").lower()
    return any(tok in low for tok in _TIMER_MEDIA_TOKENS)

_INDIC_RE = re.compile(r"[\u0900-\u0FFF]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def is_broken_answer_artifact(answer: Optional[str]) -> bool:
    s = (answer or "").strip()
    if not s:
        return True
    low = s.lower()
    if low.startswith("data:image/"):
        return True
    stripped = re.sub(r"[\.\!\:\-]+$", "", low).strip()
    if stripped in _BROKEN_PLACEHOLDER_ANSWERS:
        return True
    if re.fullmatch(r"[\s\|\-\_\.\!\?\,\:\;]+", s):
        return True
    return False


def is_bumper_question_artifact(question_text: Optional[str]) -> bool:
    raw = question_text or ""
    if re.search(r"(?:\n|\s{2,})Back\s*$", raw, re.IGNORECASE):
        return True
    s = re.sub(r"\s+", " ", raw).strip()
    if not s:
        return True
    if re.fullmatch(r"[\d\s\|\-_\.\!\?\,\:\;\(\)\*]+", s):
        return True
    if re.match(r"^\?\s+\d+[\.\)]", s):
        return True
    if _BUMPER_QUESTION_RE.match(s):
        return True
    low = s.lower()
    if low.startswith("topics:") and len(s.split()) <= 30 and "?" not in s:
        return True
    return False


def _is_indic_without_latin(text: Optional[str]) -> bool:
    s = (text or "").strip()
    return bool(s) and bool(_INDIC_RE.search(s)) and not bool(_LATIN_RE.search(s))


def repair_kannada_only_answers(db: Session, dry_run: bool = False) -> Dict[str, int]:
    """Repair isolated questions whose `answer` is pure Kannada/Indic script or contains base64 data URIs
    when an English answer is recoverable from `answer_slide`, `slide.speaker_notes`, or `explanation`.
    """
    from backend.app.models.document import Slide
    from backend.app.models.question import Question
    from backend.app.services.deduplication.deduplicator import Deduplicator
    from backend.app.services.ingestion.question_extractor import QuestionExtractor

    extractor = QuestionExtractor()
    rows: List[Question] = db.query(Question).all()
    slide_ids: Set[str] = set()
    candidates: List[Question] = []
    for q in rows:
        ans = (q.answer or "").strip()
        exp = (q.explanation or "").strip()
        if (
            _is_indic_without_latin(ans)
            or ans.lower().startswith("data:image/")
            or "data:image/" in exp.lower()
        ):
            candidates.append(q)
            if q.answer_slide_id:
                slide_ids.add(q.answer_slide_id)
            if q.slide_id:
                slide_ids.add(q.slide_id)

    if not candidates:
        return {"repaired": 0, "sanitized": 0}

    slide_map: Dict[str, Slide] = {}
    if slide_ids:
        for s in db.query(Slide).filter(Slide.id.in_(list(slide_ids))).all():
            slide_map[s.id] = s

    repaired = 0
    sanitized = 0
    for q in candidates:
        changed = False
        clean_exp = extractor._strip_data_uris(q.explanation or "")
        if clean_exp != (q.explanation or "").strip():
            if not dry_run:
                q.explanation = clean_exp
            changed = True
            sanitized += 1

        if (q.answer or "").strip().lower().startswith("data:image/"):
            ans_slide = slide_map.get(q.answer_slide_id) if q.answer_slide_id else slide_map.get(q.slide_id)
            new_ans = ""
            new_exp = clean_exp
            if ans_slide and ans_slide.extracted_text:
                new_ans, parsed_exp = extractor._parse_answer_and_explanation(ans_slide.extracted_text)
                if parsed_exp and not new_exp:
                    new_exp = parsed_exp
            if not extractor._is_usable_answer_text(new_ans) and ans_slide and ans_slide.title and ans_slide.title != "NO_TITLE":
                t_ans, t_exp = extractor._parse_answer_and_explanation(ans_slide.title)
                if extractor._is_usable_answer_text(t_ans):
                    new_ans = t_ans
                    if t_exp and not new_exp:
                        new_exp = t_exp
            if extractor._is_usable_answer_text(new_ans):
                if not dry_run:
                    q.answer = new_ans
                    q.explanation = new_exp
                    q.content_hash = Deduplicator.compute_hash(q.question_text, new_ans)
                repaired += 1
                changed = True

        elif _is_indic_without_latin(q.answer):
            ans_slide = slide_map.get(q.answer_slide_id) if q.answer_slide_id else None
            sources = [
                ans_slide.extracted_text if ans_slide else "",
                ans_slide.speaker_notes if ans_slide else "",
                f"{q.answer}\n{clean_exp}".strip(),
                slide_map[q.slide_id].speaker_notes if q.slide_id and q.slide_id in slide_map else "",
            ]
            for src in sources:
                if not src:
                    continue
                cand_ans, cand_exp = extractor._parse_answer_and_explanation(src)
                if extractor._is_usable_answer_text(cand_ans) and _LATIN_RE.search(cand_ans):
                    if not dry_run:
                        q.answer = cand_ans
                        if cand_exp:
                            q.explanation = cand_exp
                        q.content_hash = Deduplicator.compute_hash(q.question_text, cand_ans)
                    repaired += 1
                    changed = True
                    break

    if not dry_run and (repaired or sanitized):
        db.commit()
    return {"repaired": repaired, "sanitized": sanitized}


def _delete_orphan_questions(db: Session, dead_ids: List[str]) -> None:
    if not dead_ids:
        return
    from backend.app.models.question import Question
    from backend.app.models.qshala_tag import QshalaQuestionMetadata, QshalaTag, QshalaTagChange
    from backend.app.models.question_meta import (
        QuestionEditHistory,
        QuestionRepeat,
        QuestionTopicPath,
    )
    from backend.app.models.quiz import RetrievalSource

    db.query(Question).filter(Question.duplicate_of_id.in_(dead_ids)).update(
        {Question.duplicate_of_id: None}, synchronize_session=False
    )
    db.query(RetrievalSource).filter(RetrievalSource.historical_question_id.in_(dead_ids)).update(
        {RetrievalSource.historical_question_id: None}, synchronize_session=False
    )
    db.query(QshalaTagChange).filter(QshalaTagChange.question_id.in_(dead_ids)).delete(synchronize_session=False)
    db.query(QshalaTag).filter(QshalaTag.question_id.in_(dead_ids)).delete(synchronize_session=False)
    db.query(QshalaQuestionMetadata).filter(QshalaQuestionMetadata.question_id.in_(dead_ids)).delete(
        synchronize_session=False
    )
    db.query(QuestionTopicPath).filter(QuestionTopicPath.question_id.in_(dead_ids)).delete(synchronize_session=False)
    db.query(QuestionRepeat).filter(QuestionRepeat.question_id.in_(dead_ids)).delete(synchronize_session=False)
    db.query(QuestionEditHistory).filter(QuestionEditHistory.question_id.in_(dead_ids)).delete(
        synchronize_session=False
    )
    db.query(Question).filter(Question.id.in_(dead_ids)).delete(synchronize_session=False)


def _collect_salvage_media(
    existing_qs: List[Any],
    slide_dicts: List[Dict[str, Any]],
) -> Tuple[Set[str], Dict[str, Dict[str, Any]]]:
    """Index unique pre-existing audio/video refs by slide ID while filtering furniture and timers."""
    url_slides: Dict[str, Set[str]] = defaultdict(set)
    for sd in slide_dicts:
        sid = sd.get("id") or str(sd.get("slide_number") or "")
        for u in (sd.get("audio_paths") or []) + (sd.get("video_paths") or []):
            if u:
                url_slides[u].add(sid)
    for eq in existing_qs:
        sid = eq.slide_id or eq.id
        for u in (
            (eq.audio_refs or [])
            + (eq.question_audio or [])
            + (eq.answer_audio or [])
            + (eq.video_refs or [])
            + (eq.question_videos or [])
            + (eq.answer_videos or [])
        ):
            if u:
                url_slides[u].add(sid)

    ignored_urls: Set[str] = {
        u
        for u, sids in url_slides.items()
        if _is_timer_media_url(u) or len(sids) >= _FURNITURE_MEDIA_THRESHOLD
    }
    by_slide: Dict[str, Dict[str, Any]] = {}

    def _entry(slide_key: str) -> Dict[str, Any]:
        ent = by_slide.get(slide_key)
        if ent is None:
            ent = {"qvids": [], "avids": [], "qauds": [], "aauds": [], "vt": "", "at": ""}
            by_slide[slide_key] = ent
        return ent

    def _keep(urls: Optional[List[str]]) -> List[str]:
        return [u for u in (urls or []) if u and u not in ignored_urls]

    for eq in existing_qs:
        if eq.slide_id:
            ent = _entry(eq.slide_id)
            av = _keep(eq.answer_videos)
            qv = _keep(eq.question_videos) or [u for u in _keep(eq.video_refs) if u not in av]
            aa = _keep(eq.answer_audio)
            qa = _keep(eq.question_audio) or [u for u in _keep(eq.audio_refs) if u not in aa]
            ent["qvids"].extend(qv)
            ent["avids"].extend(av)
            ent["qauds"].extend(qa)
            ent["aauds"].extend(aa)
            if (eq.video_transcript or "").strip() and not ent["vt"]:
                ent["vt"] = (eq.video_transcript or "").strip()
            if (eq.audio_transcript or "").strip() and not ent["at"]:
                ent["at"] = (eq.audio_transcript or "").strip()
        if eq.answer_slide_id:
            aent = _entry(eq.answer_slide_id)
            aent["avids"].extend(_keep(eq.answer_videos))
            aent["aauds"].extend(_keep(eq.answer_audio))

    return ignored_urls, by_slide


def _merge_item_media_and_format(
    item: Dict[str, Any],
    q_fmt: str,
    ignored_urls: Set[str],
    slide_media: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Merge extracted and salvaged non-furniture media and promote VIDEO_CLUE/AUDIO when warranted."""
    sid = item.get("slide_id")
    asid = item.get("answer_slide_id")
    q_saved = slide_media.get(sid) or {}
    a_saved = slide_media.get(asid) or {}

    def _clean(urls: List[str]) -> List[str]:
        return list(dict.fromkeys(u for u in urls if u and u not in ignored_urls))

    q_videos = _clean(list(item.get("question_videos") or []) + list(q_saved.get("qvids") or []))
    a_videos = _clean(
        list(item.get("answer_videos") or [])
        + list(q_saved.get("avids") or [])
        + list(a_saved.get("avids") or [])
        + list(a_saved.get("qvids") or [])
    )
    all_videos = _clean(list(item.get("video_refs") or []) + q_videos + a_videos)

    q_audio = _clean(list(item.get("question_audio") or []) + list(q_saved.get("qauds") or []))
    a_audio = _clean(
        list(item.get("answer_audio") or [])
        + list(q_saved.get("aauds") or [])
        + list(a_saved.get("aauds") or [])
        + list(a_saved.get("qauds") or [])
    )
    all_audio = _clean(list(item.get("audio_refs") or []) + q_audio + a_audio)

    q_type = item.get("question_type") or "SLIDE_QA"
    out_fmt = q_fmt
    if q_videos and out_fmt in ("QA", "SLIDE_QA", "STANDARD_QA", "VISUAL_CLUE"):
        out_fmt = "VIDEO_CLUE"
        q_type = "VIDEO_CLUE"
    elif q_audio and not q_videos and out_fmt in ("QA", "SLIDE_QA", "STANDARD_QA", "VISUAL_CLUE"):
        out_fmt = "AUDIO"
        q_type = "AUDIO"

    return {
        "q_fmt": out_fmt,
        "q_type": q_type,
        "question_videos": q_videos,
        "answer_videos": a_videos,
        "video_refs": all_videos,
        "question_audio": q_audio,
        "answer_audio": a_audio,
        "audio_refs": all_audio,
        "video_transcript": (item.get("video_transcript") or "").strip() or q_saved.get("vt") or a_saved.get("vt") or None,
        "audio_transcript": (item.get("audio_transcript") or "").strip() or q_saved.get("at") or a_saved.get("at") or None,
    }


def repair_broken_deck_extractions(db: Session, dry_run: bool = False) -> Dict[str, Any]:
    """Re-classify and re-extract any document whose persisted questions contain broken
    artifacts ('?????!', 'Answer!', 'data:image/...', bumpers like 'Ready for Q1.',
    answer slides leaked as questions, merged STEPPED_CLUE in Image Reveal rounds,
    mislabeled List-It as CONNECT_4, multi-slide Connect blobs >=10 images, or missing List-It/Connect).
    """
    summary = {
        "repaired_docs": 0,
        "updated_questions": 0,
        "rescued_questions": 0,
        "removed_orphans": 0,
        "kannada_fallback": {"repaired": 0, "sanitized": 0},
    }

    if not dry_run:
        try:
            bind = db.get_bind()
            if bind is not None and getattr(bind.dialect, "name", "") == "postgresql":
                acquired = db.execute(text("SELECT pg_try_advisory_xact_lock(847291)")).scalar()
                if not acquired:
                    logger.info("[repair] Another worker holds repair lock; skipping startup repair")
                    return summary
        except Exception as exc:
            logger.debug("[repair] Advisory lock check skipped: %s", exc)

    from backend.app.models.document import Document, Slide
    from backend.app.models.qshala_tag import QshalaTag
    from backend.app.models.question import Question, generate_uuid
    from backend.app.models.question_meta import DeckMetadata
    from backend.app.services.deduplication.deduplicator import Deduplicator
    from backend.app.services.ingestion.deck_metadata import annotate_questions
    from backend.app.services.ingestion.question_extractor import QuestionExtractor
    from backend.app.services.ingestion.slide_classifier import SlideClassifier
    from backend.app.services.question_meta_service import (
        deck_row_to_info,
        ensure_question_metadata,
        upsert_deck_metadata,
    )
    from backend.app.services.tagging.difficulty_engine import DifficultyEngine
    from backend.app.services.tagging.qshala_rules import classify_taxonomy
    from backend.app.services.tagging.topic_paths import has_human_paths, replace_paths

    rows = (
        db.query(
            Question.document_id,
            Question.slide_id,
            Question.question_text,
            Question.answer,
            Question.question_format,
            Question.subtopic,
            Question.question_images,
            Question.image_refs,
        )
        .all()
    )
    broken_doc_ids: Set[str] = set()
    doc_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"list_it": 0, "connect": 0, "stepped": 0, "total": 0})
    doc_q_sids: Dict[str, Set[str]] = defaultdict(set)

    for doc_id, sid, q_text, ans, q_fmt, subtopic, q_imgs, img_refs in rows:
        fmt_u = (q_fmt or "").upper()
        sub_low = (subtopic or "").lower()
        st = doc_stats[doc_id]
        st["total"] += 1
        if sid:
            doc_q_sids[doc_id].add(sid)
        if fmt_u == "LIST_IT":
            st["list_it"] += 1
        elif fmt_u in ("CONNECT_4", "CONNECT"):
            st["connect"] += 1
        elif fmt_u == "STEPPED_CLUE":
            st["stepped"] += 1

        if is_broken_answer_artifact(ans) or is_bumper_question_artifact(q_text):
            broken_doc_ids.add(doc_id)
            continue
        if fmt_u in ("CONNECT_4", "CONNECT") and (
            len(q_imgs or img_refs or []) >= 10
            or ("|" in (q_text or ""))
            or ("list" in sub_low)
            or ("image reveal" in sub_low)
        ):
            broken_doc_ids.add(doc_id)
            continue
        if fmt_u == "STEPPED_CLUE" and "image reveal" in sub_low:
            broken_doc_ids.add(doc_id)

    all_doc_ids = list(doc_stats.keys())
    if all_doc_ids:
        light_slides = (
            db.query(
                Slide.document_id,
                Slide.id,
                Slide.slide_number,
                Slide.slide_type,
                Slide.title,
                Slide.extracted_text,
            )
            .filter(Slide.document_id.in_(all_doc_ids))
            .order_by(Slide.document_id, Slide.slide_number.asc())
            .all()
        )
        slides_by_doc: Dict[str, List[Any]] = defaultdict(list)
        for ls in light_slides:
            slides_by_doc[ls[0]].append(ls)

        for did, slist in slides_by_doc.items():
            if did in broken_doc_ids:
                continue
            q_sids = doc_q_sids.get(did, set())
            st = doc_stats[did]
            has_list_it_round = False
            has_image_reveal_round = False
            has_portfolio = False
            connect_hdr_idx: Optional[int] = None
            connect_end_idx = len(slist)

            for idx_s, (_, sid_s, _, stype_s, title_s, txt_s) in enumerate(slist):
                blob = f"{title_s or ''} {txt_s or ''}".lower()
                if "list it" in blob or "list-it" in blob:
                    has_list_it_round = True
                if "image reveal" in blob:
                    has_image_reveal_round = True
                if "portfolio builder" in blob:
                    has_portfolio = True
                if "connect" in blob and ("round" in blob or (stype_s == "SECTION_MARKER" and len(blob.split()) <= 10)):
                    if connect_hdr_idx is None:
                        connect_hdr_idx = idx_s
                elif connect_hdr_idx is not None and idx_s > connect_hdr_idx and connect_end_idx == len(slist):
                    if re.search(r"\bround\s*\d+", blob):
                        connect_end_idx = idx_s

                if idx_s > 0 and sid_s in q_sids:
                    prev_st = slist[idx_s - 1][3]
                    prev_txt = re.sub(r"\s+", " ", slist[idx_s - 1][5] or "").strip()
                    if prev_st == "ANSWER_TRANSITION" or re.match(
                        r"^(?:(?:answer|answers|ans|solution|ಉತ್ತರ)[\s\!\.\:\-]*)+$", prev_txt, re.I
                    ):
                        broken_doc_ids.add(did)
                        break

            if did in broken_doc_ids:
                continue
            if has_list_it_round and st["list_it"] == 0:
                broken_doc_ids.add(did)
                continue
            if has_image_reveal_round and not has_portfolio and st["stepped"] > 0:
                broken_doc_ids.add(did)
                continue
            if connect_hdr_idx is not None:
                trans_in_connect = sum(
                    1 for k in range(connect_hdr_idx, connect_end_idx)
                    if slist[k][3] == "ANSWER_TRANSITION"
                )
                if trans_in_connect >= 4 and st["connect"] <= 2:
                    broken_doc_ids.add(did)

    _ROUND_SUBTOPIC_TOKENS = (
        "round", "closing bell", "market indicators", "ticker tangle",
        "portfolio builder", "opening bell", "list it", "list-it",
        "image reveal", "connect", "buzz", "audience question",
        "tie breaker", "dry run",
    )

    if broken_doc_ids:
        classifier = SlideClassifier()
        extractor = QuestionExtractor()
        diff_eng = DifficultyEngine()

        docs = (
            db.query(Document)
            .filter(Document.id.in_(sorted(broken_doc_ids)))
            .all()
        )
        for doc in docs:
            slides: List[Slide] = (
                db.query(Slide)
                .filter(Slide.document_id == doc.id)
                .order_by(Slide.slide_number.asc())
                .all()
            )
            if not slides:
                continue

            slide_dicts: List[Dict[str, Any]] = []
            for s in slides:
                mjson = dict(s.metadata_json or {})
                slide_dicts.append({
                    "id": s.id,
                    "slide_number": s.slide_number,
                    "slide_type": s.slide_type,
                    "title": s.title or "",
                    "extracted_text": s.extracted_text or "",
                    "speaker_notes": s.speaker_notes or "",
                    "image_paths": list(s.image_paths or []),
                    "audio_paths": list(getattr(s, "audio_paths", None) or mjson.get("audio_paths") or []),
                    "video_paths": list(getattr(s, "video_paths", None) or mjson.get("video_paths") or []),
                    "audio_items": list(mjson.get("audio_items") or []),
                    "video_items": list(mjson.get("video_items") or []),
                    "all_media_items": list(mjson.get("all_media_items") or []),
                    "has_images": bool(s.has_images),
                    "metadata_json": mjson,
                })

            new_types = classifier.classify_deck(slide_dicts)
            for sd, st in zip(slide_dicts, new_types):
                sd["slide_type"] = st

            # DB Slide rows do not store PPTX shapes/animations needed by _extract_structured;
            # legacy heuristic extraction preserves List-It, Image Reveal, and Connect rounds accurately.
            extracted = extractor.extract_from_slides(slide_dicts, doc.title, doc.year, use_structured=False)
            if not extracted:
                continue

            deck_row: Optional[DeckMetadata] = None
            deck_info = None
            try:
                deck_row = upsert_deck_metadata(db, doc)
                deck_info = deck_row_to_info(deck_row)
            except Exception:
                deck_row = None

            annotate_questions(extracted, slide_dicts, deck_info)

            if dry_run:
                summary["repaired_docs"] += 1
                continue

            for s_model, new_st in zip(slides, new_types):
                if s_model.slide_type != new_st:
                    s_model.slide_type = new_st

            existing_qs: List[Question] = (
                db.query(Question)
                .filter(Question.document_id == doc.id)
                .order_by(Question.created_at.asc())
                .all()
            )
            ignored_urls, slide_media = _collect_salvage_media(existing_qs, slide_dicts)
            by_slide: Dict[Optional[str], List[Question]] = defaultdict(list)
            for eq in existing_qs:
                by_slide[eq.slide_id].append(eq)

            matched_ids: Set[str] = set()
            for item in extracted:
                sid = item.get("slide_id")
                q_text = (item.get("question_text") or "").strip()
                ans_text = (item.get("answer") or "").strip()
                if not q_text or not extractor._is_usable_answer_text(ans_text):
                    continue

                meta_dict = item.get("_meta") or {}
                raw_fmt = meta_dict.get("question_format") or item.get("question_type") or "QA"
                media_info = _merge_item_media_and_format(item, raw_fmt, ignored_urls, slide_media)
                q_fmt = media_info["q_fmt"]
                q_type = media_info["q_type"]
                c_hash = Deduplicator.compute_hash(q_text, ans_text)

                rule_l1, rule_l2, rule_keywords = classify_taxonomy(
                    q_text, ans_text, item.get("subtopic") or "", q_fmt
                )
                raw_sub = (item.get("subtopic") or "").strip()
                if not raw_sub or raw_sub.lower() in {"topic", "topics", "direct", "general"} or any(w in raw_sub.lower() for w in _ROUND_SUBTOPIC_TOKENS):
                    final_subtopic = rule_l2
                else:
                    final_subtopic = raw_sub
                all_tags = list(dict.fromkeys((item.get("tags") or []) + rule_keywords))

                structured_fmt_data = (
                    item.get("format_payload")
                    if q_fmt in ("CONNECT_4", "CONNECT", "STEPPED_CLUE", "ARRANGE_ORDER", "LIST_IT", "IMAGE_REVEAL")
                    else None
                )

                bucket = by_slide.get(sid) if sid else None
                if bucket:
                    target = bucket.pop(0)
                    matched_ids.add(target.id)
                    target.question_text = q_text
                    target.answer = ans_text
                    target.options = item.get("options")
                    target.explanation = item.get("explanation") or ""
                    target.answer_slide_id = item.get("answer_slide_id")
                    target.source_slide_range = item.get("source_slide_range")
                    target.round_number = meta_dict.get("round_number", item.get("round_number"))
                    target.question_type = q_type or target.question_type
                    target.question_format = q_fmt
                    target.format_payload = item.get("format_payload")
                    target.format_data = structured_fmt_data
                    target.play_mode = item.get("play_mode") or getattr(target, "play_mode", None)
                    target.slot = item.get("slot") or getattr(target, "slot", None) or "LIVE"
                    target.topic_label = item.get("topic_label") or getattr(target, "topic_label", None)
                    target.format_needs_review = False
                    target.topic = rule_l1
                    target.subtopic = final_subtopic
                    target.topics = [rule_l1]
                    target.tags = all_tags
                    target.image_refs = list(item.get("image_refs") or [])
                    target.question_images = list(item.get("question_images") or [])
                    target.answer_images = list(item.get("answer_images") or [])
                    target.audio_refs = media_info["audio_refs"]
                    target.question_audio = media_info["question_audio"]
                    target.answer_audio = media_info["answer_audio"]
                    target.video_refs = media_info["video_refs"]
                    target.question_videos = media_info["question_videos"]
                    target.answer_videos = media_info["answer_videos"]
                    if media_info["video_transcript"] and not (target.video_transcript or "").strip():
                        target.video_transcript = media_info["video_transcript"]
                    if media_info["audio_transcript"] and not (target.audio_transcript or "").strip():
                        target.audio_transcript = media_info["audio_transcript"]
                    target.content_hash = c_hash

                    if not has_human_paths(db, target.id):
                        replace_paths(
                            db,
                            target.id,
                            [{"l1": rule_l1, "l2": final_subtopic, "keywords": all_tags}],
                            source="LLM",
                            record_history=False,
                            question=target,
                        )
                    qtags = (
                        db.query(QshalaTag)
                        .filter(QshalaTag.question_id == target.id, QshalaTag.source != "HUMAN")
                        .all()
                    )
                    for qtag in qtags:
                        qtag.topic_l1 = target.topic
                        qtag.subtopic_l2 = target.subtopic
                        qtag.keywords_l3 = all_tags

                    if deck_row is not None:
                        ensure_question_metadata(db, target, qmeta=meta_dict, deck=deck_row, doc=doc)
                    summary["updated_questions"] += 1
                else:
                    q_imgs = list(item.get("question_images") or [])
                    a_imgs = list(item.get("answer_images") or [])
                    is_mm = bool(
                        q_imgs
                        or a_imgs
                        or media_info["question_videos"]
                        or media_info["answer_videos"]
                        or media_info["question_audio"]
                        or media_info["answer_audio"]
                    )
                    d_data = diff_eng.evaluate(
                        q_text, ans_text, item.get("explanation") or "", is_multimodal=is_mm
                    )
                    new_q = Question(
                        id=generate_uuid(),
                        content_hash=c_hash,
                        document_id=doc.id,
                        slide_id=sid,
                        answer_slide_id=item.get("answer_slide_id"),
                        question_text=q_text,
                        answer=ans_text,
                        options=item.get("options"),
                        explanation=item.get("explanation") or "",
                        topic=rule_l1,
                        subtopic=final_subtopic,
                        topics=[rule_l1],
                        tags=all_tags,
                        difficulty=d_data["difficulty"],
                        difficulty_score=d_data["difficulty_score"],
                        cognitive_level=d_data["cognitive_level"],
                        audience_suitability=d_data["audience_suitability"],
                        provenance_decks=[{"document_id": doc.id, "slide_id": sid}],
                        occurrence_count=1,
                        question_type=q_type,
                        question_format=q_fmt,
                        format_payload=item.get("format_payload"),
                        format_data=structured_fmt_data,
                        play_mode=item.get("play_mode"),
                        slot=item.get("slot") or "LIVE",
                        topic_label=item.get("topic_label"),
                        format_needs_review=False,
                        source_year=item.get("source_year") or doc.year,
                        round_number=meta_dict.get("round_number", item.get("round_number")),
                        image_refs=list(item.get("image_refs") or []),
                        question_images=q_imgs,
                        answer_images=a_imgs,
                        audio_refs=media_info["audio_refs"],
                        question_audio=media_info["question_audio"],
                        answer_audio=media_info["answer_audio"],
                        video_refs=media_info["video_refs"],
                        question_videos=media_info["question_videos"],
                        answer_videos=media_info["answer_videos"],
                        audio_transcript=media_info["audio_transcript"],
                        video_transcript=media_info["video_transcript"],
                        source_slide_range=item.get("source_slide_range"),
                    )
                    db.add(new_q)
                    db.flush()
                    replace_paths(
                        db,
                        new_q.id,
                        [{"l1": rule_l1, "l2": final_subtopic, "keywords": all_tags}],
                        source="LLM",
                        record_history=False,
                        question=new_q,
                    )
                    if deck_row is not None:
                        ensure_question_metadata(db, new_q, qmeta=meta_dict, deck=deck_row, doc=doc)
                    summary["rescued_questions"] += 1

            dead_ids = [eq.id for eq in existing_qs if eq.id not in matched_ids]
            if dead_ids:
                _delete_orphan_questions(db, dead_ids)
                summary["removed_orphans"] += len(dead_ids)

            db.flush()
            doc.question_count = db.query(Question).filter(Question.document_id == doc.id).count()
            summary["repaired_docs"] += 1

        db.commit()

    summary["kannada_fallback"] = repair_kannada_only_answers(db, dry_run=dry_run)
    if not dry_run and (
        summary["repaired_docs"]
        or summary["kannada_fallback"].get("repaired")
        or summary["kannada_fallback"].get("sanitized")
    ):
        try:
            from backend.app.routers.stats import invalidate_stats_cache

            invalidate_stats_cache()
        except Exception:
            pass
        try:
            from backend.app.services.prompt_quiz.engine import invalidate_bank_cache

            invalidate_bank_cache()
        except Exception:
            pass
    return summary
