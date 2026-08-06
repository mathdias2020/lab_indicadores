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
from datetime import datetime, timezone
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
HERMES_AGENT_ID = os.environ.get("LAB_INDICADORES_HERMES_AGENT_ID", "hermes-indicadores")
HERMES_HEARTBEAT_PATH = LAB_ROOT / "hermes" / "outbox" / "heartbeat.json"
HERMES_STALE_SECONDS = int(os.environ.get("LAB_INDICADORES_HERMES_STALE_SECONDS", "30"))
HERMES_CAPABILITIES = ["read_development_data", "proposal_generation"]
HERMES_ALLOWED_STATUSES = {"observing", "proposing", "degraded", "error"}
HERMES_ALLOWED_MODES = {"observation", "proposal", "research", "review"}
HERMES_CONTEXT_MANIFEST = "hermes-context-absorption-v1"
HERMES_CONTEXT_PATH = LAB_ROOT / "hermes" / "context" / "absorption-research-v1.json"
HERMES_INBOX_PATH = LAB_ROOT / "hermes" / "inbox"
HERMES_PROPOSAL_ROOT = LAB_ROOT / "hermes" / "outbox" / "proposals"
HERMES_ENGINE_SERVICE = "lab-indicadores-hermes-engine.service"
ANALYSIS_MANIFEST = "hermes-analysis-absorption-v1"
ANALYSIS_CONTEXT_PATH = LAB_ROOT / "hermes" / "context" / "absorption-analysis-v1.json"
ANALYSIS_INBOX_PATH = LAB_ROOT / "work" / "analysis" / "inbox"


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
            json.dumps(["preflight", "analysis"], sort_keys=True),
            "control-plane-v1",
            json.dumps(metadata or {}, sort_keys=True),
        ),
    )
    conn.commit()


def _read_hermes_heartbeat() -> dict:
    base_metadata = {
        "project_id": PROJECT_NAME,
        "profile_id": "lab-indicadores",
        "holdout_access": False,
        "service_role_access": False,
        "docker_socket_access": False,
        "network_access": False,
        "execution_enabled": False,
        "scope": "canonical/development-only",
    }
    if not HERMES_HEARTBEAT_PATH.is_file():
        return {
            "status": "offline",
            "mode": "observation",
            "version": "0.1.0-bootstrap",
            "capabilities": HERMES_CAPABILITIES,
            "metadata": {**base_metadata, "reason": "heartbeat file not found"},
            "last_heartbeat_at": None,
        }

    try:
        payload = json.loads(HERMES_HEARTBEAT_PATH.read_text(encoding="utf-8"))
        if payload.get("kind") != "hermes_heartbeat_v1":
            raise ValueError("unsupported heartbeat kind")
        if payload.get("agent_id") != HERMES_AGENT_ID:
            raise ValueError("heartbeat agent id mismatch")

        file_mtime = HERMES_HEARTBEAT_PATH.stat().st_mtime
        heartbeat_at = datetime.fromtimestamp(file_mtime, tz=timezone.utc)
        age_seconds = max(0, time.time() - file_mtime)
        status = payload.get("status", "error")
        mode = payload.get("mode", "observation")
        if status not in HERMES_ALLOWED_STATUSES:
            raise ValueError(f"unsupported heartbeat status: {status}")
        if mode not in HERMES_ALLOWED_MODES:
            raise ValueError(f"unsupported heartbeat mode: {mode}")

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("heartbeat metadata must be an object")
        merged_metadata = {**base_metadata, **metadata, "heartbeat_age_seconds": round(age_seconds, 1)}
        if age_seconds > HERMES_STALE_SECONDS:
            status = "degraded"
            merged_metadata["reason"] = "heartbeat is stale"

        return {
            "status": status,
            "mode": mode,
            "version": str(payload.get("version", "0.1.0-bootstrap")),
            "capabilities": payload.get("capabilities") or HERMES_CAPABILITIES,
            "metadata": merged_metadata,
            "last_heartbeat_at": heartbeat_at,
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "error",
            "mode": "observation",
            "version": "0.1.0-bootstrap",
            "capabilities": HERMES_CAPABILITIES,
            "metadata": {**base_metadata, "reason": f"invalid heartbeat: {exc}"},
            "last_heartbeat_at": None,
        }


