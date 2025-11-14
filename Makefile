SHELL := powershell.exe
.SHELLFLAGS := -NoProfile -ExecutionPolicy Bypass -Command

.PHONY: help lint guards test check

help:
	@echo "Targets:"
	@echo "  make lint   - Ruff --fix, format, mypy strict, guard checks"
	@echo "  make test   - Run pytest with branch coverage"
	@echo "  make check  - Lint, then run tests"

lint:
	# Guards first to fail fast on drift patterns
	python -m scripts.guard
	# Ensure deps are in sync and dev tools are installed
	poetry lock
	poetry install --with dev
	# Ruff lint + format, then mypy strict
	poetry run ruff check . --fix
	poetry run ruff format .
	poetry run mypy .

guards:
	python -m scripts.guard

test:
	if (Test-Path ".\pyproject.toml") { Write-Host "[test] pytest with coverage (branches, xdist)" -ForegroundColor Cyan; poetry run pytest --cov=data_bank_api --cov=scripts --cov-branch --cov-report=term-missing -v -n auto; } else { Write-Host "[test] Skipped: pyproject missing" -ForegroundColor Yellow; }

check: lint test
