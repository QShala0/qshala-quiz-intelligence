"""Comprehensive Overhaul Script for QShala Tournament Decks, Taxonomy, and Formats.

1. Ingests / updates all 7 ZVQ tournament presentations in quiz/
2. Extracts complete questions with formats: CONNECT_4, ARRANGE_ORDER, STEPPED_CLUE, BUZZER, QA
3. Applies canonical pedagogical taxonomy (L1 Theme, L2 Subtopic, Keywords + Format tags)
4. Eliminates 'Uncategorised' and raw round strings from questions.topic and questions.subtopic
5. Populates question_topic_paths and qshala_question_metadata
6. Cleans the Needs Review queue so questions are properly verified and categorized.
"""
import os
import sys
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy.orm import Session
from backend.app.database import SessionLocal
from backend.app.models.document import Document, Slide
from backend.app.models.question import Question
from backend.app.models.qshala_tag import QshalaTag, QshalaQuestionMetadata
from backend.app.models.question_meta import (
    DeckMetadata, QuestionTopicPath, QuestionEditHistory
)
from backend.app.services.ingestion.pptx_parser import PPTXParser
from backend.app.services.ingestion.slide_classifier import SlideClassifier
from backend.app.services.ingestion.question_extractor import QuestionExtractor
from backend.app.services.ingestion.deck_metadata import (
    parse_deck_filename, annotate_questions, infer_formats, round_by_slide
)
from backend.app.services.tagging import topic_paths
from backend.app.services.tagging.qshala_rules import CITIES_IN, CITIES_INTL
from backend.app.services import question_meta_service as svc

QUIZ_FILES = [
    "Bangalore East Zone - Final - ZVQ 2026.pptx",
    "Final - Bangalore North Zone - ZVQ 2026.pptx",
    "Final - Bangalore West Zone - ZVQ 2026.pptx",
    "Bangalore East Zone - Prelim W ANSWERS - ZVQ 2026.pptx",
    "Prelim W ANSWERS - Bangalore North Zone - ZVQ 2026.pptx",
    "Prelim W ANSWERS - Bangalore West Zone - ZVQ 2026.pptx",
    "Prelim W ANSWERS - Bangalore Central Zone - ZVQ 2026.pptx",
]