def _register_hermes(conn: psycopg.Connection) -> None:
    heartbeat = _read_hermes_heartbeat()
    conn.execute(
        """
        insert into lab_indicadores.agents
          (agent_id, agent_type, status, mode, profile_id, version,
           capabilities, metadata, last_heartbeat_at)
        values (%s, 'hermes', %s, %s, 'lab-indicadores', %s, %s::jsonb, %s::jsonb, %s)
        on conflict (agent_id) do update set
          status = excluded.status,
          mode = excluded.mode,
          version = excluded.version,
          capabilities = excluded.capabilities,
          metadata = excluded.metadata,
          last_heartbeat_at = excluded.last_heartbeat_at
        """,
        (
            HERMES_AGENT_ID,
            heartbeat["status"],
            heartbeat["mode"],
            heartbeat["version"],
            json.dumps(heartbeat["capabilities"], sort_keys=True),
            json.dumps(heartbeat["metadata"], sort_keys=True),
            heartbeat["last_heartbeat_at"],
        ),
    )
    conn.commit()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _ingest_hermes_proposals(
    conn: psycopg.Connection,
    expected_proposal_key: str | None = None,
) -> dict | None:
    if not HERMES_PROPOSAL_ROOT.is_dir():
        return None

    selected: dict | None = None
    for artifact_path in sorted(HERMES_PROPOSAL_ROOT.glob("*.json")):
        try:
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            proposal = artifact["proposal"]
            if artifact.get("kind") != "hermes_proposal_v1":
                continue
            if artifact.get("agent_id") != HERMES_AGENT_ID:
                continue
            if expected_proposal_key and artifact.get("proposal_key") != expected_proposal_key:
                continue
            if artifact.get("holdout_accessed") is not False or proposal.get("holdout_accessed") is not False:
                raise ValueError("proposal holdout contract failed")
            if proposal.get("agent_id") != HERMES_AGENT_ID:
                raise ValueError("proposal agent mismatch")
            proposal_hash = hashlib.sha256(_canonical_json(proposal)).hexdigest()
            if proposal_hash != artifact.get("proposal_sha256"):
                raise ValueError("proposal hash mismatch")

            run_id = proposal["run_id"]
            owner_row = conn.execute(
                "select owner_id from lab_indicadores.runs where id=%s",
                (run_id,),
            ).fetchone()
            if not owner_row:
                raise ValueError("proposal run does not exist")

            artifact_sha256 = _sha256(artifact_path)
            conn.execute(
                """
                insert into lab_indicadores.proposals (
                  proposal_key, run_id, agent_id, owner_id, status, evidence_level,
                  asset, track, horizon, title, question, mechanism, hypothesis,
                  validation_plan, limitations, source_context_uri,
                  source_context_sha256, proposal_sha256, artifact_uri, holdout_accessed
                ) values (
                  %s, %s, %s, %s, 'in_review', %s,
                  %s, %s, %s, %s, %s, %s, %s,
                  %s::jsonb, %s::jsonb, %s,
                  %s, %s, %s, false
                )
                on conflict (proposal_key) do update set
                  run_id = excluded.run_id,
                  agent_id = excluded.agent_id,
                  owner_id = excluded.owner_id,
                  status = case
                    when lab_indicadores.proposals.status in ('accepted', 'rejected', 'superseded')
                      then lab_indicadores.proposals.status
                    else excluded.status
                  end,
                  evidence_level = excluded.evidence_level,
                  asset = excluded.asset,
                  track = excluded.track,
                  horizon = excluded.horizon,
                  title = excluded.title,
                  question = excluded.question,
                  mechanism = excluded.mechanism,
                  hypothesis = excluded.hypothesis,
                  validation_plan = excluded.validation_plan,
                  limitations = excluded.limitations,
                  source_context_uri = excluded.source_context_uri,
                  source_context_sha256 = excluded.source_context_sha256,
                  proposal_sha256 = excluded.proposal_sha256,
                  artifact_uri = excluded.artifact_uri,
                  holdout_accessed = false
                """,
                (
                    artifact["proposal_key"],
                    run_id,
                    HERMES_AGENT_ID,
                    owner_row["owner_id"],
                    proposal["evidence_level"],
                    proposal["asset"],
                    proposal["track"],
                    proposal["horizon"],
                    proposal["title"],
                    proposal["question"],
                    proposal["mechanism"],
                    proposal["hypothesis"],
                    json.dumps(proposal["validation_plan"], sort_keys=True),
                    json.dumps(proposal["limitations"], sort_keys=True),
                    artifact["source_context_uri"],
                    artifact["source_context_sha256"],
                    artifact["proposal_sha256"],
                    str(artifact_path),
                ),
            )
            conn.commit()
            selected = {
                "path": artifact_path,
                "proposal_key": artifact["proposal_key"],
                "proposal_sha256": artifact["proposal_sha256"],
                "artifact_sha256": artifact_sha256,
            }
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
            conn.rollback()
            if expected_proposal_key:
                raise
    return selected


