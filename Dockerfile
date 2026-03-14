# Multi-stage build for card-fraud-mcp-gateway
# Stage 1: Base (with uv installed)
FROM python:3.14-slim AS base
WORKDIR /app

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Stage 2: Dependencies (cached unless pyproject.toml changes)
FROM base AS deps
COPY pyproject.toml ./
RUN uv sync --no-dev --no-editable

# Stage 3: Runtime (smaller final image)
FROM base AS runtime
WORKDIR /app

# Copy installed dependencies from deps stage
COPY --from=deps /app/.venv /app/.venv

# Copy application code
COPY app/ app/
COPY cli/ cli/

# Non-root user for security
RUN useradd --create-home appuser
USER appuser

# Make sure uv can find the installed packages
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

ENTRYPOINT ["uv", "run", "uvicorn", "app.main:app", \
    "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
