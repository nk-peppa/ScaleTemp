#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then . .venv/bin/activate; fi

if [ "${1:-}" = "--hardware" ]; then
  if [ "$#" -lt 3 ]; then
    echo "Usage: $0 --hardware DATA_GPIO SCK_GPIO [GAIN_PULSES]" >&2
    exit 2
  fi
  export SCALETEMP_SENSOR_MODE=sysfs
  export SCALETEMP_DATA_GPIO="$2"
  export SCALETEMP_SCK_GPIO="$3"
  export SCALETEMP_GAIN_PULSES="${4:-1}"
elif [ "${SCALETEMP_SENSOR_MODE:-}" != "sysfs" ]; then
  export SCALETEMP_SENSOR_MODE="${SCALETEMP_SENSOR_MODE:-mock}"
  if [ "$SCALETEMP_SENSOR_MODE" = "mock" ]; then
    cat >&2 <<'MSG'
[ScaleTemp] Using MOCK sensor data.
[ScaleTemp] For real HX711 hardware, run:
  ./scripts/run_experiments.sh --hardware DATA_GPIO SCK_GPIO [GAIN_PULSES]
MSG
  fi
fi

make build
export PYTHONPATH="$PWD/src:${PYTHONPATH:-}"
python -m scaletemp.experiments.cli
