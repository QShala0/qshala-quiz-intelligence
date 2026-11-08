"""Regenerates the slide-dict fixtures (*.json) used by the format-aware ingestion tests.

    python backend/tests/fixtures/build_fixtures.py

The decks are synthetic miniatures of the real tournament decks: they keep the real slide numbers of the
cases in the test table (so a case is addressed as "slide 84"), and only the slides a case needs. Slide
dicts follow the schema the Phase-1 parser emits (see SLIDE_SCHEMA below).

SLIDE_SCHEMA (extends the existing parser output)
  slide_number, title, extracted_text, speaker_notes, image_paths, video_paths, all_media_items
  hidden: bool                       p:sld show="0"
  layout_name: str
  shapes: [{id, name, type, text, x, y, w, h,        # x/y/w/h in the shape's OWN space (EMU)
            groups: [group_xform, ...],             # outermost first; empty when top level
            path: [group name, ...],
            media_md5, media_kind,                  # for pictures / videos
            overlay_class}]                         # "tick" | "cross" | "unknown" (List-It overlays, from pixels)
  group_xform = {off:[x,y], ext:[w,h], chOff:[x,y], chExt:[w,h]}
  animations: [{order, cls, preset, node_type, target_id, target_name}]   # MAIN sequence only
"""
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
EMU = 12700  # 1 pt


def md5(label: str) -> str:
    return hashlib.md5(label.encode()).hexdigest()


TIMER_MD5 = md5("timer-video")      # reused on 3+ slides -> noise
MUSIC_MD5 = md5("round-music")
GIF_MD5 = md5("decor-gif")
TICK_MD5, CROSS_MD5 = md5("overlay-a"), md5("overlay-b")   # names/md5 deliberately uninformative


def xf(off, ext, ch_off=None, ch_ext=None):
    return {"off": list(off), "ext": list(ext), "chOff": list(ch_off or off), "chExt": list(ch_ext or ext)}


def text_shape(sid, name, text, x, y, w, h, groups=None, path=None):
    return {"id": sid, "name": name, "type": "TEXT_BOX", "text": text, "x": x, "y": y, "w": w, "h": h,
            "groups": groups or [], "path": path or [], "media_md5": None, "media_kind": None, "overlay_class": None}


def pic_shape(sid, name, label, x, y, w, h, groups=None, path=None, kind="image", overlay=None):
    return {"id": sid, "name": name, "type": "PICTURE" if kind != "video" else "MEDIA", "text": "",
            "x": x, "y": y, "w": w, "h": h, "groups": groups or [], "path": path or [],
            "media_md5": md5(label), "media_kind": kind, "overlay_class": overlay}


def slide(n, texts=(), title=None, hidden=False, layout="Title and Content", shapes=(), anims=(), notes="",
          images=(), videos=(), noise_timer=False):
    shapes = list(shapes)
    if noise_timer:
        shapes.append(pic_shape(900 + n, "Timer 1min", "timer-video", 0, 0, 100, 100, kind="video"))
    # text shapes feed extracted_text exactly like the live parser (document order, de-duplicated)
    chunks = [t for t in texts]
    for s in shapes:
        if s["type"] == "TEXT_BOX" and s["text"] and s["text"] not in chunks:
            chunks.append(s["text"])
    media = [{"filename": f"{lbl}.png", "media_type": "image", "rel_url": f"/media/{lbl}.png", "md5": md5(lbl)}
             for lbl in images]
    media += [{"filename": f"{lbl}.mp4", "media_type": "video", "rel_url": f"/media/{lbl}.mp4", "md5": md5(lbl)}
              for lbl in videos]
    return {
        "slide_number": n,
        "title": title or (chunks[0].split("\n")[0] if chunks else f"Slide {n}"),
        "extracted_text": "\n\n".join(chunks),
        "speaker_notes": notes,
        "image_paths": [m["rel_url"] for m in media if m["media_type"] == "image"],
        "video_paths": [m["rel_url"] for m in media if m["media_type"] == "video"],
        "all_media_items": media,
        "hidden": hidden,
        "layout_name": layout,
        "shapes": shapes,
        "animations": list(anims),
        "has_timer": noise_timer,
        "timer_seconds": 60 if noise_timer else None,
    }


