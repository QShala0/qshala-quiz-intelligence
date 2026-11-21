"""QShala voice service: POST /transcribe turns a short MediaRecorder clip into text with faster-whisper.

No third-party speech API. The model loads once at startup and is baked into the Docker image.
Security hardening is postponed on purpose (no auth); only size and duration limits are enforced.
"""
from __future__ import annotations

import io
import logging
import os
import threading
import time
import wave
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logger = logging.getLogger("voice_service")

SAMPLE_RATE = 16000
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_AUDIO_SECONDS = 90.0
MIN_AUDIO_SECONDS = 0.3
SILENCE_PEAK = 1e-3          # below this the clip is treated as silence and the model is not run
TARGET_PEAK = 0.9
MAX_GAIN = 30.0
PROMPT_MAX_CHARS = 800       # about 200 tokens of vocabulary

DEFAULT_ORIGINS = "https://qshala-quiz-intelligence-theta.vercel.app,http://localhost:3000"

_model = None
_model_lock = threading.Lock()   # one transcription at a time on the CPU; others wait their turn


def load_model():
    """Load faster-whisper small.en (int8) from the baked-in model directory."""
    from faster_whisper import WhisperModel

    name = os.getenv("WHISPER_MODEL", "small.en")
    root = os.getenv("WHISPER_MODEL_DIR") or None
    started = time.time()
    model = WhisperModel(name, device="cpu", compute_type="int8", download_root=root,
                         cpu_threads=int(os.getenv("WHISPER_CPU_THREADS", "2")))
    logger.info("model %s loaded in %.1fs", name, time.time() - started)
    return model


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _model
    if _model is None:
        _model = load_model()
    yield


app = FastAPI(title="QShala voice service", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("VOICE_ALLOWED_ORIGINS", DEFAULT_ORIGINS).split(",") if o.strip()],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- audio helpers

def _resample(samples: np.ndarray, src_rate: int) -> np.ndarray:
    if src_rate == SAMPLE_RATE or samples.size == 0:
        return samples.astype(np.float32, copy=False)
    n_out = max(1, int(round(samples.size * SAMPLE_RATE / src_rate)))
    x_old = np.linspace(0.0, 1.0, num=samples.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float32)


def _decode_wav(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data), "rb") as w:
        channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(frames)
    if width != 2:
        raise ValueError("only 16-bit WAV is supported")
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        pcm = pcm[: (pcm.size // channels) * channels].reshape(-1, channels).mean(axis=1)
    return _resample(pcm, rate)


def decode_audio(data: bytes) -> np.ndarray:
    """Any clip MediaRecorder produces (webm, mp4, ogg) or a plain WAV -> float32 mono at 16 kHz."""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return _decode_wav(data)
    from faster_whisper.audio import decode_audio as fw_decode  # uses PyAV with its bundled ffmpeg libraries

    return fw_decode(io.BytesIO(data), sampling_rate=SAMPLE_RATE)


def normalise(audio: np.ndarray) -> np.ndarray:
    """Bring a quiet voice up to a usable level (peak normalisation, gain capped so noise is not blown up)."""
    audio = audio.astype(np.float32, copy=False)
    if audio.size == 0:
        return audio
    audio = audio - float(np.mean(audio))  # remove DC offset
    peak = float(np.max(np.abs(audio)))
    if peak < SILENCE_PEAK:
        return audio
    gain = min(TARGET_PEAK / peak, MAX_GAIN)
    return np.clip(audio * gain, -1.0, 1.0)


def build_prompt(vocabulary: Optional[str]) -> Optional[str]:
    """Comma-separated deck and series names -> a short initial prompt (about 200 tokens at most)."""
    terms, seen, size = [], set(), 0
    for raw in (vocabulary or "").split(","):
        term = " ".join(raw.split())
        if not term or term.lower() in seen:
            continue
        if size + len(term) + 2 > PROMPT_MAX_CHARS:
            break
        seen.add(term.lower())
        terms.append(term)
        size += len(term) + 2
    return ("Quiz terms: " + ", ".join(terms) + ".") if terms else None


def run_model(audio: np.ndarray, prompt: Optional[str]) -> str:
    with _model_lock:
        segments, _info = _model.transcribe(
            audio,
            language="en",
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
            initial_prompt=prompt,
        )
        return " ".join(s.text.strip() for s in segments).strip()


# ---------------------------------------------------------------- routes

@app.get("/health")
def health():
    """200 only once the model is loaded, so a warm-up ping warms the model and not just the container."""
    if _model is None:
        return JSONResponse({"status": "loading", "model_loaded": False}, status_code=503)
    return {"status": "ok", "model_loaded": True}


@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...), vocabulary: Optional[str] = Form(None)):
    started = time.time()
    data = await audio.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Audio file is too large (limit 5 MB)")
    if not data:
        raise HTTPException(status_code=400, detail="Audio file is empty")
    try:
        samples = decode_audio(data)
    except Exception as exc:  # noqa: BLE001 - any decoder failure means the clip is unreadable
        logger.warning("could not decode audio: %s", exc)
        raise HTTPException(status_code=400, detail="Could not read the audio clip") from exc

    duration = float(samples.size) / SAMPLE_RATE
    if duration > MAX_AUDIO_SECONDS:
        raise HTTPException(status_code=400, detail=f"Audio is too long (limit {int(MAX_AUDIO_SECONDS)} seconds)")

    def done(text: str):
        return {"text": text, "duration_sec": round(duration, 2), "took_ms": int((time.time() - started) * 1000)}

    if duration < MIN_AUDIO_SECONDS:
        return done("")
    samples = normalise(samples)
    if float(np.max(np.abs(samples))) < SILENCE_PEAK:
        return done("")
    if _model is None:
        raise HTTPException(status_code=503, detail="Voice model is still loading")
    # Run the model in a worker thread so the event loop stays free for /health while a clip is transcribed.
    import anyio

    text = await anyio.to_thread.run_sync(run_model, samples, build_prompt(vocabulary))
    return done(text)