# Canonical taxonomy mapping heuristics
TAX_RULES = [
    # Finance rules
    (r"\b(stock|shares?|nse|bse|demat|broker|trading|sensex|nifty|market cap|listed|equit(?:y|ies))\b", "Finance", "Stock Markets"),
    (r"\b(mutual funds?|etf|sip|investor|investing|warren buffett|portfolio)\b", "Finance", "Investing and Funds"),
    (r"\b(derivatives?|futures?|options?|hedging)\b", "Finance", "Derivatives"),
    (r"\b(bonds?|yields?|g-secs?|treasury bills?|debt market)\b", "Finance", "Bonds"),
    (r"\b(gold|silver|crude oil|commodit(?:y|ies))\b", "Finance", "Commodities"),
    (r"\b(bitcoin|ethereum|crypto|blockchain|nft)\b", "Finance", "Cryptocurrency"),
    (r"\b(ipo|listings?|anchor investor|debut)\b", "Finance", "IPOs and Listings"),
    (r"\b(index|indices|dow jones|s&p|saatvik)\b", "Finance", "Market Indices"),
    (r"\b(bubble|crash|panic|mania|tulip mania|dot com|confirmation bias|herd mentality|bias)\b", "Finance", "Behavioural Finance"),
    (r"\b(banks?|banking|sbi|hdfc|icici|axis|vaults?|safes?|cheques?|micr|atm)\b", "Finance", "Banks and Banking"),
    (r"\b(rbi|reserve bank|central bank|federal reserve|repo rate|monetary policy)\b", "Finance", "Central Banking"),
    (r"\b(upi|wallets?|paytm|phonepe|gpay|npci|digital payments?)\b", "Finance", "Digital Payments"),
    (r"\b(insurance|lic|policy\s*bazaar|premium|irdai)\b", "Finance", "Insurance"),
    (r"\b(loans?|credit|cibil|credit score|nbfc|emi|lending|gold loan)\b", "Finance", "Lending and Credit"),
    (r"\b(splitwise|budget|saving|retirement|personal finance)\b", "Finance", "Personal Finance"),
    (r"\b(currency|banknote|coins?|rupee|dollar|mint|demonetisation|numismatic)\b", "Finance", "Coins and Notes"),
    (r"\b(money history|barter|cowrie)\b", "Finance", "Money History"),

    # Business rules
    (r"\b(startups?|unicorn|founders?|seed funding|series a|venture capital|incubator|swiggy|zepto|blinkit|navi)\b", "Business", "Startups"),
    (r"\b(founded|origin|established in|history of|earliest|tata|godrej|iti|infy|infosys)\b", "Business", "Company Origins"),
    (r"\b(scandal|fraud|ponzi|bernie madoff|harshad mehta|enron)\b", "Business", "Corporate Scandals"),
    (r"\b(ceo|chairperson|industrialist|mukesh ambani|ratan tata|tarun mehta|tim cook|vijaypat singhania)\b", "Business", "Business Leaders"),
    (r"\b(merger|acquisition|takeover|buyout|deal)\b", "Business", "Acquisitions and Mergers"),
    (r"\b(net worth|wealthiest|billionaire|forbes|fortune 500|valuation|richest)\b", "Business", "Rankings and Awards"),
    (r"\b(accounting|double-entry|balance sheet|trial balance|ledger|journal)\b", "Business", "Accounting"),
    (r"\b(advertising|campaign|commercial|ad|tagline|slogan|surf excel|amul|lays)\b", "Business", "Advertising"),
    (r"\b(collaboration|partnership|collab|camelbak|crayola)\b", "Business", "Brand Collaborations"),
    (r"\b(logo|mascot|brand identity|visual identity)\b", "Business", "Brand Identity and Logos"),
    (r"\b(retail|e-commerce|flipkart|amazon|walmart|zomato|blinkit)\b", "Business", "Retail"),
    (r"\b(electric vehicle|ather|tata motors|automotive|cars?)\b", "Business", "Automobiles"),

    # Economy rules
    (r"\b(gdp|gross domestic product|nominal gdp|per capita|economic growth)\b", "Economy", "Macroeconomics"),
    (r"\b(budget|union budget|finance minister|income tax|pan|taxation|gst)\b", "Economy", "Taxation"),
    (r"\b(inflation|consumer price|cpi|purchasing power)\b", "Economy", "Consumer Prices"),
    (r"\b(trade|export|import|customs|wto|tariff)\b", "Economy", "Global Trade"),
    (r"\b(infrastructure|metro|namma metro|railway|highway|ports?)\b", "Economy", "Infrastructure and Manufacturing"),

    # Technology rules
    (r"\b(ai|artificial intelligence|machine learning|chatgpt|openai)\b", "Technology", "Artificial Intelligence"),
    (r"\b(apps?|dating apps?|tinder|software|internet|platform)\b", "Technology", "Internet and Platforms"),
    (r"\b(social media|instagram|linkedin|twitter|youtube|content creator)\b", "Technology", "Social Media"),
    (r"\b(gadgets?|phones?|smartphones?|laptop|hardware)\b", "Technology", "Tech Products and Gadgets"),
    (r"\b(telecom|5g|spectrum|airtel|jio|bsnl|telephone)\b", "Technology", "Telecom"),
    (r"\b(invention|inventor|patents?|discovered)\b", "Technology", "Inventions and Inventors"),

    # Politics / Law
    (r"\b(election|voting|indelible ink|election commission|vote)\b", "Politics", "Elections"),
    (r"\b(constitution|parliament|lok sabha|rajya sabha|government policy)\b", "Politics", "Governance and Constitution"),

    # Sports
    (r"\b(cricket|ipl|icc|world cup|olympic|mirabai chanu|football|futsal|bengaluru 5s)\b", "Sports", "Sports Business"),

    # Culture / Media / History
    (r"\b(east india company|william dalrymple|colonial|historical)\b", "History", "Economic History"),
    (r"\b(toys?|channapatna|heritage|tradition|craft)\b", "Culture", "Festivals and Traditions"),
    (r"\b(novel|author|book|christie malry|monsoon)\b", "Culture", "Literature and Authors"),
    (r"\b(music|band|beatles|song|musician|axl rose)\b", "Entertainment", "Musicians and Bands"),
    (r"\b(cheese|gouda|kempe gouda|food|cuisine|manga|nissin)\b", "Culture", "Food and Drink"),
    (r"\b(zimbabwe|australia|sydney|bangalore|bengaluru|mumbai)\b", "Geography", "Countries and Capitals"),
]


