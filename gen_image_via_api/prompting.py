from __future__ import annotations

import re
import json
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


def _template_value_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _template_values(*, prompt: str, params: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {str(key): _template_value_text(value) for key, value in dict(params or {}).items()}
    values.update(
        {
            "prompt": str(prompt),
            "size": str(params.get("size") or "auto"),
            "ratio": derive_aspect_ratio(params.get("size") or "auto"),
            "quality": str(params.get("quality") or "auto"),
            "output_format": str(params.get("output_format") or "png"),
            "n": str(params.get("n") or 1),
        }
    )
    if "negativePrompt" in params and "negative_prompt" not in values:
        values["negative_prompt"] = _template_value_text(params.get("negativePrompt"))
    if "negative_prompt" in params and "negativePrompt" not in values:
        values["negativePrompt"] = _template_value_text(params.get("negative_prompt"))
    return values


def render_template_string(template_body: str, *, prompt: str, params: dict[str, Any]) -> str:
    body = str(template_body or "")
    values = _template_values(prompt=prompt, params=params)

    def replace_any(match: re.Match[str]) -> str:
        name = match.group(1)
        return values.get(name, match.group(0))

    legacy_values = {key: values[key] for key in ("prompt", "size", "ratio", "quality", "output_format", "n")}

    def replace_legacy(match: re.Match[str]) -> str:
        name = match.group(1)
        return legacy_values.get(name, match.group(0))

    # Legacy {{name}} placeholders are kept for existing configs. The boundary
    # checks avoid consuming NovelAI prompt-weight braces such as {{{tag}}}.
    rendered = re.sub(r"(?<!\{)\{\{\s*([a-zA-Z0-9_]+)\s*\}\}(?!\})", replace_legacy, body)
    # Prefer ${name} for new templates because NovelAI uses plain braces for
    # prompt weights.
    rendered = re.sub(r"\$\{\s*([a-zA-Z0-9_]+)\s*\}", replace_any, rendered)
    return rendered


def render_prompt_template(template_body: str, *, prompt: str, params: dict[str, Any]) -> str:
    body = str(template_body or "")
    if not body.strip():
        return prompt

    has_prompt = (
        re.search(r"(?<!\{)\{\{\s*prompt\s*\}\}(?!\})", body) is not None
        or re.search(r"\$\{\s*prompt\s*\}", body) is not None
    )
    rendered = render_template_string(body, prompt=prompt, params=params)
    if has_prompt:
        return rendered
    return f"{rendered.strip()}\n\n{prompt}".strip()


def render_param_templates(template_params: dict[str, Any], *, prompt: str, params: dict[str, Any]) -> dict[str, Any]:
    rendered: dict[str, Any] = {}
    for key, value in dict(template_params or {}).items():
        if isinstance(value, str):
            rendered[str(key)] = render_template_string(value, prompt=prompt, params=params)
        else:
            rendered[str(key)] = value
    return rendered
