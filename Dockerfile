FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN npm install --global pnpm@10.12.1 --registry=${NPM_REGISTRY}
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile --registry=${NPM_REGISTRY}
COPY frontend/ ./
RUN pnpm build

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DUET_DATA_DIR=/app/data \
    HF_HOME=/app/data/models DEER_FLOW_PROJECT_ROOT=/app DEER_FLOW_HOME=/app/data/deerflow-files \
    PYTHONPATH=/app/backend PATH=/app/runtime/.venv/bin:$PATH
ARG DEBIAN_MIRROR=https://deb.debian.org
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 update \
    && apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 install -y --no-install-recommends libgomp1 ca-certificates espeak-ng \
    && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir --index-url https://pypi.tuna.tsinghua.edu.cn/simple uv==0.9.5
WORKDIR /app
COPY vendor/deerflow ./vendor/deerflow
COPY runtime/pyproject.toml runtime/uv.lock ./runtime/
ARG RUNTIME_LAYER_REVISION=2
RUN --mount=type=cache,id=duet-employee-uv-v2,target=/root/.cache/uv UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple uv sync --project runtime --frozen --no-dev
COPY runtime/skills ./runtime/skills
COPY runtime/profiles ./runtime/profiles
COPY runtime/profiles.yaml ./runtime/profiles.yaml
COPY backend/app ./backend/app
COPY scripts/docker-entrypoint.sh ./scripts/docker-entrypoint.sh
COPY --from=frontend /build/frontend/dist ./frontend/dist
RUN python -c "import xxhash; assert callable(xxhash.xxh3_128_hexdigest); from deerflow.client import DeerFlowClient; from app.main import app"
ARG DUET_UID=1000
RUN useradd --create-home --uid ${DUET_UID} duet && mkdir -p /app/data && chown -R duet:duet /app/data
USER duet
EXPOSE 8818
ENTRYPOINT ["sh", "/app/scripts/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8818"]
