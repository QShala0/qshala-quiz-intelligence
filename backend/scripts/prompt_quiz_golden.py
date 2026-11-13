"""Golden-output harness for the prompt-quiz engine. Usage: python backend/scripts/prompt_quiz_golden.py <repo_root> record|compare <snapshot.json>
(no network, no LLM, seeded synthetic bank). BENCH=1 BANK_N=20000 GOLDEN_CACHED=1 PROMPT_QUIZ_BANK_TTL_S=600 times warm drafts instead."""
import json, os, random, sys, hashlib
from unittest.mock import MagicMock, patch

root, mode, path = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, root)
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["REQUIRE_POSTGRES"] = "0"
os.environ["ENABLE_INTENT_ENGINE"] = "false"
os.environ["ENABLE_AI_CURATOR"] = "false"

from backend.app.services.prompt_quiz import engine
LOADER = "_load_bank_uncached" if os.getenv("GOLDEN_CACHED") else "_load_bank"
from backend.app.services.prompt_quiz.retrieval import Index

rng = random.Random(7)
DB = MagicMock()
THEMES = {
    "Finance": ["Banks and Banking", "Central Banking", "Coins and Notes"],
    "Business": ["Brands", "Startups", "Founders"],
    "Technology": ["Artificial Intelligence", "Consumer Tech and Gadgets"],
    "Sports": ["Cricket", "Olympics and Multi-Sport Events"],
    "History": ["Freedom Struggle", "Modern History"],
    "Science": ["Space and Astronomy", "Inventions and Inventors"],
    "Culture": ["Festivals and Traditions", "Heritage"],
    "Entertainment": ["Celebrities", "Musicians and Bands"],
    "Geography": ["Cities", "Rivers"],
}
WORDS = "upi wallet rbi bank loan cricket ipl bcci isro rocket kalam ramanujan bose diwali mela bengaluru mumbai delhi chennai karnataka startup unicorn fintech paytm phonepe logo brand ai chatbot gpt coin note rupee olympics medal river city temple music film actor founder ceo".split()
bank = []
for i in range(int(os.getenv('BANK_N','600'))):
    l1 = rng.choice(list(THEMES)); l2 = rng.choice(THEMES[l1])
    kws = rng.sample(WORDS, 4)
    ans = rng.choice(["Kalam", "Srinivasa Ramanujan", "Dr. Sooryavanshi", "Mumbai", "Bengaluru", f"Answer{i%150}", "Bose"])
    bank.append({
        "id": f"q{i}", "question_text": f"Which {rng.choice(WORDS)} {rng.choice(WORDS)} {l2.lower()} item number {i} relates to {kws[0]} and {kws[1]}?" + (f" w{i%4000} x{i%977} y{i%311}" if os.getenv("BENCH") else ""),
        "answer": ans, "topic_l1": l1, "subtopic_l2": l2, "secondary_l2": [], "keywords": kws,
        "india_international": rng.choice(["IN", "INTL", "MIXED", None]),
        "shelf_life": rng.choice(["EVERGREEN", "TIME_SENSITIVE"]),
        "sensitivity": rng.choice(["NONE", "POLITICAL", "MATURE_REFERENCE", None]),
        "image_dependent": rng.random() < 0.2, "question_images": [], "answer_images": [], "image_refs": [],
        "search_summary": rng.choice([None, f"about {kws[2]} {kws[3]}"]),
        "difficulty": rng.choice(["Easy", "Medium", "Hard"]), "difficulty_score": round(rng.random(), 3),
        "document_id": f"d{i%7}", "document_title": f"Deck {i%7}", "tagged": rng.random() < 0.8,
        "question_format": rng.choice(["STANDARD_QA", "STANDARD_QA", "CONNECT_4", "STEPPED_CLUE", "ARRANGE_ORDER"]),
        "format_payload": None, "city_name": rng.choice([None, "Bengaluru", "Mumbai"]), "geo_region": rng.choice([None, "Karnataka"]),
        "primary_audience": rng.choice([None, "school", "college", "corporate"]),
    })

