"""The prompt-quiz speed-ups must not change what the engine returns."""
import random
from unittest.mock import MagicMock, patch

from backend.app.services.prompt_quiz import engine
from backend.app.services.prompt_quiz.retrieval import Index, expand, tokenize


def _bank(n=120, seed=3):
    r = random.Random(seed)
    words = "bank loan rbi cricket ipl isro rocket upi wallet diwali mela startup brand logo coin note".split()
    return [{
        "id": f"q{i}", "question_text": f"Which {r.choice(words)} {r.choice(words)} fact {i % 9} concerns {r.choice(words)}?",
        "answer": f"A{i % 30}", "topic_l1": r.choice(["Finance", "Sports"]), "subtopic_l2": r.choice(["Banks", "Cricket"]),
        "keywords": r.sample(words, 3), "difficulty_score": r.random(), "tagged": True, "image_dependent": False,
    } for i in range(n)]


def _reference_guardian(hits):
    """The original set-based rule: drop a hit whose first-14-token set overlaps an earlier kept hit by more than 70%."""
    out, seen = [], []
    for h in hits:
        fp = engine._fp(h["question_text"])
        if fp and any(len(fp & s) / max(len(fp | s), 1) > 0.7 for s in seen):
            continue
        seen.append(fp)
        out.append(h)
    return out


def test_guardian_overlap_rule_matches_set_based_reference():
    brief = engine.Brief(prompt="x", query="x", count=5)
    for seed in range(5):
        hits = [dict(h, answer=f"unique{i}") for i, h in enumerate(_bank(150, seed))]
        assert [h["id"] for h in engine.guardian(hits, brief)] == [h["id"] for h in _reference_guardian(hits)]


def test_heap_top_k_matches_full_sort_including_ties():
    bank = _bank()
    idx = Index(bank)
    full = idx.search("bank loan", {}, top_k=len(bank))
    for k in (1, 7, 40):
        assert [(h["id"], h["score"]) for h in idx.search("bank loan", {}, top_k=k)] == [(h["id"], h["score"]) for h in full[:k]]


def test_expand_cache_returns_independent_counters():
    a = expand(tokenize("fintech payments"))
    a["poison"] = 99
    assert "poison" not in expand(tokenize("fintech payments"))


def test_index_is_built_once_per_bank_cache_load():
    engine.invalidate_bank_cache()
    bank = _bank(30)
    db = MagicMock()
    with patch.object(engine, "_bank_ttl", return_value=60.0), patch.object(engine, "_load_bank_uncached", lambda d, *a, **k: bank):
        a, b = engine._load_bank(db), engine._load_bank(db)
        assert engine._index_for(a) is engine._index_for(b)
        a[0]["question_text"] = "mutated by a caller"            # a caller's copy must not leak into the shared index
        assert engine._index_for(a).q[0]["question_text"] != "mutated by a caller"
        shared = engine._index_for(b)
        engine.invalidate_bank_cache()
        assert engine._index_for(engine._load_bank(db)) is not shared
    engine.invalidate_bank_cache()


def test_untagged_bank_gets_a_fresh_index():
    bank = _bank(10)
    assert engine._index_for(bank) is not engine._index_for(bank)


def test_shared_http_client_is_rebuilt_when_httpx_client_is_patched():
    from backend.app.services.prompt_quiz import _http
    real = _http.get_client()
    assert _http.get_client() is real
    with patch("httpx.Client") as fake_cls:
        assert _http.get_client() is fake_cls.return_value
    import httpx
    assert isinstance(_http.get_client(), httpx.Client)      # real class restored, so a real client again
