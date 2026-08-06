"""Small deterministic preflight worker for the indicator laboratory."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path


MANIFEST_PATH = Path(
    os.environ.get(
        "INDICATOR_MANIFEST", "/app/manifests/indicator-lab-smoke-v1.json"
    )
)
CANONICAL_ROOT = Path(os.environ.get("CANONICAL_ROOT", "/data/canonical"))
RUNS_ROOT = Path("/app/runs")
LOGS_ROOT = Path("/app/logs")
WORK_ROOT = Path("/app/work")
WORKER_ID = os.environ.get("WORKER_ID", "lab-indicadores-worker")


def _load_manifest() -> dict:
    with MANIFEST_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _validate_manifest(manifest: dict) -> list[dict]:
    if manifest.get("project_id") != "lab-indicadores":
        raise ValueError("manifest project_id does not belong to this laboratory")
    if manifest.get("holdout_accessed") is not False:
        raise ValueError("manifest must declare holdout_accessed=false")

    checked: list[dict] = []
    for item in manifest.get("files", []):
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe dataset path: {relative}")
        if any(year in relative.as_posix() for year in ("2025", "2026")):
            raise ValueError(f"holdout path is not allowed: {relative}")

        path = CANONICAL_ROOT / relative
        if not path.is_file():
            raise FileNotFoundError(path)

        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            raise PermissionError(f"dataset file is writable: {path}")

        checked.append(
            {
                "path": str(relative),
                "bytes": path.stat().st_size,
                "expected_sha256": item["sha256"],
                "observed_mode": oct(mode),
            }
        )
    if not checked:
        raise ValueError("manifest contains no files")
    return checked


def healthcheck() -> None:
    required = (MANIFEST_PATH, CANONICAL_ROOT, RUNS_ROOT, LOGS_ROOT, WORK_ROOT)
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError("missing runtime paths: " + ", ".join(missing))
    _validate_manifest(_load_manifest())
    print("healthcheck=ok")


def smoke() -> None:
    manifest = _load_manifest()
    checked = _validate_manifest(manifest)
    run_id = manifest["run_id"]
    output_dir = RUNS_ROOT / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "run_id": run_id,
        "project_id": manifest["project_id"],
        "worker_id": WORKER_ID,
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": _stable_hash(manifest),
        "files_checked": checked,
        "holdout_accessed": False,
        "status": "succeeded",
    }
    payload["artifact_sha256"] = _stable_hash(payload)
    report_path = output_dir / "preflight-report.json"
    report_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": "succeeded", "report": str(report_path)}))


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "healthcheck"
    try:
        if command == "healthcheck":
            healthcheck()
        elif command == "smoke":
            smoke()
        else:
            raise ValueError(f"unknown command: {command}")
    except Exception as exc:  # noqa: BLE001 - CLI must emit a clear failure.
        print(f"status=failed error={exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
