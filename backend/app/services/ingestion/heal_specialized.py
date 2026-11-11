"""Idempotent healer and normalizer for CONNECT_4 and STEPPED_CLUE questions.

Transforms specialized quiz questions in the database into canonical,
unambiguous representations so they are clean to store, search, and display.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from backend.app.models.question import Question

logger = logging.getLogger(__name__)


def parse_stepped_clues(q_text: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extracts exactly 3 progressive tiers without off-by-one shifts or header bleed."""
    text = (q_text or "").replace('\r\n', '\n').strip()
    markers = [
        (1, re.search(r'(?:^|\n)[^\n]*?\bClue\s*1\b', text, re.I)),
        (2, re.search(r'(?:^|\n)[^\n]*?\bClue\s*2\b', text, re.I)),
        (3, re.search(r'(?:^|\n)[^\n]*?\bClue\s*3\b', text, re.I)),
    ]
    if markers[0][1] and markers[1][1] and markers[2][1] and markers[0][1].start() < markers[1][1].start() < markers[2][1].start():
        raw1 = text[markers[0][1].end():markers[1][1].start()]
        raw2 = text[markers[1][1].end():markers[2][1].start()]
        raw3 = text[markers[2][1].end():]

        def clean_tier(raw: str) -> str:
            s = raw.strip()
            pattern = r'^(?:\s*\(?[+\-\d/]+\)?|\s*[:\-\•\*\.]|\s*\d+[\.\)]|\s*Clue\s*\d)+[.:\s-]*'
            s = re.sub(pattern, '', s, flags=re.I).strip()
            s = re.sub(pattern, '', s, flags=re.I).strip()
            # Strip any trailing repeat header e.g. "\n1. Clue 1: +20/-15"
            s = re.sub(r'(?:^|\n)\s*(?:\d+[\.\)])?\s*Clue\s*\d\s*:?\s*[+\-\d/]*.*$', '', s, flags=re.I | re.M).strip()
            # Strip lone '?' lines
            s = re.sub(r'(?:^|\n)\s*\?\s*(?:\n|$)', '\n', s).strip()
            return s.strip()

        return clean_tier(raw1), clean_tier(raw2), clean_tier(raw3)
    return None, None, None


def is_format_aware(q: Question) -> bool:
    """True for a question read by format-aware ingestion (its payload carries an `extraction` block). Those payloads
    are already canonical; this legacy healer, which runs on every boot, must never rewrite them into the older shapes."""
    fp = q.format_payload
    return isinstance(fp, dict) and isinstance(fp.get("extraction"), dict)


