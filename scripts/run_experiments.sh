#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then . .venv/bin/activate; fi
make build
export PYTHONPATH="$PWD/src:${PYTHONPATH:-}"
python -m scaletemp.experiments.cli
