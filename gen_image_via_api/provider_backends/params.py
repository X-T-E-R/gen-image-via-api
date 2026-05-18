from __future__ import annotations

from pathlib import Path
from typing import Any

from .legacy import ProviderCallError


OPENAI_IMAGE_PARAM_KEYS = {
    "quality",
    "moderation",
    "background",
    "output_format",
    "output_compression",
    "response_format",
    "stream",
    "force_responses_stream",
    "responses_stream_partial_images",
    "partial_images",
    "codex_cli",
    "prompt_rewrite_guard",
    "append_size_to_prompt",
}


def pop_size(params: dict[str, Any], default_size: str = "1024x1024") -> tuple[int, int]:
    width = params.pop("width", None)
    height = params.pop("height", None)
    if width is not None and height is not None:
        return _positive_int(width, "width"), _positive_int(height, "height")

    raw = str(params.pop("size", None) or default_size or "1024x1024").strip()
    if raw.lower() == "auto":
        raw = "1024x1024"
    for separator in ("x", "X", "×"):
        if separator in raw:
            left, right = raw.split(separator, 1)
            return _positive_int(left.strip(), "width"), _positive_int(right.strip(), "height")
    raise ProviderCallError(f"Provider requires an explicit image size like 1024x1024, got: {raw}", retryable=False)


def _positive_int(value: Any, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ProviderCallError(f"Invalid {name}: {value}", retryable=False) from exc
    if number <= 0:
        raise ProviderCallError(f"Invalid {name}: {value}", retryable=False)
    return number


def pop_first(params: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        if key in params:
            return params.pop(key)
    return default


def pop_bool(params: dict[str, Any], key: str, default: bool = False) -> bool:
    if key not in params:
        return default
    value = params.pop(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def file_to_base64(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise ProviderCallError(f"Input image not found: {p}", retryable=False)
    import base64

    return base64.b64encode(p.read_bytes()).decode("ascii")


def discard_openai_params(params: dict[str, Any]) -> None:
    for key in OPENAI_IMAGE_PARAM_KEYS:
        params.pop(key, None)


def base_url(provider, fallback: str) -> str:
    return (provider.base_url or fallback).rstrip("/")
