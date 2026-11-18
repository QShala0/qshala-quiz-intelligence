import json
from unittest.mock import MagicMock, patch
import pytest

from backend.app.services.prompt_quiz import engine, _http
from backend.app.services.prompt_quiz.intent_engine import deconstruct_prompt


def _mock_intent_response(subject="Kia", expansions=None):
    if expansions is None:
        expansions = ["car", "automobile", "Hyundai", "SUV"]
    payload = {
        "normalized_subject": subject,
        "expansion_terms": expansions,
        "target_audience": "general",
        "mapped_topics_l1": [],
        "mapped_subtopics_l2": [],
    }
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": json.dumps(payload)}]
                }
            }
        ]
    }
    return m


def _make_car_bank():
    kia_questions = [
        ("Which compact SUV was launched in India by Kia in 2019 as its debut model?", "Seltos", ["kia", "seltos", "suv"]),
        ("What is the sub-4-meter compact crossover SUV introduced by Kia in 2020?", "Sonet", ["kia", "sonet", "crossover"]),
        ("Which all-electric crossover built on the E-GMP platform was released by Kia in 2021?", "EV6", ["kia", "ev6", "electric"]),
        ("What multi-purpose luxury MPV is sold by Kia targeting premium family travel?", "Carnival", ["kia", "carnival", "mpv"]),
    ]

    generic_car_questions = [
        ("Henry Ford introduced the moving assembly line for which iconic mass-market vehicle?", "Model T", ["ford", "automobile", "car"]),
        ("What was the first mass-produced hybrid passenger car introduced by Toyota in 1997?", "Prius", ["toyota", "hybrid", "car"]),
        ("Which Italian sports car brand founded by Enzo features a prancing horse emblem?", "Ferrari", ["ferrari", "sports car", "automobile"]),
        ("Karl Benz patented the Motorwagen in what year to birth the modern gasoline automobile?", "1886", ["benz", "motorwagen", "car"]),
        ("Which German performance manufacturer produces the rear-engine 911 sports coupe?", "Porsche", ["porsche", "sports car", "auto"]),
        ("What Swedish automaker is world-famous for inventing the three-point safety seatbelt?", "Volvo", ["volvo", "safety", "automobile"]),
        ("Which company introduced the high-performance Roadster and later the Model S luxury sedan?", "Tesla", ["tesla", "electric car", "automobile"]),
        ("What British ultra-luxury marque handcrafts the Phantom and Ghost flagship limousines?", "Rolls-Royce", ["rolls-royce", "luxury", "automobile"]),
        ("Which American sports car icon has been produced under General Motors Chevrolet since 1953?", "Corvette", ["chevrolet", "corvette", "car"]),
        ("What Japanese brand is celebrated for its lightweight rotary Wankel engine sports cars like RX-7?", "Mazda", ["mazda", "wankel", "car"]),
        ("Which Italian manufacturer created the iconic wedge-shaped scissor-door Countach supercar?", "Lamborghini", ["lamborghini", "supercar", "auto"]),
        ("What French automaker pioneered hydropneumatic suspension in models like the futuristic DS?", "Citroen", ["citroen", "suspension", "automobile"]),
        ("Which American automaker produces the best-selling full-size pickup truck line named F-150?", "Ford", ["ford", "pickup", "truck", "automobile"]),
        ("What German automotive giant uses the historic slogan Das Auto and builds the Golf hatchback?", "Volkswagen", ["volkswagen", "golf", "car"]),
        ("Which Bavarian luxury brand uses the four interlocking rings symbolising the Auto Union merger?", "Audi", ["audi", "auto union", "luxury"]),
        ("What Japanese manufacturer produces the iconic Land Cruiser off-road SUV and Hilux pickup?", "Toyota", ["toyota", "suv", "automobile"]),
        ("Which British sports car marque is famous for being James Bond preferred vehicle in Goldfinger?", "Aston Martin", ["aston martin", "sports car", "auto"]),
        ("What American brand pioneered the rugged 4x4 off-road vehicle during World War II?", "Jeep", ["jeep", "4x4", "suv", "automobile"]),
        ("Which Japanese brand produces the symmetrical all-wheel drive Impreza rally legend?", "Subaru", ["subaru", "rally", "car"]),
        ("What luxury car brand from Stuttgart is famous for the three-pointed star hood ornament?", "Mercedes-Benz", ["mercedes", "luxury", "automobile"]),
    ]

    bank = []
    for i, (q, a, kw) in enumerate(kia_questions, 1):
        bank.append({
            "id": f"kia-{i}",
            "question_text": q,
            "answer": a,
            "topic_l1": "Business & Startups",
            "subtopic_l2": "Automotive",
            "keywords": kw,
            "difficulty_score": 0.5,
            "difficulty": "medium",
            "image_dependent": False,
            "tagged": True,
            "question_format": "STANDARD_QA",
        })

    for i, (q, a, kw) in enumerate(generic_car_questions, 1):
        bank.append({
            "id": f"car-{i}",
            "question_text": q,
            "answer": a,
            "topic_l1": "Business & Startups",
            "subtopic_l2": "Automotive",
            "keywords": kw + ["car", "automobile"],
            "difficulty_score": 0.5,
            "difficulty": "medium",
            "image_dependent": False,
            "tagged": True,
            "question_format": "STANDARD_QA",
        })

    return bank


