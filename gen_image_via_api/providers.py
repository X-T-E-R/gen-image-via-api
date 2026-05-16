from __future__ import annotations

from dataclasses import dataclass
import asyncio
import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

from .config import AppConfig, ProviderConfig, ProviderKeyConfig
from .queue import JobRecord
from .prompting import add_prompt_rewrite_guard, append_size_instruction
from .utils import (
    MOCK_PNG_BASE64,
    build_url,
    get_all_by_path,
    get_by_path,
    is_data_url,
    is_http_url,
    json_dumps,
    parse_data_url,
    resolve_template,
)


RETRYABLE_STATUSES = {408, 409, 425, 429}


@dataclass
class ImagePayload:
    data: bytes
    mime: str
    raw_url: str | None = None
    metadata: dict[str, Any] | None = None


class ProviderCallError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


def _httpx():
    try:
        import httpx  # type: ignore
    except ImportError as exc:
        raise ProviderCallError(
            "Missing dependency 'httpx'. Install this project with `pip install -e .` "
            "or run `python -m pip install httpx` in the active environment.",
            retryable=False,
        ) from exc
    return httpx


def _key_images_per_request(provider: ProviderConfig, key: ProviderKeyConfig) -> int:
    return max(1, int(key.images_per_request or provider.images_per_request or 1))


def _request_timeout(config: AppConfig, provider: ProviderConfig) -> float:
    return float(provider.timeout_seconds or config.queue.request_timeout_seconds)


def _auth_headers(provider: ProviderConfig, key: ProviderKeyConfig) -> dict[str, str]:
    headers = dict(provider.headers)
    secret = key.resolve_secret()
    if secret:
        headers.setdefault("Authorization", f"Bearer {secret}")
    return headers


def _load_input_images(paths: list[str]) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            raise ProviderCallError(f"Input image not found: {path}", retryable=False)
        out.append((path.name, path.read_bytes()))
    return out


async def _download_image(url: str, *, timeout: float) -> ImagePayload:
    httpx = _httpx()
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url)
    if response.status_code >= 400:
        raise ProviderCallError(
            f"Image URL download failed: HTTP {response.status_code}",
            retryable=response.status_code in RETRYABLE_STATUSES or response.status_code >= 500,
            status_code=response.status_code,
        )
    mime = response.headers.get("content-type", "image/png").split(";", 1)[0].strip() or "image/png"
    return ImagePayload(data=response.content, mime=mime, raw_url=url)


async def _payload_from_value(value: str, *, fallback_mime: str, timeout: float) -> ImagePayload:
    if is_http_url(value):
        return await _download_image(value, timeout=timeout)
    data, mime = parse_data_url(value, fallback_mime)
    return ImagePayload(data=data, mime=mime)


async def _extract_images(
    payload: Any,
    *,
    result_mapping: dict[str, Any] | None,
    fallback_mime: str,
    timeout: float,
) -> list[ImagePayload]:
    outputs: list[ImagePayload] = []

    if result_mapping:
        b64_paths = result_mapping.get("b64_json_paths") or result_mapping.get("b64_paths") or []
        url_paths = result_mapping.get("image_url_paths") or result_mapping.get("url_paths") or []
        for path in b64_paths:
            for value in get_all_by_path(payload, str(path)):
                if isinstance(value, str) and value.strip():
                    image = await _payload_from_value(value, fallback_mime=fallback_mime, timeout=timeout)
                    image.metadata = {"source_path": path}
                    outputs.append(image)
        for path in url_paths:
            for value in get_all_by_path(payload, str(path)):
                if isinstance(value, str) and (is_http_url(value) or is_data_url(value)):
                    image = await _payload_from_value(value, fallback_mime=fallback_mime, timeout=timeout)
                    image.metadata = {"source_path": path}
                    outputs.append(image)
        return outputs

    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            value = item.get("b64_json") or item.get("url")
            if isinstance(value, str) and value.strip():
                image = await _payload_from_value(value, fallback_mime=fallback_mime, timeout=timeout)
                image.metadata = {
                    "revised_prompt": item.get("revised_prompt"),
                    "raw": {k: v for k, v in item.items() if k not in {"b64_json"}},
                }
                outputs.append(image)
    return outputs


