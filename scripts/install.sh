#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
make install
cat <<MSG
Install complete.
Start web dashboard: ./scripts/start_web.sh
Start CLI experiments: ./scripts/run_experiments.sh
MSG
