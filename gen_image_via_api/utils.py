from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


MOCK_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)

MIME_TO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, fallback: Any = None) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def is_http_url(value: Any) -> bool:
    return isinstance(value, str) and value.lower().startswith(("http://", "https://"))


def is_data_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("data:")


def parse_data_url(value: str, fallback_mime: str = "image/png") -> tuple[bytes, str]:
    if not is_data_url(value):
        return base64.b64decode(value), fallback_mime
    header, _, payload = value.partition(",")
    mime = fallback_mime
    if header.startswith("data:"):
        mime = header[5:].split(";", 1)[0] or fallback_mime
    if ";base64" in header:
        return base64.b64decode(payload), mime
    return payload.encode("utf-8"), mime


def extension_for_mime(mime: str, fallback: str = "png") -> str:
    return MIME_TO_EXT.get(mime.lower(), fallback)


def get_by_path(source: Any, path: str | None) -> Any:
    if not path:
        return source
    current = source
    for part in [p for p in path.split(".") if p]:
        if current is None:
            return None
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def get_all_by_path(source: Any, path: str | None) -> list[Any]:
    if not path:
        return [source]
    current = [source]
    for part in [p for p in path.split(".") if p]:
        next_items: list[Any] = []
        for item in current:
            if item is None:
                continue
            if part == "*":
                if isinstance(item, list):
                    next_items.extend(item)
                elif isinstance(item, dict):
                    next_items.extend(item.values())
                continue
            if isinstance(item, list) and part.isdigit():
                index = int(part)
                if 0 <= index < len(item):
                    next_items.append(item[index])
                continue
            if isinstance(item, dict):
                next_items.append(item.get(part))
        current = next_items
    out: list[Any] = []
    for item in current:
        if item is None:
            continue
        if isinstance(item, list):
            out.extend(x for x in item if x is not None)
        else:
            out.append(item)
    return out


def resolve_template(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return get_by_path(context, value[1:])
    if isinstance(value, list):
        return [
            resolved
            for item in value
            if (resolved := resolve_template(item, context)) not in (None, "", [])
        ]
    if isinstance(value, dict):
        return {
            str(key): resolved
            for key, item in value.items()
            if (resolved := resolve_template(item, context)) not in (None, "", [])
        }
    return value


def build_url(base_url: str, path: str, query: dict[str, Any] | None = None) -> str:
    base = str(base_url or "").rstrip("/")
    clean_path = str(path or "").lstrip("/")
    url = f"{base}/{clean_path}" if base else clean_path
    if query:
        rendered = {k: str(v) for k, v in query.items() if v is not None and str(v) != ""}
        if rendered:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urlencode(rendered)}"
    return url


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def safe_prefix(value: str, fallback: str = "image") -> str:
    cleaned = []
    for char in value.lower():
        if char.isalnum():
            cleaned.append(char)
        elif char in {"-", "_", " "}:
            cleaned.append("-")
    text = "".join(cleaned).strip("-")
    while "--" in text:
        text = text.replace("--", "-")
    return text[:72] or fallback
