"""QShala retrieval core — drop into backend/app/services/tagging/.

Framework-free. Build an index from a list of question dicts, then search with a
free-text query plus optional filters. Ranking is BM25 over (question text + answer +
keywords + subtopics + summary), with domain query-expansion so "fintech" also matches
"UPI", "wallet", etc. No external dependencies.

To use in FastAPI: build the index once at startup from your qshala_tags rows
(latest row per question, any status), then call index.search(query, filters) per request.
Swap this BM25 stage for pgvector later without changing the call site — see search().

Question dict shape (use your qshala_tags field names):
    {id, question_text, answer, topic_l1, subtopic_l2, secondary_l2[list],
     keywords (or keywords_l3)[list], india_international, shelf_life, sensitivity,
     image_dependent(bool)}
"""
from __future__ import annotations
import difflib
import heapq
import math, random as _rnd, re
from functools import lru_cache
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

STOP = set("the a an of to in on for and or is are was were be been being by with as at "
           "from this that it its their his her they them he she we you i which what who whom "
           "whose when where why how name named called known used also only not no than into over "
           "under after before during about more most new first world which these those there here "
           "had has have does did do your my our".split())

# Domain query-expansion: a query term on the left pulls in the terms on the right.
# These are one-directional (query -> extra query terms), tuned for QShala's decks.
SYNONYMS: Dict[str, List[str]] = {
    "fintech": ["upi", "wallet", "payments", "neobank", "paytm", "phonepe", "razorpay", "cred", "fintech"],
    "payments": ["upi", "wallet", "fastag", "rupay", "nfc", "card", "payment"],
    "crypto": ["cryptocurrency", "bitcoin", "dogecoin", "blockchain", "nft", "wazirx"],
    "startup": ["startup", "unicorn", "funding", "venture", "seed", "ipo", "founder"],
    "startups": ["startup", "unicorn", "funding", "venture", "founder"],
    "bank": ["bank", "banking", "rbi", "deposit", "loan", "nbfc", "atm"],
    "banking": ["bank", "rbi", "deposit", "npa", "loan", "nbfc"],
    "tax": ["tax", "gst", "income tax", "levy", "duty", "cess"],
    "ipo": ["ipo", "listing", "public offering", "prospectus"],
    "stock": ["stock", "share", "nse", "bse", "sensex", "nifty", "exchange", "equity"],
    "market": ["market", "stock", "sensex", "nifty", "index"],
    "cricket": ["cricket", "ipl", "bcci", "wicket", "test match", "world cup", "icc"],  # added "icc"
    "sports": ["sports", "league", "football", "olympics", "tournament", "player", "cricket", "ipl", "athlete"],  # added cricket, ipl, athlete
    "logo": ["logo", "brand", "identity", "mascot", "typeface", "symbol"],
    "brand": ["brand", "logo", "fmcg", "consumer", "marketing"],
    "ad": ["ad", "advertisement", "campaign", "advertising", "jingle", "tagline"],
    "advertising": ["ad", "advertisement", "campaign", "slogan", "tagline"],
    "insurance": ["insurance", "lic", "policy", "premium", "irdai"],
    "currency": ["currency", "note", "coin", "banknote", "rupee", "money", "mint"],
    "money": ["money", "currency", "cash", "fiat", "note", "coin"],
    "etymology": ["word", "origin", "latin", "derived", "phrase", "term"],
    "word": ["word", "origin", "etymology", "phrase", "derived"],
    "food": ["food", "cuisine", "dish", "sweet", "snack", "restaurant"],
    "ai": ["ai", "artificial intelligence", "chatbot", "openai", "gpt", "llm"],
    "ev": ["ev", "electric", "vehicle", "battery", "ola", "ather"],
    "auto": ["car", "automobile", "vehicle", "maruti", "tata motors", "ev"],
    "car": ["car", "automobile", "vehicle", "ev"],
    "acquisition": ["acquisition", "merger", "acquire", "stake", "buyout", "takeover"],
    "merger": ["merger", "acquisition", "acquire", "stake"],
    "scam": ["scam", "fraud", "scandal", "ponzi", "collapse", "phishing"],
    "scandal": ["scandal", "fraud", "collapse", "corporate crime"],
    "energy": ["energy", "solar", "power", "renewable", "hydrogen", "coal", "nuclear"],
    "festival": ["festival", "diwali", "mela", "celebration", "event"],
    "people": ["people", "person", "personality", "personalities", "figure", "figures", "leader", "leaders", "great indian", "scientist", "inventor", "pioneer", "author", "founder", "achiever"],
    "person": ["person", "people", "personality", "personalities", "figure", "leader", "pioneer", "great indian", "achiever"],
    "personality": ["personality", "personalities", "person", "people", "figure", "figures", "leader", "great indian", "achiever", "pioneer"],
    "personalities": ["personalities", "personality", "people", "person", "figure", "figures", "leaders", "great indian", "achievers", "pioneers"],
    "figure": ["figure", "figures", "personality", "personalities", "person", "people", "leader", "pioneer", "achiever"],
    "figures": ["figures", "figure", "personalities", "personality", "people", "person", "leaders", "achievers"],
    "leader": ["leader", "leaders", "personality", "pioneer", "president", "minister", "founder", "statesman", "chief", "ceo"],
    "ceo": ["ceo", "chief executive", "founder", "chairman"],
    "corporate": ["company", "business", "brand", "ceo", "industry", "merger", "corporate", "acquisition", "firm", "leader"],
    "business": ["company", "brand", "startup", "market", "industry", "business", "corporate"],
    "company": ["company", "brand", "corporate", "business", "founder", "ceo"],
    "karnataka": ["karnataka", "bangalore", "bengaluru", "mysore", "mysuru", "kannada"],
    "bangalore": ["bangalore", "bengaluru", "karnataka"],
    "bengaluru": ["bengaluru", "bangalore", "karnataka"],
    "mumbai": ["mumbai", "bombay", "maharashtra"],
    "bombay": ["bombay", "mumbai"],
    # Geography — city name synonyms
    "delhi": ["delhi", "new delhi", "ncr"],
    "chennai": ["chennai", "madras", "tamil nadu"],
    "kolkata": ["kolkata", "calcutta"],
    "hyderabad": ["hyderabad", "telangana"],
    # Entertainment and media
    "entertainment": ["entertainment", "movie", "film", "bollywood", "music", "actor", "cinema", "show"],
    "movie": ["movie", "film", "bollywood", "cinema", "actor", "director"],
    "bollywood": ["bollywood", "movie", "film", "actor", "hindi cinema"],
    # Education
    "college": ["college", "university", "campus", "student", "education", "institute"],
    "engineering": ["engineering", "engineer", "iit", "nit", "technology", "tech"],
    "school": ["school", "student", "education", "class", "kids"],
    # Finance extensions
    "rbi": ["rbi", "reserve bank", "central bank", "monetary policy"],
    "reit": ["reit", "real estate", "investment trust", "property"],
    "npa": ["npa", "bad loan", "non performing", "default"],
    # Misc
    "politics": ["politics", "political", "government", "minister", "parliament", "election"],
    "culture": ["culture", "cultural", "heritage", "tradition", "art", "festival"],
    "science": ["science", "experiment", "physics", "chemistry", "biology", "scientific", "phenomenon", "laboratory", "gravity", "light", "sound", "heat", "matter", "force", "magnet", "electricity"],
    "experiment": ["experiment", "experiments", "science", "demonstration", "observation", "phenomenon", "apparatus", "reaction"],
    "experiments": ["experiment", "experiments", "science", "demonstration", "observation", "phenomenon"],
    "everyday": ["everyday", "daily", "kitchen", "household", "common", "routine", "science"],
    "physics": ["physics", "force", "motion", "light", "sound", "electricity", "magnet", "gravity", "lens", "mirror"],
    "chemistry": ["chemistry", "acid", "base", "chemical", "reaction", "element", "molecule", "salt", "metal"],
    "biology": ["biology", "plant", "cell", "human", "body", "organ", "photosynthesis", "blood", "bacteria"],
    "quiz": ["quiz", "trivia", "question"],
    "world cup": ["world cup", "icc", "fifa", "cricket", "tournament"],
}

