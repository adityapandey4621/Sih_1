# API, document worker, deadline scheduler and migrations all run from this one image (different commands).
# Build from the backend/ directory:  docker build -t sih-api backend
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Tesseract for OCR of scanned pages (English only; add tesseract-ocr-<lang> packages for other languages). curl for the container health check.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng curl \
 && rm -rf /var/lib/apt/lists/*

# An unprivileged user. Private attachments live under /data (a volume), never inside the image.
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /data/storage && chown -R app:app /data

WORKDIR /app
# requirements.lock = the exact versions the test suites ran against (requirements.txt states the minimums).
COPY requirements.lock .
RUN pip install -r requirements.lock

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

USER app
ENV STORAGE_DIR=/data/storage \
    OCR_ENGINE=tesseract \
    ENVIRONMENT=production
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=5 CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# Default: the API. Others (see docker-compose.yml):
#   python -m alembic upgrade head          migrations (run once per release, before the API starts)
#   python -m app.worker                    document processing (OCR, extraction)
#   python -m app.scheduler                 deadlines and alerts
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
