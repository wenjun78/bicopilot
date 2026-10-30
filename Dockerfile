# Stage 1: build the React UI
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci --silent
COPY web ./
RUN npm run build

# Stage 2: API + built UI
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 HOST=0.0.0.0 PORT=8080 BICOPILOT_ROOT=/srv
WORKDIR /srv
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY admin ./admin
COPY semantic ./semantic
COPY data/schema.sql data/generate.py ./data/
COPY eval ./eval
COPY --from=web /web/dist ./web/dist
RUN python data/generate.py && useradd --create-home --uid 10001 copilot && chown -R copilot /srv
USER copilot
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/healthz', timeout=4)"
CMD ["python", "-m", "bicopilot"]