def _start_run(conn: psycopg.Connection, run_id: str, run_type: str = "preflight") -> None:
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
    message = {
        "research": "Hermes research proposal started",
        "analysis": "Deterministic indicator analysis started",
    }.get(run_type, "Preflight worker started")
    _event(conn, run_id, "run_started", message, {"worker_id": WORKER_ID, "run_type": run_type})


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
    _event(conn, run_id, "run_failed", "Run failed", {"error": message})


def _execute_research(conn: psycopg.Connection, run_id: str) -> dict:
    if not HERMES_CONTEXT_PATH.is_file():
        raise FileNotFoundError(HERMES_CONTEXT_PATH)

    if not conn.execute(
        "select 1 from lab_indicadores.runs where id=%s",
        (run_id,),
    ).fetchone():
        raise RuntimeError(f"research run not found: {run_id}")

    proposal_key = f"hermes-{run_id}"
    HERMES_INBOX_PATH.mkdir(parents=True, exist_ok=True)
    job_path = HERMES_INBOX_PATH / f"{run_id}.json"
    job = {
        "kind": "hermes_research_job_v1",
        "agent_id": HERMES_AGENT_ID,
        "run_id": run_id,
        "proposal_key": proposal_key,
        "dataset_manifest": HERMES_CONTEXT_MANIFEST,
        "context_path": str(HERMES_CONTEXT_PATH),
        "execution_profile": "fixture_proposal",
    }
    temporary_path = job_path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(job, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_path, job_path)

    completed = subprocess.run(
        ["sudo", "-n", "systemctl", "start", HERMES_ENGINE_SERVICE],
        cwd=LAB_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Hermes engine exited with code {completed.returncode}: {completed.stdout[-2000:]}"
        )

    proposal = _ingest_hermes_proposals(conn, expected_proposal_key=proposal_key)
    if not proposal:
        raise RuntimeError("Hermes engine completed without a proposal artifact")
    return {"proposal": proposal}


