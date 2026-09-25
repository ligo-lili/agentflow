# AgentFlow — single-container deployment (Phase 6.3)
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install first so source changes do not invalidate the dependency layer.
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps ./apps
COPY examples/agentflow_tools.py examples/agentflow_tools.py
# The openai-compat extra backs the real provider; the image is useless for
# task runs without it, while scenario runs stay offline.
RUN pip install --no-cache-dir ".[openai-compat]"

# Non-root: the app only writes to /data.
RUN useradd --create-home agentflow \
    && mkdir -p /data \
    && chown agentflow:agentflow /data
USER agentflow

# SQLite lives on a volume so the DB survives container replacement.
ENV AGENTFLOW_DB_PATH=/data/agentflow.db
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
