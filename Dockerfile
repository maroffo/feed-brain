# ABOUTME: Container for the feed-brain Cloud Run Job (entrypoint `feed-brain`, default command `run`).
# ABOUTME: uv installs the locked dependencies; build on Mac with `docker buildx build --platform linux/amd64`.

FROM python:3.13-slim

RUN pip install --no-cache-dir uv==0.13.5

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first for layer caching
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 feedbrain
USER feedbrain

ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["feed-brain"]
CMD ["run"]