def divider(n, label, hidden=False):
    return slide(n, [label], layout="Section Header", hidden=hidden)


def answer_marker(n, label="ANSWER", hidden=False):
    return slide(n, [label], layout="Title Only", hidden=hidden)


def qa(n_q, question, answer, answer_images=(), images=(), videos=(), hidden=False, title=None, mark="ANSWER"):
    """question slide, ANSWER marker, answer slide at n_q, n_q+1, n_q+2."""
    return [
        slide(n_q, [question], title=title, hidden=hidden, images=images, videos=videos, noise_timer=True),
        answer_marker(n_q + 1, mark, hidden),
        slide(n_q + 2, [answer], hidden=hidden, images=answer_images),
    ]


# --------------------------------------------------------------------------------------- DSU finals (mini)

def listit_slide(n, correct, wrong, team_label=None, hidden=False, option_count=9, swap_names=False):
    """3x3 grid of option text boxes, each with an overlay picture slightly offset from the option centre."""
    texts = list(correct) + list(wrong)
    texts = texts[:option_count]
    shapes, anims = [], []
    ow, oh = 300 * EMU, 60 * EMU
    for i, t in enumerate(texts):
        col, row = i % 3, i // 3
        x, y = (40 + col * 320) * EMU, (120 + row * 90) * EMU
        shapes.append(text_shape(10 + i, f"Option {i + 1}", t, x, y, ow, oh))
    for i, t in enumerate(texts):
        col, row = i % 3, i // 3
        is_right = t in correct
        shapes.append(pic_shape(30 + i, f"Picture {i + 3}", "overlay-a" if is_right != swap_names else "overlay-b",
                                (40 + col * 320 + 240) * EMU, (120 + row * 90 + 5) * EMU, 50 * EMU, 50 * EMU,
                                overlay="tick" if is_right else "cross"))
        anims.append({"order": i + 1, "cls": "entr", "preset": 1, "node_type": "clickEffect",
                      "target_id": 30 + i, "target_name": f"Picture {i + 3}"})
    return slide(n, ["List It!"] + ([team_label] if team_label else []), shapes=shapes, anims=anims,
                 hidden=hidden, noise_timer=True)


def reveal_slide(n, full_image, positions_in_order, hidden=False):
    """Full image + 4 group shapes (cover tile + badge). `positions_in_order` = cover names in reveal order."""
    ladder = ["+20/-15", "+15/-10", "+10/-5", "+5/0"]
    cells = {"top-left": (0, 0), "top-right": (1, 0), "bottom-left": (0, 1), "bottom-right": (1, 1)}
    shapes = [pic_shape(2, "Full Image", full_image, 0, 0, 800 * EMU, 400 * EMU)]
    anims = []
    # group child space is 2x the slide size so the transform matters
    for k, pos in enumerate(positions_in_order, start=1):
        cx, cy = cells[pos]
        gname = f"Group {10 + k}"
        gx = xf((cx * 400 * EMU, cy * 200 * EMU), (400 * EMU, 200 * EMU), (0, 0), (800 * EMU, 400 * EMU))
        shapes.append(pic_shape(20 + k, f"Cover {k}", f"cover-{k}", 0, 0, 800 * EMU, 400 * EMU, [gx], [gname]))
        shapes.append(text_shape(40 + k, f"Badge {k}", f"{k} | {ladder[k - 1]}", 300 * EMU, 150 * EMU, 200 * EMU, 100 * EMU,
                                 [gx], [gname]))
        anims.append({"order": k, "cls": "exit", "preset": 10, "node_type": "clickEffect",
                      "target_id": 10 + k, "target_name": gname})
    # decoy: an exit effect that lives in an interactive sequence must NOT be in `animations`
    return slide(n, ["Image Reveal"], shapes=shapes, anims=anims, hidden=hidden, noise_timer=True)


