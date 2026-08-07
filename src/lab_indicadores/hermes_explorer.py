"""Bounded, read-only DuckDB exploration for Hermes development data.

The model can choose a small number of semantic queries, but it cannot submit
SQL, file paths, columns, or arbitrary limits. This module maps an allowlisted
query kind to a fixed SQL template and returns bounded aggregates only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any


ALLOWED_QUERY_KINDS = {
    "trade_type_distribution",
    "hourly_activity",
    "minute_burst_profile",
    "agent_concentration",
}
MAX_QUERIES = 3
MAX_ROWS_PER_QUERY = 100
MAX_QUERY_SPAN_DAYS = 31
DATE_PATTERN = re.compile(r"^20(?:1[2-9]|2[0-4])-\d{2}-\d{2}$")
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$")
SAFE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{2,120}")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _stable_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _source_sql(paths: list[Path]) -> str:
    quoted = ",".join(_sql_quote(str(path)) for path in paths)
    return f"read_parquet([{quoted}], union_by_name=true, filename=true)"


def _validate_date(value: Any, field: str) -> str:
    if not isinstance(value, str) or not DATE_PATTERN.fullmatch(value):
        raise ValueError(f"{field} must be an eligible YYYY-MM-DD development date")
    parsed = date.fromisoformat(value)
    if parsed.year >= 2025:
        raise ValueError(f"{field} cannot access the holdout")
    return value


def _validate_query(query: Any, index: int) -> dict[str, Any]:
    if not isinstance(query, dict):
        raise ValueError(f"exploration query {index} must be an object")
    query_id = str(query.get("query_id") or "")
    if not SAFE_ID_PATTERN.fullmatch(query_id):
        raise ValueError(f"exploration query {index} has an unsafe query_id")
    kind = str(query.get("kind") or "")
    if kind not in ALLOWED_QUERY_KINDS:
        raise ValueError(f"exploration query kind is not allowlisted: {kind}")
    purpose = str(query.get("purpose") or "").strip()
    if not purpose or len(purpose) > 500:
        raise ValueError(f"exploration query {query_id} purpose is invalid")
    start_date = _validate_date(query.get("start_date"), "start_date")
    end_date = _validate_date(query.get("end_date"), "end_date")
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        raise ValueError(f"exploration query {query_id} date range is inverted")
    if (end - start).days > MAX_QUERY_SPAN_DAYS:
        raise ValueError(f"exploration query {query_id} exceeds the 31-day window limit")

    start_time = query.get("start_time")
    end_time = query.get("end_time")
    if start_time is not None:
        if not isinstance(start_time, str) or not TIME_PATTERN.fullmatch(start_time):
            raise ValueError(f"exploration query {query_id} start_time is invalid")
    if end_time is not None:
        if not isinstance(end_time, str) or not TIME_PATTERN.fullmatch(end_time):
            raise ValueError(f"exploration query {query_id} end_time is invalid")
    if start_time and end_time and start_time > end_time:
        raise ValueError(f"exploration query {query_id} time range is inverted")

    trade_type = query.get("trade_type")
    if trade_type is not None:
        trade_type = str(trade_type).strip()
        if not trade_type or len(trade_type) > 80:
            raise ValueError(f"exploration query {query_id} trade_type is invalid")

    top_n = query.get("top_n", 20)
    if isinstance(top_n, bool) or not isinstance(top_n, int) or not 1 <= top_n <= MAX_ROWS_PER_QUERY:
        raise ValueError(f"exploration query {query_id} top_n is invalid")

    return {
        "query_id": query_id,
        "kind": kind,
        "purpose": purpose,
        "start_date": start_date,
        "end_date": end_date,
        "start_time": start_time,
        "end_time": end_time,
        "trade_type": trade_type,
        "top_n": top_n,
    }


def validate_job(job: dict[str, Any]) -> list[dict[str, Any]]:
    if job.get("kind") != "hermes_exploration_job_v1":
        raise ValueError("unsupported Hermes exploration job kind")
    if job.get("project_id") != "lab-indicadores":
        raise ValueError("exploration job project mismatch")
    if job.get("holdout_accessed") is not False:
        raise ValueError("exploration job must declare holdout_accessed=false")
    if job.get("asset") not in {"WDO", "WIN"}:
        raise ValueError("exploration asset is not allowed")
    if not SAFE_ID_PATTERN.fullmatch(str(job.get("exploration_id") or "")):
        raise ValueError("exploration_id is unsafe")
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", str(job.get("run_id") or "")):
        raise ValueError("exploration run_id must be a UUID")
    if not re.fullmatch(r"[0-9a-f]{64}", str(job.get("source_profile_sha256") or "")):
        raise ValueError("source profile hash is invalid")

    files = job.get("files")
    if not isinstance(files, list) or not files or len(files) > 32:
        raise ValueError("exploration files are missing or exceed the limit")
    root = Path(os.environ.get("CANONICAL_ROOT", "/data/canonical")).resolve()
    for item in files:
        if not isinstance(item, dict) or item.get("asset") != job.get("asset"):
            raise ValueError("exploration file asset mismatch")
        relative = Path(str(item.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe exploration dataset path: {relative}")
        if any(re.search(r"(?:^|[-_=])(2025|2026)(?:[-_.]|$)", part) for part in relative.parts):
            raise ValueError(f"holdout path is not allowed: {relative}")
        path = (root / relative).resolve()
        if not path.is_file() or not path.is_relative_to(root):
            raise ValueError(f"exploration dataset file is unavailable: {relative}")

    queries = job.get("queries")
    if not isinstance(queries, list) or not 1 <= len(queries) <= MAX_QUERIES:
        raise ValueError(f"exploration must contain between 1 and {MAX_QUERIES} queries")
    normalized = [_validate_query(query, index) for index, query in enumerate(queries)]
    if len({query["query_id"] for query in normalized}) != len(normalized):
        raise ValueError("exploration query ids must be unique")
    return normalized


def _where_clause(query: dict[str, Any], columns: set[str]) -> str:
    predicates = [
        f"try_cast(date as date) between DATE {_sql_quote(query['start_date'])} and DATE {_sql_quote(query['end_date'])}"
    ]
    if query.get("start_time") and "time" in columns:
        predicates.append(f"try_cast(time as time) >= TIME {_sql_quote(query['start_time'])}")
    if query.get("end_time") and "time" in columns:
        predicates.append(f"try_cast(time as time) <= TIME {_sql_quote(query['end_time'])}")
    if query.get("trade_type") and "trade_type" in columns:
        predicates.append(
            "coalesce(nullif(cast(trade_type as varchar), ''), '__EMPTY__') = "
            + _sql_quote(query["trade_type"])
        )
    return " and ".join(predicates)


def _execute_query(conn, source: str, columns: set[str], query: dict[str, Any]) -> dict[str, Any]:
    required = {
        "trade_type_distribution": {"trade_type", "qty"},
        "hourly_activity": {"time", "qty"},
        "minute_burst_profile": {"date", "time", "qty", "price", "buy_agent", "sell_agent"},
        "agent_concentration": {"buy_agent", "sell_agent", "qty"},
    }[query["kind"]]
    missing = sorted(required - columns)
    if missing:
        return {
            "query_id": query["query_id"],
            "kind": query["kind"],
            "purpose": query["purpose"],
            "status": "skipped",
            "reason": "required_columns_missing",
            "missing_columns": missing,
            "rows": [],
        }

    where = _where_clause(query, columns)
    limit = query["top_n"]
    kind = query["kind"]
    if kind == "trade_type_distribution":
        sql = f"""
            select coalesce(nullif(cast(trade_type as varchar), ''), '__EMPTY__') as trade_type,
                   count(*) as raw_rows,
                   sum(try_cast(qty as double)) as qty_sum,
                   min(try_cast(price as double)) as price_min,
                   max(try_cast(price as double)) as price_max
            from {source}
            where {where}
            group by 1
            order by raw_rows desc, trade_type
            limit {limit}
        """
    elif kind == "hourly_activity":
        sql = f"""
            select try_cast(substr(cast(time as varchar), 1, 2) as integer) as hour,
                   count(*) as raw_rows,
                   sum(try_cast(qty as double)) as qty_sum
            from {source}
            where {where}
            group by 1
            having hour is not null
            order by hour
            limit {limit}
        """
    elif kind == "minute_burst_profile":
        sql = f"""
            select try_cast(concat(cast(date as varchar), ' ', substr(cast(time as varchar), 1, 8)) as timestamp) as minute_ts,
                   count(*) as raw_rows,
                   sum(try_cast(qty as double)) as qty_sum,
                   min(try_cast(price as double)) as price_min,
                   max(try_cast(price as double)) as price_max,
                   avg(try_cast(price as double)) as price_mean,
                   count(distinct nullif(cast(buy_agent as varchar), '')) as distinct_buy_agents,
                   count(distinct nullif(cast(sell_agent as varchar), '')) as distinct_sell_agents
            from {source}
            where {where}
            group by 1
            having minute_ts is not null
            order by qty_sum desc nulls last, raw_rows desc, minute_ts
            limit {limit}
        """
    else:
        sql = f"""
            select side, agent, raw_rows, qty_sum
            from (
              select 'buy' as side,
                     nullif(cast(buy_agent as varchar), '') as agent,
                     count(*) as raw_rows,
                     sum(try_cast(qty as double)) as qty_sum
              from {source}
              where {where} and nullif(cast(buy_agent as varchar), '') is not null
              group by 1, 2
              union all
              select 'sell' as side,
                     nullif(cast(sell_agent as varchar), '') as agent,
                     count(*) as raw_rows,
                     sum(try_cast(qty as double)) as qty_sum
              from {source}
              where {where} and nullif(cast(sell_agent as varchar), '') is not null
              group by 1, 2
            ) grouped
            order by raw_rows desc, side, agent
            limit {limit}
        """

    cursor = conn.execute(sql)
    names = [item[0] for item in cursor.description]
    rows = [{name: _json_safe(value) for name, value in zip(names, row, strict=True)} for row in cursor.fetchall()]
    return {
        "query_id": query["query_id"],
        "kind": query["kind"],
        "purpose": query["purpose"],
        "status": "succeeded",
        "rows": rows,
        "rows_returned": len(rows),
        "result_truncated": len(rows) >= limit,
    }


def run_exploration(job: dict[str, Any]) -> dict[str, str]:
    queries = validate_job(job)
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - image owns dependency.
        raise RuntimeError("duckdb dependency is unavailable in the worker image") from exc

    root = Path(os.environ.get("CANONICAL_ROOT", "/data/canonical")).resolve()
    paths = [(root / Path(str(item["path"]))).resolve() for item in job["files"]]
    source = _source_sql(paths)
    runs_root = Path(os.environ.get("RUNS_ROOT", "/app/runs"))
    output_dir = runs_root / str(job["run_id"])
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(database=":memory:")
    conn.execute("PRAGMA threads=1")
    conn.execute("PRAGMA memory_limit='1200MB'")
    try:
        schema_rows = conn.execute(f"describe select * from {source}").fetchall()
        columns = {str(row[0]).lower() for row in schema_rows}
        results = [_execute_query(conn, source, columns, query) for query in queries]
    finally:
        conn.close()

    payload_without_hash = {
        "kind": "hermes_exploration_report_v1",
        "project_id": "lab-indicadores",
        "run_id": job["run_id"],
        "exploration_id": job["exploration_id"],
        "asset": job["asset"],
        "track": job.get("track", "flow_price"),
        "horizon": job.get("horizon", "tactical_intraday"),
        "dataset_manifest": job["dataset_manifest"],
        "source_profile_sha256": job["source_profile_sha256"],
        "plan_sha256": job.get("plan_sha256"),
        "source_files": [
            {
                "path": item["path"],
                "bytes": item.get("bytes"),
                "sha256": item.get("sha256"),
            }
            for item in job["files"]
        ],
        "query_contract": {
            "max_queries": MAX_QUERIES,
            "max_rows_per_query": MAX_ROWS_PER_QUERY,
            "max_query_span_days": MAX_QUERY_SPAN_DAYS,
            "sql_submitted_by_model": False,
            "raw_rows_returned": False,
        },
        "queries": [
            {"spec": query, "result": result}
            for query, result in zip(queries, results, strict=True)
        ],
        "read_only_proof": {
            "canonical_root": str(root),
            "canonical_files_modified": False,
            "holdout_accessed": False,
        },
        "holdout_accessed": False,
    }
    payload = {
        **payload_without_hash,
        "exploration_sha256": _stable_hash(payload_without_hash),
        "status": "succeeded",
    }
    report_path = output_dir / "hermes-exploration.json"
    report_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return {"report": str(report_path), "exploration_sha256": payload["exploration_sha256"]}
