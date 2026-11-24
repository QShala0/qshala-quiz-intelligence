"""Reads and writes the per-deck and per-question metadata behind the v6 tags.

Storage model
-------------
* `deck_metadata` holds what the deck's file name says (quiz, college, city, round, language, audience, Varsity).
* `qshala_question_metadata` holds the *effective* value for every question. It starts as a copy of the deck
  values and is overwritten by the tagger (geography, relevant year) or by a person.
* `manual_fields` (on both) lists the fields a person edited. A deck edit, a re-parse or a re-tag never
  overwrites those.
* Every manual change is written to `question_edit_history`.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from backend.app.models.document import Document
from backend.app.models.qshala_tag import QshalaQuestionMetadata
from backend.app.models.question import Question
from backend.app.models.question_meta import DeckMetadata, QuestionEditHistory, QuestionRepeat
from backend.app.services.ingestion.deck_metadata import DeckInfo, parse_deck_filename, question_label

# deck_metadata column -> qshala_question_metadata column
DECK_TO_QUESTION_FIELD: Dict[str, str] = {
    "quiz_name": "quiz_name",
    "college": "college_institution",
    "city": "city_name",
    "round_stage": "round_stage",
    "deck_year": "deck_year",
    "language": "language",
    "primary_audience": "primary_audience",
    "is_varsity": "is_varsity",
}
DECK_EDITABLE = set(DECK_TO_QUESTION_FIELD)

# Fields a person may edit on a single question (everything except the repeated list and history).
QUESTION_EDITABLE = {
    "question_number", "round_number", "question_in_round", "question_format", "star_marked", "answer_type",
    "quiz_name", "college_institution", "city_name", "round_stage", "deck_year", "language",
    "primary_audience", "secondary_audience", "is_varsity", "relevant_year", "geo_region",
    "india_international", "source_presentation",
}
INT_FIELDS = {"question_number", "round_number", "question_in_round", "deck_year", "relevant_year"}
BOOL_FIELDS = {"star_marked", "is_varsity"}

QUESTION_FORMATS = [
    "QA",
    "CONNECT_4",
    "STEPPED_CLUE",
    "ARRANGE_ORDER",
    "BUZZER",
    "VISUAL_CLUE",
    "AUDIO_CLUE",
    "VIDEO_CLUE",
    "POTPOURRI",
    "IMAGE_REVEAL",
    "LIST_IT",
    "CONNECT",
    "THREE_CLUE",
]

FORMAT_LABELS: Dict[str, str] = {
    "QA": "Question and Answer (Direct)",
    "CONNECT_4": "Connect 4",
    "CONNECT": "Connect 4",
    "STEPPED_CLUE": "3 Clue",
    "THREE_CLUE": "3 Clue",
    "ARRANGE_ORDER": "Arrange the Order (Ticker Tangle)",
    "BUZZER": "Buzzer (Closing Bell / Rapid Fire)",
    "VISUAL_CLUE": "Visual Clue",
    "AUDIO_CLUE": "Audio Clue",
    "VIDEO_CLUE": "Video Clue",
    "POTPOURRI": "Potpourri",
    "IMAGE_REVEAL": "Image Reveal",
    "LIST_IT": "List It",
}
ANSWER_TYPES = ["TEXT", "TEXT_IMAGE", "IMAGE", "TEXT_VIDEO", "VIDEO", "TEXT_AUDIO", "AUDIO"]
GEOGRAPHIES = ["IN", "INTL", "NA"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------- edit history

def _s(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def log_edit(db: Session, question_id: str, field: str, old: Any, new: Any,
             kind: str = "EDIT", actor: Optional[str] = None) -> None:
    if _s(old) == _s(new):
        return
    db.add(QuestionEditHistory(question_id=question_id, field=field, old_value=_s(old), new_value=_s(new),
                               kind=kind, actor=actor))


# ---------------------------------------------------------------------- deck metadata

def deck_row_to_info(row: DeckMetadata) -> DeckInfo:
    return DeckInfo(
        quiz_name=row.quiz_name, college=row.college, city=row.city, round_stage=row.round_stage,
        deck_date=row.deck_date, deck_year=row.deck_year, language=row.language,
        primary_audience=row.primary_audience, is_varsity=bool(row.is_varsity),
        source_presentation=row.source_presentation, parsed_ok=bool(row.parsed_ok),
    )


def upsert_deck_metadata(db: Session, doc: Document, reparse: bool = False) -> DeckMetadata:
    """Create (or refresh) the deck row from the document's file name. Manual edits are never overwritten."""
    info = parse_deck_filename(doc.filename, doc.title)
    row = db.query(DeckMetadata).filter(DeckMetadata.document_id == doc.id).first()
    values = {
        "quiz_name": info.quiz_name, "college": info.college, "city": info.city,
        "round_stage": info.round_stage, "deck_date": info.deck_date,
        "deck_year": info.deck_year or doc.year, "language": info.language or "English",
        "primary_audience": info.primary_audience, "is_varsity": info.is_varsity,
        "source_presentation": info.source_presentation or doc.filename, "parsed_ok": info.parsed_ok,
    }
    if row is None:
        row = DeckMetadata(document_id=doc.id, manual_fields=[], **values)
        db.add(row)
        db.flush()
        return row
    if reparse:
        manual = set(row.manual_fields or [])
        for k, v in values.items():
            if k not in manual:
                setattr(row, k, v)
        db.flush()
    return row


