"""
core/llm_client.py – Azure OpenAI low-level infrastructure.

_invoke_json_task and its supporting helpers live here so that
core/analyzer.py can import them without creating a circular dependency
with llm.py.
"""

import json
import os
from typing import Any

try:
    from openai import AzureOpenAI
except ModuleNotFoundError:  # pragma: no cover - optional dependency in tests
    class AzureOpenAI:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            raise ModuleNotFoundError("openai SDK is missing; please install requirements.")

from core.config import get_config
from core.models import BaseModel, LLMResult, ValidationError
from core.evidence import _neutralize_legal_tone, _coerce_non_negative_int


def _get_client() -> tuple[AzureOpenAI, str]:
    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
    api_key = os.environ["AZURE_OPENAI_API_KEY"]
    api_version = get_config().azure_openai_api_version
    deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]

    client = AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
    )
    return client, deployment


def _trim_text_for_budget(text: str) -> tuple[str, bool]:
    max_chars = get_config().input_max_chars
    if max_chars <= 0:
        return text, False
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _parse_response_json(content: str) -> tuple[dict | None, str | None]:
    try:
        return json.loads(content), None
    except json.JSONDecodeError:
        pass

    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = content[start : end + 1]
        try:
            return json.loads(candidate), None
        except json.JSONDecodeError as exc:
            return None, f"JSONDecodeError: {exc}"
    return None, "No JSON object found in the model output."


def _ensure_env() -> None:
    missing = [
        v
        for v in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT")
        if not os.getenv(v)
    ]
    if missing:
        raise EnvironmentError(
            f"Missing environment variables: {', '.join(missing)}. "
            "Please copy .env.example to .env and fill it in."
        )


def _read_usage_value(usage: Any, *keys: str) -> int | None:
    for key in keys:
        if isinstance(usage, dict):
            if key in usage and usage.get(key) is not None:
                num = _coerce_non_negative_int(usage.get(key))
                if num is not None:
                    return num
            continue
        value = getattr(usage, key, None)
        num = _coerce_non_negative_int(value)
        if num is not None:
            return num
    return None


def _extract_usage_counts(response: Any) -> tuple[int | None, int | None, int | None]:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return None, None, None

    prompt_tokens = _read_usage_value(usage, "prompt_tokens", "input_tokens")
    completion_tokens = _read_usage_value(usage, "completion_tokens", "output_tokens")
    total_tokens = _read_usage_value(usage, "total_tokens")

    if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
        total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

    return total_tokens, prompt_tokens, completion_tokens


def _invoke_json_task(
    *,
    text: str,
    system_prompt: str,
    model_schema: type[BaseModel],
    max_output_tokens: int,
) -> LLMResult:
    _ensure_env()
    client, deployment = _get_client()
    text_for_model, input_truncated = _trim_text_for_budget(text)

    try:
        base_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text_for_model},
        ]
        response = client.chat.completions.create(
            model=deployment,
            messages=base_messages,
            temperature=0.0,
            max_tokens=max_output_tokens,
        )

        content = response.choices[0].message.content or ""
        finish_reason = response.choices[0].finish_reason
        total_tokens, prompt_tokens, completion_tokens = _extract_usage_counts(response)
        token_count = total_tokens or 0
        has_token_usage = total_tokens is not None
        model_id = response.model or deployment

        # If the output was truncated (finish_reason=length), retry once with a
        # higher token budget to avoid a broken/incomplete JSON response.
        if finish_reason == "length":
            retry_tokens = max(max_output_tokens * 2, 2200)
            retry_response = client.chat.completions.create(
                model=deployment,
                messages=base_messages
                + [
                    {
                        "role": "user",
                        "content": (
                            "The previous answer was truncated. "
                            "Output the complete JSON again, exactly matching the schema, "
                            "with no additional text."
                        ),
                    }
                ],
                temperature=0.0,
                max_tokens=retry_tokens,
            )
            content = retry_response.choices[0].message.content or content
            retry_total, retry_prompt, retry_completion = _extract_usage_counts(retry_response)
            if retry_total is not None:
                token_count = retry_total
                has_token_usage = True
            if retry_prompt is not None:
                prompt_tokens = retry_prompt
            if retry_completion is not None:
                completion_tokens = retry_completion
            model_id = retry_response.model or model_id

        parsed_json, parse_error = _parse_response_json(content)
        if parsed_json is not None:
            try:
                model = model_schema.model_validate(parsed_json)
                parsed = _neutralize_legal_tone(model.model_dump())
                return LLMResult(
                    parsed=parsed,
                    token_count=token_count,
                    model=model_id,
                    output_type="json_ok",
                    input_chars=len(text_for_model),
                    input_truncated=input_truncated,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    has_token_usage=has_token_usage,
                )
            except ValidationError as exc:
                parsed = {
                    "raw_text": content,
                    "parse_error": True,
                    "message": f"JSON schema invalid: {exc.errors()[0].get('msg', 'ValidationError')}",
                }
                return LLMResult(
                    parsed=parsed,
                    token_count=token_count,
                    model=model_id,
                    output_type="json_fallback",
                    input_chars=len(text_for_model),
                    input_truncated=input_truncated,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    has_token_usage=has_token_usage,
                )

        return LLMResult(
            parsed={
                "raw_text": content,
                "parse_error": True,
                "message": parse_error or "JSON schema invalid.",
            },
            token_count=token_count,
            model=model_id,
            output_type="json_fallback",
            input_chars=len(text_for_model),
            input_truncated=input_truncated,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            has_token_usage=has_token_usage,
        )

    except Exception as exc:
        cause = getattr(exc, "__cause__", None)
        detail = str(exc)
        if cause:
            detail = f"{detail} | cause: {type(cause).__name__}: {cause}"
        return LLMResult(
            parsed={"api_error": True, "message": detail},
            token_count=0,
            model=deployment,
            output_type="api_error",
            input_chars=len(text_for_model),
            input_truncated=input_truncated,
            has_token_usage=False,
        )
