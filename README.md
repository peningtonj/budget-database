# budget-database

A searchable database and web app for Australian federal budget data, built
from the **Portfolio Budget Statements (PBS/PAES)** and **Budget Paper No. 2
(BP2)** across every Budget and MYEFO edition from 2014-15 to 2026-27.

It answers questions the published documents make hard to ask:

- **Program spending over time** — Portfolio → Agency → Outcome → Program,
  with the mid-year *estimated actual* series pulled out as a first-class
  filter (`program_expenses`).
- **What a policy measure did** — its combined financial profile per agency
  and the specific programs it touched, reconstructed by taking the union of
  every agency's own Table 1.2 slice (`measure_impacts`, `measure_programs`).
- **The narrative behind a measure** — BP2's full write-up text, bullet
  components, headline financials and cross-references (`measure_text*`).
- **Topic / semantic search** over measure text, so "child care" finds a
  measure that only ever says "Early Childhood Education and Care"
  (`chroma_measures/`, a local Chroma embedding index).

## Repository layout

```
pipeline/                The data ingestion pipeline (run from the repo root)
  parse_pbs.py             PBS Outcome-table parser        -> used by build_db
  parse_measures.py        Table 1.2 measures parser       -> used by build_measures_db
  parse_bp2.py             Budget Paper No. 2 PDF parser   -> used by build_bp2_db
  build_db.py              -> program_expenses
  build_measures_db.py     -> measure_impacts, measure_programs
  build_bp2_db.py          -> measure_text (+ components, financials, related)
  build_agency_aliases.py  -> agency_aliases (formal <-> short agency names)
  build_measure_embeddings.py  -> chroma_measures/ vector index
  measure_id.py            Stable shareable id for a (measure_name, edition) pair
  portfolio_aliases.py     Portfolio-name canonicalisation (also used by the backend)
  export_program_audit.py  Ad-hoc Excel export for manual data-quality review
  patches/                 Per-source-file data-quality fixes applied by build_db

backend/                 Django + DRF read-only JSON API over programs.db
frontend/                Svelte + Vite single-page app that consumes the API
tests/                   pytest suite (test_measures.pdf is a fixture)
docs/                    KNOWN_GAPS.md (coverage limits — read this), TODO.md

data/                    Raw source PDFs/XLSX (~833 MB, git-ignored, local only)
programs.db              SQLite build artifact (git-ignored)
chroma_measures/         Vector-search index build artifact (git-ignored)

render.yaml / Dockerfile     Deployment (Render Blueprint / container)
package_release_data.sh      Packages the data bundle for a GitHub Release
```

`programs.db`, `chroma_measures/` and `data/` are deliberately **not**
committed — see the comments in `.gitignore`. Deploys fetch a packaged copy
of the two build artifacts from a GitHub Release rather than versioning a
large binary in git history. The backend reads both from the repo root.

## Running locally

You need the two build artifacts present at the repo root first: either run
the pipeline below, or download `programs.db` + `chroma_measures.tar.gz` from
the `data-latest` GitHub Release and unpack the tarball here.

### Backend (Django API)

```sh
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py runserver
```

Serves on `http://127.0.0.1:8000`. It reads `../programs.db` and
`../chroma_measures/` directly and needs **no environment variables** for
local dev (`settings.py` falls back to dev defaults for everything). API
routes live under `/api/measures/` — see `backend/measures/urls.py`.

### Frontend (Svelte SPA)

```sh
cd frontend
npm install
npm run dev
```

Serves on `http://127.0.0.1:5173` and calls the backend at
`http://127.0.0.1:8000/api` in dev (CORS for `:5173` is allowed by default).

## Rebuilding the data

Run from the **repo root** with `data/` populated. Order matters — later
steps read tables the earlier ones create:

```sh
python3 pipeline/build_db.py               # program_expenses
python3 pipeline/build_measures_db.py      # measure_impacts, measure_programs
python3 pipeline/build_bp2_db.py           # measure_text*
python3 pipeline/build_agency_aliases.py   # agency_aliases
backend/.venv/bin/python pipeline/build_measure_embeddings.py   # chroma_measures/
```

`build_measure_embeddings.py` uses the backend virtualenv because the API
reads the index with the same `chromadb` version. The embedding model
(`all-MiniLM-L6-v2`, via onnxruntime) downloads once and caches under
`~/.cache/chroma` — no API key.

See `docs/KNOWN_GAPS.md` for what the built data does and doesn't cover
(Defence's core programs are missing, agency names drift across years, etc.).

## Tests

```sh
python3 -m pytest tests/     # parse_bp2 extraction, against tests/test_measures.pdf
```

## Deployment

Two supported paths, both fetching the data bundle from a GitHub Release at
build time:

- **Render** — `render.yaml` is a Blueprint that deploys the API and the
  static frontend together. See its header comment for the one-time env-var
  setup.
- **Docker** — `Dockerfile` builds the API image (`frontend/`, `tests/`,
  `docs/` excluded via `.dockerignore`).

When `programs.db` or `chroma_measures/` change, run
`./package_release_data.sh` and follow its printed instructions to upload the
new bundle **before** pushing the code change that depends on it.
