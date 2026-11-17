"""Hierarchical topic tags: Theme > Subtopic > question-specific keywords.

A question can sit under several paths ("Sports" > "Cricket" and "Business" > "Sports Business").
Paths live in `question_topic_paths`; the legacy flat columns on `questions` (topic, subtopic, topics,
tags) are kept in step by `mirror_to_question` so Create Quiz, search and filters keep working.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from backend.app.models.question_meta import QuestionEditHistory, QuestionTopicPath

TAXONOMY_V6_PATH = Path(__file__).resolve().parent / "qshala_taxonomy_v6.json"
UNCATEGORISED = "Uncategorised"
MAX_PATHS = 6
MAX_KEYWORDS = 12

_cache: Dict[str, Any] = {}


def taxonomy_v6() -> Dict[str, Any]:
    if _cache:
        return _cache
    with open(TAXONOMY_V6_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    l1_names: List[str] = []
    l2_to_l1: Dict[str, str] = {}
    l2_by_l1: Dict[str, List[str]] = {}
    for grp in raw["level1"]:
        l1_names.append(grp["name"])
        l2_by_l1[grp["name"]] = [i["name"] for i in grp["level2"]]
        for item in grp["level2"]:
            l2_to_l1[item["name"]] = grp["name"]
    _cache.update(raw=raw, l1=l1_names, l2_to_l1=l2_to_l1, l2_by_l1=l2_by_l1,
                  l1_ci={n.lower(): n for n in l1_names}, l2_ci={n.lower(): n for n in l2_to_l1})
    return _cache


def clean_keywords(keywords: Optional[Iterable[Any]]) -> List[str]:
    out: List[str] = []
    seen = set()
    for k in keywords or []:
        if not isinstance(k, str):
            continue
        c = k.strip().lstrip("#").strip()
        if c and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
        if len(out) >= MAX_KEYWORDS:
            break
    return out


def normalise_path(l1: Optional[str], l2: Optional[str], keywords: Optional[Iterable[Any]] = None) -> Optional[Dict[str, Any]]:
    """Canonicalise one path. Returns None when there is nothing usable.

    A subtopic from the shipped taxonomy always gets its real theme. Anything else is kept as a custom
    theme/subtopic, flagged `is_custom`, so quizmasters can extend the list from the Edit Tags screen.
    """
    tax = taxonomy_v6()
    l1 = (l1 or "").strip()
    l2 = (l2 or "").strip() or None
    if not l1 and not l2:
        return None
    custom = False
    if l2 and l2.lower() in tax["l2_ci"]:
        l2 = tax["l2_ci"][l2.lower()]
        l1 = tax["l2_to_l1"][l2]
    else:
        if l1 and l1.lower() in tax["l1_ci"]:
            l1 = tax["l1_ci"][l1.lower()]
        elif l1:
            custom = True
        if l2:
            custom = True
        if not l1:
            return None
    return {"l1": l1, "l2": l2, "keywords": clean_keywords(keywords), "is_custom": custom}


def normalise_paths(paths: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalise a list of paths, merging duplicates (same theme + subtopic) and capping the count."""
    merged: Dict[tuple, Dict[str, Any]] = {}
    order: List[tuple] = []
    for p in paths or []:
        n = normalise_path(p.get("l1"), p.get("l2"), p.get("keywords"))
        if not n:
            continue
        key = (n["l1"].lower(), (n["l2"] or "").lower())
        if key in merged:
            merged[key]["keywords"] = clean_keywords(merged[key]["keywords"] + n["keywords"])
        else:
            merged[key] = n
            order.append(key)
    return [merged[k] for k in order][:MAX_PATHS]


def path_row_to_dict(r: QuestionTopicPath) -> Dict[str, Any]:
    return {"id": r.id, "position": r.position or 0, "l1": r.l1, "l2": r.l2,
            "keywords": list(r.keywords or []), "source": r.source, "is_custom": bool(r.is_custom)}


def get_paths(db: Session, question_id: str) -> List[Dict[str, Any]]:
    rows = (db.query(QuestionTopicPath).filter(QuestionTopicPath.question_id == question_id)
            .order_by(QuestionTopicPath.position, QuestionTopicPath.created_at).all())
    return [path_row_to_dict(r) for r in rows]


