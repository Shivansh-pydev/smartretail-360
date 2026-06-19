# SmartRetail 360 — Command Reference
# Usage: make <target>

.PHONY: help setup db-up db-down ingest test lint format

help:           ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup:          ## Install all dependencies
	pip install -r requirements.txt
	pip install -r requirements-dev.txt

db-up:          ## Start PostgreSQL and pgAdmin in Docker
	docker compose up -d
	@echo "PostgreSQL ready at localhost:5432"
	@echo "pgAdmin ready at http://localhost:5050"

db-down:        ## Stop all Docker containers
	docker compose down

ingest:         ## Run the ETL pipeline (loads raw data into PostgreSQL)
	python -m src.ingestion.load_data

test:           ## Run all tests with coverage report
	pytest tests/ -v --cov=src --cov-report=term-missing

lint:           ## Check code quality (flake8)
	flake8 src/ tests/

format:         ## Auto-format code (black + isort)
	black src/ tests/
	isort src/ tests/

clean-pyc:      ## Remove Python cache files
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete