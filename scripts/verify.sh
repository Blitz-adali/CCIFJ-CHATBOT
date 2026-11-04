#!/usr/bin/env bash
# No live model calls or production database changes.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
uv run python -m unittest discover -s tests -p 'test_*.py'
uv run python scripts/evaluate.py --mode corpus
(cd ccifj-frontend && npm run build && npm test)