async def call_provider(
    config: AppConfig,
    provider: ProviderConfig,
    key: ProviderKeyConfig,
    job: JobRecord,
    request_count: int,
) -> list[ImagePayload]:
    if provider.type == "mock":
        return await _call_mock(provider, key, job, request_count)
    if provider.type == "openai-images":
        return await _call_openai_images(config, provider, key, job, request_count)
    if provider.type in {"responses-image", "any"}:
        return await _call_responses_image(config, provider, key, job, request_count)
    if provider.type == "custom-http":
        return await _call_custom_http(config, provider, key, job, request_count)
    raise ProviderCallError(f"Unsupported provider type: {provider.type}", retryable=False)


async def _call_mock(
    provider: ProviderConfig,
    key: ProviderKeyConfig,
    job: JobRecord,
    request_count: int,
) -> list[ImagePayload]:
    await asyncio.sleep(0)
    data, mime = parse_data_url(MOCK_PNG_BASE64, "image/png")
    return [
        ImagePayload(
            data=data,
            mime=mime,
            metadata={
                "provider": provider.id,
                "key": key.id,
                "prompt": job.prompt,
                "kind": job.kind,
                "mock": True,
            },
        )
        for _ in range(max(1, int(request_count)))
    ]


async def _call_openai_images(
    config: AppConfig,
    provider: ProviderConfig,
    key: ProviderKeyConfig,
    job: JobRecord,
    request_count: int,
) -> list[ImagePayload]:
    httpx = _httpx()
    timeout = _request_timeout(config, provider)
    params = _effective_params(config, provider, job, request_count)
    prompt = _effective_prompt(config, provider, job, params)
    fallback_mime = f"image/{params.get('output_format', config.defaults.output_format)}"
    headers = _auth_headers(provider, key)
    base_url = provider.base_url.rstrip("/") or "https://api.openai.com/v1"
    model = str(params.pop("model", None) or provider.model)
    if not model:
        raise ProviderCallError(f"Provider '{provider.id}' has no model", retryable=False)

    # Responses-tool-only knobs can be accepted by the CLI for convenience, but
    # the Images API endpoints should not receive them.
    params.pop("action", None)
    params.pop("stream", None)
    params.pop("force_responses_stream", None)
    params.pop("responses_stream_partial_images", None)
    params.pop("partial_images", None)
    params.pop("codex_cli", None)

    if provider.codex_cli:
        params.pop("quality", None)

    if provider.response_format_b64_json:
        params.setdefault("response_format", "b64_json")

    if str(params.get("output_format") or "png").lower() == "png":
        params.pop("output_compression", None)

    if job.kind == "edit":
        files: list[tuple[str, tuple[str, bytes]]] = []
        for filename, content in _load_input_images(job.input_images):
            files.append(("image[]", (filename, content)))
        if job.mask:
            mask_path = Path(job.mask)
            if not mask_path.exists():
                raise ProviderCallError(f"Mask not found: {mask_path}", retryable=False)
            files.append(("mask", (mask_path.name, mask_path.read_bytes())))
        data = {
            "model": model,
            "prompt": prompt,
            "n": str(request_count),
            **{key_: str(value) for key_, value in params.items() if value is not None},
        }
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post(
                build_url(base_url, "images/edits"),
                headers=headers,
                data=data,
                files=files,
            )
    else:
        body = {
            "model": model,
            "prompt": prompt,
            "n": request_count,
            **{key_: value for key_, value in params.items() if value is not None},
        }
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post(
                build_url(base_url, "images/generations"),
                headers={**headers, "Content-Type": "application/json"},
                json=body,
            )

    if response.status_code >= 400:
        raise ProviderCallError(
            _response_error_message(response),
            retryable=response.status_code in RETRYABLE_STATUSES or response.status_code >= 500,
            status_code=response.status_code,
        )
    try:
        payload = response.json()
    except Exception as exc:
        raise ProviderCallError(f"Provider returned non-JSON response: {exc}", retryable=False) from exc
    images = await _extract_images(payload, result_mapping=None, fallback_mime=fallback_mime, timeout=timeout)
    if not images:
        raise ProviderCallError(f"Provider '{provider.id}' returned no recognizable images", retryable=False)
    return images


