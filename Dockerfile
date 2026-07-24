# --- stage 1: build the React SPA -------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
# Vite writes the bundle straight into the backend's static dir.
RUN mkdir -p /backend/static && npm run build

# --- stage 2: python runtime -------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ENVIRONMENT=production \
    LOG_FORMAT=json \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
# The built SPA from stage 1 (backend/static is gitignored, so copy it in).
COPY --from=frontend /backend/static ./static

# Writable data dir for SQLite + generated documents + uploads.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data /app/output /app/uploads \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=12s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

CMD ["sh", "-c", "uvicorn app:app --host $HOST --port $PORT"]
