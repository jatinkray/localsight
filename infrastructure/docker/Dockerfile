# LocalSight API/worker image.
# Lean: Python slim + FFmpeg (for real RTSP decoding) + the Python deps.
# Secrets are injected at runtime via env / Docker secrets — never baked in.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# FFmpeg is required only when processing real RTSP streams (not for the
# reference/demo detector). Kept here so the image is production-capable.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements-prod.txt extends requirements.txt with the PostgreSQL driver
# the compose stack needs (DATABASE_URL=postgresql+psycopg://...).
COPY requirements.txt requirements-prod.txt /app/
RUN pip install -r requirements-prod.txt

COPY . /app

# Run as a non-root user (least privilege for media/AI processing).
RUN useradd -m -u 10001 localsight \
    && mkdir -p /app/data/storage && chown -R localsight:localsight /app/data
USER localsight

# API (uvicorn) or worker (apps.worker.main) selected via COMMAND.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn apps.api.main:app --host 0.0.0.0 --port 8000"]