def connect_slide(n, n_pics, hint=None, video=None, title="Connect", hidden=False, grouped=False, gif=False):
    shapes = []
    for i in range(n_pics):
        g = [xf((i * 100 * EMU, 0), (100 * EMU, 100 * EMU))] if grouped and i % 2 else []
        shapes.append(pic_shape(10 + i, f"Picture {i}", f"connect-{n}-{i}", i * 100 * EMU, 100 * EMU, 90 * EMU, 90 * EMU,
                                g, [f"Group {i}"] if g else []))
    if gif:
        shapes.append(pic_shape(80, "Decor GIF", "decor-gif", 0, 0, 50 * EMU, 50 * EMU))
    if hint:
        shapes.append(text_shape(70, "TextBox 7", hint, 40 * EMU, 400 * EMU, 600 * EMU, 40 * EMU))
    texts = [title]
    return slide(n, texts, shapes=shapes, hidden=hidden, noise_timer=True,
                 images=[f"connect-{n}-{i}" for i in range(n_pics)], videos=[video] if video else [])


def build_dsu():
    s = []
    s += [slide(3, ["Welcome to the finals"], layout="Title Slide"),
          slide(4, ["Rules", "6 Topics & 2 questions in each topic"]),
          slide(5, ["Structure of the quiz"]),
          slide(9, ["Topic board", "SUSTAINABILITY\nENTREPRENEURSHIP\nTECHNOLOGY"], layout="Section Header")]
    # --- Topicator, round 1
    s += [slide(12, ["TOPICATOR\nROUND 1"], layout="Section Header"),
          slide(13, ["6 Topics & 2 questions in each topic", "+20 for direct, +10 for a pass"]),
          divider(14, "Question 1")]
    s += qa(15, "Sustainability - 1\nWhich material takes 450 years to decompose in the sea?", "Plastic Waste",
            title="SUSTAINABILITY - 1")
    s += [divider(22, "Question 12")]
    s += qa(23, "ENTREPRENEURSHIP - 1\nWhich bank's 2008 collapse triggered the crisis?", "Lehman Brothers",
            title="ENTREPRENEURSHIP - 1", hidden=True)
    s += qa(26, "ENTREPRENEURSHIP - 1\nWhich actor founded Ed-a-Mamma?", "Alia Bhatt", title="ENTREPRENEURSHIP - 1")
    # a Topicator-style slide whose title is only "7." and has no topic label
    s += [divider(71, "Question 7")]
    s += qa(72, "7.\nWhich street food is a mashed vegetable curry served with buttered bread?", "Pav Bhaji", title="7.")
    # --- List It, round 2
    s += [slide(80, ["LIST IT\nROUND 2"], layout="Section Header"),
          slide(81, ["EXAMPLE"], layout="Section Header"),
          listit_slide(82, ["Infosys", "TCS", "HDFC Bank", "ONGC", "Wipro", "Maruti"],
                       ["Ford", "Tesla", "Intel"]),
          divider(83, "Team 8"),
          listit_slide(84, ["SBI", "Reliance Industries Ltd.", "Larsen & Toubro Ltd.", "ITC", "Bajaj Auto", "Tata Steel"],
                       ["Nestle", "Bharti Airtel", "Sun Pharma"], team_label="Team 8"),
          divider(92, "Team 9"),
          listit_slide(93, ["Apple", "Microsoft", "Toyota", "Coca-Cola", "Samsung", "IBM"],
                       ["General Motors", "McDonald's", "Nvidia"], team_label="Team 9")]
    # --- Image Reveal, round 3
    s += [slide(108, ["IMAGE REVEAL\nROUND 3", "+20 / -15", "4 clues"], layout="Section Header"),
          slide(109, ["EXAMPLE"], layout="Section Header"),
          slide(110, ["Who is this?"], noise_timer=True), answer_marker(111), slide(112, ["Steve Jobs"]),
          divider(113, "Question 1"),
          reveal_slide(114, "ratan-tata-full", ["bottom-left", "bottom-right", "top-right", "top-left"]),
          answer_marker(115), slide(116, ["Ratan Tata"], images=["ratan-tata-full"])]
    # --- Audience
    s += [slide(101, ["AUDIENCE QUESTIONS"], layout="Section Header")]
    s += qa(102, "Which team won IPL 2008?", "IPL")
    s += [slide(105, ["AUDIENCE QUESTION"], layout="Section Header")]
    s += [slide(129, ["AUDIENCE QUESTIONS"], layout="Section Header")]
    s += qa(130, "Which band played the 2025 Mumbai tour?", "Coldplay")
    # --- Connect, round 4
    s += [slide(133, ["CONNECT\nROUND 4", "+20 for correct, -10 for wrong"], layout="Section Header"),
          slide(135, ["EXAMPLE"], layout="Section Header"),
          connect_slide(136, 4), answer_marker(137), slide(138, ["Fruits"]),
          divider(139, "Question 1"),
          connect_slide(140, 9), answer_marker(141), slide(142, ["Online Streaming Services / OTT"]),
          divider(147, "Question 5"),
          connect_slide(148, 5, hint="Unit of measuring light intensity"), answer_marker(149), slide(150, ["Soap Brands"]),
          divider(155, "Question 6"),
          connect_slide(156, 0, video="clue-video"), answer_marker(157), slide(158, ["MRF"]),
          connect_slide(163, 4, hidden=True), answer_marker(164, hidden=True), slide(165, ["Tetra Pak"], hidden=True)]
    # decorative / rules slides that must never become questions
    s += [slide(170, ["RAPID FIRE\nROUND 5"], layout="Section Header"),
          slide(207, ["Thank you"], layout="Title Slide"),
          slide(208, [""], shapes=[pic_shape(1, "Picture 1", "decor-gif", 0, 0, 10, 10)]),
          slide(209, [""], shapes=[pic_shape(1, "Picture 1", "decor-gif", 0, 0, 10, 10)]),
          slide(210, ["Scoreboard"]), slide(211, ["Sponsors"])]
    # --- Buffer
    s += [slide(223, ["BUFFER"], layout="Section Header")]
    s += qa(224, "Which planet has the most moons?", "Saturn")
    return sorted(s, key=lambda x: x["slide_number"])


