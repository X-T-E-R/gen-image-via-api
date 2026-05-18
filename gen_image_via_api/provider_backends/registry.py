from __future__ import annotations

from .legacy import ImagePayload, ProviderCallError
from .legacy import _call_custom_http, _call_mock, _call_openai_images, _call_responses_image
from .idlecloud import call_idlecloud
from .nai import call_nai


async def call_provider(config, provider, key, job, request_count) -> list[ImagePayload]:
    if provider.type == "mock":
        return await _call_mock(provider, key, job, request_count)
    if provider.type == "openai-images":
        return await _call_openai_images(config, provider, key, job, request_count)
    if provider.type in {"responses-image", "any"}:
        return await _call_responses_image(config, provider, key, job, request_count)
    if provider.type == "custom-http":
        return await _call_custom_http(config, provider, key, job, request_count)
    if provider.type == "nai":
        return await call_nai(config, provider, key, job, request_count)
    if provider.type == "idlecloud":
        return await call_idlecloud(config, provider, key, job, request_count)
    raise ProviderCallError(f"Unsupported provider type: {provider.type}", retryable=False)
