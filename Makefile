.PHONY: install test coverage lint demo web

install:
	python -m pip install -e ".[dev]"

test:
	pytest -q

coverage:
	pytest -q --cov=packages --cov-report=term-missing

lint:
	ruff check .
	mypy packages

demo:
	python examples/simple_agent.py

web:
	uvicorn apps.api.main:app --reload
