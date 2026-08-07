"""Isolated Hermes observation runtime for the indicator laboratory.

This runtime publishes a verifiable heartbeat for the Hermes observation
surface. Proposal generation remains a separate, short-lived service without
network, Docker, Supabase, holdout, or execution access.
"""

from __future__ import annotations

import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path


AGENT_ID = os.environ.get("LAB_INDICADORES_HERMES_AGENT_ID", "hermes-indicadores")
PROFILE_ID = "lab-indicadores"
VERSION = "0.3.0-data-understanding"
HEARTBEAT_SECONDS = int(os.environ.get("LAB_INDICADORES_HERMES_HEARTBEAT_SECONDS", "5"))
DATASET_ROOT = Path(
    os.environ.get(
        "LAB_INDICADORES_DATASET_ROOT",
        "/srv/labs/datasets/canonical/normalized_sample_v1",
    )
)
HERMES_ROOT = Path(
    os.environ.get("LAB_INDICADORES_HERMES_ROOT", "/srv/labs/projects/lab-b/hermes")
)
HEARTBEAT_PATH = HERMES_ROOT / "outbox" / "heartbeat.json"

STOP = False


def _stop(_signum: int, _frame: object) -> None:
    global STOP
    STOP = True


def _dataset_observation() -> dict:
    files = sorted(DATASET_ROOT.rglob("*.parquet")) if DATASET_ROOT.exists() else []
    modes = sorted({oct(file.stat().st_mode & 0o777) for file in files})
    return {
        "dataset_root": str(DATASET_ROOT),
        "dataset_files": len(files),
        "dataset_modes": modes,
        "dataset_scope": "canonical/development-only",
        "data_profile_stage": "available-before-proposal",
        "proposal_feedback": "immutable-error-review-versions",
        "holdout_access": False,
    }


def _heartbeat_payload() -> dict:
    return {
        "kind": "hermes_heartbeat_v1",
        "agent_id": AGENT_ID,
        "agent_type": "hermes",
        "status": "observing",
        "mode": "observation",
        "profile_id": PROFILE_ID,
        "version": VERSION,
        "capabilities": ["read_development_data", "data_profile", "proposal_generation", "error_review"],
        "metadata": {
            "project_id": "lab_indicadores",
            "execution_enabled": False,
            "service_role_access": False,
            "docker_socket_access": False,
            "network_access": False,
            **_dataset_observation(),
        },
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
    }


def _write_heartbeat() -> None:
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = HEARTBEAT_PATH.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(_heartbeat_payload(), ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, HEARTBEAT_PATH)


def main() -> int:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while not STOP:
        _write_heartbeat()
        time.sleep(HEARTBEAT_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
