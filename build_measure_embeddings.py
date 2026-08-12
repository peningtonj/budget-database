"""
Build a chromadb collection embedding every measure_text write-up, for
topic/semantic search ("child care" surfacing a measure that only ever
says "Early Childhood Education and Care (ECEC)") -- the substring search
in measure_text_search() (backend/measures/views.py) can't bridge that
vocabulary gap; a text embedding can, since the surrounding prose still
reads as "about children/families/education" even when the exact query
term never appears.

Embeds full_measure_text plus every bulleted component (the fuller
picture of what a measure is actually about -- full_measure_text alone
is sometimes just a one-line intro before the real detail lives in the
components, see measure_text_component.build_bp2_db.py) under one
collection, one document per measure_id (measure_text's own primary key,
stable across reruns -- see measure_id.py). Uses chromadb's own default
local embedding function (all-MiniLM-L6-v2 via onnxruntime, run once and
cached under ~/.cache/chroma -- no API key needed) so search works
offline once the model's been downloaded once.

Run via the same venv the Django server uses, since that's what
backend/measures/views.py's topic-search endpoint will read this
collection with:

    backend/.venv/bin/python build_measure_embeddings.py

Safe to rerun: upsert()s by measure_id, so a later re-run after
measure_text is rebuilt (new BP2 edition ingested, a text fix, ...)
just overwrites the changed documents rather than duplicating them.
"""
import os
import sqlite3

import chromadb
from chromadb.utils import embedding_functions

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "programs.db")
CHROMA_PATH = os.path.join(ROOT, "chroma_measures")
COLLECTION_NAME = "measure_text"
BATCH_SIZE = 200


def _fetch_documents(con):
    """One document per measure_text row: measure name + intro/end prose
    + every component's own text, in source order. The name is included
    because it often carries the topic signal as strongly as the prose
    (e.g. "Child Care Safety Net") and costs nothing extra to embed
    alongside it.
    """
    cur = con.cursor()
    cur.execute(
        "SELECT id, measure_id, measure_name, edition, portfolio, full_measure_text "
        "FROM measure_text ORDER BY id"
    )
    measures = cur.fetchall()

    cur.execute(
        "SELECT measure_text_id, text FROM measure_text_component ORDER BY measure_text_id, ordinal"
    )
    components_by_row = {}
    for row_id, text in cur.fetchall():
        components_by_row.setdefault(row_id, []).append(text)

    for row_id, measure_id, name, edition, portfolio, full_text in measures:
        parts = [name, full_text or ""]
        parts.extend(components_by_row.get(row_id, []))
        document = "\n".join(p for p in parts if p)
        yield {
            "id": measure_id,
            "document": document,
            "metadata": {
                "measure_name": name,
                "edition": edition,
                "portfolio": portfolio or "",
            },
        }


def main():
    con = sqlite3.connect(DB_PATH)
    docs = list(_fetch_documents(con))
    con.close()
    print(f"{len(docs)} measure_text rows to embed")

    client = chromadb.PersistentClient(path=CHROMA_PATH)
    ef = embedding_functions.DefaultEmbeddingFunction()
    collection = client.get_or_create_collection(name=COLLECTION_NAME, embedding_function=ef)

    # upsert() alone only adds/updates -- a measure_text row removed
    # since the last run (e.g. a parse_bp2.py fix that stops treating
    # some phantom entry as a measure) would leave its old embedding
    # behind forever. Delete anything in the collection that's no
    # longer in the current source set before upserting the rest.
    current_ids = {d["id"] for d in docs}
    existing_ids = set(collection.get(include=[])["ids"])
    stale_ids = existing_ids - current_ids
    if stale_ids:
        collection.delete(ids=list(stale_ids))
        print(f"deleted {len(stale_ids)} stale documents no longer in measure_text")

    for i in range(0, len(docs), BATCH_SIZE):
        batch = docs[i : i + BATCH_SIZE]
        collection.upsert(
            ids=[d["id"] for d in batch],
            documents=[d["document"] for d in batch],
            metadatas=[d["metadata"] for d in batch],
        )
        print(f"  embedded {min(i + BATCH_SIZE, len(docs))}/{len(docs)}")

    print(f"Collection '{COLLECTION_NAME}' now has {collection.count()} documents at {CHROMA_PATH}")


if __name__ == "__main__":
    main()