def heal_specialized_questions(db: Session) -> Dict[str, int]:
    """Scans and heals all CONNECT_4 and STEPPED_CLUE questions in the database."""
    stats = {"stepped_healed": 0, "connect4_healed": 0, "reclassified_arrange": 0, "reclassified_visual": 0}

    # 1. Reclassify mislabeled questions
    # Question 1: Stages in startup fundraising -> ARRANGE_ORDER
    q_fund = db.query(Question).filter(Question.id == "6914a915-73e1-41a9-a205-120fdaa5f67b").first()
    if q_fund and q_fund.question_format != "ARRANGE_ORDER":
        q_fund.question_format = "ARRANGE_ORDER"
        q_fund.question_type = "ARRANGE_ORDER"
        p = dict(q_fund.format_payload or {})
        p.update({"has_timer": True, "timer_seconds": 60, "time_limit_seconds": 60, "type": "ARRANGE_ORDER"})
        q_fund.format_payload = p
        q_fund.format_data = p
        stats["reclassified_arrange"] += 1

    # Question 2: Share price as on 9th September 2026 -> ARRANGE_ORDER
    q_share = db.query(Question).filter(Question.id == "02e7fc08-64d4-48b2-a443-03e6fd16d3a3").first()
    if q_share and q_share.question_format != "ARRANGE_ORDER":
        q_share.question_format = "ARRANGE_ORDER"
        q_share.question_type = "ARRANGE_ORDER"
        p = dict(q_share.format_payload or {})
        p.update({"has_timer": True, "timer_seconds": 60, "time_limit_seconds": 60, "type": "ARRANGE_ORDER"})
        q_share.format_payload = p
        q_share.format_data = p
        stats["reclassified_arrange"] += 1

    # Question 3: Instagram visual rebrand -> VISUAL_CLUE
    q_insta = db.query(Question).filter(Question.id == "c9bd82a2-d5a8-4296-aa4b-384c0b386273").first()
    if q_insta and q_insta.question_format != "VISUAL_CLUE":
        q_insta.question_format = "VISUAL_CLUE"
        q_insta.question_type = "VISUAL_CLUE"
        q_insta.format_data = {"type": "VISUAL_CLUE", "image_url": (q_insta.question_images or [None])[0]}
        q_insta.format_payload = {"type": "VISUAL_CLUE", "image_url": (q_insta.question_images or [None])[0]}
        stats["reclassified_visual"] += 1

    # Ensure every legacy ARRANGE_ORDER row carries canonical 60s timer fields alongside time_limit_seconds.
    arrange_qs = db.query(Question).filter(Question.question_format == "ARRANGE_ORDER").all()
    for aq in arrange_qs:
        if is_format_aware(aq):
            continue
        payload = dict(aq.format_payload or aq.format_data or {})
        if not payload.get("has_timer") or payload.get("timer_seconds") != 60 or payload.get("time_limit_seconds") != 60:
            payload.update({"type": "ARRANGE_ORDER", "has_timer": True, "timer_seconds": 60, "time_limit_seconds": 60})
            aq.format_payload = payload
            aq.format_data = payload
    db.commit()

    # 2. Heal STEPPED_CLUE questions
    stepped_qs = db.query(Question).filter(
        Question.question_format.in_(["STEPPED_CLUE", "THREE_CLUE"])
    ).all()

    points_config = [
        (1, 20, -15, "+20 / -15", "Hard Clue"),
        (2, 15, -10, "+15 / -10", "Medium Clue"),
        (3, 10, -5, "+10 / -5", "Giveaway Clue"),
    ]

    for q in stepped_qs:
        if is_format_aware(q):
            continue
        c1, c2, c3 = parse_stepped_clues(q.question_text)
        if not (c1 and c2 and c3):
            continue

        imgs = list(q.question_images or q.image_refs or [])

        clues = []
        for step, p_cor, p_wrg, p_lbl, d_lbl in points_config:
            tier_text = c1 if step == 1 else (c2 if step == 2 else c3)
            tier_img = imgs[step - 1] if step - 1 < len(imgs) else None
            clues.append({
                "step": step,
                "points": p_lbl,
                "points_correct": p_cor,
                "points_wrong": p_wrg,
                "points_label": p_lbl,
                "difficulty_label": d_lbl,
                "text": tier_text,
                "image": tier_img,
                "image_url": tier_img,
            })

        payload = {
            "type": "STEPPED_CLUE",
            "badge": "Portfolio Builder • Stepped Clues",
            "rules": "All on buzzer • 1 attempt per clue • Clue 1: +20/-15, Clue 2: +15/-10, Clue 3: +10/-5",
            "clues": clues,
            "answer": q.answer,
        }

        # Clean formatted question text for universal readability
        clean_text = (
            "[Portfolio Builder · Stepped Clues]\n\n"
            f"• Clue 1 (+20 / -15):\n{c1}\n\n"
            f"• Clue 2 (+15 / -10):\n{c2}\n\n"
            f"• Clue 3 (+10 / -5):\n{c3}"
        )

        q.question_text = clean_text
        q.question_format = "STEPPED_CLUE"
        q.question_type = "STEPPED_CLUE"
        q.format_payload = payload
        q.format_data = payload
        stats["stepped_healed"] += 1

    # 3. Heal CONNECT_4 questions
    reclassified_ids = {
        "6914a915-73e1-41a9-a205-120fdaa5f67b",
        "02e7fc08-64d4-48b2-a443-03e6fd16d3a3",
        "c9bd82a2-d5a8-4296-aa4b-384c0b386273",
    }
    connect_qs = db.query(Question).filter(
        Question.question_format.in_(["CONNECT_4", "CONNECT"]),
        ~Question.id.in_(reclassified_ids)
    ).all()

    connect_prompts = {
        "3a9a29a4-1192-4448-8915-c977c30795f0": ("What connects all these clues and images?", None),
        "ac1e38a7-361c-4228-b3d7-1e1ee8739cae": ("What connects all these clues and images?", None),
        "695d173b-447c-476c-81b8-287344036705": ("What event or platform connects all these clues and images?", "7th edition in 2026"),
        "a1057fde-e60d-4430-a5de-5c9da3892aa1": ("What banking institution connects all these clues and images?", "The name you can bank upon"),
        "b82bce09-f022-4173-a2de-ee896792a23b": ("What brand or venture connects all these clues and images?", "Pure, Clean air"),
        "c52fff59-30e8-4a3a-98b2-47926326669f": ("What financial service connects all these clues and images?", "Maximum 85% LTV according to RBI · 22 and above in quality accepted"),
        "e9a439f4-c573-42bc-ba13-d5d07821f393": ("What design concept or scientific structure connects all these logos?", None),
        "f6e219b2-513b-4203-afb7-b4ec50101c05": ("What government policy initiative connects all these clues and images?", "Annual Savings of ₹30,000 Crores in Foreign Exchange"),
    }

    for q in connect_qs:
        if is_format_aware(q):
            continue
        imgs = list(q.question_images or q.image_refs or [])
        default_prompt, sub_hint = connect_prompts.get(
            q.id,
            ("What common thread or concept connects all four clues and images?", None)
        )

        raw_q = (q.question_text or "").strip()
        raw_body = re.sub(r"^\[Connect 4[^\]]*\]\s*", "", raw_q).strip()
        is_generic = (
            (q.id in connect_prompts)
            or not raw_body
            or len(raw_body) < 20
            or raw_body.lower().startswith((
                "look at the 4 elements",
                "what connects all these",
                "what common thread",
                "what common concept connects these",
            ))
        )
        resolved_prompt = default_prompt if is_generic else raw_body
        # Build clean clue items (keep all clue images when > 4)
        elements = []
        for i in range(max(4, len(imgs))):
            img_url = imgs[i] if i < len(imgs) else None
            elements.append({
                "index": i + 1,
                "label": f"Clue {i + 1}",
                "text": f"Clue {i + 1}",
                "image": img_url,
                "image_url": img_url,
                "explanation": "",
            })

        payload = {
            "type": "CONNECT_4",
            "badge": "Connect 4 • Common Link",
            "prompt": resolved_prompt,
            "sub_hint": sub_hint,
            "elements": elements,
            "common_link": q.answer,
            "connection_reveal": q.answer,
        }

        clean_text = f"[Connect 4 · Common Link]\n{resolved_prompt}"
        if sub_hint:
            clean_text += f"\n\nContext Hint: {sub_hint}"

        q.question_text = clean_text
        q.question_format = "CONNECT_4"
        q.question_type = "CONNECT_4"
        q.format_payload = payload
        q.format_data = payload
        stats["connect4_healed"] += 1

    db.commit()
    logger.info(f"Specialized questions healed: {stats}")
    return stats
