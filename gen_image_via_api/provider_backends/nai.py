from __future__ import annotations

from typing import Any

from .legacy import (
    ImagePayload,
    ProviderCallError,
    _auth_headers,
    _effective_params,
    _effective_prompt,
    _httpx,
    _request_timeout,
    _response_error_message,
)
from .media import payloads_from_bytes
from .params import base_url, discard_openai_params, file_to_base64, pop_bool, pop_first, pop_size
from ..config import AppConfig, ProviderConfig, ProviderKeyConfig
from ..queue import JobRecord
from ..utils import build_url


DEFAULT_BASE_URL = "https://api.idlecloud.cc/api"


async def call_nai(
    config: AppConfig,
    provider: ProviderConfig,
    key: ProviderKeyConfig,
    job: JobRecord,
    request_count: int,
) -> list[ImagePayload]:
    if request_count != 1:
        raise ProviderCallError("nai provider supports one image per request; keep images_per_request=1", retryable=False)

    httpx = _httpx()
    timeout = _request_timeout(config, provider)
    params = _effective_params(config, provider, job, request_count)
    prompt = _effective_prompt(config, provider, job, params)
    body = _nai_body(config, provider, job, params, prompt)
    url = build_url(base_url(provider, DEFAULT_BASE_URL), "ai/generate-image")
    headers = {**_auth_headers(provider, key), "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.post(url, headers=headers, json=body)
    if response.status_code >= 400:
        raise ProviderCallError(
            _response_error_message(response),
            retryable=response.status_code in {408, 409, 425, 429} or response.status_code >= 500,
            status_code=response.status_code,
        )

    images = payloads_from_bytes(
        response.content,
        content_type=response.headers.get("content-type", ""),
        fallback_mime="image/png",
        source="nai",
    )
    for image in images:
        image.metadata = {**(image.metadata or {}), "provider_api": "nai", "model": body.get("input")}
    return images[:request_count]


def _nai_body(
    config: AppConfig,
    provider: ProviderConfig,
    job: JobRecord,
    params: dict[str, Any],
    prompt: str,
) -> dict[str, Any]:
    model = str(params.pop("model", None) or provider.model or "nai-diffusion-4-5-full")
    width, height = pop_size(params, config.defaults.size)
    negative_prompt = str(pop_first(params, ("negativePrompt", "negative_prompt", "uc"), ""))
    action = str(pop_first(params, ("nai_action", "action"), "generate" if job.kind == "generate" else "img2img"))
    sampler = str(params.pop("sampler", "k_euler"))
    steps = int(params.pop("steps", 28))
    scale = float(params.pop("scale", params.pop("cfg_scale", 5)))
    seed = params.pop("seed", None)
    n_samples = int(params.pop("n_samples", params.pop("n", 1)) or 1)
    discard_openai_params(params)

    parameters: dict[str, Any] = {
        "width": width,
        "height": height,
        "scale": scale,
        "sampler": sampler,
        "steps": steps,
        "n_samples": n_samples,
        "ucPreset": int(params.pop("ucPreset", 1)),
        "qualityToggle": pop_bool(params, "qualityToggle", False),
        "sm": pop_bool(params, "sm", False),
        "sm_dyn": pop_bool(params, "sm_dyn", False),
        "dynamic_thresholding": pop_bool(params, "dynamic_thresholding", False),
        "controlnet_strength": float(params.pop("controlnet_strength", 1.0)),
        "legacy": pop_bool(params, "legacy", False),
        "add_original_image": pop_bool(params, "add_original_image", True),
        "cfg_rescale": float(params.pop("cfg_rescale", params.pop("promptGuidanceRescale", 0))),
        "noise_schedule": params.pop("noise_schedule", "karras"),
        "params_version": int(params.pop("params_version", 3)),
        "negative_prompt": negative_prompt,
    }
    if seed is not None and str(seed) != "":
        parameters["seed"] = int(seed)

    if job.kind == "edit":
        if not job.input_images:
            raise ProviderCallError("nai edit jobs require at least one --image", retryable=False)
        parameters["image"] = file_to_base64(job.input_images[0])
        parameters["strength"] = float(params.pop("strength", 0.7))
        parameters["noise"] = float(params.pop("noise", 0.1))
        if job.mask:
            action = str(params.pop("inpaint_action", "infill"))
            parameters["mask"] = file_to_base64(job.mask)

    parameters.update(params)
    return {
        "action": action,
        "input": model,
        "parameters": parameters,
        "model": model,
        "prompt": prompt,
    }
