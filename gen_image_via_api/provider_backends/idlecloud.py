from __future__ import annotations

import asyncio
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
from .media import payloads_from_value
from .params import base_url, discard_openai_params, file_to_base64, pop_bool, pop_first, pop_size
from ..config import AppConfig, ProviderConfig, ProviderKeyConfig
from ..queue import JobRecord
from ..utils import build_url


DEFAULT_BASE_URL = "https://api.idlecloud.cc/api"


async def call_idlecloud(
    config: AppConfig,
    provider: ProviderConfig,
    key: ProviderKeyConfig,
    job: JobRecord,
    request_count: int,
) -> list[ImagePayload]:
    if request_count != 1:
        raise ProviderCallError("idlecloud provider supports one image per request; keep images_per_request=1", retryable=False)

    httpx = _httpx()
    timeout = _request_timeout(config, provider)
    params = _effective_params(config, provider, job, request_count)
    prompt = _effective_prompt(config, provider, job, params)
    body = _idlecloud_body(config, provider, job, params, prompt)
    headers = {**_auth_headers(provider, key), "Content-Type": "application/json"}
    root = base_url(provider, DEFAULT_BASE_URL)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.post(build_url(root, "generate_image"), headers=headers, json=body)
    if response.status_code >= 400:
        raise ProviderCallError(
            _response_error_message(response),
            retryable=response.status_code in {408, 409, 425, 429} or response.status_code >= 500,
            status_code=response.status_code,
        )
    payload = _json_response(response)
    job_id = str(payload.get("job_id") or payload.get("task_id") or payload.get("id") or "").strip()
    if not job_id:
        images = await _images_from_result_payload(payload, timeout=timeout)
        if images:
            return _with_metadata(images, provider_api="idlecloud", model=body.get("model"))[:request_count]
        raise ProviderCallError(f"IdleCloud returned no job_id or image result: {payload}", retryable=False)

    result = await _poll_idlecloud_result(config, provider, key, job_id, root=root, timeout=timeout)
    images = await _images_from_result_payload(result, timeout=timeout)
    if not images:
        raise ProviderCallError(f"IdleCloud task completed but returned no recognizable image: {result}", retryable=False)
    return _with_metadata(images, provider_api="idlecloud", idlecloud_job_id=job_id, model=body.get("model"))[:request_count]


def _idlecloud_body(
    config: AppConfig,
    provider: ProviderConfig,
    job: JobRecord,
    params: dict[str, Any],
    prompt: str,
) -> dict[str, Any]:
    model = str(params.pop("model", None) or provider.model or "nai-diffusion-4-5-full")
    width, height = pop_size(params, config.defaults.size)
    negative_prompt = str(pop_first(params, ("negativePrompt", "negative_prompt", "uc"), ""))
    discard_openai_params(params)

    body: dict[str, Any] = {
        "model": model,
        "positivePrompt": str(pop_first(params, ("positivePrompt", "prompt"), prompt)),
        "negativePrompt": negative_prompt,
        "qualityToggle": pop_bool(params, "qualityToggle", False),
        "scale": float(params.pop("scale", params.pop("cfg_scale", 5))),
        "steps": int(params.pop("steps", 28)),
        "width": width,
        "height": height,
        "promptGuidanceRescale": float(params.pop("promptGuidanceRescale", params.pop("cfg_rescale", 0))),
        "noise_schedule": params.pop("noise_schedule", "karras"),
        "seed": int(params.pop("seed", 0) or 0),
        "sampler": params.pop("sampler", "k_euler"),
        "sm": pop_bool(params, "sm", False),
        "sm_dyn": pop_bool(params, "sm_dyn", False),
        "decrisp": pop_bool(params, "decrisp", False),
        "variety": pop_bool(params, "variety", False),
        "n_samples": int(params.pop("n_samples", params.pop("n", 1)) or 1),
        "prefer_brownian": pop_bool(params, "prefer_brownian", True),
        "deliberate_euler_ancestral_bug": pop_bool(params, "deliberate_euler_ancestral_bug", False),
        "legacy": pop_bool(params, "legacy", False),
        "legacy_uc": pop_bool(params, "legacy_uc", False),
        "legacy_v3_extend": pop_bool(params, "legacy_v3_extend", False),
        "ucPreset": int(params.pop("ucPreset", 1)),
        "autoSmea": pop_bool(params, "autoSmea", False),
        "use_coords": pop_bool(params, "use_coords", False),
        "use_upscale_credits": pop_bool(params, "use_upscale_credits", False),
    }

    if job.kind == "edit":
        if not job.input_images:
            raise ProviderCallError("idlecloud edit jobs require at least one --image", retryable=False)
        body["action"] = True
        body["image"] = file_to_base64(job.input_images[0])
        body["strength"] = float(params.pop("strength", 0.7))
        body["noise"] = float(params.pop("noise", 0.1))
        if job.mask:
            body["mask"] = file_to_base64(job.mask)
            body["inpaint_strength"] = float(params.pop("inpaint_strength", 1.0))
            body["color_correct"] = pop_bool(params, "color_correct", True)
            body["disabled_original_image"] = pop_bool(params, "disabled_original_image", False)

    body.update(params)
    return body


async def _poll_idlecloud_result(
    config: AppConfig,
    provider: ProviderConfig,
    key: ProviderKeyConfig,
    job_id: str,
    *,
    root: str,
    timeout: float,
) -> dict[str, Any]:
    httpx = _httpx()
    interval = float(provider.poll.get("interval_seconds") or config.queue.poll_interval_seconds)
    max_polls = int(provider.poll.get("max_polls") or 120)
    headers = _auth_headers(provider, key)
    url = build_url(root, f"get_result/{job_id}")

    for _ in range(max_polls):
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
        if response.status_code >= 400:
            if response.status_code in {408, 409, 425, 429} or response.status_code >= 500:
                await asyncio.sleep(interval)
                continue
            raise ProviderCallError(_response_error_message(response), retryable=False, status_code=response.status_code)
        payload = _json_response(response)
        status = str(payload.get("status") or "").lower()
        if status in {"failed", "failure", "cancelled", "canceled"}:
            raise ProviderCallError(str(payload.get("error") or f"IdleCloud task failed: {status}"), retryable=False)
        if status in {"completed", "succeeded", "success"}:
            return payload
        await asyncio.sleep(interval)
    raise ProviderCallError(f"IdleCloud task polling timed out after {max_polls} polls", retryable=True)


async def _images_from_result_payload(payload: dict[str, Any], *, timeout: float) -> list[ImagePayload]:
    outputs: list[ImagePayload] = []
    for key in ("image_base64", "image", "base64", "b64_json"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            outputs.extend(await payloads_from_value(value, timeout=timeout, fallback_mime="image/png"))
    for key in ("image_url", "url"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            outputs.extend(await payloads_from_value(value, timeout=timeout, fallback_mime="image/png"))
    return outputs


def _with_metadata(images: list[ImagePayload], **metadata: Any) -> list[ImagePayload]:
    for image in images:
        image.metadata = {**(image.metadata or {}), **metadata}
    return images


def _json_response(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception as exc:
        raise ProviderCallError(f"Provider returned non-JSON response: {exc}", retryable=False) from exc
    if not isinstance(payload, dict):
        raise ProviderCallError(f"Provider returned non-object JSON response: {payload}", retryable=False)
    return payload
