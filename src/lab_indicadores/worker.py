"""Small deterministic preflight worker for the indicator laboratory."""

from __future__ import annotations

import hashlib
import json
import re
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
ANALYSIS_SPECS = {
    "absorption-descriptive-baseline-v1": {
        "dataset_manifest": "hermes-analysis-absorption-v1",
        "sample_design": "single preregistered January slice",
    },
    "absorption-descriptive-multi-period-wdo-v1": {
        "dataset_manifest": "hermes-analysis-absorption-multi-period-wdo-v1",
        "sample_design": "three preregistered January slices",
    },
    "absorption-descriptive-multi-period-win-v1": {
        "dataset_manifest": "hermes-analysis-absorption-multi-period-win-v1",
        "sample_design": "three preregistered January slices",
    },
}
ANALYSIS_MANIFESTS = {item["dataset_manifest"] for item in ANALYSIS_SPECS.values()}
ANALYSIS_ROOT = WORK_ROOT / "analysis"


def _load_manifest() -> dict:
    with MANIFEST_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _rows_as_dicts(cursor) -> list[dict]:
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _validate_analysis_job(job: dict) -> list[dict]:
    if job.get("kind") != "indicator_analysis_job_v1":
        raise ValueError("unsupported analysis job kind")
    if job.get("project_id") != "lab-indicadores":
        raise ValueError("analysis job project_id does not belong to this laboratory")
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", str(job.get("run_id", ""))):
        raise ValueError("analysis run_id must be a UUID")
    analysis_id = job.get("analysis_id")
    spec = ANALYSIS_SPECS.get(analysis_id)
    if not spec:
        raise ValueError("analysis id is not allowed")
    if job.get("dataset_manifest") != spec["dataset_manifest"]:
        raise ValueError("analysis manifest does not match analysis id")
    if job.get("holdout_accessed") is not False:
        raise ValueError("analysis job must declare holdout_accessed=false")
    if job.get("asset") not in {"WDO", "WIN"}:
        raise ValueError("analysis asset is not allowed")

    root = CANONICAL_ROOT.resolve()
    checked: list[dict] = []
    for item in job.get("files", []):
        if item.get("asset") != job.get("asset"):
            raise ValueError("analysis file asset does not match job asset")
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe analysis dataset path: {relative}")
        if any(re.search(r"(?:^|[-_=])(2025|2026)(?:[-_.]|$)", part) for part in relative.parts):
            raise ValueError(f"holdout path is not allowed: {relative}")
        path = (CANONICAL_ROOT / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"analysis path escaped canonical root: {relative}")
        if not path.is_file():
            raise FileNotFoundError(path)
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            raise PermissionError(f"dataset file is writable: {path}")
        expected_bytes = item.get("bytes")
        if expected_bytes is not None and path.stat().st_size != expected_bytes:
            raise ValueError(f"dataset byte count changed: {relative}")
        expected_sha256 = item.get("sha256")
        if expected_sha256 and _file_sha256(path) != expected_sha256:
            raise ValueError(f"dataset hash changed: {relative}")
        checked.append(
            {
                "asset": item.get("asset"),
                "path": str(relative),
                "bytes": path.stat().st_size,
                "sha256": expected_sha256 or _file_sha256(path),
                "observed_mode": oct(mode),
            }
        )
    if not checked:
        raise ValueError("analysis job contains no files")
    return checked