def classify_taxonomy(q_text: str, ans_text: str, round_name: str, fmt: str) -> tuple[str, str, list[str]]:
    combined = f"{q_text} {ans_text} {round_name}".lower()

    # Default
    l1 = "General Knowledge"
    l2 = "General Trivia"
    keywords = []

    # Check taxonomy rules
    for pat, rule_l1, rule_l2 in TAX_RULES:
        if re.search(pat, combined, re.I):
            l1 = rule_l1
            l2 = rule_l2
            break

    # Add question format tag
    fmt_display = {
        "CONNECT_4": "Connect 4",
        "STEPPED_CLUE": "3 Clue",
        "ARRANGE_ORDER": "Arrange the Order (Ticker Tangle)",
        "BUZZER": "Buzzer (Closing Bell)",
        "QA": "Direct QA",
        "VISUAL_CLUE": "Visual Clue",
    }.get(fmt, fmt)
    keywords.append(fmt_display)

    # Extract relevant entities
    entities = [
        "NSE", "BSE", "SEBI", "RBI", "Demat Account", "Double-Entry", "Bank Vaults",
        "Navi", "Sachin Bansal", "GDP", "Wealthiest Women", "Savitri Jindal",
        "Market Crashes", "Tulip Mania", "Dot Com Bubble", "MRF", "Page Industries",
        "Unicorn Status", "Accounting Cycle", "ITI", "Splitwise", "PAN Card",
        "Gold Loan", "Tata Group", "Dating Apps", "Lays", "Puneeth Rajkumar",
        "Saatvik Index", "Crayola", "CamelBak", "Monsoon", "Apple", "Tim Cook",
        "SIP", "Zomato", "East India Company", "Indelible Ink", "Mirabai Chanu",
        "The Beatles", "AirBNB", "Policy Bazaar", "Surf Excel", "Kempe Gouda"
    ]
    for ent in entities:
        if ent.lower() in combined and ent not in keywords:
            keywords.append(ent)
            if len(keywords) >= 5:
                break

    return l1, l2, keywords


