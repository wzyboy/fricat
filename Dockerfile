FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/app/.venv/bin:${PATH}" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY fricat ./fricat

RUN uv sync --locked --no-dev

VOLUME ["/archive"]
EXPOSE 8000

CMD ["fricat", "web", "--root", "/archive", "--host", "0.0.0.0", "--port", "8000"]
