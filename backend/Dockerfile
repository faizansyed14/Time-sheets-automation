# Backend image — FastAPI + Celery worker share this image.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# System libs:
#  - WeasyPrint: pango / cairo / gdk-pixbuf / ffi  (+ fonts for crisp render)
#  - LibreOffice (writer+calc): docx/xlsx -> pdf fallback
#  - Tesseract: local OCR for inbox AI check + pipeline scans (OCR_PROVIDER=tesseract)
#  - curl: container healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi8 \
        fonts-dejavu-core fonts-liberation \
        libreoffice-writer libreoffice-calc \
        tesseract-ocr \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Create runtime dirs (also created at runtime, but keeps perms sane)
RUN mkdir -p data storage data/pipeline_raw

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
