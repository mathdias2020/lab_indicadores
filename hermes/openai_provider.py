"""OpenAI Responses provider for auditable Hermes hypothesis generation.

The provider sends only the versioned context and the aggregate development
data profile. Raw trades stay on the VPS and are never copied to the control
plane or sent to the model. The response is constrained with JSON Schema and
is validated again before it becomes a proposal artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ALLOWED_MODELS = {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}
ALLOWED_REASONING = {"none", "low", "medium", "high", "xhigh", "max"}


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "question": {"type": "string"},
        "mechanism": {"type": "string"},
        "hypothesis": {"type": "string"},
        "data_interpretation": {"type": "string"},
        "features": {"type": "array", "items": {"type": "string"}},
        "application_context": {"type": "string"},
        "attention_points": {"type": "array", "items": {"type": "string"}},
        "failure_criteria": {"type": "array", "items": {"type": "string"}},
        "next_test": {"type": "string"},
    },
    "required": [
        "title",
        "question",
        "mechanism",
        "hypothesis",
        "data_interpretation",
        "features",
        "application_context",
        "attention_points",
        "failure_criteria",
        "next_test",
    ],
    "additionalProperties": False,
}


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safety_identifier(agent_id: str) -> str:
    return hashlib.sha256(agent_id.encode("utf-8")).hexdigest()[:32]


def _profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile_id": profile.get("profile_id"),
        "profile_sha256": profile.get("profile_sha256"),
        "asset": profile.get("asset"),
        "dataset_manifest": profile.get("dataset_manifest"),
        "coverage": profile.get("coverage", {}),
        "data_understanding": profile.get("data_understanding", {}),
        "trade_type_counts": profile.get("trade_type_counts", [])[:20],
        "source_files": [
            {
                key: item.get(key)
                for key in (
                    "path",
                    "bytes",
                    "sha256",
                    "raw_rows",
                    "session_date_min",
                    "session_date_max",
                )
            }
            for item in profile.get("source_files", [])
        ],
        "holdout_accessed": profile.get("holdout_accessed"),
    }


def _request_response(payload: dict[str, Any], api_key: str, timeout: int) -> dict[str, Any]:
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    request = Request(
        f"{base_url}/responses",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:4000]
        raise RuntimeError(f"OpenAI API returned HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"OpenAI API network error: {exc.reason}") from exc


def _extract_output(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("status") == "incomplete":
        detail = response.get("incomplete_details") or {}
        raise RuntimeError(f"OpenAI response incomplete: {detail.get('reason', 'unknown')}")

    output_text = response.get("output_text")
    if not isinstance(output_text, str) or not output_text.strip():
        for item in response.get("output", []):
            for content in item.get("content", []) if isinstance(item, dict) else []:
                if content.get("type") == "refusal":
                    raise RuntimeError(f"OpenAI refused structured proposal: {content.get('refusal', '')}")
        raise RuntimeError("OpenAI response did not contain output_text")

    try:
        value = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenAI structured output was not valid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("OpenAI structured output must be an object")
    for field in OUTPUT_SCHEMA["required"]:
        if field not in value:
            raise RuntimeError(f"OpenAI structured output missing field: {field}")
    for field in ("features", "attention_points", "failure_criteria"):
        if not isinstance(value[field], list) or not all(isinstance(item, str) for item in value[field]):
            raise RuntimeError(f"OpenAI structured output field is invalid: {field}")
    return value


def generate_openai_proposal(
    *,
    job: dict[str, Any],
    context: dict[str, Any],
    profile: dict[str, Any],
    parent_proposal: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured for the Hermes engine")

    model = str(job.get("model") or "gpt-5.6-sol")
    reasoning_effort = str(job.get("reasoning_effort") or "medium")
    if model not in ALLOWED_MODELS:
        raise ValueError(f"OpenAI model is not allowlisted: {model}")
    if reasoning_effort not in ALLOWED_REASONING:
        raise ValueError(f"OpenAI reasoning effort is not allowlisted: {reasoning_effort}")
    if profile.get("holdout_accessed") is not False:
        raise ValueError("OpenAI proposal cannot use a profile that accessed holdout")

    request_context = {
        "asset": context.get("asset"),
        "track": context.get("track"),
        "horizon": context.get("horizon"),
        "title": context.get("title"),
        "question": context.get("question"),
        "mechanism": context.get("mechanism"),
        "hypothesis": context.get("hypothesis"),
        "features": context.get("features"),
        "baselines": context.get("baselines"),
        "nulls": context.get("nulls"),
        "multiplicity": context.get("multiplicity"),
        "gates": context.get("gates"),
        "eligible_period": context.get("eligible_period"),
    }
    feedback_error = str(job.get("feedback_error") or "").strip() or None
    user_payload = {
        "research_context": request_context,
        "data_profile": _profile_summary(profile),
        "error_review": {
            "feedback_error": feedback_error,
            "parent_proposal": parent_proposal,
        },
    }
    instructions = (
        "Você é Hermes, pesquisador do laboratório de indicadores. Proponha uma "
        "hipótese explicável e testável a partir do contexto versionado e do perfil "
        "agregado dos dados de desenvolvimento. Não invente campos, não atribua "
        "causalidade ao perfil, não prometa lucro e não transforme a resposta em "
        "ordem, entrada, stop ou alvo. Preserve ativo, trilha e horizonte do contexto. "
        "Separe observação do dado, interpretação, hipótese e próximo teste. "
        "Se houver erro anterior, revise a hipótese somente de forma proporcional ao "
        "erro e mantenha o mecanismo original quando ele ainda for falsificável."
    )
    payload = {
        "model": model,
        "store": False,
        "instructions": instructions,
        "input": _canonical_json(user_payload),
        "reasoning": {"effort": reasoning_effort, "context": "current_turn"},
        "max_output_tokens": 3000,
        "safety_identifier": _safety_identifier(str(job.get("agent_id", "hermes-indicadores"))),
        "metadata": {
            "agent_id": str(job.get("agent_id", "hermes-indicadores")),
            "run_id": str(job.get("run_id", "")),
            "profile_sha256": str(profile.get("profile_sha256", "")),
        },
        "text": {
            "format": {
                "type": "json_schema",
                "name": "indicator_hypothesis",
                "strict": True,
                "schema": OUTPUT_SCHEMA,
            }
        },
    }
    response = _request_response(
        payload,
        api_key,
        int(os.environ.get("OPENAI_TIMEOUT_SECONDS", "120")),
    )
    output = _extract_output(response)
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    metadata = {
        "provider": "openai",
        "model": str(response.get("model") or model),
        "response_id": response.get("id"),
        "reasoning_effort": reasoning_effort,
        "usage": usage,
        "store": False,
    }
    return output, metadata