# =========================================================================
# BUG 1 TESTS: "Add adjacent questions" sticky cross_topic_padding flag
# =========================================================================

def test_deconstruct_prompt_preserves_cross_topic_padding(monkeypatch):
    """deconstruct_prompt builds Brief with cross_topic_padding=True when previous_brief has it."""
    monkeypatch.setenv("ENABLE_INTENT_ENGINE", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    mock_res = _mock_intent_response(subject="Kia")
    mock_db = MagicMock()
    # Mock taxonomy query in intent_engine
    mock_db.query.return_value.filter.return_value.all.return_value = []

    with patch.object(_http, "guarded_post", return_value=mock_res):
        # 1. previous_brief has cross_topic_padding=True
        prev_brief = {"query": "Kia", "count": 15, "cross_topic_padding": True}
        brief = deconstruct_prompt("15 questions on Kia", mock_db, count=15, previous_brief=prev_brief)
        assert brief is not None
        assert brief.cross_topic_padding is True

        # 2. previous_brief does not set cross_topic_padding
        brief_false = deconstruct_prompt("15 questions on Kia", mock_db, count=15, previous_brief={"count": 15})
        assert brief_false is not None
        assert brief_false.cross_topic_padding is False

        # 3. no previous_brief at all
        brief_none = deconstruct_prompt("15 questions on Kia", mock_db, count=15, previous_brief=None)
        assert brief_none is not None
        assert brief_none.cross_topic_padding is False


def test_engine_interpret_sticky_cross_topic_padding(monkeypatch):
    """engine.interpret preserves cross_topic_padding flag across refinements."""
    monkeypatch.setenv("ENABLE_INTENT_ENGINE", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    mock_res = _mock_intent_response(subject="Kia")
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = []

    with patch.object(_http, "guarded_post", return_value=mock_res):
        prev_brief = {"query": "Kia", "count": 15, "cross_topic_padding": True}
        b = engine.interpret("15 questions on Kia", count=15, previous_brief=prev_brief, db=mock_db)
        assert b.cross_topic_padding is True

        b_false = engine.interpret("15 questions on Kia", count=15, previous_brief={"count": 15}, db=mock_db)
        assert b_false.cross_topic_padding is False


def test_build_draft_pads_adjacent_when_flag_true_and_pool_short(monkeypatch):
    """build_draft pads with adjacent questions when cross_topic_padding is True and on-topic pool is short."""
    monkeypatch.setenv("ENABLE_AI_CURATOR", "false")
    monkeypatch.setenv("ENABLE_INTENT_ENGINE", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    bank = _make_car_bank()
    monkeypatch.setattr(engine, "_load_bank", lambda db, slots=None: bank)

    mock_res = _mock_intent_response(subject="Kia")
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = []

    with patch.object(_http, "guarded_post", return_value=mock_res):
        # With cross_topic_padding=True, draft should pad adjacent questions
        prev_brief = {"query": "Kia", "count": 15, "cross_topic_padding": True}
        res = engine.build_draft(mock_db, prompt="15 questions on Kia", count=15, previous_brief=prev_brief)
        ful = res["fulfillment"]
        assert ful["padded"] > 0
        assert ful["padded"] == 11
        assert len(res["questions"]) == 15


# =========================================================================
# BUG 2 TESTS: Specific brand/entity query returns shortfall without padding
# =========================================================================

def test_kia_specific_name_shortfall_and_padding_behavior(monkeypatch):
    """
    Bank fixture with 4 questions mentioning 'kia' and 20 generic car questions.
    - '15 questions on kia' with cross_topic_padding=False: returns 4 on-topic,
      fulfillment.short is true, suggestions contains add_adjacent with count 11.
    - With cross_topic_padding=True: returns 15 (4 on-topic plus 11 adjacent).
    """
    monkeypatch.setenv("ENABLE_AI_CURATOR", "false")
    monkeypatch.setenv("ENABLE_INTENT_ENGINE", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    bank = _make_car_bank()
    monkeypatch.setattr(engine, "_load_bank", lambda db, slots=None: bank)

    mock_res = _mock_intent_response(subject="Kia")
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = []

    with patch.object(_http, "guarded_post", return_value=mock_res):
        # 1. cross_topic_padding False: short on-topic pool, honest shortfall report
        res_short = engine.build_draft(mock_db, prompt="15 questions on Kia", count=15)
        ful_short = res_short["fulfillment"]

        # Assert returns 4 on-topic questions
        assert len(res_short["questions"]) == 4
        assert ful_short["delivered"] == 4
        assert ful_short["short"] is True
        # Assert all 4 questions actually mention 'kia'
        for q in res_short["questions"]:
            text_and_ans = (q["question_text"] + " " + q["answer"]).lower()
            assert "kia" in text_and_ans

        # Assert suggestions contains add_adjacent with count 11
        add_adj = [s for s in ful_short["suggestions"] if s.get("action") == "add_adjacent"]
        assert len(add_adj) == 1
        assert add_adj[0]["count"] == 11

        # 2. cross_topic_padding True: returns 15 questions (4 plus 11 adjacent)
        res_padded = engine.build_draft(
            mock_db,
            prompt="15 questions on Kia",
            count=15,
            previous_brief={**res_short["brief"], "cross_topic_padding": True},
        )
        ful_padded = res_padded["fulfillment"]

        assert len(res_padded["questions"]) == 15
        assert ful_padded["delivered"] == 15
        assert ful_padded["short"] is False
        assert ful_padded["padded"] == 11

        kia_count = sum(1 for q in res_padded["questions"] if "kia" in (q["question_text"] + " " + q["answer"]).lower())
        adjacent_count = sum(1 for q in res_padded["questions"] if "kia" not in (q["question_text"] + " " + q["answer"]).lower())
        assert kia_count == 4
        assert adjacent_count == 11


def test_thematic_prompt_unchanged(monkeypatch):
    """Thematic prompts ('everyday science', 'women pioneers in India') keep broad expansion."""
    monkeypatch.setenv("ENABLE_AI_CURATOR", "false")
    monkeypatch.setenv("ENABLE_INTENT_ENGINE", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    # Fixture with varied science questions
    science_bank = []
    science_topics = [
        ("Physics", "What is the phenomenon where light bends as it passes between media?", "Refraction"),
        ("Chemistry", "Why does an apple turn brown after being sliced and exposed to air?", "Oxidation"),
        ("Biology", "Which organelle in plant cells carries out photosynthesis?", "Chloroplast"),
        ("Astronomy", "Why do stars twinkle when observed from the surface of Earth?", "Atmospheric refraction"),
        ("Everyday", "Why does baking soda cause cake batter to rise when heated?", "Carbon dioxide"),
        ("Kitchen", "What substance in onions causes our eyes to tear up when cut?", "Syn-propanethial-S-oxide"),
        ("Weather", "What causes the formation of a rainbow after a rain shower?", "Dispersion of sunlight"),
        ("Sound", "Why does sound travel faster through water than through air?", "Higher density and elasticity"),
        ("Electricity", "Why does static electricity make your hair stand up after rubbing a balloon?", "Charge transfer"),
        ("Thermal", "Why do metal handles on cooking pots get hotter than wooden handles?", "Thermal conductivity"),
    ]
    for i, (sub, q, a) in enumerate(science_topics, 1):
        science_bank.append({
            "id": f"sci-{i}",
            "question_text": q,
            "answer": a,
            "topic_l1": "Science & Technology",
            "subtopic_l2": sub,
            "keywords": ["science", "experiments", "everyday", sub.lower()],
            "difficulty_score": 0.5,
            "difficulty": "medium",
            "image_dependent": False,
            "tagged": True,
            "question_format": "STANDARD_QA",
        })

    monkeypatch.setattr(engine, "_load_bank", lambda db, slots=None: science_bank)

    mock_res = _mock_intent_response(
        subject="Everyday Science",
        expansions=["science", "physics", "chemistry", "experiments", "phenomenon"],
    )
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = []

    with patch.object(_http, "guarded_post", return_value=mock_res):
        res = engine.build_draft(mock_db, prompt="10 questions on everyday science", count=10)
        # Broad expansion stays intact: delivers all 10 science questions across varied subtopics
        assert len(res["questions"]) == 10
        assert res["fulfillment"]["short"] is False
