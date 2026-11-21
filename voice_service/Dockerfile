# QShala voice service: speech-to-text with faster-whisper. Separate from the backend image.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WHISPER_MODEL=small.en \
    WHISPER_MODEL_DIR=/models

WORKDIR /app

# PyAV (installed with faster-whisper) carries its own ffmpeg libraries, so no ffmpeg package is needed
# to decode webm, mp4 and ogg from MediaRecorder.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake the model into the image so a cold start never downloads anything.
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('small.en', device='cpu', compute_type='int8', download_root='/models')"

COPY main.py .

ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
