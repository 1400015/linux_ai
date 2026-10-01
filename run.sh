#!/bin/bash
set -e
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
if [ -x "$PROJECT_DIR/venv/bin/python" ]; then
    exec "$PROJECT_DIR/venv/bin/python" -m src.app "$@"
fi
exec python3 -m src.app "$@"