def _effective_params(
    config: AppConfig,
    provider: ProviderConfig,
    job: JobRecord,
    request_count: int,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "size": config.defaults.size,
        "quality": config.defaults.quality,
        "output_format": config.defaults.output_format,
        "moderation": config.defaults.moderation,
    }
    params.update(provider.params)
    params.update(job.params or {})
    params["n"] = request_count
    return params


def _pop_bool(params: dict[str, Any], key: str, fallback: bool = False) -> bool:
    if key not in params:
        return fallback
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
    return fallback


def _effective_prompt(
    config: AppConfig,
    provider: ProviderConfig,
    job: JobRecord,
    params: dict[str, Any],
) -> str:
    prompt = job.prompt
    append_size = _pop_bool(params, "append_size_to_prompt", provider.append_size_to_prompt)
    if append_size:
        prompt = append_size_instruction(prompt, params.get("size") or config.defaults.size)
    prompt_guard = _pop_bool(params, "prompt_rewrite_guard", provider.codex_cli)
    if not prompt_guard:
        prompt_guard = _pop_bool(params, "codex_cli", False)
    else:
        params.pop("codex_cli", None)
    if prompt_guard:
        prompt = add_prompt_rewrite_guard(prompt)
    return prompt


def _file_to_data_url(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise ProviderCallError(f"Input image not found: {p}", retryable=False)
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"


def _responses_input(job: JobRecord, prompt: str) -> list[dict[str, Any]]:
    if job.kind == "edit":
        content = [
            {"type": "input_image", "image_url": _file_to_data_url(path)}
            for path in job.input_images
        ]
        content.append(
            {
                "type": "input_text",
                "text": (
                    "Edit the provided reference image(s) according to this request. "
                    f"Generate the resulting image directly. Request: {prompt}"
                ),
            }
        )
        return [{"role": "user", "content": content}]

    return [
        {
            "role": "system",
            "content": (
                "You are an image generation assistant. When the user asks for an image, "
                "use the image_generation tool and do not answer with only text."
            ),
        },
        {"role": "user", "content": f"Generate this image: {prompt}"},
    ]


def _responses_image_tool(
    config: AppConfig,
    provider: ProviderConfig,
    job: JobRecord,
    params: dict[str, Any],
    output_format: str,
) -> dict[str, Any]:
    tool: dict[str, Any] = {
        "type": "image_generation",
        "output_format": output_format,
    }

    omit_action = bool(params.pop("omit_action", provider.type == "any"))
    action = str(params.pop("action", None) or ("edit" if job.kind == "edit" else "generate"))
    if not omit_action:
        tool["action"] = action

    omit_size = bool(params.pop("omit_size", provider.type == "any"))
    size = str(params.pop("size", None) or config.defaults.size)
    if not omit_size:
        tool["size"] = size

    quality = params.pop("quality", None)
    omit_quality = bool(params.pop("omit_quality", _is_codex_cli_provider(provider)) or params.pop("codex_cli", False))
    if quality is not None and not omit_quality and str(quality) not in {"", "auto"}:
        tool["quality"] = quality

    output_compression = params.pop("output_compression", None)
    if output_format != "png" and output_compression is not None:
        tool["output_compression"] = output_compression

    partial_images = params.pop("partial_images", None)
    if partial_images is None:
        partial_images = params.pop("responses_stream_partial_images", provider.responses_stream_partial_images)
    try:
        partial_count = min(3, max(0, int(partial_images or 0)))
    except (TypeError, ValueError):
        partial_count = 0
    if partial_count > 0:
        tool["partial_images"] = partial_count

    background = params.pop("background", None)
    if background is not None:
        tool["background"] = background

    # The OpenAI-compatible image endpoint accepts moderation, but the Responses image tool used by
    # the source playground intentionally does not send it.
    params.pop("moderation", None)

    if job.mask:
        tool["input_image_mask"] = {"image_url": _file_to_data_url(job.mask)}

    return tool


def _is_codex_cli_provider(provider: ProviderConfig) -> bool:
    return provider.codex_cli or str(provider.headers.get("originator") or provider.headers.get("Originator") or "").lower() == "codex_cli_rs"


async def _call_responses_image(
    config: AppConfig,
    provider: ProviderConfig,
    key: ProviderKeyConfig,
    job: JobRecord,
    request_count: int,
) -> list[ImagePayload]:
    httpx = _httpx()
    timeout = _request_timeout(config, provider)
    params = _effective_params(config, provider, job, request_count)
    prompt = _effective_prompt(config, provider, job, params)
    output_format = str(params.get("output_format") or config.defaults.output_format or "png")
    model = str(params.pop("model", None) or provider.model)
    if not model:
        raise ProviderCallError(f"Provider '{provider.id}' has no model", retryable=False)

    force_stream = _pop_bool(params, "force_responses_stream", provider.force_responses_stream)
    stream = True if force_stream else bool(params.pop("stream", True))
    omit_tool_choice = bool(params.pop("omit_tool_choice", provider.type == "any"))
    body: dict[str, Any] = {
        "model": model,
        "input": _responses_input(job, prompt),
        "tools": [_responses_image_tool(config, provider, job, params, output_format)],
        "stream": stream,
    }
    if omit_tool_choice:
        params.pop("tool_choice", None)
    else:
        body["tool_choice"] = params.pop("tool_choice", "required")
    for extra_key in ("reasoning", "metadata", "max_output_tokens", "previous_response_id"):
        if extra_key in params:
            body[extra_key] = params[extra_key]

    headers = {
        **_auth_headers(provider, key),
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json",
        "chatgpt-account-id": "",
        "version": "0.122.0",
        "originator": "codex_cli_rs",
        "session_id": f"gen-image-{job.id}",
    }
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.post(build_url(provider.base_url, "responses"), headers=headers, json=body)

    if response.status_code >= 400:
        raise ProviderCallError(
            _response_error_message(response),
            retryable=response.status_code in RETRYABLE_STATUSES or response.status_code >= 500,
            status_code=response.status_code,
        )

    images = await _extract_responses_images(
        response.text,
        content_type=response.headers.get("content-type", ""),
        fallback_mime=f"image/{output_format}",
        timeout=timeout,
    )
    if not images:
        raise ProviderCallError(f"Provider '{provider.id}' returned no recognizable response image", retryable=False)
    return images[:request_count]


async def _extract_responses_images(
    text: str,
    *,
    content_type: str,
    fallback_mime: str,
    timeout: float,
) -> list[ImagePayload]:
    payloads: list[Any] = []
    if "text/event-stream" in content_type.lower() or "\ndata:" in text or text.startswith("event:"):
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                payloads.append(json.loads(data))
            except json.JSONDecodeError:
                continue
    else:
        try:
            payloads.append(json.loads(text))
        except json.JSONDecodeError as exc:
            raise ProviderCallError(f"Responses API returned non-JSON body: {exc}", retryable=False) from exc

    final_values: list[str] = []
    partial_values: list[str] = []
    for payload in payloads:
        final_values.extend(_find_image_values(payload))
        partial_values.extend(_find_partial_image_values(payload))

    values = _unique_image_values(final_values)
    if not values and partial_values:
        # Some Responses-compatible routers stream only `partial_image_b64` events for
        # certain image_generation requests. Use the last partial as a best available
        # image only when no final result/url was emitted.
        values = _unique_image_values(partial_values[-1:])

    images: list[ImagePayload] = []
    for value in values:
        image = await _payload_from_value(value, fallback_mime=fallback_mime, timeout=timeout)
        image.metadata = {"source": "responses"}
        images.append(image)
    return images


def _find_image_values(source: Any) -> list[str]:
    values: list[str] = []

    def walk(value: Any, key: str = "") -> None:
        if isinstance(value, str):
            if is_http_url(value) or is_data_url(value):
                values.append(value)
            elif key in {"result", "b64_json", "base64", "image"} and _looks_like_base64_image(value):
                values.append(value)
            return
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                walk(child_value, str(child_key))

    walk(source)
    return _unique_image_values(values)


def _find_partial_image_values(source: Any) -> list[str]:
    values: list[str] = []

    def walk(value: Any, key: str = "") -> None:
        if isinstance(value, str):
            if key == "partial_image_b64" and _looks_like_base64_image(value):
                values.append(value)
            return
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                walk(child_value, str(child_key))

    walk(source)
    return _unique_image_values(values)


def _unique_image_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in values:
        marker = item[:80]
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(item)
    return unique


def _looks_like_base64_image(value: str) -> bool:
    if value.startswith(("iVBOR", "/9j/", "UklGR")):
        return True
    return len(value) > 1000 and all(ch.isalnum() or ch in "+/=\n\r" for ch in value[:200])


async def _call_custom_http(
    config: AppConfig,
    provider: ProviderConfig,
    key: ProviderKeyConfig,
    job: JobRecord,
    request_count: int,
) -> list[ImagePayload]:
    httpx = _httpx()
    timeout = _request_timeout(config, provider)
    params = _effective_params(config, provider, job, request_count)
    prompt = _effective_prompt(config, provider, job, params)
    fallback_mime = f"image/{params.get('output_format', config.defaults.output_format)}"
    mapping = provider.edit_submit if job.kind == "edit" and provider.edit_submit else provider.submit
    if not mapping:
        raise ProviderCallError(f"Provider '{provider.id}' custom-http missing [submit] mapping", retryable=False)

    context = _template_context(config, provider, key, job, params, request_count, prompt)
    method = str(mapping.get("method") or "POST").upper()
    content_type = str(mapping.get("content_type") or "json")
    query = resolve_template(mapping.get("query") or {}, context)
    url = build_url(provider.base_url, str(mapping.get("path") or ""), query)
    headers = _auth_headers(provider, key)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        if method == "GET":
            response = await client.get(url, headers=headers)
        elif content_type == "multipart":
            data = resolve_template(mapping.get("body") or {}, context)
            files = _custom_files(mapping, job)
            response = await client.request(method, url, headers=headers, data=data, files=files)
        else:
            body = resolve_template(mapping.get("body") or {}, context)
            response = await client.request(
                method,
                url,
                headers={**headers, "Content-Type": "application/json"},
                json=body,
            )

    if response.status_code >= 400:
        raise ProviderCallError(
            _response_error_message(response),
            retryable=response.status_code in RETRYABLE_STATUSES or response.status_code >= 500,
            status_code=response.status_code,
        )
    payload = response.json()
    task_id_path = mapping.get("task_id_path")
    task_id = str(get_by_path(payload, str(task_id_path)) or "").strip() if task_id_path else ""
    if task_id:
        if not provider.poll:
            raise ProviderCallError("Custom provider returned task id but has no [poll] mapping", retryable=False)
        payload = await _poll_custom_task(config, provider, key, task_id, context)
        result_mapping = dict(provider.poll.get("result") or {})
    else:
        result_mapping = dict(mapping.get("result") or {})

    images = await _extract_images(
        payload,
        result_mapping=result_mapping,
        fallback_mime=fallback_mime,
        timeout=timeout,
    )
    if not images:
        raise ProviderCallError(
            f"Provider '{provider.id}' returned no recognizable images. Raw payload: {json_dumps(payload)[:1000]}",
            retryable=False,
        )
    return images


def _template_context(
    config: AppConfig,
    provider: ProviderConfig,
    key: ProviderKeyConfig,
    job: JobRecord,
    params: dict[str, Any],
    request_count: int,
    prompt: str,
) -> dict[str, Any]:
    return {
        "provider": {"id": provider.id, "model": provider.model, "base_url": provider.base_url},
        "key": {"id": key.id},
        "model": provider.model,
        "prompt": prompt,
        "n": request_count,
        "params": params,
        "defaults": {
            "size": config.defaults.size,
            "quality": config.defaults.quality,
            "output_format": config.defaults.output_format,
            "moderation": config.defaults.moderation,
        },
        "input_images": {"paths": job.input_images, "count": len(job.input_images)},
        "mask": {"path": job.mask},
        "job": {"id": job.id, "kind": job.kind},
    }


def _custom_files(mapping: dict[str, Any], job: JobRecord) -> list[tuple[str, tuple[str, bytes]]]:
    files: list[tuple[str, tuple[str, bytes]]] = []
    input_images = _load_input_images(job.input_images)
    for item in mapping.get("files") or []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "image[]")
        source = str(item.get("source") or "inputImages")
        if source == "inputImages":
            for filename, content in input_images:
                files.append((field, (filename, content)))
        elif source == "mask" and job.mask:
            mask_path = Path(job.mask)
            if not mask_path.exists():
                raise ProviderCallError(f"Mask not found: {mask_path}", retryable=False)
            files.append((field, (mask_path.name, mask_path.read_bytes())))
    return files