# --------------------------------------------------------------------------------------- reserve decks

def stepped_slide(n, clue_no, text, points, q_no=1, hidden=False, images=()):
    label = f"Q{q_no}. Clue {clue_no}" + (f": {points}" if points else "")
    return slide(n, [label, text], hidden=hidden, images=images, noise_timer=True)


def build_three_clues():
    s = [slide(1, ["THREE CLUES\nROUND 3", "+20/-15, +15/-10, +10/-5"], layout="Section Header"),
         divider(2, "Question 1")]
    s += [stepped_slide(3, 1, "Seat of the Sir Thyagaraya hall", "+20/-15", images=["tn1"]),
          stepped_slide(4, 2, "Famous for silk sarees", "+15/-10", images=["tn2"]),
          stepped_slide(5, 3, "Pondy Bazaar is nearby", "+10/-5", images=["tn3"]),
          answer_marker(6), slide(7, ["T. Nagar"])]
    s += [divider(12, "Question 2"),
          stepped_slide(13, 1, "Nut found in kaju katli", "+20/-15", 2),
          stepped_slide(14, 1, "Rich in zinc", "+15/-10", 2),              # duplicated Clue 1
          stepped_slide(15, 3, "Cashew apple is the fruit", "+10/-15", 2),    # typo points
          answer_marker(16), slide(17, ["Cashew"])]
    s += [divider(312, "Question 40", hidden=True),
          stepped_slide(313, 1, "State bank merged 2020", None, 40, hidden=True),
          stepped_slide(314, 2, "Lion logo", None, 40, hidden=True),
          stepped_slide(315, 3, "Headquartered in Delhi", None, 40, hidden=True),
          answer_marker(316, hidden=True), slide(317, ["Punjab National Bank (PNB)"], hidden=True)]
    return sorted(s, key=lambda x: x["slide_number"])