def deck_to_dict(row: Optional[DeckMetadata], doc: Optional[Document] = None) -> Dict[str, Any]:
    if row is None:
        return {"document_id": doc.id if doc else None}
    return {
        "document_id": row.document_id, "quiz_name": row.quiz_name, "college": row.college, "city": row.city,
        "round_stage": row.round_stage, "deck_date": row.deck_date, "deck_year": row.deck_year,
        "language": row.language, "primary_audience": row.primary_audience, "is_varsity": bool(row.is_varsity),
        "source_presentation": row.source_presentation, "parsed_ok": bool(row.parsed_ok),
        "manual_fields": list(row.manual_fields or []),
    }


def _coerce(field: str, value: Any) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if field in INT_FIELDS:
        return int(value)
    if field in BOOL_FIELDS:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "y")
        return bool(value)
    if isinstance(value, str):
        return value.strip()
    return value


def update_deck_metadata(db: Session, document_id: str, changes: Dict[str, Any], actor: Optional[str] = None) -> Dict[str, Any]:
    """Edit deck details and push the change to every question of the deck that was not hand-edited."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise LookupError("Document not found")
    row = upsert_deck_metadata(db, doc)
    manual = set(row.manual_fields or [])
    changed: List[str] = []
    for field, raw in (changes or {}).items():
        if field not in DECK_EDITABLE:
            continue
        val = _coerce(field, raw)
        if field == "language" and val is None:
            val = "English"
        if getattr(row, field) != val:
            setattr(row, field, val)
            changed.append(field)
            manual.add(field)
    row.manual_fields = sorted(manual)
    updated = propagate_deck_fields(db, document_id, changed, actor=actor)
    db.flush()
    out = deck_to_dict(row, doc)
    out["questions_updated"] = updated
    return out


def propagate_deck_fields(db: Session, document_id: str, fields: Iterable[str], actor: Optional[str] = None) -> int:
    row = db.query(DeckMetadata).filter(DeckMetadata.document_id == document_id).first()
    if not row:
        return 0
    fields = [f for f in fields if f in DECK_TO_QUESTION_FIELD]
    if not fields:
        return 0
    metas = (db.query(QshalaQuestionMetadata, Question.id)
             .join(Question, Question.id == QshalaQuestionMetadata.question_id)
             .filter(Question.document_id == document_id).all())
    n = 0
    for meta, qid in metas:
        manual = set(meta.manual_fields or [])
        touched = False
        for f in fields:
            qf = DECK_TO_QUESTION_FIELD[f]
            if qf in manual:
                continue
            new = getattr(row, f)
            if f == "language" and not new:
                new = "English"
            old = getattr(meta, qf)
            if old != new:
                setattr(meta, qf, new)
                log_edit(db, qid, qf, old, new, kind="SYSTEM", actor=actor or "deck edit")
                touched = True
        n += 1 if touched else 0
    return n


# ---------------------------------------------------------------------- question metadata

def ensure_question_metadata(
    db: Session,
    question: Question,
    qmeta: Optional[Dict[str, Any]],
    deck: Optional[DeckMetadata],
    doc: Optional[Document] = None,
) -> QshalaQuestionMetadata:
    """Create the effective-metadata row for a question (or refresh the non-manual fields of an existing one)."""
    row = db.query(QshalaQuestionMetadata).filter(QshalaQuestionMetadata.question_id == question.id).first()
    values = _metadata_values(question, qmeta, deck, doc)
    if row is None:
        row = QshalaQuestionMetadata(question_id=question.id, manual_fields=[], **values)
        db.add(row)
        db.flush()
        return row
    manual = set(row.manual_fields or [])
    for k, v in values.items():
        if k in manual:
            continue
        if v is None and getattr(row, k, None) is not None and k in ("quiz_name", "college_institution", "city_name"):
            continue
        setattr(row, k, v)
    db.flush()
    return row


def add_new_question_metadata(
    db: Session,
    questions_and_meta: Iterable[tuple],
    deck: Optional[DeckMetadata],
    doc: Optional[Document] = None,
) -> int:
    """Queue metadata rows for questions that were created a moment ago (so no row can exist yet).

    The same values as `ensure_question_metadata`, but without a lookup and a flush per question: the rows are added
    together and written by the caller's next flush or commit, in one batch.
    """
    rows = [QshalaQuestionMetadata(question_id=q.id, manual_fields=[], **_metadata_values(q, qmeta, deck, doc))
            for q, qmeta in questions_and_meta]
    db.add_all(rows)
    return len(rows)


def _metadata_values(
    question: Question,
    qmeta: Optional[Dict[str, Any]],
    deck: Optional[DeckMetadata],
    doc: Optional[Document],
) -> Dict[str, Any]:
    qmeta = qmeta or {}
    deck_date = None
    if deck and deck.deck_date:
        try:
            deck_date = datetime.fromisoformat(deck.deck_date).replace(tzinfo=timezone.utc)
        except ValueError:
            deck_date = None
    return {
        "question_number": qmeta.get("question_number"),
        "round_number": qmeta.get("round_number", question.round_number),
        "question_in_round": qmeta.get("question_in_round"),
        "question_format": qmeta.get("question_format") or "QA",
        "star_marked": bool(qmeta.get("star_marked", False)),
        "answer_type": qmeta.get("answer_type"),
        "media_type": qmeta.get("media_type"),
        "language": qmeta.get("language") or (deck.language if deck else None) or "English",
        "quiz_name": deck.quiz_name if deck else (doc.title if doc else None),
        "college_institution": deck.college if deck else None,
        "city_name": deck.city if deck else None,
        "round_stage": deck.round_stage if deck else None,
        "deck_year": (deck.deck_year if deck else None) or (doc.year if doc else None) or question.source_year,
        "primary_audience": deck.primary_audience if deck else None,
        "is_varsity": bool(deck.is_varsity) if deck else False,
        "source_presentation": (deck.source_presentation if deck else None) or (doc.filename if doc else None),
        "source_date": deck_date,
    }


def _copy_tag_to_meta(meta: QshalaQuestionMetadata, tag_row: Any) -> None:
    manual = set(meta.manual_fields or [])
    if "geo_region" not in manual and getattr(tag_row, "geo_region", None):
        meta.geo_region = tag_row.geo_region
    if "india_international" not in manual:
        v = getattr(tag_row, "india_international", None)
        meta.india_international = v if v in ("IN", "INTL", "NA") else "NA"
    if "relevant_year" not in manual:
        meta.relevant_year = getattr(tag_row, "relevant_year", None)


def apply_tag_to_meta(db: Session, question_id: str, tag_row: Any) -> None:
    """Copy the tagger's geography and relevant year onto the question's metadata (unless a person set them)."""
    meta = db.query(QshalaQuestionMetadata).filter(QshalaQuestionMetadata.question_id == question_id).first()
    if meta is None or tag_row is None:
        return
    _copy_tag_to_meta(meta, tag_row)
    db.flush()


_BULK_IN_CHUNK = 500


def apply_latest_tags_to_meta(db: Session, question_ids: List[str]) -> int:
    """`apply_tag_to_meta` for many questions: the newest tag of each one, found with one query per 500 questions, and
    the metadata rows loaded the same way, instead of two queries per question. Returns how many rows were updated."""
    from backend.app.models.qshala_tag import QshalaTag
    updated = 0
    ids = list(question_ids)
    for i in range(0, len(ids), _BULK_IN_CHUNK):
        part = ids[i:i + _BULK_IN_CHUNK]
        latest: Dict[str, Any] = {}
        for tag in (db.query(QshalaTag).filter(QshalaTag.question_id.in_(part))
                    .order_by(QshalaTag.created_at.desc())):
            latest.setdefault(tag.question_id, tag)          # newest first, so the first one seen is the latest
        if not latest:
            continue
        metas = db.query(QshalaQuestionMetadata).filter(QshalaQuestionMetadata.question_id.in_(list(latest))).all()
        for meta in metas:
            _copy_tag_to_meta(meta, latest[meta.question_id])
            updated += 1
    db.flush()
    return updated


def update_question_metadata(db: Session, question_id: str, changes: Dict[str, Any], actor: Optional[str] = None) -> QshalaQuestionMetadata:
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise LookupError("Question not found")
    meta = db.query(QshalaQuestionMetadata).filter(QshalaQuestionMetadata.question_id == question_id).first()
    if meta is None:
        doc = db.query(Document).filter(Document.id == q.document_id).first()
        deck = upsert_deck_metadata(db, doc) if doc else None
        meta = ensure_question_metadata(db, q, {}, deck, doc)
    manual = set(meta.manual_fields or [])
    for field, raw in (changes or {}).items():
        if field not in QUESTION_EDITABLE:
            continue
        val = _coerce(field, raw)
        if field == "question_format" and val is not None:
            val = str(val).upper().replace(" ", "_")
            if val not in QUESTION_FORMATS:
                raise ValueError(f"Unknown question format: {raw}")
        if field == "answer_type" and val is not None:
            val = str(val).upper().replace(" ", "_")
            if val not in ANSWER_TYPES:
                raise ValueError(f"Unknown answer type: {raw}")
        if field == "india_international" and val is not None:
            val = str(val).upper()
            if val not in GEOGRAPHIES:
                raise ValueError(f"Geography must be one of {GEOGRAPHIES}")
        if field == "language" and val is None:
            val = "English"
        old = getattr(meta, field)
        if old != val:
            setattr(meta, field, val)
            log_edit(db, question_id, field, old, val, kind="EDIT", actor=actor)
            if field == "round_number":
                q.round_number = val
            if field == "question_format":
                canonical_fmt = (
                    "CONNECT_4" if val in ("CONNECT", "CONNECT_4")
                    else ("STEPPED_CLUE" if val in ("THREE_CLUE", "STEPPED_CLUE")
                    else val)
                )
                q.question_format = canonical_fmt
                q.question_type = canonical_fmt
                short_tag = (
                    "Connect 4" if canonical_fmt == "CONNECT_4"
                    else "3 Clue" if canonical_fmt == "STEPPED_CLUE"
                    else "Arrange Order" if canonical_fmt == "ARRANGE_ORDER"
                    else "Buzzer" if canonical_fmt == "BUZZER"
                    else "Direct QA" if canonical_fmt in ("QA", "STANDARD_QA")
                    else FORMAT_LABELS.get(canonical_fmt, canonical_fmt)
                )
                fmt_tag_names = set(FORMAT_LABELS.values()) | set(FORMAT_LABELS.keys()) | {
                    "Connect 4", "Connect", "Stepped Clue", "3 Clue", "Portfolio Builder",
                    "Arrange Order", "Ticker Tangle", "Buzzer", "Closing Bell",
                    "Standard QA", "Direct QA", "QA"
                }
                current_tags = [t for t in (q.tags or []) if t not in fmt_tag_names]
                current_tags.append(short_tag)
                q.tags = current_tags

                if canonical_fmt == "CONNECT_4" and not q.format_data:
                    q.format_data = {
                        "type": "CONNECT_4",
                        "badge": "Market Indicators • Connect 4",
                        "clues": q.question_images or [],
                        "connection_reveal": q.answer
                    }
                elif canonical_fmt == "STEPPED_CLUE" and not q.format_data:
                    q.format_data = {
                        "type": "STEPPED_CLUE",
                        "badge": "Portfolio Builder • Stepped Clues",
                        "clues": [{"step": 1, "points": "+20/-15", "text": q.question_text, "image": None}],
                        "answer": q.answer
                    }
                elif canonical_fmt == "ARRANGE_ORDER" and not q.format_data:
                    q.format_data = {
                        "type": "ARRANGE_ORDER",
                        "badge": "Ticker Tangle • Arrange Order",
                        "instruction": q.question_text,
                        "raw_answer": q.answer,
                        "has_timer": True,
                        "timer_seconds": 60
                    }
                elif canonical_fmt == "BUZZER" and not q.format_data:
                    q.format_data = {
                        "type": "BUZZER",
                        "badge": "Closing Bell • Buzzer Round",
                        "scoring": {"correct": "+20", "wrong": "-10"}
                    }
            manual.add(field)
    meta.manual_fields = sorted(manual)
    db.flush()
    return meta


def meta_to_dict(meta: Optional[QshalaQuestionMetadata]) -> Dict[str, Any]:
    if meta is None:
        return {}
    return {
        "question_number": meta.question_number, "round_number": meta.round_number,
        "question_in_round": meta.question_in_round,
        "question_label": question_label(meta.question_number, meta.round_number, meta.question_in_round),
        "question_format": meta.question_format, "star_marked": bool(meta.star_marked),
        "answer_type": meta.answer_type, "media_type": meta.media_type, "language": meta.language,
        "quiz_name": meta.quiz_name, "college_institution": meta.college_institution,
        "city_name": meta.city_name, "round_stage": meta.round_stage, "deck_year": meta.deck_year,
        "primary_audience": meta.primary_audience, "secondary_audience": meta.secondary_audience,
        "is_varsity": bool(meta.is_varsity), "relevant_year": meta.relevant_year,
        "geo_region": meta.geo_region, "india_international": meta.india_international,
        "source_presentation": meta.source_presentation,
        "manual_fields": list(meta.manual_fields or []),
    }


# ---------------------------------------------------------------------- repeated tag

def repeat_label(r: QuestionRepeat) -> str:
    where = question_label(r.question_number, r.round_number, r.question_in_round)
    name = r.quiz_name or r.deck_title or "another quiz"
    return f"{name} · {where}" if where else name


def repeat_to_dict(r: QuestionRepeat) -> Dict[str, Any]:
    return {
        "id": r.id, "document_id": r.document_id, "deck_title": r.deck_title, "quiz_name": r.quiz_name,
        "round_number": r.round_number, "question_in_round": r.question_in_round,
        "question_number": r.question_number, "slide_range": r.slide_range, "match_type": r.match_type,
        "label": repeat_label(r), "found_at": r.found_at.isoformat() if r.found_at else None,
    }


def add_repeat(
    db: Session,
    original: Question,
    doc_id: str,
    qmeta: Optional[Dict[str, Any]],
    slide_range: Optional[str],
    match_type: str = "EXACT",
    actor: Optional[str] = None,
) -> Optional[QuestionRepeat]:
    """Record that `original` turned up again in deck `doc_id`. Returns None if it was already recorded."""
    if original.document_id == doc_id:
        return None
    qmeta = qmeta or {}
    q_no = qmeta.get("question_number")
    exists = db.query(QuestionRepeat).filter(
        QuestionRepeat.question_id == original.id, QuestionRepeat.document_id == doc_id,
        QuestionRepeat.question_number == q_no).first()
    if exists:
        return None
    doc = db.query(Document).filter(Document.id == doc_id).first()
    deck = db.query(DeckMetadata).filter(DeckMetadata.document_id == doc_id).first()
    rep = QuestionRepeat(
        question_id=original.id, document_id=doc_id,
        deck_title=doc.title if doc else None,
        quiz_name=(deck.quiz_name if deck else None) or (doc.title if doc else None),
        round_number=qmeta.get("round_number"), question_in_round=qmeta.get("question_in_round"),
        question_number=q_no, slide_range=slide_range, match_type=match_type,
    )
    db.add(rep)
    db.add(QuestionEditHistory(
        question_id=original.id, field="repeated", old_value=None, new_value=repeat_label(rep),
        kind="REPEAT", actor=actor or "system"))
    db.flush()
    return rep


def get_repeats(db: Session, question_id: str) -> List[Dict[str, Any]]:
    rows = (db.query(QuestionRepeat).filter(QuestionRepeat.question_id == question_id)
            .order_by(QuestionRepeat.found_at).all())
    return [repeat_to_dict(r) for r in rows]


def repeats_for_questions(db: Session, question_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {qid: [] for qid in question_ids}
    if not question_ids:
        return out
    rows = (db.query(QuestionRepeat).filter(QuestionRepeat.question_id.in_(question_ids))
            .order_by(QuestionRepeat.found_at).all())
    for r in rows:
        out.setdefault(r.question_id, []).append(repeat_to_dict(r))
    return out


def remove_repeats_for_document(db: Session, document_id: str) -> int:
    """When a deck is deleted, drop its Repeated entries from other questions and recompute their counts."""
    reps = db.query(QuestionRepeat).filter(QuestionRepeat.document_id == document_id).all()
    affected = {r.question_id for r in reps}
    for r in reps:
        db.delete(r)
    db.flush()
    for qid in affected:
        q = db.query(Question).filter(Question.id == qid).first()
        if not q:
            continue
        remaining = db.query(QuestionRepeat).filter(QuestionRepeat.question_id == qid).count()
        q.occurrence_count = 1 + remaining
        prov = [p for p in (q.provenance_decks or []) if p.get("document_id") != document_id]
        q.provenance_decks = prov
    return len(reps)
