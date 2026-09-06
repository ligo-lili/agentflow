.PHONY: install test lint demo web

install:
	python -m pip install -e ".[dev]"

test:
	pytest -q

lint:
	ruff check .
	mypy packages

demo:
	python examples/simple_agent.py

web:
	uvicorn apps.api.main:app --reload

