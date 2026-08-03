FROM python:3.12-slim AS runtime

ARG PIP_INDEX_URL=https://pypi.org/simple/
ARG PIP_TRUSTED_HOST=
ARG UV_INDEX_URL=https://pypi.org/simple/
ARG UV_VERSION=0.11.19
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    UV_INDEX_URL=${UV_INDEX_URL}
RUN groupadd --system --gid 10001 collector && useradd --system --uid 10001 --gid collector --home /app collector
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir "uv==${UV_VERSION}" && uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
USER collector
EXPOSE 8090
ENTRYPOINT ["/app/.venv/bin/opencitadel-ops-collector"]
