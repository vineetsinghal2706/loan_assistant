# DemoBank Loan Eligibility Assistant - developer shortcuts
# Educational demonstration. Synthetic data only.

PYTHON ?= python
OFFLINE_ENV = EMBEDDING_BACKEND=hash VECTOR_BACKEND=memory ENABLE_LLM=false

.DEFAULT_GOAL := help
.PHONY: help install install-dev env validate ingest api ui demo test test-offline \
        regression rag lint format eval docker-up docker-down docker-logs clean

help:
	@echo "DemoBank Loan Eligibility Assistant"
	@echo ""
	@echo "  make install       install runtime dependencies"
	@echo "  make install-dev   install runtime + test dependencies"
	@echo "  make env           create .env from .env.example"
	@echo "  make validate      check rule packs against the policy corpus"
	@echo "  make ingest        build the ChromaDB policy index"
	@echo "  make api           run the FastAPI app on :8000"
	@echo "  make ui            run the Streamlit UI on :8501"
	@echo "  make demo          run the full pipeline in the terminal"
	@echo "  make test          run the whole test suite (offline backends)"
	@echo "  make regression    run only the golden policy regression suite"
	@echo "  make eval          run the promptfoo evaluation"
	@echo "  make lint          ruff check"
	@echo "  make docker-up     build and start API + UI + Prometheus + Grafana"
	@echo "  make clean         remove var/ and caches"

install:
	$(PYTHON) -m pip install -r requirements.txt

install-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt

env:
	@test -f .env || (cp .env.example .env && echo "created .env - add ANTHROPIC_API_KEY if you have one")

validate:
	$(PYTHON) scripts/validate_policies.py

ingest:
	$(PYTHON) scripts/ingest_policies.py --rebuild

api:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

ui:
	streamlit run ui/streamlit_app.py

demo:
	$(OFFLINE_ENV) $(PYTHON) scripts/run_demo.py --compare

test:
	$(OFFLINE_ENV) $(PYTHON) -m pytest

test-offline: test

regression:
	$(OFFLINE_ENV) $(PYTHON) -m pytest tests/test_regression_golden.py tests/test_policy_versioning.py -v

rag:
	$(OFFLINE_ENV) $(PYTHON) -m pytest tests/test_rag.py -v

lint:
	ruff check app tests scripts promptfoo ui

format:
	ruff format app tests scripts promptfoo ui

eval:
	npx --yes promptfoo@latest eval -c promptfoo/promptfooconfig.yaml

docker-up:
	docker compose up --build -d
	@echo "API        http://localhost:8000/docs"
	@echo "UI         http://localhost:8501"
	@echo "Prometheus http://localhost:9090"
	@echo "Grafana    http://localhost:3000 (admin / demobank)"

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f api

clean:
	rm -rf var .pytest_cache .ruff_cache .coverage coverage.xml promptfoo/output
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