WORD_NUMS: Dict[str, int] = {
    "zero": 0,
    "a couple of": 2, "a couple": 2, "couple": 2,
    "a few": 3, "few": 3,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "twenty-five": 25, "thirty": 30,
    "forty": 40, "fifty": 50,
}

_word = re.compile(r"[a-z0-9]+")
_GLUED_QUIZ_BOUNDARY = re.compile(
    r"\bquiz(karnataka|india|bengaluru|bangalore|mumbai|bombay|delhi|chennai|madras|kolkata|calcutta|hyderabad|pune|science|sports|cricket|finance|history)\b",
    re.I,
)

def normalize_compound_typos(s: str) -> str:
    """Split accidental missing spaces around 'quiz<location/topic>' (e.g. 'quizkarnataka' -> 'quiz karnataka')."""
    if not s:
        return ""
    return _GLUED_QUIZ_BOUNDARY.sub(r"quiz \1", s)

def tokenize(s: str) -> List[str]:
    norm = normalize_compound_typos(s or "").lower()
    return [w for w in _word.findall(norm) if w not in STOP and len(w) > 1]

def _kw(q: Dict[str, Any]) -> List[str]:
    return q.get("keywords") or q.get("keywords_l3") or []


# --- deterministic question style (from text; no model, no stored column needed) ---
_STYLE_PATTERNS = [
    ("FILL_BLANK", re.compile(r"_{3,}|\bblanked?\b|fill in the blank", re.I)),
    ("ODD_ONE_OUT", re.compile(r"odd one out|does ?n.?t belong|which does not", re.I)),
    ("CONNECT", re.compile(r"\bconnect\b|what links|have in common|common (?:link|thread|factor)|identify the connect", re.I)),
    ("MCQ", re.compile(r"(?:^|\n)\s*[a-dA-D][\.\)]\s|\bwhich of the following\b|\boption[s]?\b", re.I)),
]
_NUMERIC_Q = re.compile(r"\bhow (?:many|much)\b|\bwhat (?:percentage|number|value|year|price)\b|\bestimate\b", re.I)
_NUMERIC_A = re.compile(r"^[\s\d,.%₹$+\-]+$")

