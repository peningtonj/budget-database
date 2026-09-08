"""Put pipeline/ on sys.path so the tests can import the ingestion modules
(parse_bp2, etc.) directly, the same way the build_* scripts import their
siblings when run from inside pipeline/."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
