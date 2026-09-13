FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Best-effort: pre-build the hybrid retrieval index at image build time so the
# container starts fast. If this fails (e.g. no network during build), the
# app will build the index lazily on startup instead - see app/main.py.
RUN python scripts/build_index.py || true

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
