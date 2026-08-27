#!/usr/bin/env bash
#
# setup_env.sh -- create a Python virtual environment that can run
# larpix_gui.py (and run_larpix_scripts.py) plus the pipeline scripts.
#
# Usage:
#   ./setup_env.sh                 # creates ./.venv with python3
#   PYTHON=python3.10 ./setup_env.sh
#   VENV=myenv ./setup_env.sh
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PYTHON="${PYTHON:-python3}"
VENV="${VENV:-.venv}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "ERROR: '$PYTHON' not found. Set PYTHON=<your python> and retry." >&2
  exit 1
fi

echo "==> Creating virtual environment in: $VENV"
"$PYTHON" -m venv "$VENV"

# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "==> Upgrading pip / setuptools / wheel"
python -m pip install --upgrade pip setuptools wheel

if [ -f requirements.txt ]; then
  echo "==> Installing runtime requirements"
  python -m pip install -r requirements.txt
fi

# The larpix / larpixgeometry packages and the lifetime tools are vendored under
# vendor/ and analysis/, so the app already runs without this step. If a local
# SingleCube/repos checkout is present (the DAQ machine), editable-install the
# repos too so `import larpix` resolves to the canonical copy instead.
REPO_ROOT="$HERE"
while [ "$REPO_ROOT" != "/" ] && [ ! -d "$REPO_ROOT/SingleCube/repos" ]; do
  REPO_ROOT="$(dirname "$REPO_ROOT")"
done
if [ -d "$REPO_ROOT/SingleCube/repos" ]; then
  for repo in larpix-control larpix-geometry ndlar_39Ar_reco ; do
    d="$REPO_ROOT/SingleCube/repos/$repo"
    if [ -f "$d/setup.py" ] || [ -f "$d/pyproject.toml" ]; then
      echo "==> Installing local repo (editable): $d"
      python -m pip install -e "$d" \
        || echo "   (warning: editable install of $d failed; continuing)"
    fi
  done
else
  echo "==> SingleCube/repos not found nearby; skipping local repo installs."
fi

cat <<EOF

------------------------------------------------------------------
Done.

Offline analysis (downloaded copy, no DAQ hardware) -- run from this folder;
everything resolves to the bundled vendor/ + analysis/ + data/ copies:

    source $HERE/$VENV/bin/activate
    python $HERE/larpix_gui.py       # web GUI at http://127.0.0.1:8000

Full pipeline on the DAQ machine -- launch from the directory that holds the
pipeline scripts (check_power.py, layout-2.4.0.yaml, larpix-control/scripts/...)
so the preserved relative command paths resolve:

    cd <the larpix-10x10-scripts dir>
    python $HERE/larpix_gui.py

larpix_gui.py finds its own gui_assets/ next to itself either way.
------------------------------------------------------------------
EOF
