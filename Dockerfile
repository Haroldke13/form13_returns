# Production Dockerfile for the Flask app
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps for OCR, PDF processing, and building wheels
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    build-essential \
    ghostscript \
    iproute2 \
    libgl1 \
    libglib2.0-0 \
    pngquant \
    poppler-utils \
    postgresql-client \
    qpdf \
    tesseract-ocr \
    unpaper \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .
RUN chmod +x /app/docker-entrypoint.sh

# Expose LAN-facing gunicorn port
EXPOSE 8000

# Default command: expanded by docker-entrypoint.sh from runtime env values.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn"]