async def _poll_custom_task(
    config: AppConfig,
    provider: ProviderConfig,
    key: ProviderKeyConfig,
    task_id: str,
    base_context: dict[str, Any],
) -> Any:
    httpx = _httpx()
    timeout = _request_timeout(config, provider)
    poll = provider.poll
    status_path = str(poll.get("status_path") or "")
    success_values = {str(v) for v in poll.get("success_values") or ["succeeded", "completed", "SUCCESS"]}
    failure_values = {str(v) for v in poll.get("failure_values") or ["failed", "cancelled", "FAILURE"]}
    interval = float(poll.get("interval_seconds") or config.queue.poll_interval_seconds)
    max_polls = int(poll.get("max_polls") or 120)
    headers = _auth_headers(provider, key)

    context = dict(base_context)
    context["task_id"] = task_id
    for _ in range(max_polls):
        query = resolve_template(poll.get("query") or {}, context)
        path = str(poll.get("path") or "").replace("{task_id}", task_id).replace("{taskId}", task_id)
        url = build_url(provider.base_url, path, query)
        method = str(poll.get("method") or "GET").upper()
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.request(method, url, headers=headers)
        if response.status_code >= 400:
            if response.status_code in RETRYABLE_STATUSES or response.status_code >= 500:
                await asyncio.sleep(interval)
                continue
            raise ProviderCallError(_response_error_message(response), retryable=False, status_code=response.status_code)
        payload = response.json()
        status = str(get_by_path(payload, status_path) or "")
        if status in failure_values:
            error_path = str(poll.get("error_path") or "")
            error = get_by_path(payload, error_path) if error_path else None
            raise ProviderCallError(str(error or f"Custom task failed: {status}"), retryable=False)
        if status in success_values:
            return payload
        await asyncio.sleep(interval)
    raise ProviderCallError(f"Custom task polling timed out after {max_polls} polls", retryable=True)


def _response_error_message(response: Any) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
            if isinstance(error, str):
                return error
            if data.get("message"):
                return str(data["message"])
            if data.get("detail"):
                return str(data["detail"])
    except Exception:
        pass
    text = getattr(response, "text", "")
    return f"HTTP {response.status_code}: {str(text)[:500]}"


def enabled_keys(provider: ProviderConfig) -> list[ProviderKeyConfig]:
    return [key for key in provider.keys if key.enabled]


def request_size_for_key(provider: ProviderConfig, key: ProviderKeyConfig, remaining: int) -> int:
    return min(max(1, int(remaining)), _key_images_per_request(provider, key))
