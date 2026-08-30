"""OpenAI-compatible chat completions client (JSON mode)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from dg import config

logger = logging.getLogger(__name__)


class OpenAIError(RuntimeError):
    """API or transport failure talking to the LLM provider."""


def _extract_message_content(message: Dict[str, Any]) -> str:
    """Pull text from string content or GPT-style content part arrays."""
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, str) and part.strip():
                parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            for key in ("text", "output_text", "content"):
                val = part.get(key)
                if isinstance(val, str) and val.strip():
                    parts.append(val)
                    break
        if parts:
            return "\n".join(parts)
    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal.strip():
        return refusal
    return ""


def chat_json(
    *,
    system: str,
    user: str,
    model: Optional[str] = None,
    max_tokens: int = 2000,
    timeout: Optional[float] = None,
) -> str:
    """
    POST /chat/completions with response_format json_object.
    Temperature omitted (Luna / some models reject custom temperature).
    Returns the assistant message content string (JSON text).
    """
    key = config.OPENAI_API_KEY
    if not key:
        raise OpenAIError("OPENAI_API_KEY is not set")

    url = f"{config.OPENAI_BASE_URL}/chat/completions"
    # GPT-5.x / Luna reject max_tokens; use max_completion_tokens (MatchPredictor).
    payload: Dict[str, Any] = {
        "model": model or config.OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_completion_tokens": int(max_tokens),
        "response_format": {"type": "json_object"},
    }
    effort = (config.OPENAI_REASONING_EFFORT or "").strip()
    if effort:
        payload["reasoning_effort"] = effort
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    to = float(timeout if timeout is not None else config.AI_VET_TIMEOUT_SEC)
    try:
        with httpx.Client(timeout=to) as client:
            resp = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise OpenAIError(f"OpenAI request failed: {exc}") from exc

    if resp.status_code >= 400:
        detail = (resp.text or "")[:500]
        raise OpenAIError(f"OpenAI HTTP {resp.status_code}: {detail}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise OpenAIError("OpenAI response was not JSON") from exc

    choices: List[Any] = data.get("choices") or []
    if not choices:
        raise OpenAIError("OpenAI response had no choices")
    choice0 = choices[0] if isinstance(choices[0], dict) else {}
    message = choice0.get("message") or {}
    if not isinstance(message, dict):
        message = {}
    content = _extract_message_content(message)
    if not content.strip():
        finish = choice0.get("finish_reason")
        usage = data.get("usage") or {}
        raise OpenAIError(
            "OpenAI response had empty content "
            f"(finish_reason={finish!r}, usage={usage}). "
            "Try raising AI_VET_MAX_TOKENS or lowering OPENAI_REASONING_EFFORT."
        )
    return content
