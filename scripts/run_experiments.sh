#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then . .venv/bin/activate; fi

case "${1:-}" in
  --mock)
    export SCALETEMP_SENSOR_MODE=mock
    export SCALETEMP_MOCK_HZ="${2:-80}"
    ;;
  --pins|--wiringpi|--hardware)
    if [ "$#" -lt 3 ]; then
      echo "Usage: $0 --pins DATA_PIN SCK_PIN [GAIN_PULSES]" >&2
      exit 2
    fi
    export SCALETEMP_SENSOR_MODE=wiringpi
    export SCALETEMP_DATA_PIN="$2"
    export SCALETEMP_SCK_PIN="$3"
    export SCALETEMP_GAIN_PULSES="${4:-1}"
    ;;
  --sysfs)
    if [ "$#" -lt 3 ]; then
      echo "Usage: $0 --sysfs DATA_GPIO SCK_GPIO [GAIN_PULSES]" >&2
      exit 2
    fi
    export SCALETEMP_SENSOR_MODE=sysfs
    export SCALETEMP_DATA_GPIO="$2"
    export SCALETEMP_SCK_GPIO="$3"
    export SCALETEMP_GAIN_PULSES="${4:-1}"
    ;;
  "")
    export SCALETEMP_SENSOR_MODE="${SCALETEMP_SENSOR_MODE:-wiringpi}"
    export SCALETEMP_DATA_PIN="${SCALETEMP_DATA_PIN:-5}"
    export SCALETEMP_SCK_PIN="${SCALETEMP_SCK_PIN:-1}"
    export SCALETEMP_GAIN_PULSES="${SCALETEMP_GAIN_PULSES:-1}"
    cat >&2 <<MSG
[ScaleTemp] Using default real HX711 wiringPi pins: DATA/DT=${SCALETEMP_DATA_PIN}, SCK=${SCALETEMP_SCK_PIN}, gain=${SCALETEMP_GAIN_PULSES}.
[ScaleTemp] Use --mock for simulated data, --pins to change wiringPi pins, or --sysfs for Linux GPIO numbers.
MSG
    ;;
  *)
    echo "Usage: $0 [--mock [HZ] | --pins DATA_PIN SCK_PIN [GAIN] | --sysfs DATA_GPIO SCK_GPIO [GAIN]]" >&2
    exit 2
    ;;
esac

make build
export PYTHONPATH="$PWD/src:${PYTHONPATH:-}"
python -m scaletemp.experiments.cli
