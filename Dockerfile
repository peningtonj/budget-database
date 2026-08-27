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
#
# DATA_BUNDLE_VERSION is unused by the command itself — its only job is to
# sit in an ARG that changes, so Docker's layer cache can't serve a stale
# download from a previous build. Without it, re-publishing the release
# with new file contents at the same URL would silently keep whatever was
# fetched the first time this layer was ever built (see auto-deploy.sh,
# which sets this to the release's current updated-at timestamp).
ARG DATA_RELEASE_URL_BASE=https://github.com/peningtonj/budget-database/releases/download/data-latest
ARG DATA_BUNDLE_VERSION=unknown
RUN curl -fL -o programs.db "$DATA_RELEASE_URL_BASE/programs.db" \
    && curl -fL "$DATA_RELEASE_URL_BASE/chroma_measures.tar.gz" | tar xz \
    && python -c "from chromadb.utils import embedding_functions; embedding_functions.DefaultEmbeddingFunction()"

# backend/measures/views.py puts the repo root on sys.path (see its own
# comment) so it can import shared modules that live there alongside it
# rather than inside backend/ — portfolio_aliases.py today, plausibly
# others later. Copy the whole repo (see .dockerignore for what's excluded:
# .git, frontend/, and the data-bundle files fetched separately above),
# not just backend/, so none of those imports go missing on the next one.
COPY . .
RUN cd backend && python manage.py collectstatic --noinput

RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
# Bare `gunicorn` defaults to ONE sync worker -- every request handled
# strictly one at a time. The "By program" summary page's own worst
# case makes this concrete: summarising N selected programs fires 2N
# concurrent requests (fetchMeasuresByProgram + fetchProgramProfile per
# program, see ProgramMeasuresPage.svelte) -- the browser sends them all
# at once, but a single sync worker processes them one after another, so
# the page's total load time becomes the SUM of every request's own
# time instead of the max. Invisible on localhost (sub-millisecond
# round trips hide it), but exactly what made this page "very slow" on
# a real deployment. `--worker-class gthread --threads 4` gives real
# concurrency within one process (SQLite queries and onnxruntime
# inference both release the GIL) without loading the embedding model
# into a second process. `--timeout 120` (gunicorn's own default is
# 30s) gives a legitimately slow cold-model-load request room to finish
# instead of being SIGKILLed mid-request.
#
# This exact combination was reverted once before on suspicion of
# causing the self-hosted Docker deployment to time out across the
# board -- confirmed afterward that that issue was unrelated and has
# since been resolved independently, so reinstating it here.
CMD ["gunicorn", "--chdir", "backend", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--worker-class", "gthread", "--timeout", "120"]
