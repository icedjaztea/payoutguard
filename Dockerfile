# ---------- stage 1: build the frontend if there is one ----------
FROM node:22-alpine AS web
WORKDIR /src
# Copy the whole (dockerignore-trimmed) context so the build works with or without web/.
COPY . .
RUN if [ -f web/package.json ]; then \
      cd web && (npm ci || npm install) && npm run build; \
    else \
      echo "no web/ to build"; mkdir -p web/dist; \
    fi

# ---------- stage 2: the API, serving web/dist ----------
FROM python:3.12-slim AS app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    AUDIT_MODE=cached
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY data/ ./data/
COPY scripts/ ./scripts/
COPY tools/ ./tools/
COPY README.md LICENSE ./
COPY --from=web /src/web/dist ./web/dist

# Run state and the audit cache are written at runtime.
RUN mkdir -p runs out/cache

EXPOSE 8000
# $PORT is set by the host (Render); the shell form expands it.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