PROMPTS = [
    "10 questions on banking", "15 hard questions on cricket and IPL", "school quiz on important people in India",
    "fintech in bangalore", "5 questions", "surprise me with 8 questions", "20 evergreen questions on startups",
    "easy questions on space for kids", "connect 4 round", "stepped clue round on brands", "arrange in chronological order history",
    "text only questions on technology", "karnataka", "mumbai food and culture 12 questions", "corporate offsite quiz on business",
    "college fest sports 10 questions medium", "international finance questions", "ai and chatbots 7 questions",
    "diwali festival questions for school", "market quiz", "rbi coins and notes", "give me 6 questions on rocket and isro",
    "freedom fighters", "hard bank loan questions or related",
]

if os.getenv("BENCH"):
    import time
    ps=["10 questions on banking","school quiz on important people in India","fintech in bangalore","5 questions","15 hard questions on cricket and IPL"]
    def run():
        with patch.object(engine, LOADER, lambda db, *a, **k: [dict(r) for r in bank]), patch("backend.app.config.settings.GEMINI_API_KEY",""):
            for q in ps: engine.build_draft(DB, q)
    run()  # warm cache
    t=time.perf_counter(); [run() for _ in range(3)]
    print("BENCH N=%d: %.0f ms per draft"%(len(bank),(time.perf_counter()-t)/15*1000)); sys.exit(0)

def snap_draft(prompt, **kw):
    random.seed(1234)
    with patch.object(engine, LOADER, lambda db, *a, **k: [dict(r) for r in bank]), \
         patch("backend.app.config.settings.GEMINI_API_KEY", ""):
        d = engine.build_draft(DB, prompt, **kw)
    return {"ids": [q["id"] for q in d["questions"]], "scores": [q.get("score") for q in d["questions"]],
            "message": d["message"], "bank_size": d["bank_size"], "stages": d["stages"],
            "fulfillment": d["fulfillment"], "brief": d["brief"]}

out = {"drafts": {}, "swaps": {}, "search": {}}
for p in PROMPTS:
    out["drafts"][p] = snap_draft(p)
out["drafts"]["__prev_brief__more"] = snap_draft("5 more", previous_brief=out["drafts"]["10 questions on banking"]["brief"])
out["drafts"]["__exclude__"] = snap_draft("10 questions on banking", exclude_ids=set(out["drafts"]["10 questions on banking"]["ids"][:4]))
for p in ["10 questions on banking", "cricket", "school quiz on important people in India"]:
    ids = snap_draft(p)["ids"]
    random.seed(99)
    with patch.object(engine, LOADER, lambda db, *a, **k: [dict(r) for r in bank]), patch("backend.app.config.settings.GEMINI_API_KEY", ""):
        s = engine.swap_one(MagicMock(), p, ids[0], ids[1:])
    out["swaps"][p] = s and {"id": s["id"], "score": s.get("score")}

idx = Index(bank)
for q, f in [("banking loans", {}), ("cricket ipl", {"exclude_image": True}), ("kalam ramanujan", {"india_international": "IN", "school_safe": True}),
             ("", {}), ("bnak", {}), ("fintech", {"evergreen_only": True}), ("startup", {"topic_l1": "Business"}), ("upi payments", {"question_style": ["STANDARD"]})]:
    random.seed(5)
    for k in (10, 80, 600):
        out["search"][f"{q}|{json.dumps(f, sort_keys=True)}|{k}"] = [(h["id"], h["score"]) for h in idx.search(q, f, top_k=k)]

blob = json.dumps(out, sort_keys=True, default=str)
if mode == "record":
    open(path, "w").write(blob); print("recorded", len(blob), hashlib.md5(blob.encode()).hexdigest())
else:
    gold = open(path, newline="").read().replace("\r\n", "\n")      # tolerate a Windows checkout
    if gold == blob: print("IDENTICAL", hashlib.md5(blob.encode()).hexdigest())
    else:
        g, n = json.loads(gold), json.loads(blob)
        for sec in g:
            for k in g[sec]:
                if g[sec][k] != n[sec].get(k): print("DIFF", sec, k)
        sys.exit(1)
if os.getenv("GOLDEN_CACHED"):
    import backend.app.services.prompt_quiz.retrieval as R
    print("index cached:", engine._BANK_CACHE["index"] is not None)
