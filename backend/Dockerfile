FROM python:3.12-slim

# Don't write .pyc files; print logs straight away instead of buffering them.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first, so Docker can reuse this layer when only code changes.
COPY pyproject.toml ./
COPY app/__init__.py app/__init__.py
RUN pip install .

# Bake the embedding model (~130 MB) into the image, so a cold start on a host
# with a wiped disk doesn't download it again.
ENV EMBEDDING_CACHE_DIR=/app/models
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/models')"

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./

# Run as a normal user, not root.
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

# Hosts like Railway set $PORT; default to 8000 locally.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