def question_style(text: str, answer: str = "", image_dependent: bool = False) -> str:
    t = text or ""
    if image_dependent and re.search(r"identify|whose logo|which (?:brand|company).{0,20}(?:logo|ad)|which logo|name the", t, re.I):
        return "VISUAL_ID"
    for name, pat in _STYLE_PATTERNS:
        if pat.search(t):
            return name
    if _NUMERIC_Q.search(t) or _NUMERIC_A.match(str(answer).strip()):
        return "NUMERIC"
    return "STANDARD"

def expand(tokens: List[str]) -> Counter:
    """Return a weighted bag of query terms: originals weight 1.0, expansions 0.4.
    Includes fuzzy matching for common typos against domain vocabulary."""
    return Counter(_expand_cached(tuple(tokens)))


@lru_cache(maxsize=2048)
def _expand_cached(tokens: tuple) -> Counter:
    tokens = list(tokens)
    bag: Counter = Counter()
    expanded_tokens = list(tokens)
    
    # Check for close matches in SYNONYMS keys for any token not directly matched
    known_keys = list(SYNONYMS.keys())
    for t in tokens:
        if t not in SYNONYMS and len(t) >= 4:
            close = difflib.get_close_matches(t, known_keys, n=1, cutoff=0.75)
            if close:
                matched_key = close[0]
                if matched_key not in expanded_tokens:
                    expanded_tokens.append(matched_key)
                    bag[matched_key] += 0.8

    for t in expanded_tokens:
        bag[t] += 1.0
    text = " ".join(expanded_tokens)
    for key, extra in SYNONYMS.items():
        if key in expanded_tokens or (" " in key and key in text):
            for e in extra:
                for et in tokenize(e):
                    bag[et] = max(bag[et], 0.4)
    return bag

