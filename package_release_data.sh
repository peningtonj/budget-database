#!/bin/bash
# Packages programs.db + chroma_measures/ for upload to a GitHub Release,
# the data source render.yaml's own buildCommand fetches from at deploy
# time (see .gitignore's own comment for why these aren't committed to
# git directly). Run this locally whenever either changes -- a new
# edition ingested, a parsing fix rebuilt, a fresh topic-search index.
#
# ORDERING MATTERS: always finish the `gh release upload` step (and the
# verify curl below) *before* `git push`-ing any related code change.
# Render's own auto-deploy fires the instant a push lands on `main`, and
# its build immediately curls this same release asset -- if that push
# lands while `gh release upload --clobber` is still mid-swap on
# GitHub's side, the build can curl a torn, incomplete file and fail
# with "gzip: stdin: not in gzip format" (hit exactly this once). Data
# first, verified, then code -- never the other way around.
#
# This script only packages; it deliberately does NOT push anything to
# GitHub itself. Review the printed command and run it yourself once
# `gh` is authenticated (`gh auth login`) -- see also render.yaml's own
# header for the one-time DATA_RELEASE_URL_BASE setup this feeds into.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f programs.db ]; then
  echo "programs.db not found -- run the build_*.py pipeline first (see KNOWN_GAPS.md)." >&2
  exit 1
fi
if [ ! -d chroma_measures ]; then
  echo "chroma_measures/ not found -- run build_measure_embeddings.py first." >&2
  exit 1
fi

echo "Packaging chroma_measures/ -> chroma_measures.tar.gz ..."
tar czf chroma_measures.tar.gz chroma_measures/

du -h programs.db chroma_measures.tar.gz

echo
echo "Review, then run one of:"
echo
echo "  # First time (creates the release + tag):"
echo "  gh release create data-latest programs.db chroma_measures.tar.gz \\"
echo "    --title \"Data bundle\" --notes \"programs.db + chroma_measures, updated \$(date +%Y-%m-%d)\""
echo
echo "  # Subsequent updates (same tag, overwrites the assets in place --"
echo "  # DATA_RELEASE_URL_BASE in Render never needs to change):"
echo "  gh release upload data-latest programs.db chroma_measures.tar.gz --clobber"
echo
echo "  # THEN verify the upload actually landed before pushing any code"
echo "  # (confirms real gzip data, not a redirect/HTML error page):"
echo "  curl -sL https://github.com/peningtonj/budget-database/releases/download/data-latest/chroma_measures.tar.gz | gzip -t && echo OK"
echo
echo "  # Only once that prints OK: git push"
