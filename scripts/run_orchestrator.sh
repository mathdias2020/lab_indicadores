#!/usr/bin/env bash
set -euo pipefail

cd /srv/labs/projects/lab-b
ORCHESTRATOR_PYTHON="${ORCHESTRATOR_PYTHON:-/srv/labs/projects/lab-b/work/orchestrator-venv/bin/python}"

if [[ ! -x "$ORCHESTRATOR_PYTHON" ]]; then
  echo "orchestrator virtualenv is missing: $ORCHESTRATOR_PYTHON" >&2
  exit 1
fi

export PYTHONPATH="${PWD}/src"
exec "$ORCHESTRATOR_PYTHON" -m lab_indicadores.orchestrator
