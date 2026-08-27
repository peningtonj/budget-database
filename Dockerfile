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
# Reverted to bare gunicorn defaults (no --worker-class/--threads/
# --timeout) -- the gthread + 120s-timeout combination made the
# self-hosted Docker deployment on constrained laptop hardware time out
# across the board instead of only the one heavy endpoint it targeted:
# a 120s timeout means a genuinely stuck request now hangs 4x longer
# (gunicorn's own default is 30s) before failing, and that's before the
# frontend's own fetchWithRetry compounds it with up to 3 automatic
# retries. program_hierarchy() is cached server-side now regardless
# (see its own docstring), which was the actual fix for the one endpoint
# that was slow -- so this concurrency tuning isn't pulling its weight
# here and isn't worth the regression. Revisit if program_hierarchy's
# cache alone doesn't hold up once further tested.
CMD ["gunicorn", "--chdir", "backend", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
