from __future__ import annotations

import json
import math
from typing import Any

from .config import AppConfig, ProviderConfig
from .prompting import render_param_templates, render_prompt_template


RATIO_ALIASES = {
    "square": "1:1",
    "landscape": "3:2",
    "portrait": "2:3",
}
SIZE_MULTIPLE = 16
MAX_EDGE = 3840
MAX_ASPECT_RATIO = 3
MIN_PIXELS = 655_360
MAX_PIXELS = 8_294_400

NAI_IMAGE_MODELS = ("nai-diffusion-3", "nai-diffusion-4-full", "nai-diffusion-4-5-full")
SAMPLERS = ("k_euler", "k_euler_ancestral", "ddim")
NOISE_SCHEDULES = ("karras", "native", "exponential", "polyexponential")
NAI_PROVIDER_TYPES = {"nai", "idlecloud"}
OPENAI_PROVIDER_TYPES = {"openai-images", "responses-image", "any"}


def parse_extra_params(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Extra params must be a JSON object: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Extra params must be a JSON object.")
    return dict(parsed)


def selected_provider(config: AppConfig, provider_id: str | None) -> ProviderConfig | None:
    clean_id = str(provider_id or "").strip()
    provider_map = config.provider_map()
    if clean_id:
        return provider_map.get(clean_id)
    if config.defaults.provider:
        return provider_map.get(config.defaults.provider)
    enabled = sorted((provider for provider in config.providers if provider.enabled), key=lambda item: (item.priority, item.id))
    return enabled[0] if enabled else None


def provider_type(config: AppConfig, provider_id: str | None) -> str:
    provider = selected_provider(config, provider_id)
    return provider.type if provider else ""


def is_nai_like_provider(config: AppConfig, provider_id: str | None) -> bool:
    return provider_type(config, provider_id) in NAI_PROVIDER_TYPES


def is_openai_like_provider(config: AppConfig, provider_id: str | None) -> bool:
    kind = provider_type(config, provider_id)
    return not kind or kind in OPENAI_PROVIDER_TYPES


def provider_model_choices(config: AppConfig, provider_id: str | None) -> list[str]:
    provider = selected_provider(config, provider_id)
    if provider is None:
        return []
    choices: list[str] = []
    choices.extend(provider.models)
    if provider.model:
        choices.append(provider.model)
    if provider.type in NAI_PROVIDER_TYPES:
        choices.extend(NAI_IMAGE_MODELS)
    unique: list[str] = []
    seen: set[str] = set()
    for item in choices:
        clean = str(item or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            unique.append(clean)
    return unique


def is_nai_v4_model(model: str) -> bool:
    text = str(model or "").strip().lower()
    return text.startswith("nai-diffusion-4")


def params_for_prompt_render(config: AppConfig, params: dict[str, Any], count: int) -> dict[str, Any]:
    return {
        "size": config.defaults.size,
        "quality": config.defaults.quality,
        "output_format": config.defaults.output_format,
        "moderation": config.defaults.moderation,
        **params,
        "n": count,
    }


def parse_character_center(value: Any) -> dict[str, float] | None:
    text = str(value or "").strip()
    if not text:
        return None
    for separator in (",", ":", "x", "×"):
        if separator in text:
            left, right = text.split(separator, 1)
            try:
                x = float(left.strip())
                y = float(right.strip())
            except ValueError as exc:
                raise ValueError(f"Invalid character center '{value}'. Use x,y such as 0.25,0.4.") from exc
            return {"x": x, "y": y}
    raise ValueError(f"Invalid character center '{value}'. Use x,y such as 0.25,0.4.")


def parse_character_rows(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if hasattr(value, "to_dict"):
        rows = value.to_dict(orient="records")
    else:
        rows = value
    normalized: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                prompt = str(row.get("prompt") or row.get("character") or "").strip()
                uc = str(row.get("uc") or row.get("negative") or "").strip()
                center_obj = row.get("center") if isinstance(row.get("center"), dict) else None
                x = center_obj.get("x") if center_obj else row.get("x")
                y = center_obj.get("y") if center_obj else row.get("y")
            elif isinstance(row, (list, tuple)):
                prompt = str(row[0] if len(row) > 0 else "").strip()
                uc = str(row[1] if len(row) > 1 else "").strip()
                x = row[2] if len(row) > 2 else None
                y = row[3] if len(row) > 3 else None
            else:
                continue
            if not prompt and not uc and x in (None, "") and y in (None, ""):
                continue
            item: dict[str, Any] = {"prompt": prompt, "uc": uc}
            if x not in (None, "") and y not in (None, ""):
                item["center"] = {"x": float(x), "y": float(y)}
            elif x not in (None, "") or y not in (None, ""):
                raise ValueError("Character rows must include both x and y when either coordinate is set.")
            normalized.append(item)
    return normalized


def build_character_rows(
    prompts: list[str] | None,
    ucs: list[str] | None,
    centers: list[str] | None,
) -> list[dict[str, Any]]:
    prompt_items = [str(item).strip() for item in prompts or [] if str(item).strip()]
    uc_items = [str(item).strip() for item in ucs or []]
    center_items = [str(item).strip() for item in centers or []]
    if not prompt_items:
        return []
    if uc_items and len(uc_items) != len(prompt_items):
        raise ValueError("--character-uc count must match --character count.")
    if center_items and len(center_items) != len(prompt_items):
        raise ValueError("--character-center count must match --character count.")
    rows: list[dict[str, Any]] = []
    for index, prompt in enumerate(prompt_items):
        row: dict[str, Any] = {"prompt": prompt, "uc": uc_items[index] if index < len(uc_items) else ""}
        if index < len(center_items):
            center = parse_character_center(center_items[index])
            if center:
                row["center"] = center
        rows.append(row)
    return rows


def build_v4_character_params(
    model: str,
    characters: list[dict[str, Any]],
    *,
    use_coords: bool = False,
) -> dict[str, Any]:
    rows = parse_character_rows(characters)
    if not rows:
        return {}
    if not is_nai_v4_model(model):
        raise ValueError("Character control is currently only available for NAI V4 / 4.5 models.")
    if use_coords and any("center" not in row for row in rows):
        raise ValueError("use_coords requires every character row to include both x and y.")
    payload: dict[str, Any] = {
        "characterPrompts": [],
        "v4_prompt_char_captions": [],
        "v4_negative_prompt_char_captions": [],
        "use_coords": bool(use_coords),
    }
    for row in rows:
        entry: dict[str, Any] = {"prompt": row.get("prompt", ""), "uc": row.get("uc", "")}
        center = row.get("center")
        if center:
            entry["center"] = center
        payload["characterPrompts"].append(entry)
        if center and row.get("prompt"):
            payload["v4_prompt_char_captions"].append({"char_caption": row["prompt"], "centers": [center]})
        if center and row.get("uc"):
            payload["v4_negative_prompt_char_captions"].append({"char_caption": row["uc"], "centers": [center]})
    if not payload["v4_prompt_char_captions"]:
        payload.pop("v4_prompt_char_captions")
    if not payload["v4_negative_prompt_char_captions"]:
        payload.pop("v4_negative_prompt_char_captions")
    return payload


def selected_template_id(config: AppConfig, template_id: str | None, *, no_template: bool = False) -> str | None:
    if no_template:
        return None
    value = str(template_id or "").strip()
    if value in {"", "__default__"}:
        return config.defaults.prompt_template
    if value == "__none__":
        return None
    return value


def apply_prompt_template(
    config: AppConfig,
    raw_prompt: str,
    params: dict[str, Any],
    count: int,
    *,
    template_id: str | None = None,
    no_template: bool = False,
) -> str:
    selected = selected_template_id(config, template_id, no_template=no_template)
    if not selected:
        return raw_prompt
    template = config.prompt_template_map().get(selected)
    if not template or not template.enabled:
        raise ValueError(f"Unknown or disabled prompt template: {selected}")
    render_params = params_for_prompt_render(config, params, count)
    if template.params:
        params.update(render_param_templates(template.params, prompt=raw_prompt, params=render_params))
        render_params = params_for_prompt_render(config, params, count)
    return render_prompt_template(template.body, prompt=raw_prompt, params=render_params)


def build_job_params(
    config: AppConfig,
    *,
    provider_id: str | None = None,
    extra_params: str | dict[str, Any] = "",
    size: str = "",
    aspect_ratio: str = "",
    size_tier: str = "1K",
    model: str = "",
    output_format: str = "",
    quality: str = "",
    background: str = "",
    negative_prompt: str = "",
    steps: Any = None,
    scale: Any = None,
    seed: Any = None,
    sampler: str = "",
    noise_schedule: str = "",
    uc_preset: Any = None,
    quality_toggle: Any = None,
    cfg_rescale: Any = None,
    characters: Any = None,
    use_coords: Any = None,
) -> dict[str, Any]:
    params = dict(extra_params) if isinstance(extra_params, dict) else parse_extra_params(str(extra_params or ""))
    if str(size or "").strip():
        params["size"] = normalize_image_size(str(size))
    elif str(aspect_ratio or "").strip():
        params["size"] = size_from_aspect_ratio(str(aspect_ratio), str(size_tier or "1K"))
    for key, value in {
        "model": model,
        "output_format": "jpeg" if str(output_format or "") == "jpg" else output_format,
        "quality": quality,
        "background": background,
    }.items():
        if str(value or "").strip():
            params[key] = str(value).strip()
    if str(negative_prompt or "").strip():
        params["negative_prompt"] = str(negative_prompt).strip()
    if is_nai_like_provider(config, provider_id):
        for key, value, cast in (
            ("steps", steps, int),
            ("scale", scale, float),
            ("seed", seed, int),
            ("ucPreset", uc_preset, int),
            ("cfg_rescale", cfg_rescale, float),
        ):
            if value not in (None, ""):
                params[key] = cast(value)
        if str(sampler or "").strip():
            params["sampler"] = str(sampler).strip()
        if str(noise_schedule or "").strip():
            params["noise_schedule"] = str(noise_schedule).strip()
        if quality_toggle not in (None, ""):
            params["qualityToggle"] = bool(quality_toggle)
        effective_provider = selected_provider(config, provider_id)
        effective_model = str(params.get("model") or (effective_provider.model if effective_provider else "")).strip()
        character_payload = build_v4_character_params(
            effective_model,
            characters if isinstance(characters, list) else parse_character_rows(characters),
            use_coords=bool(use_coords),
        )
        params.update(character_payload)
    return params


def parse_ratio(value: str) -> tuple[float, float] | None:
    normalized = RATIO_ALIASES.get(value.strip().lower().replace(" ", ""), value.strip().lower().replace(" ", ""))
    for separator in (":", "x", "×"):
        if separator in normalized:
            left, right = normalized.split(separator, 1)
            try:
                width = float(left)
                height = float(right)
            except ValueError:
                return None
            if width > 0 and height > 0:
                return width, height
    return None


def _round_multiple(value: float, multiple: int = SIZE_MULTIPLE) -> int:
    return max(multiple, int(round(value / multiple)) * multiple)


def _floor_multiple(value: float, multiple: int = SIZE_MULTIPLE) -> int:
    return max(multiple, int(value // multiple) * multiple)


def _ceil_multiple(value: float, multiple: int = SIZE_MULTIPLE) -> int:
    return max(multiple, int(math.ceil(value / multiple)) * multiple)


def normalize_dimensions(width: float, height: float) -> tuple[int, int]:
    normalized_width = _round_multiple(width)
    normalized_height = _round_multiple(height)

    for _ in range(4):
        max_edge = max(normalized_width, normalized_height)
        if max_edge > MAX_EDGE:
            scale = MAX_EDGE / max_edge
            normalized_width = _floor_multiple(normalized_width * scale)
            normalized_height = _floor_multiple(normalized_height * scale)

        if normalized_width / normalized_height > MAX_ASPECT_RATIO:
            normalized_width = _floor_multiple(normalized_height * MAX_ASPECT_RATIO)
        elif normalized_height / normalized_width > MAX_ASPECT_RATIO:
            normalized_height = _floor_multiple(normalized_width * MAX_ASPECT_RATIO)

        pixels = normalized_width * normalized_height
        if pixels > MAX_PIXELS:
            scale = math.sqrt(MAX_PIXELS / pixels)
            normalized_width = _floor_multiple(normalized_width * scale)
            normalized_height = _floor_multiple(normalized_height * scale)
        elif pixels < MIN_PIXELS:
            scale = math.sqrt(MIN_PIXELS / pixels)
            normalized_width = _ceil_multiple(normalized_width * scale)
            normalized_height = _ceil_multiple(normalized_height * scale)

    return normalized_width, normalized_height


def normalize_image_size(value: str) -> str:
    trimmed = str(value or "").strip()
    if trimmed.lower() == "auto":
        return "auto"
    for separator in ("x", "X", "×"):
        if separator in trimmed:
            left, right = trimmed.split(separator, 1)
            if left.strip().isdigit() and right.strip().isdigit():
                width, height = normalize_dimensions(float(left), float(right))
                return f"{width}x{height}"
    return trimmed


def size_from_aspect_ratio(value: str, tier: str) -> str:
    parsed = parse_ratio(value)
    if not parsed:
        raise ValueError("Unsupported aspect ratio. Use values like 1:1, 4:3, 3:2, 16:9, 9:16, or 21:9.")
    ratio_width, ratio_height = parsed
    normalized_tier = str(tier or "1K").upper()
    if normalized_tier not in {"1K", "2K", "4K"}:
        raise ValueError("size_tier must be one of: 1K, 2K, 4K")

    if ratio_width == ratio_height:
        side = 1024 if normalized_tier == "1K" else 2048 if normalized_tier == "2K" else 3840
        return normalize_image_size(f"{side}x{side}")

    if normalized_tier == "1K":
        short_side = 1024
        width = _round_multiple(short_side * ratio_width / ratio_height) if ratio_width > ratio_height else short_side
        height = short_side if ratio_width > ratio_height else _round_multiple(short_side * ratio_height / ratio_width)
        return f"{width}x{height}"

    long_side = 2048 if normalized_tier == "2K" else 3840
    width = long_side if ratio_width > ratio_height else _round_multiple(long_side * ratio_width / ratio_height)
    height = _round_multiple(long_side * ratio_height / ratio_width) if ratio_width > ratio_height else long_side
    return normalize_image_size(f"{width}x{height}")
