from __future__ import annotations

import re
from typing import Any


SIZE_PROMPT_SUFFIX_PREFIX = "Output size instruction: use size"
PROMPT_REWRITE_GUARD_PREFIX = "Use the following text as the complete prompt. Do not rewrite it:"


def _gcd(a: int, b: int) -> int:
    x = abs(int(a))
    y = abs(int(b))
    while y:
        x, y = y, x % y
    return x or 1


def derive_aspect_ratio(size: Any) -> str:
    text = str(size or "").strip()
    match = re.match(r"^(\d+)\s*x\s*(\d+)$", text, flags=re.IGNORECASE)
    if not match:
        return "auto"
    width = int(match.group(1))
    height = int(match.group(2))
    if width <= 0 or height <= 0:
        return "auto"
    divisor = _gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def append_size_instruction(prompt: str, size: Any) -> str:
    text = str(prompt or "")
    if SIZE_PROMPT_SUFFIX_PREFIX in text:
        return text
    normalized_size = str(size or "").strip() or "auto"
    ratio = derive_aspect_ratio(normalized_size)
    return f"{text}\n\n{SIZE_PROMPT_SUFFIX_PREFIX} {normalized_size} and aspect ratio {ratio}."


def add_prompt_rewrite_guard(prompt: str) -> str:
    text = str(prompt or "")
    if text.startswith(PROMPT_REWRITE_GUARD_PREFIX):
        return text
    return f"{PROMPT_REWRITE_GUARD_PREFIX}\n{text}"


def render_prompt_template(template_body: str, *, prompt: str, params: dict[str, Any]) -> str:
    body = str(template_body or "")
    if not body.strip():
        return prompt

    values: dict[str, str] = {
        "prompt": str(prompt),
        "size": str(params.get("size") or "auto"),
        "ratio": derive_aspect_ratio(params.get("size") or "auto"),
        "quality": str(params.get("quality") or "auto"),
        "output_format": str(params.get("output_format") or "png"),
        "n": str(params.get("n") or 1),
    }

    has_prompt = re.search(r"\{\{\s*prompt\s*\}\}", body) is not None

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return values.get(name, match.group(0))

    rendered = re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", replace, body)
    if has_prompt:
        return rendered
    return f"{rendered.strip()}\n\n{prompt}".strip()
