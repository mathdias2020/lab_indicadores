"""Generate an auditable Hermes proposal after a read-only data profile.

The current provider is still a deterministic fixture, but campaign jobs now
carry a hash-checked profile generated from the declared development Parquets.
That profile is the input seam for a future model provider. It never includes
holdout data and it never mutates an earlier proposal.
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
ALLOWED_PROFILE_MANIFESTS = {
    "hermes-analysis-absorption-v1",
    "hermes-analysis-absorption-multi-period-wdo-v1",
    "hermes-analysis-absorption-multi-period-win-v1",
}
PROFILE_ROOT = HERMES_ROOT / "outbox" / "profiles"


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
    if job.get("dataset_manifest") not in {ALLOWED_MANIFEST, *ALLOWED_PROFILE_MANIFESTS}:
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


def load_profile(path_value: str, expected_sha256: str, expected_artifact_sha256: str | None = None) -> tuple[Path, dict]:
    path = Path(path_value).resolve()
    if not path.is_relative_to(PROFILE_ROOT.resolve()):
        raise ValueError("data profile is outside the Hermes profile boundary")
    if not path.is_file():
        raise FileNotFoundError(path)
    if expected_artifact_sha256 and sha256_file(path) != expected_artifact_sha256:
        raise ValueError("data profile hash mismatch")
    profile = json.loads(path.read_text(encoding="utf-8"))
    if profile.get("kind") != "hermes_data_profile_v1":
        raise ValueError("unsupported data profile kind")
    if profile.get("holdout_accessed") is not False:
        raise ValueError("data profile holdout contract failed")
    if profile.get("profile_sha256") != expected_sha256:
        raise ValueError("data profile content hash mismatch")
    if profile.get("dataset_manifest") not in ALLOWED_PROFILE_MANIFESTS:
        raise ValueError("data profile manifest is not allowlisted")
    return path, profile


def build_fixture_proposal(job: dict, context_path: Path, context: dict, profile_path: Path | None, profile: dict | None) -> dict:
    revision_no = int(job.get("revision_no", 1))
    change_kind = str(job.get("change_kind") or "initial")
    feedback_error = str(job.get("feedback_error") or "").strip() or None
    data_understanding = (profile or {}).get("data_understanding", {})
    coverage = (profile or {}).get("coverage", {})
    profile_sha256 = (profile or {}).get("profile_sha256")
    profile_summary = {
        "profile_id": (profile or {}).get("profile_id"),
        "profile_sha256": profile_sha256,
        "asset": (profile or {}).get("asset"),
        "dataset_manifest": (profile or {}).get("dataset_manifest"),
        "coverage": coverage,
        "trade_type_counts": (profile or {}).get("trade_type_counts", [])[:20],
        "data_understanding": data_understanding,
        "source_files": [
            {key: item.get(key) for key in ("path", "bytes", "sha256", "raw_rows", "session_date_min", "session_date_max")}
            for item in (profile or {}).get("source_files", [])
        ],
    }
    revised_hypothesis = context["hypothesis"]
    if change_kind == "error_review":
        revised_hypothesis = (
            f"Revisar a hipótese original após a falha registrada ({feedback_error or 'erro sem detalhe'}): "
            "manter o mecanismo como hipótese, mas testar primeiro a cobertura, a qualidade dos campos "
            "e a sensibilidade dos limiares declarados antes de propor qualquer promoção."
        )
    validation_plan = {
        "eligible_period": context["eligible_period"],
        "features": context["features"],
        "separate_components": context["separate_components"],
        "baselines": context["baselines"],
        "nulls": context["nulls"],
        "multiplicity": context["multiplicity"],
        "gates": context["gates"],
        "pre_registration": True,
        "data_profile_sha256": profile_sha256,
        "error_review": feedback_error is not None,
    }
    proposal = {
        "proposal_version": "hermes-proposal-v2" if profile else "hermes-proposal-v1",
        "proposal_type": "research_hypothesis",
        "claim_status": "HYPOTHESIS_ONLY",
        "verdict": "NOT_TESTED",
        "title": ("Revisão Hermes — " + context["title"]) if change_kind == "error_review" else context["title"],
        "question": context["question"],
        "mechanism": context["mechanism"],
        "hypothesis": revised_hypothesis,
        "asset": context["asset"],
        "track": context["track"],
        "horizon": context["horizon"],
        "evidence_level": "not_tested",
        "validation_plan": validation_plan,
        "limitations": [
            "The generator consulted only a deterministic profile of declared development Parquets; it did not copy raw trades into the control plane.",
            "The proposal is not a signal, entry, stop, target, or order.",
            "Absorption is neutral until a directional response is separately tested.",
            "Holdout 2025-2026 remains closed.",
        ],
        "source_context_uri": str(context_path),
        "data_profile_uri": str(profile_path) if profile_path else None,
        "data_profile_id": job.get("data_profile_id"),
        "data_profile_sha256": profile_sha256,
        "data_profile_summary": profile_summary if profile else None,
        "revision_no": revision_no,
        "change_kind": change_kind,
        "parent_proposal_key": job.get("parent_proposal_key"),
        "feedback_run_id": job.get("feedback_run_id"),
        "feedback_error": feedback_error,
        "campaign_id": job.get("campaign_id"),
        "holdout_accessed": False,
        "agent_id": AGENT_ID,
        "run_id": job["run_id"],
        "proposal_key": job["proposal_key"],
    }
    return proposal


def process_job(job_path: Path, provider: str) -> Path:
    job = load_job(job_path)
    context_path, context = load_context(job["context_path"])
    profile_path = None
    profile = None
    if job.get("data_profile_path"):
        profile_path, profile = load_profile(
            job["data_profile_path"],
            str(job.get("data_profile_sha256") or ""),
            str(job.get("data_profile_artifact_sha256") or "") or None,
        )
    elif job.get("research_mode") == "campaign":
        raise ValueError("campaign research requires a data profile")
    if provider != "fixture":
        raise RuntimeError("only the fixture provider is enabled in this isolated bootstrap")

    proposal = build_fixture_proposal(job, context_path, context, profile_path, profile)
    proposal_hash = sha256_bytes(canonical_json(proposal))
    artifact = {
        "kind": "hermes_proposal_v2" if profile else "hermes_proposal_v1",
        "agent_id": AGENT_ID,
        "run_id": job["run_id"],
        "proposal_key": job["proposal_key"],
        "dataset_manifest": job["dataset_manifest"],
        "source_context_uri": str(context_path),
        "source_context_sha256": sha256_file(context_path),
        "data_profile_uri": str(profile_path) if profile_path else None,
        "data_profile_sha256": profile.get("profile_sha256") if profile else None,
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
