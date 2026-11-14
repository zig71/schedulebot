"""The Groq transport — the only code that talks to a model.

Deliberately dumb: build the request, send it, hand the JSON to `parse_capture`.
No retries that hide a failure, no "the model said something nearly right" repair
passes. If the response does not satisfy the schema, that is an error with the
response attached, not something to paper over.

`httpx.Client` is injectable so tests exercise the real request-building and
response-handling with no network and no key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from ..categories import Category
from .contract import CAPTURE_SCHEMA, CaptureError, CaptureResult, parse_capture
from .prompt import build_system_prompt, build_user_message


class LlmError(RuntimeError):
    """The model could not be reached, or answered outside the contract."""


@dataclass(frozen=True, slots=True)
class GroqConfig:
    api_key: str
    model: str = "qwen/qwen3.8-27b"
    base_url: str = "https://api.groq.com/openai/v1"
    timeout_seconds: float = 60.0
    max_tokens: int = 4096

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("api_key must not be empty")


def _body(config: GroqConfig, system: str, user: str) -> dict[str, Any]:
    return {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        # Deterministic as far as the endpoint allows: a plan that changes because
        # of sampling noise is a plan the user cannot trust.
        "temperature": 0,
        "max_tokens": config.max_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "capture", "schema": CAPTURE_SCHEMA, "strict": True},
        },
    }


def _content(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise LlmError(f"model endpoint returned non-JSON: {response.text[:300]}") from exc
    if not isinstance(payload, dict):
        raise LlmError("model endpoint returned an unexpected body")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LlmError(f"model endpoint returned no choices: {json.dumps(payload)[:300]}")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LlmError("model returned an empty message")
    return content


class GroqClient:
    """Capture, over an OpenAI-compatible chat-completions endpoint."""

    def __init__(self, config: GroqConfig, *, client: httpx.Client | None = None) -> None:
        self._config = config
        self._client = client or httpx.Client(
            base_url=config.base_url, timeout=config.timeout_seconds
        )

    def __enter__(self) -> GroqClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _send(self, body: dict[str, Any]) -> httpx.Response:
        try:
            return self._client.post(
                "/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self._config.api_key}"},
            )
        except httpx.HTTPError as exc:
            raise LlmError(f"could not reach the model endpoint: {exc}") from exc

    def capture(
        self, note: str, *, now: datetime, categories: dict[str, Category], horizon_days: int = 90
    ) -> CaptureResult:
        body = _body(
            self._config,
            build_system_prompt(categories, now=now),
            build_user_message(note),
        )
        response = self._send(body)
        if response.status_code == 400 and "json_validate_failed" in response.text:
            # Observed live: the model sometimes emits a markdown fence instead of
            # JSON even under a strict schema, and the endpoint rejects it with 400
            # before returning anything usable. That is a sampling failure, not a
            # contract failure, so one retry is honest. A second failure is
            # surfaced with the body attached.
            response = self._send(body)

        if response.status_code >= 400:
            # Surfaced verbatim: a 401 and a 400 "schema not supported" need
            # different fixes, and swallowing the body hides which one it is.
            raise LlmError(f"model endpoint returned {response.status_code}: {response.text[:300]}")

        content = _content(response)
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LlmError(f"model returned invalid JSON: {content[:300]}") from exc
        try:
            return parse_capture(payload, now=now, categories=categories,
                                 horizon_days=horizon_days)
        except CaptureError as exc:
            raise LlmError(f"model response did not match the schema: {exc}") from exc
