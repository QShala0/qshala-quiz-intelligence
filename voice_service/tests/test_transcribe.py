"""Voice service tests. The Whisper model is mocked, so they run without faster-whisper installed."""
import io
import os
import sys
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main  # noqa: E402


def make_wav(seconds: float, amplitude: float = 0.5, rate: int = 16000, freq: float = 220.0) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    pcm = (np.sin(2 * np.pi * freq * t) * amplitude * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class FakeSegment:
    def __init__(self, text):
        self.text = text


class FakeModel:
    def __init__(self, text=" Which river flows through Delhi "):
        self.text = text
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append({"audio": audio, **kwargs})
        return [FakeSegment(self.text)], object()


@pytest.fixture()
def model(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(main, "_model", fake)
    return fake


@pytest.fixture()
def client():
    return TestClient(main.app)  # no `with`: the startup model load is skipped, the mock is used


def post(client, data: bytes, vocabulary=None, name="clip.wav", ctype="audio/wav"):
    return client.post("/transcribe", files={"audio": (name, data, ctype)},
                       data={"vocabulary": vocabulary} if vocabulary is not None else {})


def test_response_shape_and_model_settings(client, model):
    r = post(client, make_wav(2.0), vocabulary="Quizzards, Agastya Quiz")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "Which river flows through Delhi"
    assert set(body) == {"text", "duration_sec", "took_ms"}
    assert body["duration_sec"] == pytest.approx(2.0, abs=0.05) and isinstance(body["took_ms"], int)
    call = model.calls[0]
    assert call["beam_size"] == 5 and call["vad_filter"] is True and call["condition_on_previous_text"] is False
    assert call["initial_prompt"] == "Quiz terms: Quizzards, Agastya Quiz."
    assert call["audio"].dtype == np.float32


def test_a_silent_clip_returns_empty_text_without_running_the_model(client, model):
    r = post(client, make_wav(2.0, amplitude=0.0))
    assert r.status_code == 200 and r.json()["text"] == ""
    assert model.calls == []


def test_a_very_short_clip_is_ignored(client, model):
    r = post(client, make_wav(0.2))
    assert r.status_code == 200 and r.json()["text"] == "" and model.calls == []


def test_a_file_over_5_mb_is_rejected_with_413(client, model):
    r = post(client, b"\x00" * (5 * 1024 * 1024 + 10))
    assert r.status_code == 413 and "5 MB" in r.json()["detail"]
    assert model.calls == []


def test_audio_over_90_seconds_is_rejected_with_400(client, model):
    wav = make_wav(95.0, rate=8000)  # 8 kHz keeps the upload under 5 MB
    assert len(wav) < 5 * 1024 * 1024
    r = post(client, wav)
    assert r.status_code == 400 and "90 seconds" in r.json()["detail"]
    assert model.calls == []


def test_an_unreadable_clip_is_a_clear_400(client, model):
    r = post(client, b"RIFF....WAVEjunk" + b"\x01" * 50)
    assert r.status_code == 400 and "Could not read" in r.json()["detail"]


def test_an_empty_upload_is_a_400(client, model):
    assert post(client, b"").status_code == 400


def test_a_quiet_voice_is_brought_up_before_transcribing(client, model):
    r = post(client, make_wav(2.0, amplitude=0.05))
    assert r.status_code == 200
    peak = float(np.max(np.abs(model.calls[0]["audio"])))
    assert 0.85 <= peak <= 1.0


def test_gain_is_capped_so_noise_is_not_blown_up():
    quiet = (0.005 * np.sin(np.arange(16000) / 7)).astype(np.float32)
    peak_in = float(np.max(np.abs(quiet - quiet.mean())))
    out = main.normalise(quiet)
    assert float(np.max(np.abs(out))) == pytest.approx(peak_in * main.MAX_GAIN, rel=0.01)


def test_health_waits_for_the_model(client, monkeypatch):
    monkeypatch.setattr(main, "_model", None)
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["model_loaded"] is False
    monkeypatch.setattr(main, "_model", FakeModel())
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "model_loaded": True}


def test_transcribing_before_the_model_loads_is_a_503(client, monkeypatch):
    monkeypatch.setattr(main, "_model", None)
    assert post(client, make_wav(1.0)).status_code == 503


def test_vocabulary_prompt_is_capped_and_deduplicated():
    assert main.build_prompt(None) is None and main.build_prompt(" , ,") is None
    assert main.build_prompt("A, a,  B ") == "Quiz terms: A, B."
    long = ",".join(f"Series number {i} with a long name" for i in range(200))
    assert len(main.build_prompt(long)) <= main.PROMPT_MAX_CHARS + 20


def test_cors_allows_the_site_and_localhost(client):
    for origin in ("https://qshala-quiz-intelligence-theta.vercel.app", "http://localhost:3000"):
        r = client.options("/transcribe", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
        assert r.headers.get("access-control-allow-origin") == origin
    r = client.options("/transcribe", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers


def test_resampling_keeps_the_duration():
    out = main._resample(np.zeros(44100, dtype=np.float32), 44100)
    assert out.size == 16000


@pytest.mark.skipif(not os.getenv("VOICE_TEST_REAL_MODEL"), reason="set VOICE_TEST_REAL_MODEL=1 with faster-whisper "
                    "installed to run a real transcription (needs the model and a spoken sample, tests/fixtures/hello.wav)")
def test_real_model_with_a_spoken_fixture():
    path = os.path.join(os.path.dirname(__file__), "fixtures", "hello.wav")
    if not os.path.exists(path):
        pytest.skip("no spoken fixture file; none can be generated offline")
    main._model = main.load_model()
    r = TestClient(main.app).post("/transcribe", files={"audio": ("hello.wav", open(path, "rb").read(), "audio/wav")})
    assert r.status_code == 200 and r.json()["text"]
