"""Generate an auditable Hermes research proposal from a frozen context.

The fixture provider intentionally proposes a hypothesis without looking at
results. It validates the control-plane contract; it is not evidence and does
not calculate an indicator. A future model provider can replace this adapter
only behind the same schema, hashes, and review gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path


AGENT_ID = "hermes-indicadores"
HERMES_ROOT = Path(os.environ.get("LAB_INDICADORES_HERMES_ROOT", "/srv/labs/projects/lab-b/hermes"))
CONTEXT_ROOT = HERMES_ROOT / "context"
PROPOSAL_ROOT = HERMES_ROOT / "outbox" / "proposals"
ALLOWED_MANIFEST = "hermes-context-absorption-v1"


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


def load_job(path: Path) -> dict:
    job = json.loads(path.read_text(encoding="utf-8"))
    if job.get("kind") != "hermes_research_job_v1":
        raise ValueError("unsupported research job kind")
    if job.get("agent_id") != AGENT_ID:
        raise ValueError("research job agent mismatch")
    if job.get("dataset_manifest") != ALLOWED_MANIFEST:
        raise ValueError("research manifest is not allowlisted")
    if not job.get("run_id") or not job.get("proposal_key"):
        raise ValueError("run_id and proposal_key are required")
    if Path(str(job["proposal_key"])).name != job["proposal_key"]:
        raise ValueError("proposal key contains a path separator")
    return job


def load_context(path_value: str) -> tuple[Path, dict]:
    path = Path(path_value).resolve()
    if not path.is_relative_to(CONTEXT_ROOT.resolve()):
        raise ValueError("context path is outside the Hermes context boundary")
    context = json.loads(path.read_text(encoding="utf-8"))
    if context.get("context_version") != "indicator-research-context-v1":
        raise ValueError("unsupported context version")
    if context.get("holdout_accessed") is not False:
        raise ValueError("context holdout contract failed")
    if context.get("asset") not in {"WDO", "WIN"}:
        raise ValueError("context asset must be WDO or WIN")
    return path, context


def build_fixture_proposal(job: dict, context_path: Path, context: dict) -> dict:
    validation_plan = {
        "eligible_period": context["eligible_period"],
        "features": context["features"],
        "separate_components": context["separate_components"],
        "baselines": context["baselines"],
        "nulls": context["nulls"],
        "multiplicity": context["multiplicity"],
        "gates": context["gates"],
        "pre_registration": True,
    }
    proposal = {
        "proposal_version": "hermes-proposal-v1",
        "proposal_type": "research_hypothesis",
        "claim_status": "HYPOTHESIS_ONLY",
        "verdict": "NOT_TESTED",
        "title": context["title"],
        "question": context["question"],
        "mechanism": context["mechanism"],
        "hypothesis": context["hypothesis"],
        "asset": context["asset"],
        "track": context["track"],
        "horizon": context["horizon"],
        "evidence_level": "not_tested",
        "validation_plan": validation_plan,
        "limitations": [
            "No market result was consulted by this proposal generator.",
            "The proposal is not a signal, entry, stop, target, or order.",
            "Absorption is neutral until a directional response is separately tested.",
            "Holdout 2025-2026 remains closed.",
        ],
        "source_context_uri": str(context_path),
        "holdout_accessed": False,
        "agent_id": AGENT_ID,
        "run_id": job["run_id"],
        "proposal_key": job["proposal_key"],
    }
    return proposal


def process_job(job_path: Path, provider: str) -> Path:
    job = load_job(job_path)
    context_path, context = load_context(job["context_path"])
    if provider != "fixture":
        raise RuntimeError("only the fixture provider is enabled in this isolated bootstrap")

    proposal = build_fixture_proposal(job, context_path, context)
    proposal_hash = sha256_bytes(canonical_json(proposal))
    artifact = {
        "kind": "hermes_proposal_v1",
        "agent_id": AGENT_ID,
        "run_id": job["run_id"],
        "proposal_key": job["proposal_key"],
        "dataset_manifest": ALLOWED_MANIFEST,
        "source_context_uri": str(context_path),
        "source_context_sha256": sha256_file(context_path),
        "proposal_sha256": proposal_hash,
        "holdout_accessed": False,
        "generated_by": {"provider": provider, "model": None},
        "proposal": proposal,
    }
    output_path = PROPOSAL_ROOT / f"{job['proposal_key']}.json"
    write_json_atomic(output_path, artifact)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--provider", default=os.environ.get("HERMES_PROVIDER", "fixture"))
    args = parser.parse_args()

    jobs = sorted(args.job_dir.glob("*.json"))
    if not jobs:
        return 0
    for job_path in jobs:
        process_job(job_path, args.provider)
        job_path.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
