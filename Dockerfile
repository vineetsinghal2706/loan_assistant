# =====================================================================
# DemoBank Loan Eligibility Assistant - API image
# Educational demonstration only.
# =====================================================================
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/home/demo/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/home/demo/.cache/sentence-transformers

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app
COPY data ./data
COPY scripts ./scripts
COPY pyproject.toml ./

RUN useradd --create-home --uid 10001 demo \
    && mkdir -p /app/var /home/demo/.cache \
    && chown -R demo:demo /app /home/demo
USER demo

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/live || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
