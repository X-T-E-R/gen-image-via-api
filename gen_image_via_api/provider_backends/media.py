from __future__ import annotations

import io
import mimetypes
import zipfile
from typing import Any

from .legacy import ImagePayload, ProviderCallError, _httpx
from ..utils import is_data_url, is_http_url, parse_data_url


_IMAGE_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def mime_from_image_name(name: str, fallback: str = "image/png") -> str:
    guessed = mimetypes.guess_type(name)[0]
    if guessed and guessed.startswith("image/"):
        return guessed
    lowered = name.lower()
    for suffix, mime in _IMAGE_MIME_BY_EXT.items():
        if lowered.endswith(suffix):
            return mime
    return fallback


def is_zip_bytes(data: bytes) -> bool:
    return data.startswith(b"PK\x03\x04") or data.startswith(b"PK\x05\x06") or data.startswith(b"PK\x07\x08")


def extract_images_from_zip(data: bytes, *, source: str = "zip") -> list[ImagePayload]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ProviderCallError("Provider returned invalid zip image payload", retryable=False) from exc

    images: list[ImagePayload] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        mime = mime_from_image_name(info.filename, fallback="")
        if not mime:
            continue
        content = archive.read(info)
        if not content:
            continue
        images.append(
            ImagePayload(
                data=content,
                mime=mime,
                metadata={"source": source, "filename": info.filename},
            )
        )
    if not images:
        raise ProviderCallError("Provider zip payload contained no recognizable image files", retryable=False)
    return images


def payloads_from_bytes(
    data: bytes,
    *,
    content_type: str = "",
    fallback_mime: str = "image/png",
    raw_url: str | None = None,
    source: str = "response",
) -> list[ImagePayload]:
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime in {"application/zip", "application/x-zip-compressed"} or is_zip_bytes(data):
        images = extract_images_from_zip(data, source=source)
        if raw_url:
            for image in images:
                image.raw_url = raw_url
        return images
    if mime.startswith("image/"):
        return [ImagePayload(data=data, mime=mime, raw_url=raw_url, metadata={"source": source})]
    return [ImagePayload(data=data, mime=fallback_mime, raw_url=raw_url, metadata={"source": source})]


async def payloads_from_value(value: str, *, timeout: float, fallback_mime: str = "image/png") -> list[ImagePayload]:
    if is_http_url(value):
        httpx = _httpx()
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(value)
        if response.status_code >= 400:
            raise ProviderCallError(f"Image URL download failed: HTTP {response.status_code}", retryable=response.status_code >= 500)
        return payloads_from_bytes(
            response.content,
            content_type=response.headers.get("content-type", ""),
            fallback_mime=fallback_mime,
            raw_url=value,
            source="url",
        )
    data, mime = parse_data_url(value, fallback_mime)
    return payloads_from_bytes(data, content_type=mime if is_data_url(value) else "", fallback_mime=mime, source="base64")


def json_payload(response: Any) -> Any:
    try:
        return response.json()
    except Exception as exc:
        raise ProviderCallError(f"Provider returned non-JSON response: {exc}", retryable=False) from exc