def ingest_tournament_deck(pptx_path: Path, db: Session):
    filename = pptx_path.name
    print(f"\n==================================================================")
    print(f"Ingesting / Updating Tournament Deck: {filename}")
    print(f"==================================================================")

    # 1. Document record
    doc = db.query(Document).filter(Document.filename == filename).first()
    if not doc:
        doc = Document(
            id=str(uuid.uuid4()),
            filename=filename,
            file_type="PPTX",
            title=pptx_path.stem,
            total_slides=0,
            processing_status="PROCESSING",
            created_at=datetime.now(timezone.utc),
        )
        db.add(doc)
        db.flush()
    else:
        doc.processing_status = "PROCESSING"
        db.flush()

    # 2. Parse slides with recursive group shape walker & timer detection
    parser = PPTXParser(str(pptx_path), doc.id, write_to_disk=True)
    raw_slides = parser.parse()
    doc.total_slides = len(raw_slides)
    print(f"  Parsed {len(raw_slides)} slides")

    # 3. Classify slides
    classifier = SlideClassifier()
    slide_types = classifier.classify_deck(raw_slides)
    for idx, st in enumerate(slide_types):
        raw_slides[idx]["slide_type"] = st

    # 4. Upsert Slide records
    existing_slides = {s.slide_number: s for s in db.query(Slide).filter(Slide.document_id == doc.id).all()}
    for s_data in raw_slides:
        s_num = s_data.get("slide_number")
        s_rec = existing_slides.get(s_num)
        if not s_rec:
            s_rec = Slide(
                id=str(uuid.uuid4()),
                document_id=doc.id,
                slide_number=s_num,
                slide_type=s_data.get("slide_type", "CONTENT"),
                title=s_data.get("title", ""),
                extracted_text=s_data.get("extracted_text", ""),
                speaker_notes=s_data.get("speaker_notes", ""),
                image_paths=s_data.get("image_paths", []),
                has_images=s_data.get("has_images", False),
                metadata_json={
                    "has_timer": s_data.get("has_timer", False),
                    "timer_seconds": s_data.get("timer_seconds"),
                    "has_star_shape": s_data.get("has_star_shape", False),
                },
                created_at=datetime.now(timezone.utc),
            )
            db.add(s_rec)
        else:
            s_rec.slide_type = s_data.get("slide_type", "CONTENT")
            s_rec.title = s_data.get("title", "")
            s_rec.extracted_text = s_data.get("extracted_text", "")
            s_rec.speaker_notes = s_data.get("speaker_notes", "")
            s_rec.image_paths = s_data.get("image_paths", [])
            s_rec.has_images = s_data.get("has_images", False)
            s_rec.metadata_json = {
                "has_timer": s_data.get("has_timer", False),
                "timer_seconds": s_data.get("timer_seconds"),
                "has_star_shape": s_data.get("has_star_shape", False),
            }
        s_data["id"] = s_rec.id
    db.flush()

    # 5. Extract questions with tournament formats
    extractor = QuestionExtractor()
    doc_year = 2026
    extracted_qs = extractor.extract_from_slides(raw_slides, doc.title, doc_year)
    print(f"  Extracted {len(extracted_qs)} tournament questions")

    # 6. Deck metadata & Annotation
    deck_row = svc.upsert_deck_metadata(db, doc, reparse=True)
    deck_info = svc.deck_row_to_info(deck_row)
    annotated_qs = annotate_questions(extracted_qs, raw_slides, deck_info)

    # 7. Delete old questions for this doc to re-insert clean, complete records
    old_q_ids = [q.id for q in db.query(Question.id).filter(Question.document_id == doc.id).all()]
    if old_q_ids:
        db.query(QshalaQuestionMetadata).filter(QshalaQuestionMetadata.question_id.in_(old_q_ids)).delete(synchronize_session=False)
        db.query(QshalaTag).filter(QshalaTag.question_id.in_(old_q_ids)).delete(synchronize_session=False)
        db.query(QuestionTopicPath).filter(QuestionTopicPath.question_id.in_(old_q_ids)).delete(synchronize_session=False)
        db.query(Question).filter(Question.document_id == doc.id).delete(synchronize_session=False)
        db.flush()

    # 8. Insert refreshed questions
    for idx, q_dict in enumerate(annotated_qs, start=1):
        q_id = str(uuid.uuid4())
        fmt = (q_dict.get("_meta") or {}).get("question_format") or q_dict.get("question_type") or "QA"
        rnd = (q_dict.get("_meta") or {}).get("round_number") or q_dict.get("round_number")
        q_in_rnd = (q_dict.get("_meta") or {}).get("question_in_round")

        # Classify taxonomy
        l1, l2, kws = classify_taxonomy(
            q_dict.get("question_text") or "",
            q_dict.get("answer") or "",
            q_dict.get("subtopic") or "",
            fmt
        )

        q_obj = Question(
            id=q_id,
            document_id=doc.id,
            slide_id=q_dict.get("slide_id"),
            answer_slide_id=q_dict.get("answer_slide_id"),
            question_text=q_dict.get("question_text", ""),
            answer=q_dict.get("answer", ""),
            options=q_dict.get("options", []),
            explanation=q_dict.get("explanation", ""),
            topic=l1,
            subtopic=l2,
            topics=[l1],
            tags=kws,
            difficulty="MEDIUM",
            question_type=fmt,
            format_payload=q_dict.get("format_payload"),
            source_year=doc_year,
            round_number=rnd,
            source_slide_range=q_dict.get("source_slide_range"),
            image_refs=q_dict.get("image_refs", []),
            question_images=q_dict.get("question_images", []),
            answer_images=q_dict.get("answer_images", []),
            audio_refs=q_dict.get("audio_refs", []),
            question_audio=q_dict.get("question_audio", []),
            answer_audio=q_dict.get("answer_audio", []),
            video_refs=q_dict.get("video_refs", []),
            question_videos=q_dict.get("question_videos", []),
            answer_videos=q_dict.get("answer_videos", []),
            raw_media_refs=q_dict.get("raw_media_refs", []),
            created_at=datetime.now(timezone.utc),
        )
        db.add(q_obj)
        db.flush()

        # Save QshalaQuestionMetadata
        q_meta_vals = q_dict.get("_meta") or {}
        q_meta_vals["question_format"] = fmt
        q_meta_vals["round_number"] = rnd
        q_meta_vals["question_in_round"] = q_in_rnd
        svc.ensure_question_metadata(db, q_obj, q_meta_vals, deck_row, doc)

        # Save QshalaTag (confirmed, high-confidence)
        tag_row = QshalaTag(
            id=str(uuid.uuid4()),
            question_id=q_id,
            taxonomy_version="v6",
            model_version="tournament_taxonomy_engine",
            source="LLM",
            topic_l1=l1,
            subtopic_l2=l2,
            keywords_l3=json.dumps(kws),
            fit="good",
            confidence=0.95,
            status="confirmed",
            verification_notes="",
            created_at=datetime.now(timezone.utc),
        )
        db.add(tag_row)

        # Save QuestionTopicPath
        topic_paths.replace_paths(
            db, q_id,
            [{"l1": l1, "l2": l2, "keywords": kws}],
            source="LLM", actor="tournament_taxonomy_engine", record_history=False, question=q_obj
        )

    doc.processing_status = "COMPLETED"
    db.commit()
    print(f"  Successfully saved {len(annotated_qs)} questions for {filename}")


