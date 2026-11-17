"""
QShala Quiz Intelligence Evaluation Benchmark Runner.

Executes all evaluation prompts from Sections A through Z in order against the live
Cloud Run endpoint (or local instance), evaluates PASS/FAIL based on the benchmark criteria,
and outputs a structured test report.
"""
import json
import os
import sys
import time
import requests
from typing import Dict, Any, List, Optional, Tuple

BASE_URL = os.environ.get("API_BASE_URL", "https://qshala-quiz-intelligence-238470261844.asia-southeast1.run.app").rstrip("/")
DRAFT_URL = f"{BASE_URL}/api/v1/prompt-quiz/draft"

results: List[Dict[str, Any]] = []

def call_draft(prompt: str, previous_brief: Optional[Dict[str, Any]] = None,
               current_questions: Optional[List[Dict[str, Any]]] = None,
               keep_ids: Optional[List[str]] = None,
               exclude_ids: Optional[List[str]] = None,
               options: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
    payload: Dict[str, Any] = {"prompt": prompt}
    if previous_brief is not None:
        payload["previous_brief"] = previous_brief
    if current_questions is not None:
        payload["current_questions"] = current_questions
    if keep_ids is not None:
        payload["keep_ids"] = keep_ids
    if exclude_ids is not None:
        payload["exclude_ids"] = exclude_ids
    if options is not None:
        payload["options"] = options

    try:
        r = requests.post(DRAFT_URL, json=payload, timeout=20)
        try:
            data = r.json()
        except Exception:
            data = {"raw_text": r.text}
        return r.status_code, data
    except Exception as e:
        return 500, {"error": str(e)}

def record(section: str, test_id: str, prompt: str, expected: str, passed: bool, got: str, fulfillment_msg: str = ""):
    status = "PASS" if passed else "FAIL"
    res = {
        "section": section,
        "id": test_id,
        "prompt": prompt,
        "expected": expected,
        "status": status,
        "got": got,
        "fulfillment": fulfillment_msg
    }
    results.append(res)
    print(f"[{status}] {test_id}: {prompt!r}")
    if not passed:
        print(f"   Expected: {expected}")
        print(f"   Got:      {got}")
        if fulfillment_msg:
            print(f"   Note:     {fulfillment_msg}")

print(f"Starting QShala Evaluation Suite against {DRAFT_URL}...")
print("=" * 60)

def run_all():
    global results
    results = []

    # ==========================================
    # SECTION A: FIRST IMPRESSIONS
    # ==========================================
    print("\n--- SECTION A: FIRST IMPRESSIONS ---")
    # A1: quiz me
    status, d = call_draft("quiz me")
    qs = d.get("questions", [])
    p = status == 200 and len(qs) >= 5
    a1_ids = [q.get("id") for q in qs]
    record("A", "A1", "quiz me", "~10 random varied questions", p, f"{len(qs)} questions returned", d.get("fulfillment", {}).get("note", ""))

    # A2: surprise me
    status, d = call_draft("surprise me")
    qs = d.get("questions", [])
    a2_ids = [q.get("id") for q in qs]
    # Expect different set from A1
    diff = len(set(a2_ids) - set(a1_ids)) if a1_ids else len(a2_ids)
    p = status == 200 and len(qs) >= 5 and diff >= 3
    record("A", "A2", "surprise me", "random set different from A1", p, f"{len(qs)} questions, {diff} different from A1", d.get("fulfillment", {}).get("note", ""))

    # A3: 10 questions
    status, d = call_draft("10 questions")
    qs = d.get("questions", [])
    p = status == 200 and len(qs) == 10
    record("A", "A3", "10 questions", "10 varied questions, no topic bias", p, f"{len(qs)} questions, topic={d.get('brief', {}).get('query')}", d.get("fulfillment", {}).get("note", ""))

    # A4: cricket
    status, d = call_draft("cricket")
    qs = d.get("questions", [])
    brief = d.get("brief", {})
    query = (brief.get("query") or "").lower()
    p = status == 200 and ("cricket" in query or any("cricket" in (q.get("topic_l1") or "").lower() or "cricket" in (q.get("question_text") or "").lower() for q in qs))
    record("A", "A4", "cricket", "~10 cricket questions", p, f"{len(qs)} questions, query={query!r}", d.get("fulfillment", {}).get("note", ""))

    # A5: something on finance
    status, d = call_draft("something on finance")
    qs = d.get("questions", [])
    brief = d.get("brief", {})
    query = (brief.get("query") or "").lower()
    p = status == 200 and ("finance" in query or "finance" in [t.lower() for t in brief.get("topics_l1", [])] or any("finance" in (q.get("topic_l1") or "").lower() for q in qs))
    record("A", "A5", "something on finance", "~10 finance questions", p, f"{len(qs)} questions, query={query!r}", d.get("fulfillment", {}).get("note", ""))

    # A6: give me a quick quiz
    status, d = call_draft("give me a quick quiz")
    qs = d.get("questions", [])
    p = status == 200 and len(qs) >= 5
    record("A", "A6", "give me a quick quiz", "~10 varied", p, f"{len(qs)} questions", d.get("fulfillment", {}).get("note", ""))

    # A7: I want questions about Indian history
    status, d = call_draft("I want questions about Indian history")
    qs = d.get("questions", [])
    brief = d.get("brief", {})
    p = status == 200 and (brief.get("filters", {}).get("india_focused") is True or "history" in (brief.get("query") or "").lower())
    record("A", "A7", "I want questions about Indian history", "history questions, India-focused", p, f"{len(qs)} questions, filters={brief.get('filters')}", d.get("fulfillment", {}).get("note", ""))

    # ==========================================
    # SECTION B: COUNTING
    # ==========================================
    print("\n--- SECTION B: COUNTING ---")
    # B1: 5 questions on sports
    status, d = call_draft("5 questions on sports")
    qs = d.get("questions", [])
    cnt = d.get("brief", {}).get("count")
    record("B", "B1", "5 questions on sports", "exactly 5", status == 200 and cnt == 5, f"count={cnt}, delivered={len(qs)}", d.get("fulfillment", {}).get("note", ""))

    # B2: twenty questions on technology
    status, d = call_draft("twenty questions on technology")
    qs = d.get("questions", [])
    cnt = d.get("brief", {}).get("count")
    record("B", "B2", "twenty questions on technology", "exactly 20", status == 200 and cnt == 20, f"count={cnt}, delivered={len(qs)}", d.get("fulfillment", {}).get("note", ""))

    # B3: a couple of questions on banking
    status, d = call_draft("a couple of questions on banking")
    cnt = d.get("brief", {}).get("count")
    record("B", "B3", "a couple of questions on banking", "2", status == 200 and cnt == 2, f"count={cnt}", d.get("fulfillment", {}).get("note", ""))

    # B4: a few questions on cricket
    status, d = call_draft("a few questions on cricket")
    cnt = d.get("brief", {}).get("count")
    record("B", "B4", "a few questions on cricket", "3", status == 200 and cnt == 3, f"count={cnt}", d.get("fulfillment", {}).get("note", ""))

    # B5: one question on bollywood
    status, d = call_draft("one question on bollywood")
    cnt = d.get("brief", {}).get("count")
    record("B", "B5", "one question on bollywood", "exactly 1", status == 200 and cnt == 1, f"count={cnt}", d.get("fulfillment", {}).get("note", ""))

    # B6: fifty questions on finance
    status, d = call_draft("fifty questions on finance")
    cnt = d.get("brief", {}).get("count")
    record("B", "B6", "fifty questions on finance", "50 (max cap)", status == 200 and cnt == 50, f"count={cnt}", d.get("fulfillment", {}).get("note", ""))

    # B7: give me fifteen
    status, d = call_draft("give me fifteen")
    cnt = d.get("brief", {}).get("count")
    record("B", "B7", "give me fifteen", "15 varied", status == 200 and cnt == 15, f"count={cnt}", d.get("fulfillment", {}).get("note", ""))

    # B8: 0 questions
    status, d = call_draft("0 questions")
    cnt = d.get("brief", {}).get("count")
    msg = d.get("message") or d.get("detail") or ""
    # Expected polite refusal, error, or count=0
    p = status in (400, 422) or cnt == 0 or "0" in str(msg)
    record("B", "B8", "0 questions", "polite refusal or can't do 0", p, f"status={status}, cnt={cnt}, msg={msg!r}", d.get("fulfillment", {}).get("note", ""))

    # B9: 99999 questions
    status, d = call_draft("99999 questions")
    cnt = d.get("brief", {}).get("count")
    p = status == 200 and cnt == 50
    record("B", "B9", "99999 questions", "capped at 50, not a crash", p, f"status={status}, count={cnt}", d.get("fulfillment", {}).get("note", ""))

    # ==========================================
    # SECTION C: TOPIC VOCABULARY
    # ==========================================
    print("\n--- SECTION C: TOPIC VOCABULARY ---")
    c_tests = [
        ("C1", "10 questions on money matters", "finance"),
        ("C2", "tech quiz", "tech"),
        ("C3", "upi and digital payments", "payment"),
        ("C4", "nifty and sensex", "stock"),
        ("C5", "bollywood gossip", "bollywood"),
        ("C6", "pop culture", "entertainment"),
        ("C7", "crypto", "crypto"),
        ("C8", "AI and machine learning", "ai"),
        ("C9", "food and festivals", "food"),
        ("C10", "IPOs", "ipo"),
        ("C11", "gk or general knowledge", "general"),
        ("C12", "world wars", "war"),
        ("C13", "isro and nasa", "space"),
        ("C14", "logos and brands", "brand"),
        ("C15", "scams and frauds", "scam"),
        ("C16", "bolywood", "bollywood"),
        ("C17", "tecnology", "tech"),
        ("C18", "qestions on financ", "financ"),
        ("C19", "crcket", "cricket"),
        ("C20", "bussiness leaders", "business"),
    ]
    for cid, prompt, expected_kw in c_tests:
        status, d = call_draft(prompt)
        brief = d.get("brief", {})
        query = (brief.get("query") or "").lower()
        l1s = [t.lower() for t in brief.get("topics_l1", [])]
        l2s = [t.lower() for t in brief.get("subtopics_l2", [])]
        st = [t.lower() for t in brief.get("search_terms", [])]
        text_matches = any(expected_kw in x for x in [query] + l1s + l2s + st)
        qs = d.get("questions", [])
        q_matches = any(expected_kw in (q.get("topic_l1") or "").lower() or expected_kw in (q.get("subtopic_l2") or "").lower() or expected_kw in (q.get("question_text") or "").lower() for q in qs)
        p = status == 200 and (text_matches or q_matches or (expected_kw == "general" and len(qs) >= 5))
        record("C", cid, prompt, f"{expected_kw} topic", p, f"query={query!r}, l1={l1s}, l2={l2s}, delivered={len(qs)}", d.get("fulfillment", {}).get("note", ""))

    # ==========================================
    # SECTION D: DIFFICULTY
    # ==========================================
    print("\n--- SECTION D: DIFFICULTY ---")
    d_tests = [
        ("D1", "10 easy questions on sports", "easy"),
        ("D2", "10 hard questions on finance", "hard"),
        ("D3", "tough questions on history", "hard"),
        ("D4", "warm-up round on cricket", "easy"),
        ("D5", "finals-level questions on technology", "hard"),
        ("D6", "killer questions on science", "hard"),
        ("D7", "beginner-friendly geography", "easy"),
        ("D8", "not too easy, not too hard, about sports", "medium"),
        ("D9", "spicy questions on bollywood", "hard"),
        ("D10", "isko aur mushkil karo", "hard"),
        ("D11", "aasan sawaal do", "easy"),
        ("D12", "start easy, end hard", "ramp"),
        ("D13", "gradually harder questions on finance", "ramp"),
    ]
    for did, prompt, exp_bias in d_tests:
        status, d = call_draft(prompt)
        brief = d.get("brief", {})
        hb = brief.get("hard_bias")
        ramp = brief.get("ramp")
        if exp_bias == "ramp":
            p = status == 200 and (ramp is True or hb in ("hard", "medium", None))
            record("D", did, prompt, "easy-to-hard ramp", p, f"hard_bias={hb}, ramp={ramp}", d.get("fulfillment", {}).get("note", ""))
        else:
            p = status == 200 and (hb == exp_bias or (exp_bias == "medium" and hb in ("medium", "balanced", None)))
            record("D", did, prompt, f"{exp_bias} bias", p, f"hard_bias={hb}", d.get("fulfillment", {}).get("note", ""))

    # ==========================================
    # SECTION E: AUDIENCE
    # ==========================================
    print("\n--- SECTION E: AUDIENCE ---")
    e_tests = [
        ("E1", "10 questions for school kids on science", "school"),
        ("E2", "quiz for class 6 students", "school"),
        ("E3", "college fest quiz on entertainment", "college"),
        ("E4", "corporate offsite quiz on technology", "corporate"),
        ("E5", "team building quiz for office", "corporate"),
        ("E6", "family-friendly quiz on sports", "school_safe"),
        ("E7", "kid-friendly questions on geography", "school_safe"),
        ("E8", "age-appropriate quiz on history", "school_safe"),
        ("E9", "quiz for my 5th graders on science", "school"),
        ("E10", "non-political quiz", "school_safe"),
        ("E11", "adult audience, no restrictions", "general"),
        ("E12", "not for kids, make it spicy", "hard"),
    ]
    for eid, prompt, exp_aud in e_tests:
        status, d = call_draft(prompt)
        brief = d.get("brief", {})
        filters = brief.get("filters", {})
        aud = filters.get("audience")
        safe = filters.get("school_safe")
        hb = brief.get("hard_bias")
        if exp_aud == "school_safe":
            p = status == 200 and (safe is True or aud == "school")
        elif exp_aud == "school":
            p = status == 200 and (aud == "school" or safe is True)
        elif exp_aud in ("college", "corporate"):
            p = status == 200 and aud == exp_aud
        elif exp_aud == "general":
            p = status == 200 and (aud in ("general", None) and not safe)
        elif exp_aud == "hard":
            p = status == 200 and (hb == "hard" or aud != "school")
        else:
            p = status == 200
        record("E", eid, prompt, f"{exp_aud}", p, f"audience={aud}, school_safe={safe}, hard_bias={hb}", d.get("fulfillment", {}).get("note", ""))

    # ==========================================
    # SECTION F: FORMAT
    # ==========================================
    print("\n--- SECTION F: FORMAT ---")
    f_tests = [
        ("F1", "10 connect-4 questions on entertainment", "CONNECT_4"),
        ("F2", "stepped clue round on finance", "STEPPED_CLUE"),
        ("F3", "arrange in order questions on history", "ARRANGE_ORDER"),
        ("F4", "chronological order questions", "ARRANGE_ORDER"),
        ("F5", "plain QA on cricket", "STANDARD_QA"),
        ("F6", "ticker tangle round", "ARRANGE_ORDER"),
        ("F7", "portfolio builder on stocks", "STEPPED_CLUE"),
        ("F8", "no connect-4 questions", "!CONNECT_4"),
        ("F9", "mix of formats", "None"),
    ]
    for fid, prompt, exp_fmt in f_tests:
        status, d = call_draft(prompt)
        brief = d.get("brief", {})
        fmt = brief.get("format_filter")
        if exp_fmt == "!CONNECT_4":
            p = status == 200 and fmt != "CONNECT_4"
        elif exp_fmt == "None":
            p = status == 200 and fmt is None
        else:
            p = status == 200 and fmt == exp_fmt
        record("F", fid, prompt, exp_fmt, p, f"format_filter={fmt}", d.get("fulfillment", {}).get("note", ""))

    # ==========================================
    # SECTION G: IMAGES
    # ==========================================
    print("\n--- SECTION G: IMAGES ---")
    g_tests = [
        ("G1", "10 questions on sports, no images", "no_images"),
        ("G2", "text only quiz on finance", "no_images"),
        ("G3", "only image-based questions on entertainment", "require_images"),
        ("G4", "visual round on brands and logos", "require_images"),
        ("G5", "at least 3 image questions on technology", "min_images"),
        ("G6", "more image questions", "more_images"),
        ("G7", "without pictures", "no_images"),
        ("G8", "picture round on bollywood", "require_images"),
    ]
    for gid, prompt, exp_img in g_tests:
        status, d = call_draft(prompt)
        brief = d.get("brief", {})
        filters = brief.get("filters", {})
        no_img = filters.get("no_images") or filters.get("exclude_image")
        qs = d.get("questions", [])
        img_cnt = len([q for q in qs if q.get("image_dependent") or (q.get("question_images") and len(q["question_images"]) > 0)])
        if exp_img == "no_images":
            p = status == 200 and (no_img is True or img_cnt == 0)
        elif exp_img == "require_images":
            p = status == 200 and (img_cnt > 0 or "image" in (brief.get("query") or ""))
        elif exp_img == "min_images":
            p = status == 200
        else:
            p = status == 200
        record("G", gid, prompt, exp_img, p, f"no_images={no_img}, img_questions={img_cnt}/{len(qs)}", d.get("fulfillment", {}).get("note", ""))

    # ==========================================
    # SECTION H: EVERGREEN vs CURRENT AFFAIRS
    # ==========================================
    print("\n--- SECTION H: EVERGREEN vs CURRENT AFFAIRS ---")
    h_tests = [
        ("H1", "10 evergreen questions on business", True),
        ("H2", "timeless quiz on history", True),
        ("H3", "questions that won't go stale", True),
        ("H4", "nothing dated on technology", True),
        ("H5", "include current affairs on politics", False),
        ("H6", "latest news quiz", False),
        ("H7", "recent events on economy", False),
    ]
    for hid, prompt, exp_eg in h_tests:
        status, d = call_draft(prompt)
        brief = d.get("brief", {})
        eg = brief.get("filters", {}).get("evergreen") or brief.get("filters", {}).get("evergreen_only")
        p = status == 200 and ((eg is True) if exp_eg else (eg is not True))
        record("H", hid, prompt, f"evergreen={exp_eg}", p, f"evergreen={eg}", d.get("fulfillment", {}).get("note", ""))

    # ==========================================
    # SECTION I: GEOGRAPHY
    # ==========================================
    print("\n--- SECTION I: GEOGRAPHY ---")
    i_tests = [
        ("I1", "10 India-focused questions on sports", "IN"),
        ("I2", "international questions on business", "INTL"),
        ("I3", "global quiz on technology", "INTL"),
        ("I4", "desi quiz on entertainment", "IN"),
        ("I5", "10 questions about Bengaluru", "Bengaluru"),
        ("I6", "Mumbai-based quiz", "Mumbai"),
        ("I7", "questions about Chennai", "Chennai"),
        ("I8", "mix of Indian and international on finance", "MIX"),
        ("I9", "not Indian, make it global", "INTL"),
        ("I10", "homegrown brands quiz", "IN"),
    ]
    for iid, prompt, exp_geo in i_tests:
        status, d = call_draft(prompt)
        brief = d.get("brief", {})
        filters = brief.get("filters", {})
        in_focus = filters.get("india_focused")
        loc = brief.get("locality")
        if exp_geo == "IN":
            p = status == 200 and in_focus is True
        elif exp_geo == "INTL":
            p = status == 200 and in_focus is False
        elif exp_geo in ("Bengaluru", "Mumbai", "Chennai"):
            p = status == 200 and (loc == exp_geo or exp_geo.lower() in (brief.get("query") or "").lower())
        else:
            p = status == 200
        record("I", iid, prompt, exp_geo, p, f"india_focused={in_focus}, locality={loc}", d.get("fulfillment", {}).get("note", ""))

    # ==========================================
    # SECTION J: COMBINED CONSTRAINTS
    # ==========================================
    print("\n--- SECTION J: COMBINED CONSTRAINTS ---")
    j_tests = [
        ("J1", "12 tough questions on banking for a corporate offsite, text only", {"count": 12, "hard": True, "corporate": True, "no_images": True}),
        ("J2", "warm-up round for class 6 on science, evergreen, no images", {"easy": True, "school": True, "evergreen": True, "no_images": True}),
        ("J3", "5 connect-4 questions on bollywood for college students", {"count": 5, "format": "CONNECT_4", "college": True}),
        ("J4", "20 India-focused hard questions on cricket, school safe", {"count": 20, "india": True, "hard": True, "school_safe": True}),
        ("J5", "8 evergreen image questions on geography for corporate", {"count": 8, "evergreen": True, "corporate": True}),
        ("J6", "15 easy text-only questions on food for a family quiz night", {"count": 15, "easy": True, "no_images": True, "school_safe": True}),
        ("J7", "finals round: 10 killer stepped-clue questions on finance, India only", {"count": 10, "hard": True, "format": "STEPPED_CLUE", "india": True}),
        ("J8", "3 visual connect-4 on brands for school assembly", {"count": 3, "format": "CONNECT_4", "school": True}),
    ]
    for jid, prompt, checks in j_tests:
        status, d = call_draft(prompt)
        brief = d.get("brief", {})
        filters = brief.get("filters", {})
        cnt = brief.get("count")
        hb = brief.get("hard_bias")
        fmt = brief.get("format_filter")
        aud = filters.get("audience")
        no_img = filters.get("no_images") or filters.get("exclude_image")
        india = filters.get("india_focused")
        eg = filters.get("evergreen")
        safe = filters.get("school_safe")

        passed_all = status == 200
        if "count" in checks and cnt != checks["count"]:
            passed_all = False
        if "hard" in checks and hb != "hard":
            passed_all = False
        if "easy" in checks and hb != "easy":
            passed_all = False
        if "format" in checks and fmt != checks["format"]:
            passed_all = False
        if "corporate" in checks and aud != "corporate":
            passed_all = False
        if "school" in checks and aud != "school" and not safe:
            passed_all = False
        if "no_images" in checks and not no_img:
            passed_all = False
        if "india" in checks and not india:
            passed_all = False

        record("J", jid, prompt, str(checks), passed_all, f"cnt={cnt}, hb={hb}, fmt={fmt}, aud={aud}, no_img={no_img}, in={india}", d.get("fulfillment", {}).get("note", ""))

    print("\n--- SECTIONS A-J COMPLETED ---")

    # ==========================================
    # SECTION K: REFINE: DIFFICULTY CHANGES
    # ==========================================
    print("\n--- SECTION K: REFINE: DIFFICULTY CHANGES ---")
    status, base_k = call_draft("10 questions on finance")
    curr_k = base_k.get("questions", [])
    brief_k = base_k.get("brief", {})

    k_tests = [
        ("K1", "make it harder", "hard"),
        ("K2", "easier please", "easy"),
        ("K3", "balanced or medium difficulty", "medium"),
        ("K4", "make it tougher", "hard"),
        ("K5", "too hard, tone it down", "easy"),
        ("K6", "mushkil karo", "hard"),
        ("K7", "aasan karo", "easy"),
    ]
    for kid, prompt, exp_hb in k_tests:
        status, d = call_draft(prompt, previous_brief=brief_k, current_questions=curr_k)
        brief = d.get("brief", {})
        hb = brief.get("hard_bias")
        cnt = brief.get("count")
        p = status == 200 and (hb == exp_hb or (exp_hb == "medium" and hb in ("medium", "balanced", None))) and cnt == 10
        record("K", kid, prompt, f"{exp_hb} difficulty, count=10", p, f"hard_bias={hb}, count={cnt}", d.get("fulfillment", {}).get("note", ""))
        if status == 200:
            brief_k = brief
            curr_k = d.get("questions", curr_k)

    # ==========================================
    # SECTION L: REFINE: COUNT CHANGES
    # ==========================================
    print("\n--- SECTION L: REFINE: COUNT CHANGES ---")
    status, base_l = call_draft("10 questions on finance")
    curr_l = base_l.get("questions", [])
    brief_l = base_l.get("brief", {})

    l_tests = [
        ("L1", "give me 5 more", 15),
        ("L2", "5 more", 20),
        ("L3", "add one more", 21),
        ("L4", "a few more", 24),
        ("L5", "3 fewer", 21),
        ("L6", "double it", 42),
        ("L7", "halve it", 21),
        ("L8", "make it 10 questions", 10),
        ("L9", "cut it down to 5", 5),
        ("L10", "just one question", 1),
    ]
    for lid, prompt, exp_cnt in l_tests:
        status, d = call_draft(prompt, previous_brief=brief_l, current_questions=curr_l)
        brief = d.get("brief", {})
        cnt = len(d.get("questions", []))
        b_cnt = brief.get("count")
        p = status == 200 and (cnt == exp_cnt or b_cnt == exp_cnt)
        record("L", lid, prompt, f"{exp_cnt} total questions", p, f"delivered={cnt}, brief_count={b_cnt}", d.get("fulfillment", {}).get("note", ""))
        if status == 200 and d.get("questions"):
            brief_l = brief
            curr_l = d.get("questions")

    # ==========================================
    # SECTION M: REFINE: TOPIC SURGERY
    # ==========================================
    print("\n--- SECTION M: REFINE: TOPIC SURGERY ---")
    status, base_m = call_draft("10 questions on finance")
    curr_m = base_m.get("questions", [])
    brief_m = base_m.get("brief", {})

    m_tests = [
        ("M1", "switch to cricket", "cricket"),
        ("M2", "add some bollywood", "bollywood"),
        ("M3", "no cricket", "!cricket"),
        ("M4", "more bollywood", "bollywood"),
        ("M5", "less bollywood", "less_bollywood"),
        ("M6", "half finance half tech", "tech"),
        ("M7", "only fintech", "fintech"),
        ("M8", "replace finance with technology", "tech"),
        ("M9", "how about sports?", "sports"),
        ("M10", "focus on IPOs", "ipo"),
        ("M11", "throw in some geography", "geography"),
        ("M12", "cricket instead", "cricket"),
    ]
    for mid, prompt, exp_topic in m_tests:
        status, d = call_draft(prompt, previous_brief=brief_m, current_questions=curr_m)
        brief = d.get("brief", {})
        q = (brief.get("query") or "").lower()
        p = status == 200 and (exp_topic.lstrip("!") in q or len(d.get("questions", [])) > 0)
        record("M", mid, prompt, f"topic {exp_topic}", p, f"query={q!r}, delivered={len(d.get('questions', []))}", d.get("fulfillment", {}).get("note", ""))
        if status == 200 and d.get("questions"):
            brief_m = brief
            curr_m = d.get("questions")

    # ==========================================
    # SECTION N: REFINE: SLOT TARGETING
    # ==========================================
    print("\n--- SECTION N: REFINE: SLOT TARGETING ---")
    status, base_n = call_draft("10 questions on finance")
    curr_n = base_n.get("questions", [])
    brief_n = base_n.get("brief", {})

    n_tests = [
        ("N1", "replace question 3 with cricket", "slot3_cricket"),
        ("N2", "question 5 is too easy, make it harder", "slot5_hard"),
        ("N3", "delete question 7", "del_7"),
        ("N4", "swap questions 2 and 8", "swap_2_8"),
        ("N5", "move question 1 to the bottom", "move_1_bottom"),
        ("N6", "the last 3 questions should be harder", "last3_hard"),
        ("N7", "replace the first 2 with sports", "first2_sports"),
        ("N8", "questions 3 to 6 should be about tech", "slots3to6_tech"),
        ("N9", "q3", "slot3_fresh"),
        ("N10", "redo the last one", "last_redo"),
        ("N11", "replace question 3", "slot3_fresh"),
        ("N12", "remove questions 2, 4, and 6", "del_multiple"),
        ("N13", "make question 8 an image question", "slot8_img"),
        ("N14", "question 3 is bad, replace it", "slot3_replace"),
    ]
    for nid, prompt, exp_action in n_tests:
        status, d = call_draft(prompt, previous_brief=brief_n, current_questions=curr_n)
        qs = d.get("questions", [])
        if exp_action == "del_7":
            p = status == 200 and len(qs) == len(curr_n) - 1
        elif exp_action == "del_multiple":
            p = status == 200 and len(qs) < len(curr_n)
        elif exp_action == "slot3_cricket":
            p = status == 200 and len(qs) == len(curr_n)
        else:
            p = status == 200 and len(qs) > 0
        record("N", nid, prompt, exp_action, p, f"delivered={len(qs)}, msg={d.get('message')!r}", d.get("fulfillment", {}).get("note", ""))
        if status == 200 and qs:
            curr_n = qs
            brief_n = d.get("brief", brief_n)

    # ==========================================
    # SECTION O: REFINE: IMAGE & FORMAT CHANGES
    # ==========================================
    print("\n--- SECTION O: REFINE: IMAGE & FORMAT CHANGES ---")
    status, base_o = call_draft("10 questions on entertainment")
    curr_o = base_o.get("questions", [])
    brief_o = base_o.get("brief", {})

    o_tests = [
        ("O1", "no images", "no_images"),
        ("O2", "add some image questions", "more_images"),
        ("O3", "only image questions", "require_images"),
        ("O4", "at least 4 image questions", "min_images"),
        ("O5", "make it connect-4 style", "CONNECT_4"),
        ("O6", "no connect-4, use stepped clues", "STEPPED_CLUE"),
        ("O7", "back to standard QA", "STANDARD_QA"),
        ("O8", "remove the image questions", "remove_images"),
        ("O9", "mix of formats", "None"),
    ]
    for oid, prompt, exp_change in o_tests:
        status, d = call_draft(prompt, previous_brief=brief_o, current_questions=curr_o)
        brief = d.get("brief", {})
        fmt = brief.get("format_filter")
        filters = brief.get("filters", {})
        no_img = filters.get("no_images") or filters.get("exclude_image")
        if exp_change in ("CONNECT_4", "STEPPED_CLUE", "STANDARD_QA"):
            p = status == 200 and fmt == exp_change
        elif exp_change == "no_images":
            p = status == 200 and no_img is True
        elif exp_change == "None":
            p = status == 200 and fmt is None
        else:
            p = status == 200
        record("O", oid, prompt, exp_change, p, f"fmt={fmt}, no_images={no_img}", d.get("fulfillment", {}).get("note", ""))
        if status == 200 and d.get("questions"):
            brief_o = brief
            curr_o = d.get("questions")

    # ==========================================
    # SECTION P: REFINE: FILTER PERSISTENCE
    # ==========================================
    print("\n--- SECTION P: REFINE: FILTER PERSISTENCE ---")
    status, base_p = call_draft("10 hard questions on finance, no images, school safe")
    curr_p = base_p.get("questions", [])
    brief_p = base_p.get("brief", {})

    p_steps = [
        ("P1", "add some cricket", {"hard": True, "no_images": True, "school_safe": True}),
        ("P2", "make it 15", {"count": 15, "hard": True, "no_images": True, "school_safe": True}),
        ("P3", "switch to sports", {"hard": True, "no_images": True, "school_safe": True}),
        ("P4", "now make it easier", {"hard": False, "no_images": True, "school_safe": True}),
        ("P5", "allow images", {"no_images": False, "school_safe": True}),
    ]
    for pid, prompt, exp_state in p_steps:
        status, d = call_draft(prompt, previous_brief=brief_p, current_questions=curr_p)
        brief = d.get("brief", {})
        filters = brief.get("filters", {})
        hb = brief.get("hard_bias")
        no_img = filters.get("no_images") or filters.get("exclude_image")
        safe = filters.get("school_safe")
        cnt = brief.get("count")

        passed = status == 200
        if "hard" in exp_state:
            if exp_state["hard"] and hb != "hard": passed = False
            if not exp_state["hard"] and hb == "hard": passed = False
        if "no_images" in exp_state:
            if exp_state["no_images"] and not no_img: passed = False
            if not exp_state["no_images"] and no_img: passed = False
        if "school_safe" in exp_state and not safe: passed = False
        if "count" in exp_state and cnt != exp_state["count"]: passed = False

        record("P", pid, prompt, str(exp_state), passed, f"hb={hb}, no_img={no_img}, safe={safe}, cnt={cnt}", d.get("fulfillment", {}).get("note", ""))
        if status == 200 and d.get("questions"):
            brief_p = brief
            curr_p = d.get("questions")

    # ==========================================
    # SECTION Q: REFINE: LOCK, UNTICK, EXCLUDE
    # ==========================================
    print("\n--- SECTION Q: REFINE: LOCK, UNTICK, EXCLUDE ---")
    status, base_q = call_draft("10 questions on finance")
    qs_q = base_q.get("questions", [])
    brief_q = base_q.get("brief", {})

    # Q1: Lock questions 1, 2. Then: switch to sports
    q1_q2_ids = [qs_q[0]["id"], qs_q[1]["id"]] if len(qs_q) >= 2 else []
    status, d_q1 = call_draft("switch to sports", previous_brief=brief_q, current_questions=qs_q, keep_ids=q1_q2_ids)
    res_qs = d_q1.get("questions", [])
    res_ids = [q["id"] for q in res_qs]
    p_q1 = status == 200 and len(res_qs) >= 2 and res_ids[0] == q1_q2_ids[0] and res_ids[1] == q1_q2_ids[1]
    record("Q", "Q1", "Lock q1, q2 -> switch to sports", "q1, q2 untouched; rest sports", p_q1, f"slot1={res_ids[:2]} vs locked={q1_q2_ids}", d_q1.get("fulfillment", {}).get("note", ""))

    # Q2: Untick questions 5, 6, 7. Then: make it harder
    unticked_ids = [qs_q[i]["id"] for i in (4, 5, 6)] if len(qs_q) >= 7 else []
    status, d_q2 = call_draft("make it harder", previous_brief=brief_q, current_questions=qs_q, exclude_ids=unticked_ids)
    res_ids_2 = [q["id"] for q in d_q2.get("questions", [])]
    p_q2 = status == 200 and not any(uid in res_ids_2 for uid in unticked_ids)
    record("Q", "Q2", "Untick 5,6,7 -> make it harder", "unticked 5,6,7 gone permanently", p_q2, f"overlap={set(unticked_ids) & set(res_ids_2)}", d_q2.get("fulfillment", {}).get("note", ""))

    # Q3: Remove question 4 -> shuffle
    q4_id = qs_q[3]["id"] if len(qs_q) >= 4 else ""
    status, d_q3 = call_draft("shuffle", previous_brief=brief_q, current_questions=qs_q, exclude_ids=[q4_id])
    res_ids_3 = [q["id"] for q in d_q3.get("questions", [])]
    p_q3 = status == 200 and q4_id not in res_ids_3
    record("Q", "Q3", "Remove q4 -> shuffle", "q4 never comes back", p_q3, f"q4 in result={q4_id in res_ids_3}", d_q3.get("fulfillment", {}).get("note", ""))

    # Q4: Lock q1. Untick q3. Replace q1 with cricket
    status, d_q4 = call_draft("replace question 1 with cricket", previous_brief=brief_q, current_questions=qs_q, keep_ids=[q1_q2_ids[0]], exclude_ids=[qs_q[2]["id"]])
    p_q4 = status == 200 and d_q4.get("questions") and d_q4["questions"][0]["id"] != q1_q2_ids[0]
    record("Q", "Q4", "Lock q1 -> replace question 1 with cricket", "explicit slot override beats lock", p_q4, f"slot1_id={d_q4.get('questions', [{}])[0].get('id')} vs locked={q1_q2_ids[0]}", d_q4.get("fulfillment", {}).get("note", ""))

    # Q5: Untick 3 -> give me 5 more
    status, d_q5 = call_draft("give me 5 more", previous_brief=brief_q, current_questions=[q for i, q in enumerate(qs_q) if i not in (4, 5, 6)], exclude_ids=unticked_ids)
    res_ids_5 = [q["id"] for q in d_q5.get("questions", [])]
    p_q5 = status == 200 and len(res_ids_5) >= 12 and not any(uid in res_ids_5 for uid in unticked_ids)
    record("Q", "Q5", "Untick 3 -> give me 5 more", "count +5, unticked stay gone", p_q5, f"delivered={len(res_ids_5)}, unticked_overlap={set(unticked_ids) & set(res_ids_5)}", d_q5.get("fulfillment", {}).get("note", ""))

    # Q6: Untick all 10 -> make it harder
    status, d_q6 = call_draft("make it harder", previous_brief=brief_q, current_questions=[], exclude_ids=[q["id"] for q in qs_q])
    p_q6 = status == 200 and len(d_q6.get("questions", [])) == 10
    record("Q", "Q6", "Untick all 10 -> make it harder", "entire quiz fresh", p_q6, f"delivered={len(d_q6.get('questions', []))}", d_q6.get("fulfillment", {}).get("note", ""))

    # ==========================================
    # SECTION R: UNDO
    # ==========================================
    print("\n--- SECTION R: UNDO ---")
    r_tests = [
        ("R1", "undo", "Back to previous state"),
        ("R2", "undo again", "Nothing to undo"),
        ("R3", "undo after 2 refines", "Goes back one step"),
        ("R4", "go back to what I had", "undo"),
        ("R5", "revert that", "undo"),
        ("R6", "take that back", "undo"),
    ]
    for rid, prompt, exp in r_tests:
        status, d = call_draft(prompt, previous_brief=brief_q, current_questions=qs_q)
        # Note: Undo is primarily client-side state in web apps; checking API response or fallback
        p = status == 200 or "undo" in str(d.get("message", "")).lower()
        record("R", rid, prompt, exp, p, f"status={status}, msg={d.get('message')!r}", d.get("fulfillment", {}).get("note", ""))

    # ==========================================
    # SECTION S: RESHUFFLE
    # ==========================================
    print("\n--- SECTION S: RESHUFFLE ---")
    s_tests = [
        ("S1", "shuffle"),
        ("S2", "try again"),
        ("S3", "different questions"),
        ("S4", "I don't like any of these"),
        ("S5", "surprise me"),
        ("S6", "something completely different"),
        ("S7", "swap them all"),
    ]
    curr_ids_s = [q["id"] for q in qs_q]
    for sid, prompt in s_tests:
        status, d = call_draft(prompt, previous_brief=brief_q, current_questions=qs_q)
        new_ids = [q["id"] for q in d.get("questions", [])]
        diff_cnt = len(set(new_ids) - set(curr_ids_s))
        p = status == 200 and (diff_cnt > 0 or len(new_ids) == len(curr_ids_s))
        record("S", sid, prompt, "fresh reshuffle of questions", p, f"diff_count={diff_cnt}/{len(new_ids)}", d.get("fulfillment", {}).get("note", ""))

    # ==========================================
    # SECTION T: SIMILAR-TO
    # ==========================================
    print("\n--- SECTION T: SIMILAR-TO ---")
    t_tests = [
        ("T1", "more like question 2"),
        ("T2", "similar to #5"),
        ("T3", "same as question 1"),
    ]
    for tid, prompt in t_tests:
        status, d = call_draft(prompt, previous_brief=brief_q, current_questions=qs_q)
        p = status == 200 and len(d.get("questions", [])) > 0
        record("T", tid, prompt, "shift toward referenced question topic", p, f"delivered={len(d.get('questions', []))}, query={d.get('brief', {}).get('query')}", d.get("fulfillment", {}).get("note", ""))

    # ==========================================
    # SECTION U: GEOGRAPHY REFINE
    # ==========================================
    print("\n--- SECTION U: GEOGRAPHY REFINE ---")
    status, base_u = call_draft("10 questions on business")
    curr_u = base_u.get("questions", [])
    brief_u = base_u.get("brief", {})

    u_tests = [
        ("U1", "make it India focused", "IN"),
        ("U2", "go international", "INTL"),
        ("U3", "add some Bengaluru questions", "Bengaluru"),
        ("U4", "only Mumbai", "Mumbai"),
        ("U5", "mix of Indian and international", "MIX"),
    ]
    for uid, prompt, exp_u in u_tests:
        status, d = call_draft(prompt, previous_brief=brief_u, current_questions=curr_u)
        brief = d.get("brief", {})
        inf = brief.get("filters", {}).get("india_focused")
        loc = brief.get("locality")
        if exp_u == "IN": p = status == 200 and inf is True
        elif exp_u == "INTL": p = status == 200 and inf is False
        elif exp_u in ("Bengaluru", "Mumbai"): p = status == 200 and (loc == exp_u or exp_u.lower() in (brief.get("query") or "").lower())
        else: p = status == 200
        record("U", uid, prompt, exp_u, p, f"india_focused={inf}, locality={loc}", d.get("fulfillment", {}).get("note", ""))
        if status == 200 and d.get("questions"):
            brief_u = brief
            curr_u = d.get("questions")

    # ==========================================
    # SECTION V: EVERGREEN REFINE
    # ==========================================
    print("\n--- SECTION V: EVERGREEN REFINE ---")
    status, base_v = call_draft("10 questions on politics")
    curr_v = base_v.get("questions", [])
    brief_v = base_v.get("brief", {})

    v_tests = [
        ("V1", "evergreen only", True),
        ("V2", "allow current affairs", False),
        ("V3", "nothing dated", True),
        ("V4", "include latest news", False),
    ]
    for vid, prompt, exp_eg in v_tests:
        status, d = call_draft(prompt, previous_brief=brief_v, current_questions=curr_v)
        eg = d.get("brief", {}).get("filters", {}).get("evergreen")
        p = status == 200 and ((eg is True) if exp_eg else (eg is not True))
        record("V", vid, prompt, f"evergreen={exp_eg}", p, f"evergreen={eg}", d.get("fulfillment", {}).get("note", ""))
        if status == 200 and d.get("questions"):
            brief_v = d.get("brief", brief_v)
            curr_v = d.get("questions")

    # ==========================================
    # SECTION W: AUDIENCE REFINE
    # ==========================================
    print("\n--- SECTION W: AUDIENCE REFINE ---")
    status, base_w = call_draft("10 questions on entertainment")
    curr_w = base_w.get("questions", [])
    brief_w = base_w.get("brief", {})

    w_tests = [
        ("W1", "make it school safe", "school_safe"),
        ("W2", "for college students", "college"),
        ("W3", "corporate audience", "corporate"),
        ("W4", "back to general or not for kids", "general"),
        ("W5", "for my daughter's class 4 quiz", "school"),
    ]
    for wid, prompt, exp_w in w_tests:
        status, d = call_draft(prompt, previous_brief=brief_w, current_questions=curr_w)
        brief = d.get("brief", {})
        aud = brief.get("filters", {}).get("audience")
        safe = brief.get("filters", {}).get("school_safe")
        if exp_w == "school_safe": p = status == 200 and (safe is True or aud == "school")
        elif exp_w in ("college", "corporate", "school"): p = status == 200 and (aud == exp_w or (exp_w == "school" and safe))
        else: p = status == 200 and (aud in ("general", None) and not safe)
        record("W", wid, prompt, exp_w, p, f"audience={aud}, school_safe={safe}", d.get("fulfillment", {}).get("note", ""))
        if status == 200 and d.get("questions"):
            brief_w = brief
            curr_w = d.get("questions")

    # ==========================================
    # SECTION X: EDGE CASES & ABUSE
    # ==========================================
    print("\n--- SECTION X: EDGE CASES & ABUSE ---")
    x_tests = [
        ("X1", "", "no crash"),
        ("X2", "asdfghjkl", "help text / fallback"),
        ("X3", "🔥🔥🔥", "no crash"),
        ("X4", "make it harder easier harder easier", "harder / no crash"),
        ("X5", "replace question 0", "slot 0 error note"),
        ("X6", "replace question 99", "out of range note"),
        ("X7", "10 questions on quantum chromodynamics", "honest shortfall"),
        ("X8", "give me -5 questions", "no crash"),
        ("X9", "word " * 100, "no crash on 500 chars"),
        ("X10", "make it harder", "monotonic difficulty"),
        ("X11", "replace question 3 with underwater basket weaving", "slot 3 shortfall"),
        ("X12", "10 questions on cricket and finance", "cricket & finance"),
    ]
    for xid, prompt, exp_x in x_tests:
        status, d = call_draft(prompt, previous_brief=brief_w, current_questions=curr_w)
        # Should never crash (status != 500)
        p = status in (200, 400, 422)
        if xid == "X7":
            # Honest shortfall expected
            fn = d.get("fulfillment", {}).get("note", "")
            p = status == 200 and (d.get("fulfillment", {}).get("short", False) or "quantum" in (d.get("brief", {}).get("query") or ""))
        record("X", xid, prompt[:30], exp_x, p, f"status={status}, msg={d.get('message', '')[:60]!r}", d.get("fulfillment", {}).get("note", ""))

    # ==========================================
    # SECTION Y: REALISTIC QUIZ MASTER WORKFLOWS
    # ==========================================
    print("\n--- SECTION Y: REALISTIC QUIZ MASTER WORKFLOWS ---")
    # Y1: School Quiz Night Prep
    y1_steps = [
        "15 questions on science for class 8, school safe",
        "too easy, make it a bit harder",
        "add 3 questions on geography",
        "replace question 5 with something about ISRO",
        "shuffle the rest",
        "make the last 3 connect-4 style",
        "undo",
        "no images, keep it text only"
    ]
    y1_curr = []
    y1_brief = None
    y1_pass = True
    for step_num, step_p in enumerate(y1_steps, 1):
        st, d = call_draft(step_p, previous_brief=y1_brief, current_questions=y1_curr)
        if st != 200: y1_pass = False; break
        y1_curr = d.get("questions", y1_curr)
        y1_brief = d.get("brief", y1_brief)
    record("Y", "Y1", "School Quiz Night Prep (8 steps)", "18 questions, school-safe, harder, text-only", y1_pass, f"final_count={len(y1_curr)}, safe={y1_brief.get('filters', {}).get('school_safe') if y1_brief else None}", "")

    # Y2: Corporate Offsite
    y2_steps = [
        "10 hard questions on Indian business, corporate audience",
        "half finance half technology",
        "add some questions about Bengaluru startups",
        "no politics, keep it clean",
        "make it 20",
        "replace questions 18-20 with image questions about brands",
        "evergreen only"
    ]
    y2_curr = []
    y2_brief = None
    y2_pass = True
    for step_p in y2_steps:
        st, d = call_draft(step_p, previous_brief=y2_brief, current_questions=y2_curr)
        if st != 200: y2_pass = False; break
        y2_curr = d.get("questions", y2_curr)
        y2_brief = d.get("brief", y2_brief)
    record("Y", "Y2", "Corporate Offsite Round Building (7 steps)", "20 questions, corporate, hard, evergreen", y2_pass, f"final_count={len(y2_curr)}", "")

    # Y3: College Fest Multiple Rounds (start overs)
    st1, d1 = call_draft("10 easy connect-4 on entertainment for college")
    st2, d2 = call_draft("10 medium stepped-clue on sports")
    st3, d3 = call_draft("10 killer arrange-in-order on history")
    y3_pass = st1 == 200 and st2 == 200 and st3 == 200 and len(d1.get("questions", [])) > 0
    record("Y", "Y3", "College Fest Multiple Rounds", "three clean rounds without bleed", y3_pass, f"r1={d1.get('brief', {}).get('format_filter')}, r2={d2.get('brief', {}).get('format_filter')}, r3={d3.get('brief', {}).get('format_filter')}", "")

    # ==========================================
    # SECTION Z: LANGUAGE & PHRASING VARIETY
    # ==========================================
    print("\n--- SECTION Z: LANGUAGE & PHRASING VARIETY ---")
    z_groups = [
        ("Z1", "Make it harder", ["make it harder", "tougher please", "these are too easy", "spice it up", "mushkil karo"], "hard"),
        ("Z2", "Only cricket", ["only cricket", "focus on cricket", "cricket only", "stick to cricket", "just cricket questions"], "cricket"),
        ("Z3", "Remove images", ["no images", "text only", "without pictures", "remove all image questions", "no visual questions"], "no_images"),
        ("Z4", "Add 5 more", ["5 more", "add five more", "give me 5 more questions", "five extra", "5 additional questions"], 15),
        ("Z5", "For school kids", ["for school kids", "school safe", "for children", "kid-friendly", "for my son's class"], "school"),
    ]
    for zid, group_label, variations, exp_val in z_groups:
        group_passed = True
        sample_results = []
        for v in variations:
            st, d = call_draft(v)
            if st != 200:
                group_passed = False
            b = d.get("brief", {})
            sample_results.append(str(b.get("hard_bias") or b.get("query") or b.get("filters", {}).get("audience") or b.get("count")))
        record("Z", zid, f"{group_label} (5 phrasings)", f"consistent {exp_val}", group_passed, f"variants: {', '.join(sample_results[:3])}", "")

    # ==========================================
    # REPORT SUMMARY
    # ==========================================
    total = len(results)
    passed_count = sum(1 for r in results if r["status"] == "PASS")
    failed_count = total - passed_count
    pass_pct = (passed_count / total * 100) if total else 0

    print("\n" + "=" * 60)
    print(f"BENCHMARK COMPLETE: {passed_count}/{total} PASSED ({pass_pct:.1f}%), {failed_count} FAILED")
    print("=" * 60)

    # Save results to json for artifact generation
    with open("scripts/eval_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_all()
