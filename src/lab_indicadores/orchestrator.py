"""Host-side control-plane runner for the first indicator-lab command.

This process is deliberately not a Docker service. It may invoke the local
Docker CLI, while the worker container remains isolated and never receives the
Docker socket or a Supabase credential.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
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
HERMES_CAPABILITIES = [
    "read_development_data",
    "bounded_duckdb_exploration",
    "proposal_generation",
    "openai_structured_proposal",
    "error_review",
]
HERMES_ALLOWED_STATUSES = {"observing", "proposing", "degraded", "error"}
HERMES_ALLOWED_MODES = {"observation", "proposal", "research", "review"}
HERMES_CONTEXT_MANIFEST = "hermes-context-absorption-v1"
HERMES_CONTEXTS = {
    "absorption-baseline-v1": LAB_ROOT / "hermes" / "context" / "absorption-research-v1.json",
    "absorption-baseline-win-v1": LAB_ROOT / "hermes" / "context" / "absorption-research-win-v1.json",
}
HERMES_INBOX_PATH = LAB_ROOT / "hermes" / "inbox"
HERMES_PROPOSAL_ROOT = LAB_ROOT / "hermes" / "outbox" / "proposals"
HERMES_EXPLORATION_PLAN_ROOT = LAB_ROOT / "hermes" / "outbox" / "exploration-plans"
HERMES_EXPLORATION_ROOT = LAB_ROOT / "hermes" / "outbox" / "explorations"
HERMES_ENGINE_SERVICE = "lab-indicadores-hermes-engine.service"
ANALYSIS_CONTEXTS = {
    "absorption-descriptive-baseline-v1": {
        "path": LAB_ROOT / "hermes" / "context" / "absorption-analysis-v1.json",
        "manifest": "hermes-analysis-absorption-v1",
    },
    "absorption-descriptive-multi-period-wdo-v1": {
        "path": LAB_ROOT / "hermes" / "context" / "absorption-analysis-multi-period-wdo-v1.json",
        "manifest": "hermes-analysis-absorption-multi-period-wdo-v1",
    },
    "absorption-descriptive-multi-period-win-v1": {
        "path": LAB_ROOT / "hermes" / "context" / "absorption-analysis-multi-period-win-v1.json",
        "manifest": "hermes-analysis-absorption-multi-period-win-v1",
    },
}
ANALYSIS_MANIFESTS = {item["manifest"] for item in ANALYSIS_CONTEXTS.values()}
ANALYSIS_INBOX_PATH = LAB_ROOT / "work" / "analysis" / "inbox"
PROFILE_INBOX_PATH = LAB_ROOT / "work" / "hermes-profile" / "inbox"
EXPLORATION_INBOX_PATH = LAB_ROOT / "work" / "hermes-explorer" / "inbox"
HERMES_PROFILE_ROOT = LAB_ROOT / "hermes" / "outbox" / "profiles"
PROFILE_CONTEXTS = {
    "WDO": "absorption-descriptive-multi-period-wdo-v1",
    "WIN": "absorption-descriptive-multi-period-win-v1",
}


def _database_url() -> str:
    value = os.environ.get("LAB_INDICADORES_DATABASE_URL")
    if not value:
        raise RuntimeError("LAB_INDICADORES_DATABASE_URL is required")
    return value


def _hermes_ai_settings(conn: psycopg.Connection) -> dict:
    row = conn.execute(
        """
        select provider, model, reasoning_effort, enabled
        from lab_indicadores.ai_provider_settings
        where setting_key = 'hermes-proposal'
        limit 1
        """
    ).fetchone()
    if not row or not row["enabled"]:
        return {"provider": "fixture", "model": None, "reasoning_effort": "medium"}
    return {
        "provider": str(row["provider"]),
        "model": str(row["model"]),
        "reasoning_effort": str(row["reasoning_effort"]),
    }


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
          (run_id, event_type, message, payload, created_at)
        values (%s, %s, %s, %s::jsonb, clock_timestamp())
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
        values (%s, %s, %s::jsonb, %s, clock_timestamp(), %s::jsonb)
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
            json.dumps(["preflight", "data_profile", "analysis", "error_review"], sort_keys=True),
            "control-plane-v2-hermes-data-understanding",
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
            if artifact.get("kind") not in {"hermes_proposal_v1", "hermes_proposal_v2"}:
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
                  source_context_sha256, proposal_sha256, artifact_uri, holdout_accessed,
                  campaign_id, parent_proposal_key, revision_no, change_kind, change_reason,
                  feedback_run_id, data_profile_id, data_profile_sha256
                ) values (
                  %s, %s, %s, %s, 'in_review', %s,
                  %s, %s, %s, %s, %s, %s, %s,
                  %s::jsonb, %s::jsonb, %s,
                  %s, %s, %s, false,
                  %s, %s, %s, %s, %s, %s, %s, %s
                )
                on conflict (proposal_key) do nothing
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
                    proposal.get("campaign_id"),
                    proposal.get("parent_proposal_key"),
                    int(proposal.get("revision_no", 1)),
                    proposal.get("change_kind", "initial"),
                    proposal.get("change_reason") or proposal.get("feedback_error"),
                    proposal.get("feedback_run_id"),
                    proposal.get("data_profile_id"),
                    proposal.get("data_profile_sha256"),
                ),
            )
            persisted = conn.execute(
                "select proposal_sha256 from lab_indicadores.proposals where proposal_key=%s",
                (artifact["proposal_key"],),
            ).fetchone()
            if not persisted or persisted["proposal_sha256"] != artifact["proposal_sha256"]:
                raise ValueError("proposal key already exists with different content")
            conn.commit()
            selected = {
                "path": artifact_path,
                "proposal_key": artifact["proposal_key"],
                "proposal_sha256": artifact["proposal_sha256"],
                "artifact_sha256": artifact_sha256,
                "exploration_result_uri": artifact.get("exploration_result_uri"),
                "exploration_sha256": artifact.get("exploration_sha256"),
                "exploration_artifact_sha256": artifact.get("exploration_artifact_sha256"),
                "exploration_query_count": artifact.get("proposal", {}).get("exploration_query_count", 0),
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
            started_at = coalesce(started_at, clock_timestamp()),
            heartbeat_at = clock_timestamp()
        where id = %s
        """,
        (run_id,),
    )
    conn.commit()
    message = {
        "research": "Hermes research proposal started",
        "analysis": "Deterministic indicator analysis started",
        "data_profile": "Hermes development data profile started",
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
        "update lab_indicadores.runs set status='succeeded', heartbeat_at=clock_timestamp(), finished_at=clock_timestamp() where id=%s",
        (run_id,),
    )
    conn.execute(
        "update lab_indicadores.commands set status='completed', completed_at=clock_timestamp() where id=%s",
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
    run_context = conn.execute(
        "select run_type, campaign_id from lab_indicadores.runs where id=%s",
        (run_id,),
    ).fetchone()
    conn.execute(
        "update lab_indicadores.runs set status='failed', error_message=%s, finished_at=clock_timestamp() where id=%s",
        (message, run_id),
    )
    conn.execute(
        "update lab_indicadores.commands set status='failed', error_message=%s, completed_at=clock_timestamp() where id=%s",
        (message, command_id),
    )
    if run_context and run_context["run_type"] == "research" and run_context["campaign_id"]:
        conn.execute(
            """
            update lab_indicadores.research_campaign_steps
            set status='failed', error_payload=%s::jsonb, finished_at=clock_timestamp()
            where run_id=%s
            """,
            (json.dumps({"error": message, "holdout_accessed": False}, sort_keys=True), run_id),
        )
        conn.execute(
            "update lab_indicadores.research_campaigns set status='failed', stage='error_review' where id=%s",
            (run_context["campaign_id"],),
        )
    conn.commit()
    _event(conn, run_id, "run_failed", "Run failed", {"error": message})
    try:
        _queue_error_review(conn, run_id, message)
    except Exception as review_error:
        # Preserve the original failure as the primary evidence. A review
        # scheduling failure is operational metadata, not a replacement for
        # the research failure and must not terminate the orchestrator loop.
        conn.rollback()
        _event(
            conn,
            run_id,
            "error_review_failed",
            "Automatic error review could not be queued",
            {"error": str(review_error)[:4000], "holdout_accessed": False},
        )


def _queue_error_review(conn: psycopg.Connection, failed_run_id: str, error: str) -> None:
    """Create a new Hermes revision proposal after an analysis failure.

    The failed proposal and run remain immutable evidence. The only automatic
    action is to enqueue a bounded review proposal; acceptance and re-analysis
    still require the human gate in the dashboard.
    """
    if "holdout" in error.lower():
        return
    run = conn.execute(
        "select run_type, config, owner_id, campaign_id from lab_indicadores.runs where id=%s",
        (failed_run_id,),
    ).fetchone()
    if not run or run["run_type"] != "analysis":
        return
    config = run["config"] or {}
    proposal_key = str(config.get("proposal_key") or "")
    campaign_id = run["campaign_id"] or config.get("campaign_id")
    if not campaign_id and proposal_key:
        proposal_row = conn.execute(
            "select campaign_id from lab_indicadores.proposals where proposal_key=%s",
            (proposal_key,),
        ).fetchone()
        campaign_id = proposal_row["campaign_id"] if proposal_row else None
    if not campaign_id:
        return
    campaign = conn.execute(
        "select campaign_key, asset, track, horizon, iteration, max_iterations from lab_indicadores.research_campaigns where id=%s",
        (campaign_id,),
    ).fetchone()
    if not campaign:
        return
    next_iteration = int(campaign["iteration"]) + 1
    if next_iteration > int(campaign["max_iterations"]):
        conn.execute(
            "update lab_indicadores.research_campaigns set status='failed', stage='error_review' where id=%s",
            (campaign_id,),
        )
        conn.commit()
        _event(conn, failed_run_id, "error_review_limit_reached", "Hermes review limit reached; human intervention required", {
            "campaign_id": str(campaign_id), "max_iterations": campaign["max_iterations"], "holdout_accessed": False,
        })
        return
    profile = conn.execute(
        """
        select id, artifact_uri, profile_sha256
        from lab_indicadores.data_profiles
        where campaign_id=%s
        order by created_at desc
        limit 1
        """,
        (campaign_id,),
    ).fetchone()
    if not profile:
        return
    context_id = "absorption-baseline-win-v1" if campaign["asset"] == "WIN" else "absorption-baseline-v1"
    research_run_key = f"{campaign['campaign_key']}:error-review:{next_iteration}"
    idempotency_key = f"{research_run_key}:start"
    research_run = conn.execute(
        """
        insert into lab_indicadores.runs (
          run_key, run_type, status, dataset_manifest, config, owner_id,
          requested_by, campaign_id
        ) values (
          %s, 'research', 'queued', %s, %s::jsonb, %s, 'orchestrator', %s
        ) on conflict (run_key) do update set run_key=excluded.run_key
        returning id
        """,
        (
            research_run_key,
            HERMES_CONTEXT_MANIFEST,
            json.dumps({
                "campaign_id": str(campaign_id), "stage": "error_review", "context_id": context_id,
                "asset": campaign["asset"], "track": campaign["track"], "horizon": campaign["horizon"],
                "parent_proposal_key": proposal_key, "feedback_run_id": failed_run_id,
                "feedback_error": error, "data_profile_id": str(profile["id"]),
                "data_profile_path": profile["artifact_uri"], "data_profile_sha256": profile["profile_sha256"],
                "research_mode": "campaign", "revision_no": next_iteration,
                "change_kind": "error_review", "holdout_accessed": False,
            }, sort_keys=True),
            run["owner_id"], campaign_id,
        ),
    ).fetchone()["id"]
    command_row = conn.execute(
        """
        insert into lab_indicadores.commands (
          run_id, command_type, status, idempotency_key, payload, owner_id, requested_by
        ) values (
          %s, 'start_research', 'queued', %s, %s::jsonb, %s, 'orchestrator'
        ) on conflict (idempotency_key) do update set idempotency_key=excluded.idempotency_key
        returning id
        """,
        (
            research_run, idempotency_key,
            json.dumps({
                "campaign_id": str(campaign_id), "context_id": context_id,
                "asset": campaign["asset"], "track": campaign["track"], "horizon": campaign["horizon"],
                "parent_proposal_key": proposal_key, "feedback_run_id": failed_run_id,
                "feedback_error": error, "data_profile_id": str(profile["id"]),
                "data_profile_path": profile["artifact_uri"], "data_profile_sha256": profile["profile_sha256"],
                "research_mode": "campaign", "revision_no": next_iteration,
                "change_kind": "error_review", "holdout_accessed": False,
            }, sort_keys=True),
            run["owner_id"],
        ),
    ).fetchone()["id"]
    conn.execute(
        """
        insert into lab_indicadores.research_campaign_steps (
          campaign_id, step_key, stage, sequence_no, status, run_id, input
        ) values (%s, %s, 'error_review', %s, 'queued', %s, %s::jsonb)
        on conflict (campaign_id, step_key) do nothing
        """,
        (
            campaign_id, f"error-review-{next_iteration}", next_iteration + 1, research_run,
            json.dumps({"parent_proposal_key": proposal_key, "feedback_run_id": failed_run_id, "holdout_accessed": False}, sort_keys=True),
        ),
    )
    conn.execute(
        "update lab_indicadores.research_campaigns set status='running', stage='error_review', iteration=%s where id=%s",
        (next_iteration, campaign_id),
    )
    conn.commit()
    _event(conn, failed_run_id, "error_review_queued", "Hermes queued an immutable revision proposal after analysis failure", {
        "campaign_id": str(campaign_id), "command_id": str(command_row), "research_run_id": str(research_run),
        "parent_proposal_key": proposal_key, "revision_no": next_iteration, "holdout_accessed": False,
    })


def _execute_data_profile(conn: psycopg.Connection, run_id: str, payload: dict) -> dict:
    context_id = str(payload.get("profile_context_id") or "")
    context_spec = ANALYSIS_CONTEXTS.get(context_id)
    if not context_spec:
        raise ValueError(f"unsupported Hermes profile context: {context_id}")
    context_path = context_spec["path"]
    if not context_path.is_file():
        raise FileNotFoundError(context_path)
    context = json.loads(context_path.read_text(encoding="utf-8"))
    if context.get("holdout_accessed") is not False:
        raise ValueError("profile context opened the holdout")
    if context.get("dataset_manifest") != context_spec["manifest"]:
        raise ValueError("profile context manifest mismatch")

    PROFILE_INBOX_PATH.mkdir(parents=True, exist_ok=True)
    job_path = PROFILE_INBOX_PATH / f"{run_id}.json"
    job = {
        "kind": "hermes_data_profile_job_v1",
        "project_id": "lab-indicadores",
        "run_id": run_id,
        "profile_id": f"hermes-profile-{run_id}",
        "profile_context_id": context_id,
        "dataset_manifest": context_spec["manifest"],
        "asset": context["asset"],
        "track": context["track"],
        "horizon": context["horizon"],
        "files": context["files"],
        "holdout_accessed": False,
    }
    temporary_path = job_path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(job, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_path, job_path)

    log_path = LOG_ROOT / f"data-profile-{run_id}.log"
    command = [
        "sudo", "-n", "docker", "compose", "-p", PROJECT_NAME,
        "run", "--rm", "indicator-worker", "profile",
        f"/app/work/hermes-profile/inbox/{run_id}.json",
    ]
    return_code = _run_docker_job(conn, command, log_path, run_id)
    if return_code != 0:
        raise RuntimeError(f"data profile job exited with code {return_code}; log={log_path}")
    report_path = LAB_ROOT / "runs" / run_id / "data-profile.json"
    if not report_path.is_file():
        raise FileNotFoundError(report_path)
    return {"report": report_path, "log": log_path, "context": context}


def _load_hermes_exploration_plan(run_id: str, profile_sha256: str) -> tuple[Path, dict]:
    plan_path = (HERMES_EXPLORATION_PLAN_ROOT / f"{run_id}.json").resolve()
    if not plan_path.is_file() or not plan_path.is_relative_to(HERMES_EXPLORATION_PLAN_ROOT.resolve()):
        raise FileNotFoundError(f"Hermes exploration plan not found for run {run_id}")
    plan_artifact = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan_artifact.get("kind") != "hermes_exploration_plan_v1":
        raise ValueError("unsupported Hermes exploration plan kind")
    if plan_artifact.get("run_id") != run_id:
        raise ValueError("Hermes exploration plan run mismatch")
    if plan_artifact.get("source_profile_sha256") != profile_sha256:
        raise ValueError("Hermes exploration plan profile mismatch")
    if plan_artifact.get("holdout_accessed") is not False:
        raise ValueError("Hermes exploration plan opened the holdout")
    without_hash = {
        key: value
        for key, value in plan_artifact.items()
        if key not in {"plan_sha256", "status"}
    }
    if plan_artifact.get("plan_sha256") != hashlib.sha256(_canonical_json(without_hash)).hexdigest():
        raise ValueError("Hermes exploration plan hash mismatch")
    queries = (plan_artifact.get("plan") or {}).get("queries")
    if not isinstance(queries, list) or not 1 <= len(queries) <= 3:
        raise ValueError("Hermes exploration plan query count is invalid")
    return plan_path, plan_artifact


def _execute_hermes_exploration(
    conn: psycopg.Connection,
    run_id: str,
    profile: dict,
    plan_path: Path,
    plan_artifact: dict,
) -> dict:
    EXPLORATION_INBOX_PATH.mkdir(parents=True, exist_ok=True)
    HERMES_EXPLORATION_ROOT.mkdir(parents=True, exist_ok=True)
    job_path = EXPLORATION_INBOX_PATH / f"{run_id}.json"
    job = {
        "kind": "hermes_exploration_job_v1",
        "project_id": PROJECT_NAME.replace("_", "-"),
        "run_id": run_id,
        "exploration_id": f"hermes-exploration-{run_id}",
        "asset": profile["asset"],
        "track": profile.get("track", "flow_price"),
        "horizon": profile.get("horizon", "tactical_intraday"),
        "dataset_manifest": profile["dataset_manifest"],
        "source_profile_sha256": profile["profile_sha256"],
        "plan_sha256": plan_artifact["plan_sha256"],
        "files": profile["source_files"],
        "queries": (plan_artifact["plan"] or {}).get("queries", []),
        "holdout_accessed": False,
    }
    temporary_path = job_path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(job, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_path, job_path)

    log_path = LOG_ROOT / f"hermes-exploration-{run_id}.log"
    command = [
        "sudo", "-n", "docker", "compose", "-p", PROJECT_NAME,
        "run", "--rm", "indicator-worker", "explore",
        f"/app/work/hermes-explorer/inbox/{run_id}.json",
    ]
    return_code = _run_docker_job(conn, command, log_path, run_id)
    if return_code != 0:
        raise RuntimeError(f"Hermes exploration exited with code {return_code}; log={log_path}")

    report_path = (LAB_ROOT / "runs" / run_id / "hermes-exploration.json").resolve()
    if not report_path.is_file() or not report_path.is_relative_to((LAB_ROOT / "runs" / run_id).resolve()):
        raise FileNotFoundError("Hermes exploration report was not produced")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (
        report.get("kind") != "hermes_exploration_report_v1"
        or report.get("status") != "succeeded"
        or report.get("run_id") != run_id
        or report.get("source_profile_sha256") != profile["profile_sha256"]
        or report.get("plan_sha256") != plan_artifact["plan_sha256"]
        or report.get("holdout_accessed") is not False
    ):
        raise RuntimeError("Hermes exploration report failed its source/holdout contract")

    result_path = HERMES_EXPLORATION_ROOT / f"{run_id}.json"
    shutil.copyfile(report_path, result_path)
    return {
        "path": result_path,
        "exploration_sha256": report["exploration_sha256"],
        "artifact_sha256": _sha256(result_path),
        "plan_sha256": plan_artifact["plan_sha256"],
        "query_count": len(report.get("queries", [])),
    }


def _execute_research(conn: psycopg.Connection, run_id: str, payload: dict) -> dict:
    context_id = str(payload.get("context_id") or "absorption-baseline-v1")
    context_path = HERMES_CONTEXTS.get(context_id)
    if not context_path:
        raise ValueError(f"unsupported Hermes research context: {context_id}")
    if not context_path.is_file():
        raise FileNotFoundError(context_path)

    if not conn.execute(
        "select 1 from lab_indicadores.runs where id=%s",
        (run_id,),
    ).fetchone():
        raise RuntimeError(f"research run not found: {run_id}")

    profile_path = None
    profile = None
    if payload.get("data_profile_path"):
        profile_path = Path(str(payload["data_profile_path"])).resolve()
        if not profile_path.is_file() or not profile_path.is_relative_to(HERMES_PROFILE_ROOT.resolve()):
            raise RuntimeError("research data profile path is outside Hermes profile boundary")
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        if profile.get("holdout_accessed") is not False:
            raise RuntimeError("research data profile opened the holdout")
        if profile.get("profile_sha256") != payload.get("data_profile_sha256"):
            raise RuntimeError("research data profile logical hash mismatch")

    parent_proposal = None
    parent_proposal_key = payload.get("parent_proposal_key")
    if parent_proposal_key:
        parent_row = conn.execute(
            """
            select title, question, mechanism, hypothesis, validation_plan, limitations,
                   proposal_sha256, revision_no
            from lab_indicadores.proposals
            where proposal_key=%s
            """,
            (parent_proposal_key,),
        ).fetchone()
        if parent_row:
            parent_proposal = dict(parent_row)

    ai_settings = _hermes_ai_settings(conn)

    proposal_key = f"hermes-{run_id}"
    HERMES_INBOX_PATH.mkdir(parents=True, exist_ok=True)
    job_path = HERMES_INBOX_PATH / f"{run_id}.json"
    job = {
        "kind": "hermes_research_job_v1",
        "agent_id": HERMES_AGENT_ID,
        "run_id": run_id,
        "proposal_key": proposal_key,
        "dataset_manifest": HERMES_CONTEXT_MANIFEST,
        "context_id": context_id,
        "context_path": str(context_path),
        "execution_profile": "data_informed_fixture" if profile else "fixture_proposal",
        "research_mode": payload.get("research_mode", "campaign" if profile else "legacy"),
        "campaign_id": payload.get("campaign_id"),
        "data_profile_path": str(profile_path) if profile_path else None,
        "data_profile_id": payload.get("data_profile_id"),
        "data_profile_sha256": profile.get("profile_sha256") if profile else None,
        "data_profile_artifact_sha256": _sha256(profile_path) if profile_path else None,
        "parent_proposal_key": payload.get("parent_proposal_key"),
        "parent_proposal": parent_proposal,
        "revision_no": payload.get("revision_no", 1),
        "change_kind": payload.get("change_kind", "initial"),
        "feedback_run_id": payload.get("feedback_run_id"),
        "feedback_error": payload.get("feedback_error"),
        "provider": ai_settings["provider"],
        "model": ai_settings["model"],
        "reasoning_effort": ai_settings["reasoning_effort"],
    }
    temporary_path = job_path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(job, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_path, job_path)

    def start_hermes_engine() -> None:
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

    start_hermes_engine()

    exploration_execution = None
    if ai_settings["provider"] == "openai" and profile is not None and not job.get("exploration_results_path"):
        plan_path = HERMES_EXPLORATION_PLAN_ROOT / f"{run_id}.json"
        if plan_path.is_file():
            loaded_plan_path, plan_artifact = _load_hermes_exploration_plan(
                run_id,
                str(profile["profile_sha256"]),
            )
            exploration_execution = _execute_hermes_exploration(
                conn,
                run_id,
                profile,
                loaded_plan_path,
                plan_artifact,
            )
            job.update(
                {
                    "exploration_results_path": str(exploration_execution["path"]),
                    "exploration_sha256": exploration_execution["exploration_sha256"],
                    "exploration_artifact_sha256": exploration_execution["artifact_sha256"],
                    "exploration_plan_sha256": exploration_execution["plan_sha256"],
                }
            )
            temporary_path = job_path.with_suffix(".tmp")
            temporary_path.write_text(json.dumps(job, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary_path, job_path)
            start_hermes_engine()

    proposal = _ingest_hermes_proposals(conn, expected_proposal_key=proposal_key)
    if not proposal:
        raise RuntimeError("Hermes engine completed without a proposal artifact")
    if exploration_execution:
        proposal["exploration_result_uri"] = str(exploration_execution["path"])
        proposal["exploration_sha256"] = exploration_execution["exploration_sha256"]
        proposal["exploration_artifact_sha256"] = exploration_execution["artifact_sha256"]
        proposal["exploration_query_count"] = exploration_execution["query_count"]
    return {"proposal": proposal}


def _succeed_data_profile(conn: psycopg.Connection, run_id: str, command_id: str, result: dict) -> None:
    report = Path(result["report"]).resolve()
    expected_root = (LAB_ROOT / "runs" / run_id).resolve()
    if not report.is_file() or not report.is_relative_to(expected_root):
        raise RuntimeError("unexpected Hermes data profile path")
    profile = json.loads(report.read_text(encoding="utf-8"))
    if (
        profile.get("kind") != "hermes_data_profile_v1"
        or profile.get("status") != "succeeded"
        or profile.get("run_id") != run_id
        or profile.get("holdout_accessed") is not False
    ):
        raise RuntimeError("data profile failed the success/holdout contract")
    profile_sha256 = profile.get("profile_sha256")
    without_hash = {key: value for key, value in profile.items() if key not in {"profile_sha256", "status"}}
    if profile_sha256 != hashlib.sha256(_canonical_json(without_hash)).hexdigest():
        raise RuntimeError("data profile logical hash mismatch")

    run = conn.execute(
        "select campaign_id, owner_id from lab_indicadores.runs where id=%s",
        (run_id,),
    ).fetchone()
    if not run or not run["campaign_id"]:
        raise RuntimeError("data profile run is not linked to a campaign")
    campaign = conn.execute(
        "select campaign_key, asset, track, horizon, iteration, max_iterations from lab_indicadores.research_campaigns where id=%s",
        (run["campaign_id"],),
    ).fetchone()
    if not campaign:
        raise RuntimeError("data profile campaign does not exist")

    profile_key = f"{profile['profile_id']}-{run_id}"
    HERMES_PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
    profile_copy = HERMES_PROFILE_ROOT / f"{profile_key}.json"
    shutil.copyfile(report, profile_copy)
    report_sha256 = _sha256(report)
    conn.execute(
        """
        insert into lab_indicadores.artifacts
          (run_id, artifact_type, uri, sha256, metadata)
        values (%s, 'hermes_data_profile', %s, %s, %s::jsonb)
        """,
        (
            run_id,
            str(profile_copy),
            report_sha256,
            json.dumps({
                "profile_id": profile["profile_id"],
                "profile_sha256": profile_sha256,
                "asset": profile["asset"],
                "raw_rows": profile.get("coverage", {}).get("raw_rows"),
                "source_files": len(profile.get("source_files", [])),
                "holdout_accessed": False,
            }, sort_keys=True),
        ),
    )
    data_profile = conn.execute(
        """
        insert into lab_indicadores.data_profiles (
          profile_key, campaign_id, run_id, step_id, profile_version,
          profile_context_id, asset, dataset_manifest, profile,
          profile_sha256, artifact_uri, holdout_accessed
        ) values (
          %s, %s, %s,
          (select id from lab_indicadores.research_campaign_steps where campaign_id=%s and step_key='data-profile-1'),
          'hermes-data-profile-v1', %s, %s, %s, %s::jsonb, %s, %s, false
        ) returning id
        """,
        (
            profile_key,
            run["campaign_id"],
            run_id,
            run["campaign_id"],
            profile["profile_context_id"],
            profile["asset"],
            profile["dataset_manifest"],
            json.dumps(profile, sort_keys=True),
            profile_sha256,
            str(profile_copy),
        ),
    ).fetchone()
    data_profile_id = data_profile["id"]
    next_iteration = int(campaign["iteration"]) + 1
    research_context_id = "absorption-baseline-win-v1" if campaign["asset"] == "WIN" else "absorption-baseline-v1"
    research_run_key = f"{campaign['campaign_key']}:hypothesis:{next_iteration}"
    research_idempotency_key = f"{research_run_key}:start"
    research_run = conn.execute(
        """
        insert into lab_indicadores.runs (
          run_key, run_type, status, dataset_manifest, config, owner_id,
          requested_by, campaign_id
        ) values (
          %s, 'research', 'queued', %s, %s::jsonb, %s, 'orchestrator', %s
        ) returning id
        """,
        (
            research_run_key,
            HERMES_CONTEXT_MANIFEST,
            json.dumps({
                "campaign_id": str(run["campaign_id"]),
                "stage": "hypothesis",
                "context_id": research_context_id,
                "asset": campaign["asset"],
                "track": campaign["track"],
                "horizon": campaign["horizon"],
                "data_profile_id": str(data_profile_id),
                "data_profile_path": str(profile_copy),
                "data_profile_sha256": profile_sha256,
                "data_profile_artifact_sha256": report_sha256,
                "research_mode": "campaign",
                "revision_no": next_iteration,
                "change_kind": "initial",
                "holdout_accessed": False,
            }, sort_keys=True),
            run["owner_id"],
            run["campaign_id"],
        ),
    ).fetchone()["id"]
    research_command = conn.execute(
        """
        insert into lab_indicadores.commands (
          run_id, command_type, status, idempotency_key, payload,
          owner_id, requested_by
        ) values (
          %s, 'start_research', 'queued', %s, %s::jsonb, %s, 'orchestrator'
        ) returning id
        """,
        (
            research_run,
            research_idempotency_key,
            json.dumps({
                "campaign_id": str(run["campaign_id"]),
                "context_id": research_context_id,
                "asset": campaign["asset"],
                "track": campaign["track"],
                "horizon": campaign["horizon"],
                "data_profile_id": str(data_profile_id),
                "data_profile_path": str(profile_copy),
                "data_profile_sha256": profile_sha256,
                "data_profile_artifact_sha256": report_sha256,
                "research_mode": "campaign",
                "revision_no": next_iteration,
                "change_kind": "initial",
                "holdout_accessed": False,
            }, sort_keys=True),
            run["owner_id"],
        ),
    ).fetchone()["id"]
    conn.execute(
        """
        insert into lab_indicadores.research_campaign_steps (
          campaign_id, step_key, stage, sequence_no, status, run_id, input
        ) values (
          %s, %s, 'hypothesis', %s, 'queued', %s,
          %s::jsonb
        )
        """,
        (
            run["campaign_id"],
            f"hypothesis-{next_iteration}",
            next_iteration + 1,
            research_run,
            json.dumps({"data_profile_id": str(data_profile_id), "holdout_accessed": False}, sort_keys=True),
        ),
    )
    conn.execute(
        "update lab_indicadores.research_campaign_steps set status='succeeded', output=%s::jsonb, finished_at=clock_timestamp() where campaign_id=%s and step_key='data-profile-1'",
        (json.dumps({"data_profile_id": str(data_profile_id), "profile_sha256": profile_sha256}, sort_keys=True), run["campaign_id"]),
    )
    conn.execute(
        "update lab_indicadores.research_campaigns set status='running', stage='hypothesis', iteration=%s where id=%s",
        (next_iteration, run["campaign_id"]),
    )
    conn.execute(
        "update lab_indicadores.runs set status='succeeded', heartbeat_at=clock_timestamp(), finished_at=clock_timestamp() where id=%s",
        (run_id,),
    )
    conn.execute(
        "update lab_indicadores.commands set status='completed', completed_at=clock_timestamp() where id=%s",
        (command_id,),
    )
    conn.commit()
    _event(conn, run_id, "run_succeeded", "Hermes data profile completed before hypothesis generation", {
        "artifact_type": "hermes_data_profile", "profile_sha256": profile_sha256,
        "artifact_sha256": report_sha256, "raw_rows": profile.get("coverage", {}).get("raw_rows"),
        "holdout_accessed": False,
    })
    _event(conn, research_run, "hypothesis_queued", "Hermes hypothesis queued with data profile context", {
        "campaign_id": str(run["campaign_id"]), "command_id": str(research_command),
        "data_profile_id": str(data_profile_id), "profile_sha256": profile_sha256,
        "holdout_accessed": False,
    })


def _run_docker_job(
    conn: psycopg.Connection,
    command: list[str],
    log_path: Path,
    run_id: str,
) -> int:
    """Run a worker while keeping the dashboard heartbeat alive."""
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    last_heartbeat = started
    heartbeat_interval = max(10.0, min(30.0, POLL_SECONDS * 10.0))
    with log_path.open("ab") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=LAB_ROOT,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        try:
            while True:
                return_code = process.poll()
                now = time.monotonic()
                if return_code is not None:
                    return return_code
                if now - started >= JOB_TIMEOUT_SECONDS:
                    process.kill()
                    process.wait()
                    raise subprocess.TimeoutExpired(command, JOB_TIMEOUT_SECONDS)
                if now - last_heartbeat >= heartbeat_interval:
                    try:
                        _register_worker(
                            conn,
                            "busy",
                            {
                                "run_id": run_id,
                                "elapsed_seconds": round(now - started, 1),
                            },
                        )
                    except Exception:  # noqa: BLE001 - monitoring must not kill a healthy job.
                        conn.rollback()
                    last_heartbeat = now
                time.sleep(min(1.0, max(0.25, POLL_SECONDS)))
        except BaseException:
            if process.poll() is None:
                process.kill()
                process.wait()
            raise


def _succeed_research(conn: psycopg.Connection, run_id: str, command_id: str, result: dict) -> None:
    proposal = result["proposal"]
    artifact_path = Path(proposal["path"]).resolve()
    if not artifact_path.is_file() or not artifact_path.is_relative_to(HERMES_PROPOSAL_ROOT.resolve()):
        raise RuntimeError("unexpected Hermes proposal artifact path")
    proposal_row = conn.execute(
        "select campaign_id, revision_no from lab_indicadores.proposals where proposal_key=%s",
        (proposal["proposal_key"],),
    ).fetchone()

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
    exploration_uri = proposal.get("exploration_result_uri")
    if exploration_uri:
        exploration_path = Path(str(exploration_uri)).resolve()
        if (
            not exploration_path.is_file()
            or not exploration_path.is_relative_to(HERMES_EXPLORATION_ROOT.resolve())
        ):
            raise RuntimeError("unexpected Hermes exploration artifact path")
        expected_exploration_artifact_sha256 = proposal.get("exploration_artifact_sha256")
        if expected_exploration_artifact_sha256 and _sha256(exploration_path) != expected_exploration_artifact_sha256:
            raise RuntimeError("Hermes exploration artifact hash mismatch")
        conn.execute(
            """
            insert into lab_indicadores.artifacts
              (run_id, artifact_type, uri, sha256, metadata)
            values (%s, 'hermes_exploration', %s, %s, %s::jsonb)
            """,
            (
                run_id,
                str(exploration_path),
                expected_exploration_artifact_sha256 or _sha256(exploration_path),
                json.dumps(
                    {
                        "exploration_sha256": proposal.get("exploration_sha256"),
                        "query_count": proposal.get("exploration_query_count", 0),
                        "holdout_accessed": False,
                    },
                    sort_keys=True,
                ),
            ),
        )
    conn.execute(
        "update lab_indicadores.runs set status='succeeded', heartbeat_at=clock_timestamp(), finished_at=clock_timestamp() where id=%s",
        (run_id,),
    )
    conn.execute(
        "update lab_indicadores.commands set status='completed', completed_at=clock_timestamp() where id=%s",
        (command_id,),
    )
    if proposal_row and proposal_row["campaign_id"]:
        conn.execute(
            "update lab_indicadores.research_campaign_steps set status='awaiting_review', output=%s::jsonb, finished_at=clock_timestamp() where campaign_id=%s and step_key=%s",
            (
                json.dumps({"proposal_key": proposal["proposal_key"], "proposal_sha256": proposal["proposal_sha256"], "holdout_accessed": False}, sort_keys=True),
                proposal_row["campaign_id"],
                f"hypothesis-{proposal_row['revision_no']}",
            ),
        )
        conn.execute(
            "update lab_indicadores.research_campaigns set status='awaiting_review', stage='gate' where id=%s",
            (proposal_row["campaign_id"],),
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
            "exploration_queries": proposal.get("exploration_query_count", 0),
        },
    )


def _execute_analysis(conn: psycopg.Connection, run_id: str, payload: dict) -> dict:
    context_id = str(payload.get("analysis_context_id") or "absorption-descriptive-baseline-v1")
    context_spec = ANALYSIS_CONTEXTS.get(context_id)
    if not context_spec:
        raise ValueError(f"unsupported analysis context: {context_id}")
    context_path = context_spec["path"]
    if not context_path.is_file():
        raise FileNotFoundError(context_path)
    context = json.loads(context_path.read_text(encoding="utf-8"))
    if context.get("context_id") != context_id:
        raise ValueError("analysis context id mismatch")
    if context.get("dataset_manifest") != context_spec["manifest"]:
        raise ValueError("analysis context manifest mismatch")
    if payload.get("dataset_manifest") not in {None, context_spec["manifest"]}:
        raise ValueError("analysis payload manifest does not match context")
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
        "project_id": "lab-indicadores",
        "run_id": run_id,
        "analysis_id": context["analysis_id"],
        "proposal_key": proposal_key,
        "dataset_manifest": context_spec["manifest"],
        "analysis_context_id": context_id,
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
    return_code = _run_docker_job(conn, command, log_path, run_id)
    if return_code != 0:
        raise RuntimeError(f"analysis job exited with code {return_code}; log={log_path}")
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
                    "analysis_context_id": payload.get("analysis_id"),
                    "proposal_key": payload.get("proposal_key"),
                    "evidence_level": payload.get("evidence_level"),
                    "asset": payload.get("asset"),
                    "raw_rows": payload.get("coverage", {}).get("raw_rows"),
                    "valid_rows": payload.get("coverage", {}).get("valid_rows"),
                    "windows": payload.get("coverage", {}).get("windows", {}).get("windows"),
                    "periods": payload.get("coverage", {}).get("periods", []),
                    "candidate_windows_returned": payload.get("coverage", {}).get("candidate_windows_returned"),
                    "artifact_sha256": artifact_hash,
                    "holdout_accessed": False,
                },
                sort_keys=True,
            ),
        ),
    )
    conn.execute(
        "update lab_indicadores.runs set status='succeeded', heartbeat_at=clock_timestamp(), finished_at=clock_timestamp() where id=%s",
        (run_id,),
    )
    conn.execute(
        "update lab_indicadores.commands set status='completed', completed_at=clock_timestamp() where id=%s",
        (command_id,),
    )
    campaign_row = conn.execute(
        """
        select coalesce(r.campaign_id, p.campaign_id) as campaign_id
        from lab_indicadores.runs r
        left join lab_indicadores.proposals p on p.proposal_key=%s
        where r.id=%s
        """,
        (payload.get("proposal_key"), run_id),
    ).fetchone()
    if campaign_row and campaign_row["campaign_id"]:
        conn.execute(
            "update lab_indicadores.research_campaigns set status='awaiting_review', stage='gate' where id=%s",
            (campaign_row["campaign_id"],),
        )
        conn.execute(
            "update lab_indicadores.research_campaign_steps set status='awaiting_review', output=%s::jsonb, finished_at=clock_timestamp() where campaign_id=%s and run_id=%s",
            (json.dumps({"analysis_artifact_sha256": artifact_hash, "holdout_accessed": False}, sort_keys=True), campaign_row["campaign_id"], run_id),
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
    if command_type == "start_data_profile" and manifest not in ANALYSIS_MANIFESTS:
        _fail_run(conn, run_id, command_id, f"data profile manifest not allowed: {manifest}")
        return True
    if command_type == "start_analysis" and manifest not in ANALYSIS_MANIFESTS:
        _fail_run(conn, run_id, command_id, f"analysis manifest not allowed: {manifest}")
        return True

    _register_worker(conn, "busy", {"run_id": run_id})
    try:
        if command_type == "start_data_profile":
            _start_run(conn, run_id, "data_profile")
            result = _execute_data_profile(conn, run_id, command["payload"] or {})
            _succeed_data_profile(conn, run_id, command_id, result)
        elif command_type == "start_research":
            _start_run(conn, run_id, "research")
            result = _execute_research(conn, run_id, command["payload"] or {})
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