class Index:
    def __init__(self, questions: List[Dict[str, Any]], k1: float = 1.5, b: float = 0.75):
        self.q = questions
        self.k1, self.b = k1, b
        self.docs: List[Counter] = []
        self.len: List[int] = []
        self.style: List[str] = []
        self._tl_tokens: List[set] = []
        self._geo_txt: List[Optional[str]] = [None] * len(questions)
        df: Counter = Counter()
        for item in questions:
            parts = [item.get("question_text", ""), item.get("answer", ""),
                     " ".join(_kw(item)), item.get("subtopic_l2") or "",
                     " ".join(item.get("secondary_l2") or []), item.get("search_summary") or "",
                     item.get("topic_l1") or "", item.get("document_title") or ""]
            # keywords/answer/subtopic/topic repeated so they weigh more than body prose
            toks = tokenize(" ".join(parts)) + tokenize(" ".join(_kw(item))) \
                   + tokenize(item.get("answer", "")) + tokenize(item.get("subtopic_l2") or "") \
                   + tokenize(item.get("topic_l1") or "") + tokenize(item.get("document_title") or "")
            c = Counter(toks)
            self.docs.append(c)
            self.len.append(sum(c.values()))
            self._tl_tokens.append(set(tokenize(((item.get("topic_l1") or "") + " " + (item.get("subtopic_l2") or "")).lower())))
            self.style.append(item.get('question_style') or question_style(item.get('question_text',''), item.get('answer',''), bool(item.get('image_dependent'))))
            for term in c:
                df[term] += 1
        self.N = len(questions)
        self.avgdl = (sum(self.len) / self.N) if self.N else 0.0
        self.idf = {t: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def _bm25(self, i: int, qbag: Counter) -> float:
        c, dl = self.docs[i], self.len[i]
        s = 0.0
        for term, qw in qbag.items():
            tf = c.get(term, 0)
            if not tf:
                continue
            idf = self.idf.get(term, 0.0)
            denom = tf + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
            s += qw * idf * (tf * (self.k1 + 1)) / denom
        return s

    def search(self, query: str, filters: Optional[Dict[str, Any]] = None,
               top_k: int = 20) -> List[Dict[str, Any]]:
        filters = filters or {}
        qbag = expand(tokenize(query))
        # --- structured filters: hard gates that a keyword match can't override ---
        def passes(idx: int, item: Dict[str, Any]) -> bool:
            if filters.get("exclude_image") and item.get("image_dependent"):
                return False
            geo = filters.get("india_international")
            if geo and item.get("india_international") not in (geo, "MIXED"):
                if geo == "IN":
                    txt = self._geo_txt[idx]
                    if txt is None:
                        txt = self._geo_txt[idx] = (
                            (item.get("question_text") or "")
                            + " "
                            + (item.get("answer") or "")
                            + " "
                            + (item.get("topic_l1") or "")
                            + " "
                            + (item.get("subtopic_l2") or "")
                            + " "
                            + " ".join(item.get("tags") or [])
                            + " "
                            + (item.get("geo_region") or "")
                        ).lower()
                    in_markers = (
                        "india", "indian", "karnataka", "bangalore", "bengaluru",
                        "mumbai", "bombay", "delhi", "chennai", "madras",
                        "kolkata", "calcutta", "pune", "hyderabad", "mysore", "mysuru", "hassan"
                    )
                    if not any(m in txt for m in in_markers):
                        return False
                else:
                    return False
            if filters.get("evergreen_only") and item.get("shelf_life") == "TIME_SENSITIVE":
                return False
            if filters.get("school_safe") and item.get("sensitivity") in ("POLITICAL", "MATURE_REFERENCE"):
                return False
            topic = filters.get("topic_l1")
            if topic and item.get("topic_l1") != topic:
                return False
            styles = filters.get("question_style")
            if styles and self.style[idx] not in styles:
                return False
            # reuse history: item["used_in"] is a list of {quiz, audience, year} (or plain strings).
            # exclude a question that was already used at a given venue/quiz, optionally since a year.
            ex_venues = filters.get("exclude_used_in")   # list of quiz/venue/audience names
            since = filters.get("exclude_since_year")    # int or None
            if ex_venues:
                for u in (item.get("used_in") or []):
                    quiz = u.get("quiz") if isinstance(u, dict) else u
                    aud = u.get("audience") if isinstance(u, dict) else None
                    yr = u.get("year") if isinstance(u, dict) else None
                    if (quiz in ex_venues or aud in ex_venues) and (since is None or (yr or 0) >= since):
                        return False
            return True
        scored = []
        is_broad = len(qbag) == 0
        for i, item in enumerate(self.q):
            if not passes(i, item):
                continue
            if is_broad:
                # Broad/empty query: all questions are equally valid,
                # so add randomness to get variety instead of same order every time
                s = 1.0 + (0.5 if item.get("tagged") else 0.0) + _rnd.random() * 0.3
            else:
                s = self._bm25(i, qbag)
                if s > 0:
                    # small boost if the query words appear in the chosen topic/subtopic
                    tl_tokens = self._tl_tokens[i]
                    if any(t in tl_tokens for t in qbag):
                        s += 0.5
                    if item.get("image_dependent") and not filters.get("exclude_image"):
                        s *= 1.15  # multiplicative: won't create a false floor for zero-match questions
            if s > 0:
                scored.append((s, i, item))
        if 0 < top_k < len(scored):
            top = heapq.nlargest(top_k, scored, key=lambda x: x[0])   # same result and tie order as sort+slice
        else:
            scored.sort(key=lambda x: x[0], reverse=True)
            top = scored[:top_k]
        out = []
        for s, i, it in top:
            row = dict(it)
            row["score"] = round(s, 3)
            row["question_style"] = it.get("question_style") or self.style[i]
            out.append(row)
        return out


# ---- very light prompt -> (clean query, filters) parser, for a chat-style box ----
_FORMAT_PATTERNS = {
    "CONNECT_4": re.compile(
        r"\b(connect\s*[-–]?\s*(4|four)|common\s+link|connect\s+round)\b", re.I
    ),
    "STEPPED_CLUE": re.compile(
        r"\b(stepped\s+clue|decreasing\s+value|portfolio\s+builder|3[- ]clue|three[- ]clue)\b", re.I
    ),
    "ARRANGE_ORDER": re.compile(
        r"\b(arrange|chronolog|ticker\s+tangle|correct\s+order)\b", re.I
    ),
    "STANDARD_QA": re.compile(
        r"\b(plain\s+qa|standard\s+qa|regular\s+qa|standard\s+format)\b", re.I
    ),
}

def parse_prompt(prompt: str):
    p = normalize_compound_typos(prompt or "").lower()
    filters: Dict[str, Any] = {}
    for fmt, pattern in _FORMAT_PATTERNS.items():
        if pattern.search(p):
            filters["format_filter"] = fmt
            p = pattern.sub(" ", p)
            break
    if re.search(r"\bevergreen|timeless|any year\b", p):
        filters["evergreen_only"] = True
    if re.search(r"\bschool|kids|children|family|safe|non-?political|no politics\b", p):
        filters["school_safe"] = True
    if re.search(r"\bno image|text only|without images?|non-?image\b", p):
        filters["exclude_image"] = True
    if re.search(r"\bindia|indian\b", p):
        filters["india_international"] = "IN"
    elif re.search(r"\binternational|global|world\b", p):
        filters["india_international"] = "INTL"
    # Remove explicit grade/class ranges before extracting requested question count
    p = re.sub(r"\b(?:grade|class|std|standard)\s*\d{1,2}(?:\s*(?:&|and|to|-)\s*\d{1,2})?\b", " ", p)
    m = re.search(r"\b(\d{1,3})\b", p)
    count = None
    if m:
        count = int(m.group(1))
    else:
        for w, num in WORD_NUMS.items():
            if re.search(r"\b" + re.escape(w) + r"\b", p):
                count = num
                break
    final_count = count if count is not None else 20
    # Strip meta/wrapper words so phrases like 'state board', 'students', 'relevant', 'around'
    # don't match corporate boards or state banks in BM25 retrieval.
    q = re.sub(r"\b(?:state\s+board|cbse|icse|ncert|state\s+syllabus|syllabus|middle\s+school|high\s+school|primary\s+school|focus\s+on)\b", " ", p)
    word_num_pattern = "|".join(WORD_NUMS.keys())
    q = re.sub(r"\b(\d{1,3}|" + word_num_pattern + r"|questions?|quiz|round|rounds?|evergreen|international|global|"
               r"school|safe|family|kids|children|students?|relevant|around|friendly|focus|focused|perspective|"
               r"without images?|without image|no images?|no image|text only|non-?image|"
               r"easy|warm-?ups?|beginner|gentle|hard|tough|difficult|finals?|balanced|"
               r"standard|qa|trivia|night|tonight|quick|surprise|some|good|best|great|nice|cool|fun|"
               r"on|about|a|an|the|of|for|with|give me|pull|me|show me|create|generate|want|make it)\b",
               " ", q)
    q = re.sub(r"[^a-z0-9 ]", " ", q)
    return re.sub(r"\s+", " ", q).strip(), filters, final_count