def _succeed_research(conn: psycopg.Connection, run_id: str, command_id: str, result: dict) -> None:
    proposal = result["proposal"]
    artifact_path = Path(proposal["path"]).resolve()
    if not artifact_path.is_file() or not artifact_path.is_relative_to(HERMES_PROPOSAL_ROOT.resolve()):
        raise RuntimeError("unexpected Hermes proposal artifact path")

    conn.execute(
        """
        insert into lab_indicadores.artifacts
          (run_id, artifact_type, uri, sha256, metadata)
        values (%s, 'hermes_proposal', %s, %s, %s::jsonb)
        """,
        (
            run_id,
            str(artifact_path),
            proposal["artifact_sha256"],
            json.dumps(
                {
                    "proposal_key": proposal["proposal_key"],
                    "proposal_sha256": proposal["proposal_sha256"],
                    "holdout_accessed": False,
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
        "Hermes proposal generated for review",
        {
            "artifact_type": "hermes_proposal",
            "proposal_key": proposal["proposal_key"],
            "sha256": proposal["artifact_sha256"],
        },
    )


def _execute_analysis(conn: psycopg.Connection, run_id: str, payload: dict) -> dict:
    if not ANALYSIS_CONTEXT_PATH.is_file():
        raise FileNotFoundError(ANALYSIS_CONTEXT_PATH)
    context = json.loads(ANALYSIS_CONTEXT_PATH.read_text(encoding="utf-8"))
    if context.get("context_id") != "absorption-descriptive-baseline-v1":
        raise ValueError("unsupported analysis context")
    if context.get("dataset_manifest") != ANALYSIS_MANIFEST:
        raise ValueError("analysis context manifest mismatch")
    if context.get("holdout_accessed") is not False:
        raise ValueError("analysis context opened the holdout")

    proposal_key = str(payload.get("proposal_key") or "")
    run = conn.execute(
        "select owner_id from lab_indicadores.runs where id=%s",
        (run_id,),
    ).fetchone()
    if not run:
        raise RuntimeError(f"analysis run not found: {run_id}")
    proposal = conn.execute(
        """
        select proposal_key, status, owner_id
        from lab_indicadores.proposals
        where proposal_key=%s
        """,
        (proposal_key,),
    ).fetchone()
    if not proposal or proposal["owner_id"] != run["owner_id"]:
        raise RuntimeError("analysis proposal does not belong to the run owner")
    if proposal["status"] not in {"in_review", "accepted"}:
        raise RuntimeError(f"proposal is not eligible for analysis: {proposal['status']}")

    ANALYSIS_INBOX_PATH.mkdir(parents=True, exist_ok=True)
    job_path = ANALYSIS_INBOX_PATH / f"{run_id}.json"
    job = {
        "kind": "indicator_analysis_job_v1",
        "project_id": PROJECT_NAME,
        "run_id": run_id,
        "analysis_id": context["analysis_id"],
        "proposal_key": proposal_key,
        "dataset_manifest": ANALYSIS_MANIFEST,
        "asset": context["asset"],
        "track": context["track"],
        "horizon": context["horizon"],
        "files": context["files"],
        "parameters": context["parameters"],
        "holdout_accessed": False,
    }
    temporary_path = job_path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(job, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_path, job_path)

    log_path = LOG_ROOT / f"analysis-{run_id}.log"
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
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
        "analysis",
        f"/app/work/analysis/inbox/{run_id}.json",
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
        raise RuntimeError(f"analysis job exited with code {completed.returncode}; log={log_path}")
    report_path = LAB_ROOT / "runs" / run_id / "analysis-report.json"
    if not report_path.is_file():
        raise FileNotFoundError(report_path)
    return {"report": report_path, "log": log_path}


def _succeed_analysis(conn: psycopg.Connection, run_id: str, command_id: str, result: dict) -> None:
    report = Path(result["report"]).resolve()
    expected_root = (LAB_ROOT / "runs" / run_id).resolve()
    if not report.is_file() or not report.is_relative_to(expected_root):
        raise RuntimeError("unexpected deterministic analysis report path")
    payload = json.loads(report.read_text(encoding="utf-8"))
    if (
        payload.get("kind") != "indicator_analysis_report_v1"
        or payload.get("status") != "succeeded"
        or payload.get("run_id") != run_id
        or payload.get("holdout_accessed") is not False
        or payload.get("evidence_level") != "descriptive"
    ):
        raise RuntimeError("analysis report failed the descriptive/holdout contract")
    artifact_hash = payload.get("artifact_sha256")
    payload_without_hash = {key: value for key, value in payload.items() if key != "artifact_sha256"}
    if artifact_hash != hashlib.sha256(_canonical_json(payload_without_hash)).hexdigest():
        raise RuntimeError("analysis report artifact hash mismatch")

    report_sha256 = _sha256(report)
    conn.execute(
        """
        insert into lab_indicadores.artifacts
          (run_id, artifact_type, uri, sha256, metadata)
        values (%s, 'indicator_analysis_report', %s, %s, %s::jsonb)
        """,
        (
            run_id,
            str(report),
            report_sha256,
            json.dumps(
                {
                    "analysis_id": payload.get("analysis_id"),
                    "proposal_key": payload.get("proposal_key"),
                    "evidence_level": payload.get("evidence_level"),
                    "candidate_windows_returned": payload.get("coverage", {}).get("candidate_windows_returned"),
                    "artifact_sha256": artifact_hash,
                    "holdout_accessed": False,
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
        "Deterministic descriptive analysis completed",
        {
            "artifact_type": "indicator_analysis_report",
            "sha256": report_sha256,
            "candidate_windows_returned": payload.get("coverage", {}).get("candidate_windows_returned"),
        },
    )


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
    command_type = command["command_type"]
    manifest = command["dataset_manifest"]
    if command_type == "start_run" and manifest != ALLOWED_MANIFEST:
        _fail_run(conn, run_id, command_id, f"manifest not allowed: {manifest}")
        return True
    if command_type == "start_research" and manifest != HERMES_CONTEXT_MANIFEST:
        _fail_run(conn, run_id, command_id, f"research manifest not allowed: {manifest}")
        return True
    if command_type == "start_analysis" and manifest != ANALYSIS_MANIFEST:
        _fail_run(conn, run_id, command_id, f"analysis manifest not allowed: {manifest}")
        return True

    _register_worker(conn, "busy", {"run_id": run_id})
    try:
        if command_type == "start_research":
            _start_run(conn, run_id, "research")
            result = _execute_research(conn, run_id)
            _succeed_research(conn, run_id, command_id, result)
        elif command_type == "start_analysis":
            _start_run(conn, run_id, "analysis")
            result = _execute_analysis(conn, run_id, command["payload"] or {})
            _succeed_analysis(conn, run_id, command_id, result)
        else:
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
        _register_hermes(conn)
        try:
            if once:
                processed = process_one(conn)
                print("orchestrator_once=" + ("processed" if processed else "idle"))
                return 0
            while True:
                _register_hermes(conn)
                _ingest_hermes_proposals(conn)
                if not process_one(conn):
                    time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            _register_worker(conn, "offline", {"reason": "keyboard_interrupt"})
        finally:
            _register_worker(conn, "offline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(once="--once" in sys.argv[1:]))