def arrange_group(sid, label, name, pic, gx, gy, value=None):
    g = xf((gx * EMU, gy * EMU), (300 * EMU, 200 * EMU), (0, 0), (300 * EMU, 200 * EMU))
    sh = [text_shape(sid + 1, "Label", str(label), 10 * EMU, 10 * EMU, 40 * EMU, 40 * EMU, [g], [f"Group {sid}"]),
          text_shape(sid + 2, "Name", name, 60 * EMU, 10 * EMU, 200 * EMU, 40 * EMU, [g], [f"Group {sid}"]),
          pic_shape(sid + 3, "Picture", pic, 10 * EMU, 60 * EMU, 280 * EMU, 130 * EMU, [g], [f"Group {sid}"])]
    if value is not None:
        sh.append(text_shape(sid + 4, "Value", value, 60 * EMU, 160 * EMU, 200 * EMU, 30 * EMU, [g], [f"Group {sid}"]))
    return sh


def arrange_pair(n, title, items, values, grouped=True):
    """items: [(label, name)], values: [str] -> question slide n, ANSWER n+1, answer n+2."""
    pos = [(20, 20), (360, 20), (20, 260), (360, 260)]
    q, a = [], []
    for i, ((lab, name), val) in enumerate(zip(items, values)):
        if grouped:
            q += arrange_group(100 + i * 10, lab, name, f"arr-{n}-{lab}", *pos[i])
            a += arrange_group(100 + i * 10, lab, name, f"arr-{n}-{lab}", *pos[i], value=val)
        else:
            q += [text_shape(100 + i, f"TextBox {i}", f"{lab} {name}", pos[i][0] * EMU, pos[i][1] * EMU, 200 * EMU, 40 * EMU)]
            a += [text_shape(100 + i, f"TextBox {i}", f"{lab} {name} {val}", pos[i][0] * EMU, pos[i][1] * EMU, 200 * EMU, 40 * EMU)]
    return [slide(n, [title], shapes=q, noise_timer=True), answer_marker(n + 1),
            slide(n + 2, ["Answer"], shapes=a)]


def build_arrange():
    s = [slide(1, ["TICKER TANGLE\nROUND 2"], layout="Section Header"),
         slide(2, ["4 correct +20, 2 correct +10, 1 correct +5"])]
    s += arrange_pair(3, "Arrange the budgets from highest to lowest",
                      [(1, "Pushpa 2"), (2, "Kantara"), (3, "Kalki 2898 AD"), (4, "RRR")],
                      ["11,301", "3,604", "18,518", "7,609"])
    s += arrange_pair(6, "Arrange the films by year of release, earliest first",
                      [(1, "Sholay"), (2, "Lagaan"), (3, "Mughal-e-Azam"), (4, "DDLJ")],
                      ["1975", "2001", "1960", "1995"])
    s += arrange_pair(132, "Arrange the cities from the strongest to the weakest",
                      [(1, "A"), (2, "B"), (3, "C"), (4, "D")], ["Many", "Few", "Some", "None"])
    s += arrange_pair(141, "Arrange the rivers from longest to shortest",
                      [(1, "Ganga"), (2, "Yamuna"), (3, "Godavari"), (4, "Krishna")],
                      ["2,525 km", "1,376 km", "1,465 km", "1,400 km"], grouped=False)
    s += arrange_pair(183, "Arrange from lowest to highest",
                      [(1, "x"), (2, "y"), (3, "z"), (4, "w")], ["1", "2", "3", "4"], grouped=False)
    return sorted(s, key=lambda x: x["slide_number"])


def mcq_slide(n, stem, options, star=False, note=None):
    shapes = [text_shape(5, "Title", stem, 40 * EMU, 20 * EMU, 700 * EMU, 80 * EMU)]
    for i, o in enumerate(options):
        shapes.append(text_shape(6 + i, f"Option {i}", o, 40 * EMU, (120 + i * 60) * EMU, 400 * EMU, 40 * EMU))
    texts = [stem] + (["14.*"] if star else []) + ([note] if note else [])
    sl = slide(n, texts, shapes=shapes, noise_timer=True)
    sl["metadata_json"] = {"has_star_shape": star}
    return sl