def paths_for_questions(db: Session, question_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Batch lookup for list screens (one query, not one per row)."""
    out: Dict[str, List[Dict[str, Any]]] = {qid: [] for qid in question_ids}
    if not question_ids:
        return out
    rows = (db.query(QuestionTopicPath).filter(QuestionTopicPath.question_id.in_(question_ids))
            .order_by(QuestionTopicPath.position, QuestionTopicPath.created_at).all())
    for r in rows:
        out.setdefault(r.question_id, []).append(path_row_to_dict(r))
    return out


def has_human_paths(db: Session, question_id: str) -> bool:
    return db.query(QuestionTopicPath.id).filter(
        QuestionTopicPath.question_id == question_id, QuestionTopicPath.source == "HUMAN").first() is not None


def _fmt(paths: List[Dict[str, Any]]) -> str:
    return json.dumps([{"l1": p["l1"], "l2": p.get("l2"), "keywords": p.get("keywords") or []} for p in paths],
                      ensure_ascii=False)


def replace_paths(
    db: Session,
    question_id: str,
    paths: Iterable[Dict[str, Any]],
    source: str = "LLM",
    actor: Optional[str] = None,
    record_history: bool = True,
    question: Optional[Any] = None,
    existing_rows: Optional[List[QuestionTopicPath]] = None,
) -> List[Dict[str, Any]]:
    """Replace every path of a question. Writes an edit-history row when something actually changed.

    Pass the `Question` object as `question` to also refresh its flat legacy columns.

    Pass `existing_rows` (the question's current path rows, already loaded, in display order) to skip the reads, the
    delete statement and the flush: the old rows are deleted and the new ones added through the session, and everything
    is written by the caller's next flush, together with the other questions of a batch.
    """
    new = normalise_paths(paths)
    if existing_rows is not None:
        old = [path_row_to_dict(r) for r in existing_rows]
        old_keywords = [k for p in old for k in (p.get("keywords") or [])]
        for r in existing_rows:
            db.delete(r)
        for i, p in enumerate(new):
            db.add(QuestionTopicPath(question_id=question_id, position=i, l1=p["l1"], l2=p["l2"],
                                     keywords=p["keywords"], source=source, is_custom=p["is_custom"]))
        if record_history and _fmt(old) != _fmt(new) and (old or source == "HUMAN"):
            db.add(QuestionEditHistory(
                question_id=question_id, field="topic_paths", old_value=_fmt(old), new_value=_fmt(new),
                kind="EDIT" if source == "HUMAN" else "RETAG", actor=actor or ("system" if source != "HUMAN" else None)))
        result = [{"id": None, "position": i, "l1": p["l1"], "l2": p["l2"], "keywords": list(p["keywords"]),
                   "source": source, "is_custom": bool(p["is_custom"])} for i, p in enumerate(new)]
        if question is not None:
            mirror_to_question(question, result, old_keywords=old_keywords)
        return result
    old = get_paths(db, question_id)
    old_keywords = [k for p in old for k in (p.get("keywords") or [])]
    db.query(QuestionTopicPath).filter(QuestionTopicPath.question_id == question_id).delete(synchronize_session=False)
    for i, p in enumerate(new):
        db.add(QuestionTopicPath(question_id=question_id, position=i, l1=p["l1"], l2=p["l2"],
                                 keywords=p["keywords"], source=source, is_custom=p["is_custom"]))
    if record_history and _fmt(old) != _fmt(new) and (old or source == "HUMAN"):
        db.add(QuestionEditHistory(
            question_id=question_id, field="topic_paths", old_value=_fmt(old), new_value=_fmt(new),
            kind="EDIT" if source == "HUMAN" else "RETAG", actor=actor or ("system" if source != "HUMAN" else None)))
    db.flush()
    result = get_paths(db, question_id)
    if question is not None:
        mirror_to_question(question, result, old_keywords=old_keywords)
    return result


def add_new_question_paths(db: Session, question: Any, paths: Iterable[Dict[str, Any]], source: str = "RULES") -> None:
    """`replace_paths` for a question created a moment ago: it has no paths yet and no history to record, so nothing is
    read or deleted and nothing is flushed. The rows are written with the caller's next flush, in one batch."""
    new = normalise_paths(paths)
    for i, p in enumerate(new):
        db.add(QuestionTopicPath(question_id=question.id, position=i, l1=p["l1"], l2=p["l2"],
                                 keywords=p["keywords"], source=source, is_custom=p["is_custom"]))
    result = [{"position": i, "l1": p["l1"], "l2": p["l2"], "keywords": list(p["keywords"]), "source": source,
               "is_custom": bool(p["is_custom"])} for i, p in enumerate(new)]
    mirror_to_question(question, result, old_keywords=[])


def mirror_to_question(q: Any, paths: List[Dict[str, Any]], old_keywords: Optional[List[str]] = None) -> None:
    """Keep the flat legacy columns in step with the paths (first path is the primary one).

    `questions.tags` holds path keywords *and* hashtags a person added. Tags that were not keywords of the
    previous paths are hashtags, so they are kept.
    """
    old_kw = {k.lower() for k in (old_keywords or [])}
    extras = [t for t in (q.tags or []) if isinstance(t, str) and t.lower() not in old_kw] if old_keywords is not None else []
    if not paths:
        q.topic = q.topic or UNCATEGORISED
        return
    primary = paths[0]
    q.topic = primary["l1"]
    q.subtopic = primary.get("l2")
    topics: List[str] = []
    for p in paths:
        if p["l1"] not in topics:
            topics.append(p["l1"])
    q.topics = topics
    tags: List[str] = []
    seen = set()
    for p in paths:
        for k in p.get("keywords") or []:
            if k.lower() not in seen:
                seen.add(k.lower())
                tags.append(k)
    for t in extras:
        if t.lower() not in seen:
            seen.add(t.lower())
            tags.append(t)
    q.tags = tags[:MAX_KEYWORDS * 3]
