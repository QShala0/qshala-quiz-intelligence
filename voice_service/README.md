# QShala voice service

Speech-to-text for the microphone buttons, using `faster-whisper` (model `small.en`, int8, CPU). No Gemini and no
third-party speech API. It is a separate service and is not part of the backend image.

- `GET /health`: 200 `{"status":"ok","model_loaded":true}` once the model is loaded, otherwise 503. A warm-up ping
  therefore warms the model, not just the container.
- `POST /transcribe`: multipart `audio` (webm, mp4, ogg or wav from `MediaRecorder`) and an optional `vocabulary`
  string (comma-separated deck and series names, used as the Whisper prompt, about 200 tokens at most).
  Returns `{"text": "...", "duration_sec": n, "took_ms": n}`.
  Limits: over 5 MB is 413, over 90 seconds is 400, an unreadable clip is 400, under 0.3 seconds or silence returns empty text.
  The volume is normalised before transcribing, so a quiet voice still works.
- No auth on purpose (security hardening is postponed).

Settings (environment variables): `VOICE_ALLOWED_ORIGINS` (comma-separated, default is the production site and
`http://localhost:3000`), `WHISPER_MODEL` (default `small.en`), `WHISPER_MODEL_DIR` (set to `/models` in the image),
`WHISPER_CPU_THREADS` (default 2).

## Run the tests

```powershell
pip install -r voice_service/requirements-dev.txt
python -m pytest voice_service/tests -q
```

The model is mocked, so the tests run without downloading anything. The real-model test is skipped unless
`VOICE_TEST_REAL_MODEL=1` is set and `tests/fixtures/hello.wav` (a short spoken clip) exists.

## Build and run locally

The model is downloaded once while the image is built, so the build needs internet and takes a few minutes.

```powershell
docker build -t qshala-voice voice_service
docker run --rm -p 8080:8080 qshala-voice
curl http://localhost:8080/health
curl -F "audio=@clip.webm" -F "vocabulary=Quizzards, Agastya Quiz" http://localhost:8080/transcribe
```

## Cloud Run settings

| Setting | Value |
|---|---|
| Region | `asia-southeast1` (Singapore only) |
| CPU | 2 vCPU |
| Memory | 2 GiB |
| Concurrency | 2 |
| Max instances | 2 |
| Min instances | 0 |
| Request timeout | 60 s |
| Env | `VOICE_ALLOWED_ORIGINS=https://qshala-quiz-intelligence-theta.vercel.app,http://localhost:3000` |

With min 0 the first request after idle starts a container and loads the model, so the frontend pings `/health`
when the Create quiz or Question bank page opens (see `frontend/src/lib/voice/serverTranscribe.ts`).
