FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ASR_MODEL_PATH=/models/vosk-model-small-de-0.15 \
    ASR_WATCHLIST_FILE=/app/data/asr-watchlist.json \
    ASR_ACTIVE_FILE=/app/data/asr-active.json \
    ASR_OUTPUT_PATH=/app/data/asr-words.json

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY asr/requirements.txt ./asr-requirements.txt
RUN pip install --no-cache-dir -r asr-requirements.txt

RUN mkdir -p /models \
    && curl -fsSL -o /tmp/vosk-model.zip \
        https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip \
    && unzip -q /tmp/vosk-model.zip -d /models \
    && rm /tmp/vosk-model.zip

COPY asr ./asr

RUN useradd --uid 1001 --create-home --user-group appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app /models

USER appuser

CMD ["python", "-m", "asr.worker"]