def build_mcq():
    s = [slide(1, ["MCQ\nROUND 4", "4 questions"], layout="Section Header"),
         divider(13, "Question 14"),
         mcq_slide(14, "Which year was the Jallianwala Bagh massacre?", ["1919", "1930", "1942", "1947"], star=True),
         answer_marker(15), slide(16, ["A. 1919"]),
         mcq_slide(17, "Which of these is the year of the Salt March?", ["1024", "1930", "100", "786"]),
         answer_marker(18), slide(19, ["B. 1930"]),
         divider(46, "Question 40"),
         mcq_slide(47, "Which account type has two owners?", ["Joint Accounts", "Salary", "Zero balance", "Demat"],
                   note="[Larger Image on next slide]"),
         slide(48, [""], shapes=[pic_shape(3, "Picture 3", "mcq-48", 0, 0, 800 * EMU, 400 * EMU)], images=["mcq-48"]),
         answer_marker(49), slide(50, ["A. Joint Accounts"])]
    return sorted(s, key=lambda x: x["slide_number"])


def build_hotspot():
    s = [slide(1, ["ROUND 5 - HOTSPOT"], layout="Section Header"),
         slide(2, ["+20 / -15", "8 questions"])]
    s += qa(3, "Which Dickens character is shown?", "Ebenezer Scrooge", images=["hotspot-3"])
    for n in (348, 352, 356):   # number-only picture slides and decorative slides after answers
        s.append(slide(n, [str(n % 10)], shapes=[pic_shape(1, "Picture 1", f"hotspot-{n}", 0, 0, 100, 100)],
                       images=[f"hotspot-{n}"]))
    return sorted(s, key=lambda x: x["slide_number"])


def build_listit_reserve():
    s = [slide(1, ["LIST IT\nROUND 2"], layout="Section Header"),
         listit_slide(3, ["a1", "a2", "a3", "a4", "a5", "a6"], ["b1", "b2", "b3"], hidden=False)]
    s.append(listit_slide(68, ["c1", "c2", "c3", "c4", "c5"], ["d1", "d2", "d3"], option_count=8))
    return s


# --------------------------------------------------------------------------------------- expectations

