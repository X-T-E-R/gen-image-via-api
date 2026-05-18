from __future__ import annotations

import re
from typing import Any


COMMON_DIRECT_CLI_PARAMS = ("size", "aspect_ratio", "size_tier", "output_format", "background", "output_compression")


def provider_parameter_support(provider) -> dict[str, Any]:
    """Describe user-facing parameter support for a provider.

    The CLI and WebUI both use this report so provider-specific differences
    stay centralized as new provider backends are added.
    """

    if provider.type == "mock":
        return {
            "common_cli_params": [],
            "provider_cli_params": [],
            "direct_cli_params": [],
            "extra_params_via_param": [],
            "ignored_params": [],
            "notes": ["mock provider returns a fixed offline PNG and ignores image tuning params"],
        }

    if provider.type == "openai-images":
        return {
            "common_cli_params": list(COMMON_DIRECT_CLI_PARAMS),
            "provider_cli_params": ["quality", "moderation", "model"],
            "direct_cli_params": [*COMMON_DIRECT_CLI_PARAMS, "quality", "moderation", "model"],
            "extra_params_via_param": ["input_fidelity", "response_format", "prompt_rewrite_guard", "append_size_to_prompt"],
            "ignored_params": ["action", "stream"],
            "notes": [
                "action and stream are accepted by the CLI but ignored for Images API providers",
                "provider.response_format_b64_json adds response_format=b64_json when the job does not set one",
                "provider.codex_cli omits quality and prefixes the no-rewrite prompt guard",
                "provider.append_size_to_prompt appends a size/ratio instruction to the submitted prompt",
                "output_compression is sent only when output_format is jpeg or webp",
                "transparent background support is model-specific and some providers may reject it",
                "model belongs in provider config; per-job model override is also accepted with --model",
            ],
        }

    if provider.type in {"responses-image", "any"}:
        extras = [
            "tool_choice",
            "omit_tool_choice",
            "omit_action",
            "omit_size",
            "omit_quality",
            "reasoning",
            "metadata",
            "max_output_tokens",
            "previous_response_id",
            "partial_images",
            "responses_stream_partial_images",
            "force_responses_stream",
            "prompt_rewrite_guard",
            "append_size_to_prompt",
        ]
        notes = [
            "moderation is not sent to the Responses image_generation tool",
            "provider.force_responses_stream forces stream=true even if a job passes --no-stream",
            "provider.responses_stream_partial_images adds partial_images to the image_generation tool, clamped to 0-3",
            "provider.append_size_to_prompt appends a size/ratio instruction to the submitted prompt",
            "output_compression is sent only when output_format is jpeg or webp",
            "transparent background support is model-specific and some providers may reject it",
            "omit_action/omit_size/omit_tool_choice can match minimal browser-style routers",
        ]
        if provider.type == "any":
            notes.append("type=any defaults to omitting action, size, and tool_choice to match the browser sample")
        if _is_codex_cli_like_provider(provider):
            notes.append("quality is intentionally omitted for codex-cli-style routers")
        return {
            "common_cli_params": list(COMMON_DIRECT_CLI_PARAMS),
            "provider_cli_params": ["quality", "model", "action", "stream"],
            "direct_cli_params": [*COMMON_DIRECT_CLI_PARAMS, "quality", "model", "action", "stream"],
            "extra_params_via_param": extras,
            "ignored_params": ["moderation"],
            "notes": notes,
        }

    if provider.type == "nai":
        extras = [
            "negativePrompt",
            "negative_prompt",
            "uc",
            "seed",
            "steps",
            "scale",
            "cfg_scale",
            "sampler",
            "noise_schedule",
            "ucPreset",
            "qualityToggle",
            "sm",
            "sm_dyn",
            "promptGuidanceRescale",
            "cfg_rescale",
            "n_samples",
            "strength",
            "noise",
            "nai_action",
        ]
        return {
            "common_cli_params": ["size", "aspect_ratio", "size_tier", "model"],
            "provider_cli_params": ["model"],
            "direct_cli_params": ["size", "aspect_ratio", "size_tier", "model"],
            "extra_params_via_param": extras,
            "ignored_params": ["quality", "background", "moderation", "output_format", "output_compression", "stream"],
            "notes": [
                "NAI-compatible endpoint posts to /api/ai/generate-image and extracts images from the returned zip",
                "use --param for NovelAI-specific generation knobs such as negativePrompt, seed, steps, scale, sampler, and ucPreset",
                "edit jobs map the first --image to the NovelAI image parameter; --mask maps to mask for inpaint-style requests",
                "keep images_per_request=1 unless the endpoint is known to return multiple images safely",
            ],
        }

    if provider.type == "idlecloud":
        extras = [
            "negativePrompt",
            "negative_prompt",
            "uc",
            "seed",
            "steps",
            "scale",
            "cfg_scale",
            "sampler",
            "noise_schedule",
            "ucPreset",
            "qualityToggle",
            "sm",
            "sm_dyn",
            "autoSmea",
            "promptGuidanceRescale",
            "cfg_rescale",
            "n_samples",
            "strength",
            "noise",
            "inpaint_strength",
            "color_correct",
            "reference_image_multiple",
            "reference_strength_multiple",
            "characterPrompts",
            "v4_prompt_char_captions",
            "v4_negative_prompt_char_captions",
        ]
        return {
            "common_cli_params": ["size", "aspect_ratio", "size_tier", "model"],
            "provider_cli_params": ["model"],
            "direct_cli_params": ["size", "aspect_ratio", "size_tier", "model"],
            "extra_params_via_param": extras,
            "ignored_params": ["quality", "background", "moderation", "output_format", "output_compression", "stream", "action"],
            "notes": [
                "IdleCloud endpoint submits /api/generate_image, polls /api/get_result/{job_id}, and reads image_base64 or image_url",
                "the API documents a 20 second request interval and one concurrent task per user; configure max_concurrent_requests=1",
                "use --param for IdleCloud/NAI-specific knobs such as negativePrompt, seed, steps, scale, sampler, reference images, and V4 character controls",
                "edit jobs map the first --image to image=base64; --mask enables inpaint fields",
            ],
        }

    if provider.type == "custom-http":
        refs = sorted(
            _extract_template_param_refs(provider.submit)
            | _extract_template_param_refs(provider.edit_submit)
            | _extract_template_param_refs(provider.poll)
        )
        direct = [param for param in COMMON_DIRECT_CLI_PARAMS if param in refs]
        extra = [param for param in refs if param not in COMMON_DIRECT_CLI_PARAMS]
        return {
            "common_cli_params": direct,
            "provider_cli_params": extra,
            "direct_cli_params": direct,
            "extra_params_via_param": extra,
            "ignored_params": [],
            "notes": [
                "custom-http support depends on $params.* references in submit/edit/poll mappings",
            ],
        }

    return {
        "common_cli_params": list(COMMON_DIRECT_CLI_PARAMS),
        "provider_cli_params": [],
        "direct_cli_params": list(COMMON_DIRECT_CLI_PARAMS),
        "extra_params_via_param": [],
        "ignored_params": [],
        "notes": [f"unknown provider type: {provider.type}"],
    }


def _is_codex_cli_like_provider(provider) -> bool:
    originator = str(provider.headers.get("originator") or provider.headers.get("Originator") or "").lower()
    return provider.codex_cli or originator == "codex_cli_rs"


def _extract_template_param_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, str):
        refs.update(re.findall(r"\$params\.([A-Za-z_][A-Za-z0-9_]*)", value))
    elif isinstance(value, dict):
        for child in value.values():
            refs.update(_extract_template_param_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(_extract_template_param_refs(child))
    return refs
