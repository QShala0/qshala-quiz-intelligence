import os
import sys
import tempfile
import uuid
from pathlib import Path

# Ensure project root is in sys.path
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Set DATABASE_URL to a temp SQLite database before any backend.* import
_tmp_db = Path(tempfile.gettempdir()) / f"qshala_test_{os.getpid()}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db.resolve().as_posix()}"
os.environ["REQUIRE_POSTGRES"] = "0"
# Tests that ingest with an unusable Gemini key would otherwise sit through the 4 s pause before the embedding
# retry pass (and tests that record asyncio.sleep calls would see it). The pause is for real rate limits only.
os.environ.setdefault("EMBEDDING_RETRY_DELAY_S", "0")
os.environ.setdefault("PROMPT_QUIZ_BANK_TTL_S", "0")     # tests expect the bank to reflect the database at once

import pytest

@pytest.fixture(scope="session", autouse=True)
def setup_test_schema(tmp_path_factory):
    import backend.app.main  # noqa: F401
    from backend.app.database import Base, engine, ensure_schema_columns, SessionLocal
    from backend.app.models.document import Document
    from backend.app.models.question import Question

    Base.metadata.create_all(bind=engine)
    ensure_schema_columns()

    # Seed diverse mock questions so search/prompt-quiz tests work on empty hermetic DB
    db = SessionLocal()
    try:
        if db.query(Question).count() == 0:
            doc_id = str(uuid.uuid4())
            db.add(Document(
                id=doc_id, filename="test_sample.pptx", title="Test Sample Deck",
                year=2024, file_type="pptx", storage_path="dummy", file_size_bytes=1000,
                processing_status="COMPLETED"
            ))
            sample_bank = [
                ("banking", "Banking & Finance", ["Banking", "Finance"], [
                    ("What is the central bank of India called?", "Reserve Bank of India"),
                    ("Which financial ratio compares current assets to current liabilities?", "Current Ratio"),
                    ("What does ATM stand for in the banking sector?", "Automated Teller Machine"),
                    ("Which institution regulates securities markets in India?", "SEBI"),
                    ("What type of banking operates purely online without physical branches?", "Neobank"),
                    ("What does KYC stand for in customer onboarding?", "Know Your Customer"),
                    ("Which monetary policy rate determines interbank borrowing overnight?", "Repo Rate"),
                    ("What is the primary role of a commercial bank?", "Accepting deposits and lending money"),
                    ("Which system handles high-value real-time gross settlement?", "RTGS"),
                    ("What is the deposit insurance corporation in India?", "DICGC"),
                    ("Which document serves as proof of a fixed bank deposit?", "Fixed Deposit Receipt"),
                    ("What is compound interest in savings accounts?", "Interest on principal plus accumulated interest"),
                ]),
                ("finance", "Corporate Finance", ["Finance", "Economy"], [
                    ("What is a bull market in stock exchange terminology?", "A market on the rise"),
                    ("What does IPO stand for in corporate finance?", "Initial Public Offering"),
                    ("Which financial statement shows revenue and expenses over a period?", "Income Statement"),
                    ("What is market capitalization calculated from?", "Share price multiplied by total shares"),
                    ("What does ROI stand for in investment appraisal?", "Return on Investment"),
                    ("Which asset class represents partial ownership in a firm?", "Equities"),
                    ("What is capital budgeting used for in business?", "Evaluating long-term investment projects"),
                    ("What is liquidity in corporate finance?", "Ease of converting assets to cash"),
                    ("Which term describes spreading investments to reduce risk?", "Diversification"),
                    ("What is a dividend paid to shareholders?", "Distribution of company profits"),
                    ("What does EBITDA measure in company earnings?", "Operating performance before non-cash expenses"),
                    ("What is working capital?", "Current assets minus current liabilities"),
                ]),
                ("startups", "Indian Startups", ["Startups", "Business", "Technology"], [
                    ("What is a startup valued at over one billion dollars called?", "Unicorn"),
                    ("Who coined the term Minimum Viable Product in lean startup methodology?", "Frank Robinson / Eric Ries"),
                    ("Which Indian fintech company pioneered QR soundbox payments?", "Paytm"),
                    ("What is a pitch deck used for by startup founders?", "Presenting business plans to investors"),
                    ("What is venture capital?", "Financing provided to early-stage growth companies"),
                    ("Which city is widely regarded as the Silicon Valley of India?", "Bangalore"),
                    ("What does CAC stand for in startup customer acquisition?", "Customer Acquisition Cost"),
                    ("What is a pivot in startup strategy?", "Shifting business model based on market feedback"),
                    ("What is boot-strapping in entrepreneurship?", "Self-funding without external venture capital"),
                    ("What is the burn rate of a startup company?", "Rate at which cash is spent each month"),
                    ("What does SaaS stand for in cloud technology businesses?", "Software as a Service"),
                    ("What is an accelerator program for early startups?", "Cohort-based mentorship and seed funding"),
                ]),
                ("technology", "Tech & AI", ["Technology", "Science"], [
                    ("What does CPU stand for in computer hardware?", "Central Processing Unit"),
                    ("Which programming language was created by Guido van Rossum?", "Python"),
                    ("What does HTTP stand for in web browsing protocols?", "Hypertext Transfer Protocol"),
                    ("Which company developed the Gemini family of AI models?", "Google"),
                    ("What is open-source software distributed with?", "Source code that anyone can inspect and modify"),
                    ("What does GPU stand for in computing?", "Graphics Processing Unit"),
                    ("Which data structure operates on a Last In First Out basis?", "Stack"),
                    ("What does API stand for in software development?", "Application Programming Interface"),
                    ("What is machine learning in artificial intelligence?", "Algorithms that improve automatically through experience"),
                    ("What does SQL stand for in database management?", "Structured Query Language"),
                    ("Which company developed the Linux operating system kernel?", "Linus Torvalds"),
                    ("What is cloud computing?", "On-demand delivery of IT resources over the internet"),
                ]),
            ]
            topic_map = {
                "banking": ("Finance", "Banks and Banking"),
                "finance": ("Finance", "Corporate Finance"),
                "startups": ("Business", "Startups"),
                "technology": ("Technology", "Technology"),
            }
            for kw, sub, top_list, q_tuples in sample_bank:
                mapped_top, mapped_sub = topic_map.get(kw, ("General Knowledge", sub))
                for idx, (q_txt, ans) in enumerate(q_tuples, 1):
                    db.add(Question(
                        id=str(uuid.uuid4()),
                        document_id=doc_id,
                        question_text=q_txt,
                        answer=ans,
                        topic=mapped_top,
                        subtopic=mapped_sub,
                        topics=top_list,
                        tags=[kw, "sample"],
                        difficulty="Hard" if idx > 6 else "Medium",
                        difficulty_score=0.85 if idx > 6 else 0.50,
                    ))
            db.commit()
    finally:
        db.close()

    yield
    try:
        if _tmp_db.exists():
            _tmp_db.unlink(missing_ok=True)
    except Exception:
        pass

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_llm_breaker():
    from backend.app.services.prompt_quiz import llm_breaker
    llm_breaker.reset()
    yield
    llm_breaker.reset()
