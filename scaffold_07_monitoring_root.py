#!/usr/bin/env python3
"""Scaffolds monitoring/ (Prometheus + Grafana), docker-compose.yml, and the
remaining top-level project files (Makefile, .env.example, .gitignore, LICENSE)."""
import os


def write_file(relpath, content):
    root = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print("wrote", relpath)


FILES = {}

FILES["monitoring/prometheus/prometheus.yml"] = r'''global:
  scrape_interval: 5s

scrape_configs:
  - job_name: "loan-eligibility-backend"
    metrics_path: /metrics
    static_configs:
      - targets: ["backend:8000"]
'''

FILES["monitoring/grafana/provisioning/datasources/datasource.yml"] = r'''apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: true
'''

FILES["monitoring/grafana/provisioning/dashboards/dashboard.yml"] = r'''apiVersion: 1

providers:
  - name: "Loan Eligibility Assistant"
    orgId: 1
    folder: ""
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    options:
      path: /var/lib/grafana/dashboards
'''

FILES["monitoring/grafana/dashboards/loan_eligibility_dashboard.json"] = r'''{
  "annotations": { "list": [] },
  "editable": true,
  "fiscalYearStartMonth": 0,
  "graphTooltip": 0,
  "id": null,
  "links": [],
  "panels": [
    {
      "type": "timeseries",
      "title": "p95 request latency (s)",
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 0 },
      "id": 1,
      "targets": [
        {
          "expr": "histogram_quantile(0.95, sum(rate(loan_assistant_request_latency_seconds_bucket[5m])) by (le, endpoint))",
          "legendFormat": "{{endpoint}}",
          "refId": "A"
        }
      ]
    },
    {
      "type": "timeseries",
      "title": "Request throughput (req/s)",
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 0 },
      "id": 2,
      "targets": [
        {
          "expr": "sum(rate(loan_assistant_requests_total[5m])) by (endpoint, status)",
          "legendFormat": "{{endpoint}} - {{status}}",
          "refId": "A"
        }
      ]
    },
    {
      "type": "timeseries",
      "title": "Eligibility decisions by outcome",
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 8 },
      "id": 3,
      "targets": [
        {
          "expr": "sum(rate(loan_assistant_eligibility_decisions_total[5m])) by (outcome, rule_version)",
          "legendFormat": "{{outcome}} ({{rule_version}})",
          "refId": "A"
        }
      ]
    },
    {
      "type": "timeseries",
      "title": "Mid-stream errors",
      "gridPos": { "h": 8, "w": 6, "x": 12, "y": 8 },
      "id": 4,
      "targets": [
        {
          "expr": "sum(rate(loan_assistant_stream_errors_total[5m]))",
          "legendFormat": "errors/s",
          "refId": "A"
        }
      ]
    },
    {
      "type": "timeseries",
      "title": "Tokens streamed / s",
      "gridPos": { "h": 8, "w": 6, "x": 18, "y": 8 },
      "id": 5,
      "targets": [
        {
          "expr": "sum(rate(loan_assistant_tokens_streamed_total[5m]))",
          "legendFormat": "tokens/s",
          "refId": "A"
        }
      ]
    },
    {
      "type": "timeseries",
      "title": "Retrieval latency (p95, s)",
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 16 },
      "id": 6,
      "targets": [
        {
          "expr": "histogram_quantile(0.95, sum(rate(loan_assistant_retrieval_latency_seconds_bucket[5m])) by (le))",
          "legendFormat": "retrieval p95",
          "refId": "A"
        }
      ]
    }
  ],
  "refresh": "10s",
  "schemaVersion": 39,
  "tags": ["loan-eligibility-assistant"],
  "templating": { "list": [] },
  "time": { "from": "now-6h", "to": "now" },
  "timepicker": {},
  "timezone": "",
  "title": "Loan Eligibility Assistant",
  "uid": "loan-eligibility-assistant",
  "version": 1
}
'''

FILES["docker-compose.yml"] = r'''services:
  backend:
    build: ./backend
    container_name: loan-eligibility-backend
    ports:
      - "8000:8000"
    env_file:
      - .env
    volumes:
      - ./backend/data:/app/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 10s
      timeout: 5s
      retries: 5

  frontend:
    build: ./frontend
    container_name: loan-eligibility-frontend
    ports:
      - "8501:8501"
    environment:
      - BACKEND_URL=http://backend:8000
    depends_on:
      - backend

  prometheus:
    image: prom/prometheus:v2.53.0
    container_name: loan-eligibility-prometheus
    volumes:
      - ./monitoring/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"
    depends_on:
      - backend

  grafana:
    image: grafana/grafana:11.1.0
    container_name: loan-eligibility-grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_AUTH_ANONYMOUS_ENABLED=true
      - GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer
    volumes:
      - ./monitoring/grafana/provisioning:/etc/grafana/provisioning
      - ./monitoring/grafana/dashboards:/var/lib/grafana/dashboards
    depends_on:
      - prometheus
'''

FILES["Makefile"] = r'''.PHONY: up down build-index test eval promptfoo logs

up:
	docker compose up --build

down:
	docker compose down

build-index:
	cd backend && python scripts/build_index.py

test:
	cd backend && pytest tests -v

eval:
	cd backend && python scripts/run_regression_eval.py --base-url http://localhost:8000

promptfoo:
	cd promptfoo && npx promptfoo@latest eval -c promptfooconfig.yaml

logs:
	docker compose logs -f backend
'''

FILES[".env.example"] = r'''# Copy this file to .env (used by docker-compose) and, for local non-Docker
# runs, also to backend/.env

# "stub" = fully offline, deterministic answers (default; used by tests/CI)
# "anthropic" = real streamed answers from the Anthropic API
LLM_PROVIDER=stub
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-5

CURRENT_RULE_VERSION=v2
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
TOP_K=5
'''

FILES[".gitignore"] = r'''__pycache__/
*.pyc
.venv/
venv/
backend/data/index/
backend/data/audit/
.env
backend/.env
node_modules/
.pytest_cache/
*.log
.DS_Store
'''

FILES["LICENSE"] = r'''MIT License

Copyright (c) 2026 Loan Eligibility Assistant contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to
deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
IN THE SOFTWARE.
'''

if __name__ == "__main__":
    for path, content in FILES.items():
        write_file(path, content)
    print(f"\n{len(FILES)} files written.")
