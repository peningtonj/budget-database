FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# programs.db and chroma_measures/ aren't committed to git (see .gitignore's
# own comment) — fetched from the repo's "data-latest" GitHub Release instead,
# same as the render.yaml Blueprint does. Both need to land one directory
# above backend/ (see settings.py's DATABASES and views.py's CHROMA_PATH).
ARG DATA_RELEASE_URL_BASE=https://github.com/peningtonj/budget-database/releases/download/data-latest
RUN curl -fL -o programs.db "$DATA_RELEASE_URL_BASE/programs.db" \
    && curl -fL "$DATA_RELEASE_URL_BASE/chroma_measures.tar.gz" | tar xz \
    && python -c "from chromadb.utils import embedding_functions; embedding_functions.DefaultEmbeddingFunction()"

COPY backend/ backend/
RUN cd backend && python manage.py collectstatic --noinput

RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
CMD ["gunicorn", "--chdir", "backend", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
