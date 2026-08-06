"""Host-side control-plane runner for the first indicator-lab command.

This process is deliberately not a Docker service. It may invoke the local
Docker CLI, while the worker container remains isolated and never receives the
Docker socket or a Supabase credential.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


PROJECT_NAME = "lab_indicadores"
ALLOWED_MANIFEST = "indicator-lab-smoke-v1"
WORKER_ID = os.environ.get("LAB_INDICADORES_WORKER_ID", "lab-indicadores-worker")
LAB_ROOT = Path(os.environ.get("LAB_INDICADORES_ROOT", "/srv/labs/projects/lab-b"))
POLL_SECONDS = float(os.environ.get("LAB_INDICADORES_POLL_SECONDS", "2"))
JOB_TIMEOUT_SECONDS = int(
    os.environ.get("LAB_INDICADORES_JOB_TIMEOUT_SECONDS", "900")
)
REPORT_PATH = LAB_ROOT / "runs" / "indicator-lab-preflight-v1" / "preflight-report.json"
LOG_ROOT = LAB_ROOT / "logs" / "orchestrator"


def _database_url() -> str:
    value = os.environ.get("LAB_INDICADORES_DATABASE_URL")
    if not value:
        raise RuntimeError("LAB_INDICADORES_DATABASE_URL is required")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _event(conn: psycopg.Connection, run_id: str, event_type: str, message: str, payload: dict) -> None:
    conn.execute(
        """
        insert into lab_indicadores.events
          (run_id, event_type, message, payload)
        values (%s, %s, %s, %s::jsonb)
        """,
        (run_id, event_type, message, json.dumps(payload, sort_keys=True)),
    )
    conn.commit()


def _claim(conn: psycopg.Connection) -> dict | None:
    row = conn.execute(
        "select * from lab_indicadores.claim_next_command(%s)",
        (WORKER_ID,),
    ).fetchone()
    conn.commit()
    return row


def _register_worker(conn: psycopg.Connection, status: str, metadata: dict | None = None) -> None:
    conn.execute(
        """
        insert into lab_indicadores.workers
          (worker_id, status, capabilities, version, last_heartbeat_at, metadata)
        values (%s, %s, %s::jsonb, %s, now(), %s::jsonb)
        on conflict (worker_id) do update set
          status = excluded.status,
          capabilities = excluded.capabilities,
          version = excluded.version,
          last_heartbeat_at = excluded.last_heartbeat_at,
          metadata = excluded.metadata
        """,
        (
            WORKER_ID,
            status,
            json.dumps(["preflight"], sort_keys=True),
            "control-plane-v1",
            json.dumps(metadata or {}, sort_keys=True),
        ),
    )
    conn.commit()


def _start_run(conn: psycopg.Connection, run_id: str) -> None:
    conn.execute(
        """
        update lab_indicadores.runs
        set status = 'running',
            started_at = coalesce(started_at, now()),
            heartbeat_at = now()
        where id = %s
        """,
        (run_id,),
    )
    conn.commit()
    _event(conn, run_id, "run_started", "Preflight worker started", {"worker_id": WORKER_ID})


def _succeed_run(conn: psycopg.Connection, run_id: str, command_id: str, result: dict) -> None:
    report = Path(result["report"])
    if report != REPORT_PATH:
        raise RuntimeError(f"unexpected report path: {report}")
    if not report.is_file():
        raise FileNotFoundError(report)

    payload = json.loads(report.read_text(encoding="utf-8"))
    if payload.get("status") != "succeeded" or payload.get("holdout_accessed") is not False:
        raise RuntimeError("worker report failed the success/holdout contract")

    report_sha256 = _sha256(report)
    conn.execute(
        """
        insert into lab_indicadores.artifacts
          (run_id, artifact_type, uri, sha256, metadata)
        values (%s, 'preflight_report', %s, %s, %s::jsonb)
        """,
        (
            run_id,
            str(report),
            report_sha256,
            json.dumps(
                {
                    "holdout_accessed": False,
                    "worker_artifact_sha256": payload.get("artifact_sha256"),
                },
                sort_keys=True,
            ),
        ),
    )
    conn.execute(
        "update lab_indicadores.runs set status='succeeded', heartbeat_at=now(), finished_at=now() where id=%s",
        (run_id,),
    )
    conn.execute(
        "update lab_indicadores.commands set status='completed', completed_at=now() where id=%s",
        (command_id,),
    )
    conn.commit()
    _event(
        conn,
        run_id,
        "run_succeeded",
        "Preflight completed successfully",
        {"artifact_type": "preflight_report", "sha256": report_sha256},
    )


def _fail_run(conn: psycopg.Connection, run_id: str, command_id: str, error: str) -> None:
    message = error[:4000]
    conn.execute(
        "update lab_indicadores.runs set status='failed', error_message=%s, finished_at=now() where id=%s",
        (message, run_id),
    )
    conn.execute(
        "update lab_indicadores.commands set status='failed', error_message=%s, completed_at=now() where id=%s",
        (message, command_id),
    )
    conn.commit()
    _event(conn, run_id, "run_failed", "Preflight failed", {"error": message})


def _execute_preflight() -> dict:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LOG_ROOT / "indicator-lab-preflight.log"
    command = [
        "sudo",
        "-n",
        "docker",
        "compose",
        "-p",
        PROJECT_NAME,
        "run",
        "--rm",
        "indicator-worker",
        "smoke",
    ]
    with log_path.open("ab") as log_handle:
        completed = subprocess.run(
            command,
            cwd=LAB_ROOT,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=JOB_TIMEOUT_SECONDS,
        )
    if completed.returncode != 0:
        raise RuntimeError(f"docker job exited with code {completed.returncode}; log={log_path}")
    return {"report": str(REPORT_PATH), "log": str(log_path)}


def process_one(conn: psycopg.Connection) -> bool:
    command = _claim(conn)
    if not command:
        return False

    run_id = str(command["run_id"])
    command_id = str(command["command_id"])
    manifest = command["dataset_manifest"]
    if manifest != ALLOWED_MANIFEST:
        _fail_run(conn, run_id, command_id, f"manifest not allowed: {manifest}")
        return True

    _register_worker(conn, "busy", {"run_id": run_id})
    try:
        _start_run(conn, run_id)
        result = _execute_preflight()
        _succeed_run(conn, run_id, command_id, result)
    except Exception as exc:  # noqa: BLE001 - persist all job failures.
        conn.rollback()
        _fail_run(conn, run_id, command_id, str(exc))
    finally:
        conn.rollback()
        _register_worker(conn, "online")
    return True


def main(once: bool = False) -> int:
    with psycopg.connect(_database_url(), row_factory=dict_row) as conn:
        _register_worker(conn, "online")
        try:
            if once:
                processed = process_one(conn)
                print("orchestrator_once=" + ("processed" if processed else "idle"))
                return 0
            while True:
                if not process_one(conn):
                    time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            _register_worker(conn, "offline", {"reason": "keyboard_interrupt"})
        finally:
            _register_worker(conn, "offline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(once="--once" in sys.argv[1:]))
