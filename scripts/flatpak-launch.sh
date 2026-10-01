#!/bin/sh
# Launcher used by the Flatpak build (see flatpak/*.json).
#
# The Python dependencies are installed with `pip3 install --prefix=/app`, so
# they live in /app/lib/python3.*/site-packages - a directory that is NOT on
# the default sys.path of the runtime interpreter. run.sh assumes a venv next
# to the sources, which does not exist here, so it falls back to `python3` and
# would fail to import `requests`/`dotenv`/... The version is discovered
# instead of hard-coded.

APP_DIR="$(cd "$(dirname "$0")" && pwd)"

for dir in /app/lib/python3*/site-packages; do
    if [ -d "$dir" ]; then
        PYTHONPATH="$PYTHONPATH:$dir"
    fi
done
export PYTHONPATH

cd "$APP_DIR"
exec python3 -m src.app "$@"