EXPECTED = {
    "dsu_mini": {
        "cases": [
            {"q": 15, "a": 17, "format": "STANDARD_QA", "slot": "LIVE", "round": "Topicator", "play_mode": "PASS",
             "topic_label": "Sustainability 1", "answer": "Plastic Waste"},
            {"q": 84, "format": "LIST_IT", "slot": "LIVE", "play_mode": "DIRECTED", "team": 8,
             "correct": ["SBI", "Reliance Industries Ltd.", "Larsen & Toubro Ltd.", "ITC", "Bajaj Auto", "Tata Steel"],
             "wrong": ["Nestle", "Bharti Airtel", "Sun Pharma"]},
            {"q": 93, "format": "LIST_IT", "wrong": ["General Motors", "McDonald's", "Nvidia"]},
            {"q": 114, "a": 116, "format": "IMAGE_REVEAL", "play_mode": "BUZZER",
             "cover_order": ["bottom-left", "bottom-right", "top-right", "top-left"],
             "ladder": [[20, -15], [15, -10], [10, -5], [5, 0]], "answer": "Ratan Tata"},
            {"q": 140, "a": 142, "format": "CONNECT", "n_items": 9, "answer": "Online Streaming Services / OTT"},
            {"q": 148, "a": 150, "format": "CONNECT", "n_items": 5, "text_hint": "Unit of measuring light intensity",
             "answer": "Soap Brands"},
            {"q": 156, "a": 158, "format": "CONNECT", "has_video": True, "answer": "MRF"},
            {"q": 23, "a": 25, "format": "STANDARD_QA", "slot": "RESERVE", "topic_label": "Entrepreneurship 1",
             "answer": "Lehman Brothers"},
            {"q": 26, "a": 28, "format": "STANDARD_QA", "slot": "LIVE", "topic_label": "Entrepreneurship 1",
             "answer": "Alia Bhatt", "not_deduped_with": 23},
            {"q": 72, "a": 74, "format": "STANDARD_QA", "needs_review": True, "topic_label": None, "answer": "Pav Bhaji"},
            {"q": 102, "a": 104, "format": "STANDARD_QA", "play_mode": "AUDIENCE", "answer": "IPL"},
            {"q": 130, "a": 132, "format": "STANDARD_QA", "play_mode": "AUDIENCE", "answer": "Coldplay"},
            {"q": 224, "a": 226, "format": "STANDARD_QA", "section": "BUFFER", "topic_label": None, "answer": "Saturn"},
            {"q": 82, "slot": "EXAMPLE"}, {"q": 110, "slot": "EXAMPLE"}, {"q": 136, "slot": "EXAMPLE"},
            {"q": 163, "a": 165, "format": "CONNECT", "slot": "RESERVE", "answer": "Tetra Pak"},
        ],
        "no_question_slides": [3, 4, 5, 9, 83, 105, 170, 207, 208, 209, 210, 211],
    },
    "listit_reserve": {"cases": [{"q": 3, "format": "LIST_IT", "ticks": 6, "crosses": 3},
                                 {"q": 68, "needs_review": True, "options": 8}]},
    "three_clues_reserve": {"cases": [
        {"q": 3, "a": 7, "format": "STEPPED_CLUE", "ladder": [[20, -15], [15, -10], [10, -5]], "scoring_ladder_ok": True,
         "answer": "T. Nagar"},
        {"q": 13, "a": 17, "format": "STEPPED_CLUE", "scoring_ladder_ok": False, "answer": "Cashew"},
        {"q": 313, "a": 317, "format": "STEPPED_CLUE", "slot": "RESERVE", "points_missing": True,
         "answer": "Punjab National Bank (PNB)"}]},
    "arrange_reserve": {"cases": [
        {"q": 3, "a": 5, "format": "ARRANGE_ORDER", "direction": "desc", "correct_order": [3, 1, 4, 2],
         "order_source": "computed"},
        {"q": 6, "a": 8, "format": "ARRANGE_ORDER", "direction": "asc", "correct_order": [3, 1, 4, 2],
         "order_source": "computed"},
        {"q": 132, "a": 134, "format": "ARRANGE_ORDER", "correct_order": [], "order_source": "none",
         "needs_review": True, "editable": True},
        {"q": 141, "needs_review": True}, {"q": 183, "needs_review": True}]},
    "mcq_reserve": {"cases": [
        {"q": 17, "a": 19, "format": "MCQ", "options": ["1024", "1930", "100", "786"], "correct_letter": "B"},
        {"q": 14, "a": 16, "format": "MCQ", "star": True},
        {"q": 47, "a": 50, "format": "MCQ", "joined_slides": [48], "correct_letter": "A", "answer": "Joint Accounts"}]},
    "hotspot_reserve": {"cases": [
        {"q": 3, "a": 5, "format": "STANDARD_QA", "media_kind": "IMAGE", "play_mode": "BUZZER", "answer": "Ebenezer Scrooge"}],
        "no_question_slides": [348, 352, 356]},
}

DECKS = {
    "dsu_mini": build_dsu,
    "listit_reserve": build_listit_reserve,
    "three_clues_reserve": build_three_clues,
    "arrange_reserve": build_arrange,
    "mcq_reserve": build_mcq,
    "hotspot_reserve": build_hotspot,
}


def main():
    for name, fn in DECKS.items():
        slides = fn()
        nums = [s["slide_number"] for s in slides]
        assert len(nums) == len(set(nums)), f"{name}: duplicate slide numbers {sorted(n for n in nums if nums.count(n) > 1)}"
        (OUT / f"{name}.json").write_text(json.dumps(slides, indent=1), encoding="utf-8")
    (OUT / "expected.json").write_text(json.dumps(EXPECTED, indent=1), encoding="utf-8")
    print("wrote", ", ".join(DECKS), "+ expected.json")


if __name__ == "__main__":
    main()