def _run_absorption_analysis(job: dict, checked: list[dict]) -> dict:
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - image build owns this dependency.
        raise RuntimeError("duckdb dependency is unavailable in the worker image") from exc

    run_id = job["run_id"]
    output_dir = RUNS_ROOT / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = ANALYSIS_ROOT / "duckdb-tmp" / run_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    parameters = job.get("parameters") or {}
    window_seconds = int(parameters.get("window_seconds", 60))
    if window_seconds != 60:
        raise ValueError("only the preregistered 60-second window is enabled")
    min_trade_count = int(parameters.get("min_trade_count", 10))
    aggression_quantile = float(parameters.get("aggression_quantile", 0.95))
    displacement_quantile = float(parameters.get("displacement_quantile", 0.35))
    hhi_min = float(parameters.get("player_concentration_hhi_min", 0.20))
    persistence_min = float(parameters.get("level_persistence_min", 0.50))
    if not 0 < displacement_quantile < 1 or not 0 < aggression_quantile < 1:
        raise ValueError("quantile parameters must be between zero and one")

    source_paths = [str(Path("/data/canonical") / item["path"]) for item in checked]
    source_sql = "read_parquet([" + ",".join(_sql_quote(path) for path in source_paths) + "], union_by_name=true, filename=true)"
    conn = duckdb.connect(database=":memory:")
    conn.execute("PRAGMA threads=1")
    conn.execute("PRAGMA memory_limit='1400MB'")
    conn.execute(f"PRAGMA temp_directory={_sql_quote(str(temp_dir))}")
    try:
        conn.execute(
            f"""
            create temp table raw_trades as
            select
              regexp_extract(filename, '([0-9]{{4}}-[0-9]{{2}})', 1) as period_id,
              filename as source_file,
              try_cast(date as date) as session_date,
              try_cast(concat(cast(date as varchar), ' ', time) as timestamp) as event_ts,
              try_cast(trade_number as bigint) as trade_number,
              try_cast(price as double) as price,
              try_cast(qty as double) as qty,
              coalesce(trade_type, '') as trade_type,
              coalesce(buy_agent, '') as buy_agent,
              coalesce(sell_agent, '') as sell_agent,
              case
                when trade_type = 'AggressorBuyer' then 1
                when trade_type = 'AggressorSeller' then -1
                else 0
              end as aggression_sign,
              case
                when trade_type = 'AggressorBuyer' then coalesce(nullif(buy_agent, ''), 'UNKNOWN')
                when trade_type = 'AggressorSeller' then coalesce(nullif(sell_agent, ''), 'UNKNOWN')
                else 'NON_AGGRESSIVE'
              end as aggressor_agent
            from {source_sql}
            """
        )
        conn.execute(
            """
            create temp table valid_trades as
            select * from raw_trades
            where session_date is not null
              and event_ts is not null
              and price is not null
              and qty is not null
              and qty >= 0
            """
        )
        conn.execute(
            """
            create temp table minute_windows as
            select
              period_id,
              date_trunc('minute', event_ts) as window_start,
              min(session_date) as session_date,
              first(price order by event_ts, trade_number) as price_open,
              last(price order by event_ts, trade_number) as price_close,
              min(price) as price_low,
              max(price) as price_high,
              count(*) as trade_count,
              count(*) filter (where aggression_sign <> 0) as aggression_trade_count,
              sum(case when aggression_sign = 1 then qty else 0 end) as aggression_buy_qty,
              sum(case when aggression_sign = -1 then qty else 0 end) as aggression_sell_qty,
              sum(case when aggression_sign <> 0 then qty else 0 end) as aggression_abs_qty,
              sum(qty * aggression_sign) as aggression_net_qty,
              sum(case when trade_type = 'Auction' then qty else 0 end) as auction_qty,
              sum(case when trade_type = 'CrossTrade' then qty else 0 end) as cross_trade_qty
            from valid_trades
            group by 1, 2
            """
        )
        conn.execute(
            """
            create temp table player_concentration as
            with by_player as (
              select period_id, date_trunc('minute', event_ts) as window_start,
                     aggressor_agent,
                     sum(qty) as player_qty
              from valid_trades
              where aggression_sign <> 0
              group by 1, 2, 3
            ), totals as (
              select *, sum(player_qty) over (partition by period_id, window_start) as total_qty
              from by_player
            )
            select period_id, window_start,
                   sum(power(player_qty / nullif(total_qty, 0), 2)) as player_concentration_hhi
            from totals
            group by 1, 2
            """
        )
        conn.execute(
            """
            create temp table level_persistence as
            with by_level as (
              select period_id, date_trunc('minute', event_ts) as window_start,
                     price,
                     sum(qty) as level_qty
              from valid_trades
              where aggression_sign <> 0
              group by 1, 2, 3
            )
            select period_id, window_start,
                   max(level_qty) / nullif(sum(level_qty), 0) as level_persistence_share
            from by_level
            group by 1, 2
            """
        )
        conn.execute(
            """
            create temp table metrics as
            select
              w.*,
              abs(w.price_close - w.price_open) as price_displacement,
              abs(w.price_high - w.price_low) as price_range,
              abs(w.aggression_net_qty) / nullif(w.aggression_abs_qty, 0) as aggression_imbalance,
              coalesce(pc.player_concentration_hhi, 0) as player_concentration_hhi,
              coalesce(lp.level_persistence_share, 0) as level_persistence_share
            from minute_windows w
            left join player_concentration pc using (period_id, window_start)
            left join level_persistence lp using (period_id, window_start)
            """
        )
        thresholds = conn.execute(
            f"""
            select
              approx_quantile(aggression_abs_qty, {aggression_quantile}) as aggression_abs_qty_threshold,
              approx_quantile(price_displacement, {displacement_quantile}) as price_displacement_threshold
            from metrics
            where aggression_abs_qty > 0
            """
        ).fetchone()
        if not thresholds or thresholds[0] is None or thresholds[1] is None:
            raise RuntimeError("analysis has no non-empty aggression windows")
        aggression_threshold, displacement_threshold = thresholds
        candidates_cursor = conn.execute(
            """
            select
              period_id,
              session_date,
              window_start,
              price_open,
              price_close,
              price_displacement,
              price_range,
              aggression_buy_qty,
              aggression_sell_qty,
              aggression_abs_qty,
              aggression_net_qty,
              aggression_imbalance,
              player_concentration_hhi,
              level_persistence_share,
              auction_qty,
              cross_trade_qty,
              trade_count,
              aggression_trade_count,
              case when aggression_net_qty > 0 then 'buyer'
                   when aggression_net_qty < 0 then 'seller'
                   else 'balanced' end as dominant_aggression
            from metrics
            where trade_count >= ?
              and aggression_abs_qty >= ?
              and price_displacement <= ?
              and player_concentration_hhi >= ?
              and level_persistence_share >= ?
            order by aggression_abs_qty desc, window_start
            limit 100
            """,
            [min_trade_count, aggression_threshold, displacement_threshold, hhi_min, persistence_min],
        )
        candidates = _rows_as_dicts(candidates_cursor)
        for row in candidates:
            for key, value in row.items():
                if hasattr(value, "isoformat"):
                    row[key] = value.isoformat()

        raw_rows = conn.execute("select count(*) from raw_trades").fetchone()[0]
        valid_rows = conn.execute("select count(*) from valid_trades").fetchone()[0]
        metrics_row = conn.execute(
            """
            select
              count(*) as windows,
              min(window_start) as first_window,
              max(window_start) as last_window,
              count(*) filter (where aggression_abs_qty > 0) as aggression_windows,
              avg(aggression_abs_qty) filter (where aggression_abs_qty > 0) as mean_aggression_abs_qty,
              avg(price_displacement) as mean_price_displacement
            from metrics
            """
        ).fetchone()
        metric_columns = [item[0] for item in conn.description]
        metric_summary = dict(zip(metric_columns, metrics_row, strict=True))
        for key, value in metric_summary.items():
            if hasattr(value, "isoformat"):
                metric_summary[key] = value.isoformat()

        type_rows = _rows_as_dicts(conn.execute("select trade_type, count(*) as rows from raw_trades group by 1 order by rows desc"))
        period_rows = _rows_as_dicts(
            conn.execute(
                """
                select
                  period_id,
                  count(*) as windows,
                  min(window_start) as first_window,
                  max(window_start) as last_window,
                  count(*) filter (where aggression_abs_qty > 0) as aggression_windows,
                  avg(aggression_abs_qty) filter (where aggression_abs_qty > 0) as mean_aggression_abs_qty,
                  avg(price_displacement) as mean_price_displacement,
                  count(*) filter (
                    where trade_count >= ?
                      and aggression_abs_qty >= ?
                      and price_displacement <= ?
                      and player_concentration_hhi >= ?
                      and level_persistence_share >= ?
                  ) as candidate_windows
                from metrics
                group by 1
                order by 1
                """,
                [min_trade_count, aggression_threshold, displacement_threshold, hhi_min, persistence_min],
            )
        )
        for period in period_rows:
            for key, value in period.items():
                if hasattr(value, "isoformat"):
                    period[key] = value.isoformat()
        payload = {
            "kind": "indicator_analysis_report_v1",
            "project_id": "lab-indicadores",
            "analysis_id": job["analysis_id"],
            "run_id": run_id,
            "proposal_key": job["proposal_key"],
            "indicator_id": "absorption-baseline-v1",
            "asset": job["asset"],
            "track": job["track"],
            "horizon": job["horizon"],
            "evidence_level": "descriptive",
            "verdict": "DESCRIPTIVE_CANDIDATES_ONLY",
            "status": "succeeded",
            "dataset_manifest": job["dataset_manifest"],
            "files": checked,
            "parameters": {
                "window_seconds": window_seconds,
                "min_trade_count": min_trade_count,
                "aggression_quantile": aggression_quantile,
                "displacement_quantile": displacement_quantile,
                "player_concentration_hhi_min": hhi_min,
                "level_persistence_min": persistence_min,
            },
            "thresholds": {
                "aggression_abs_qty": aggression_threshold,
                "price_displacement": displacement_threshold,
            },
            "coverage": {
                "raw_rows": raw_rows,
                "valid_rows": valid_rows,
                "invalid_rows": raw_rows - valid_rows,
                "trade_types": type_rows,
                "windows": metric_summary,
                "periods": period_rows,
                "candidate_windows_returned": len(candidates),
                "passive_net": None,
                "rlp": None,
                "direct_trades": "cross_trade_only",
            },
            "candidate_events": candidates,
            "limitations": [
                "descriptive event detection only; no future label was computed",
                "passive order book, RLP and direct-trade semantics are not present in this raw contract",
                "thresholds are distributional within the declared analysis scope and are not an out-of-sample claim",
                "period slices are representative monthly samples, not a full-year census",
                "no automatic indicator promotion or profit claim",
            ],
            "holdout_accessed": False,
            "engine": {"duckdb_version": duckdb.__version__, "threads": 1},
        }
        payload["artifact_sha256"] = _stable_hash(payload)
        report_path = output_dir / "analysis-report.json"
        report_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        return {"report": str(report_path), "candidate_count": len(candidates)}
    finally:
        conn.close()


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


def analysis(job_path: str) -> None:
    path = Path(job_path).resolve()
    inbox = (ANALYSIS_ROOT / "inbox").resolve()
    if not path.is_relative_to(inbox):
        raise ValueError("analysis job path is outside the laboratory inbox")
    job = json.loads(path.read_text(encoding="utf-8"))
    checked = _validate_analysis_job(job)
    result = _run_absorption_analysis(job, checked)
    print(json.dumps({"status": "succeeded", **result}, sort_keys=True))


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "healthcheck"
    try:
        if command == "healthcheck":
            healthcheck()
        elif command == "smoke":
            smoke()
        elif command == "analysis":
            if len(argv) != 3:
                raise ValueError("analysis requires a job path")
            analysis(argv[2])
        else:
            raise ValueError(f"unknown command: {command}")
    except Exception as exc:  # noqa: BLE001 - CLI must emit a clear failure.
        print(f"status=failed error={exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