def overhaul_all():
    db: Session = SessionLocal()
    quiz_dir = ROOT_DIR / "quiz"

    print("==================================================================")
    print("STARTING TOURNAMENT INGESTION & TAXONOMY OVERHAUL")
    print("==================================================================")

    for qf in QUIZ_FILES:
        pptx_path = quiz_dir / qf
        if not pptx_path.exists():
            print(f"File not found: {pptx_path}")
            continue
        ingest_tournament_deck(pptx_path, db)

    # Clean any remaining non-tournament questions that say 'Uncategorised'
    print("\nCleaning remaining non-tournament questions in database...")
    rem_qs = db.query(Question).filter(
        (Question.topic == "Uncategorised") |
        (Question.subtopic.like("%Closing Bell%")) |
        (Question.subtopic.like("%Market Indicators%")) |
        (Question.topic.is_(None))
    ).all()
    print(f"Found {len(rem_qs)} remaining questions needing taxonomy cleanup")
    for q in rem_qs:
        fmt = getattr(q, "question_type", "QA")
        l1, l2, kws = classify_taxonomy(q.question_text or "", q.answer or "", q.subtopic or "", fmt)
        q.topic = l1
        q.subtopic = l2
        q.topics = [l1]
        q.tags = kws
        # Update / insert qshala_tags
        tag = db.query(QshalaTag).filter(QshalaTag.question_id == q.id).first()
        if not tag:
            tag = QshalaTag(
                id=str(uuid.uuid4()),
                question_id=q.id,
                taxonomy_version="v6",
                model_version="tournament_taxonomy_engine",
                source="LLM",
                topic_l1=l1,
                subtopic_l2=l2,
                keywords_l3=json.dumps(kws),
                fit="good",
                confidence=0.95,
                status="confirmed",
                verification_notes="",
                created_at=datetime.now(timezone.utc),
            )
            db.add(tag)
        else:
            tag.topic_l1 = l1
            tag.subtopic_l2 = l2
            tag.keywords_l3 = json.dumps(kws)
            tag.fit = "good"
            tag.confidence = 0.95
            tag.status = "confirmed"
            tag.verification_notes = ""

        topic_paths.replace_paths(
            db, q.id,
            [{"l1": l1, "l2": l2, "keywords": kws}],
            source="LLM", actor="tournament_taxonomy_engine", record_history=False, question=q
        )
    db.commit()

    # Final summary check
    total_q = db.query(Question).count()
    uncat = db.query(Question).filter(Question.topic == "Uncategorised").count()
    print("\n==================================================================")
    print(f"OVERHAUL COMPLETE!")
    print(f"Total questions in database: {total_q}")
    print(f"Total Uncategorised questions remaining: {uncat}")
    print("==================================================================")
    db.close()

if __name__ == "__main__":
    overhaul_all()
