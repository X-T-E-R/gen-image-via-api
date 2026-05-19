from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from gen_image_via_api.config import AppConfig, DefaultsConfig, ProviderConfig, ProviderKeyConfig, QueueConfig
from gen_image_via_api.provider_backends.idlecloud import call_idlecloud
from gen_image_via_api.provider_backends.nai import call_nai
from gen_image_via_api.queue import JobRecord
from gen_image_via_api.utils import MOCK_PNG_BASE64


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_data=None, content: bytes = b"", headers=None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data
        self.content = content
        self.headers = headers or {}
        self.text = text or (json.dumps(json_data) if json_data is not None else "")

    def json(self):
        if self._json_data is None:
            raise ValueError("no json")
        return self._json_data


class _FakeAsyncClient:
    calls: list[tuple[str, str, dict]] = []
    queue: list[_FakeResponse] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.queue.pop(0)

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.queue.pop(0)


class _FakeHttpx:
    AsyncClient = _FakeAsyncClient


def _zip_png() -> bytes:
    out = io.BytesIO()
    import base64

    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("image_0.png", base64.b64decode(MOCK_PNG_BASE64))
    return out.getvalue()


def _config(provider: ProviderConfig) -> AppConfig:
    root = Path(tempfile.gettempdir())
    return AppConfig(
        path=root / "gen-image.toml",
        base_dir=root,
        queue=QueueConfig(db=root / "queue.sqlite3", output_dir=root / "out", poll_interval_seconds=0.0),
        defaults=DefaultsConfig(size="1024x768", output_format="png"),
        providers=(provider,),
    )


def _job(kind: str = "generate", *, params=None, input_images=None, mask=None) -> JobRecord:
    return JobRecord(
        id="job",
        kind=kind,
        status="queued",
        provider_id=None,
        prompt="1girl",
        input_images=input_images or [],
        mask=mask,
        params=params or {},
        desired_count=1,
        out_dir=None,
        out_prefix=None,
        attempts=0,
        max_attempts=1,
        priority=0,
        error=None,
        created_at="",
        updated_at="",
        started_at=None,
        finished_at=None,
    )


class NaiAndIdleCloudProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeAsyncClient.calls = []
        _FakeAsyncClient.queue = []

    def test_nai_posts_novelai_shape_and_extracts_zip_images(self) -> None:
        import gen_image_via_api.provider_backends.nai as nai_mod

        original = nai_mod._httpx
        nai_mod._httpx = lambda: _FakeHttpx
        try:
            _FakeAsyncClient.queue = [
                _FakeResponse(content=_zip_png(), headers={"content-type": "application/zip"})
            ]
            provider = ProviderConfig(id="nai", type="nai", model="nai-diffusion-4-5-full", base_url="https://api.example/api")
            images = asyncio.run(
                call_nai(_config(provider), provider, ProviderKeyConfig(id="k", api_key="secret"), _job(params={"seed": 123}), 1)
            )
        finally:
            nai_mod._httpx = original

        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].mime, "image/png")
        method, url, kwargs = _FakeAsyncClient.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://api.example/api/ai/generate-image")
        self.assertEqual(kwargs["json"]["action"], "generate")
        self.assertEqual(kwargs["json"]["input"], "nai-diffusion-4-5-full")
        self.assertEqual(kwargs["json"]["prompt"], "1girl")
        self.assertEqual(kwargs["json"]["parameters"]["width"], 1024)
        self.assertEqual(kwargs["json"]["parameters"]["height"], 768)
        self.assertEqual(kwargs["json"]["parameters"]["seed"], 123)

    def test_idlecloud_submits_job_polls_and_extracts_base64_image(self) -> None:
        import gen_image_via_api.provider_backends.idlecloud as idle_mod

        original_httpx = idle_mod._httpx
        idle_mod._httpx = lambda: _FakeHttpx
        try:
            _FakeAsyncClient.queue = [
                _FakeResponse(json_data={"job_id": "abc"}),
                _FakeResponse(json_data={"status": "pending", "queue_position": 1}),
                _FakeResponse(json_data={"status": "completed", "image_base64": MOCK_PNG_BASE64}),
            ]
            provider = ProviderConfig(id="idle", type="idlecloud", model="nai-diffusion-4-5-full", base_url="https://api.example/api")
            images = asyncio.run(
                call_idlecloud(_config(provider), provider, ProviderKeyConfig(id="k", api_key="secret"), _job(params={"negative_prompt": "bad"}), 1)
            )
        finally:
            idle_mod._httpx = original_httpx

        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].mime, "image/png")
        self.assertEqual(_FakeAsyncClient.calls[0][0], "POST")
        self.assertEqual(_FakeAsyncClient.calls[0][1], "https://api.example/api/generate_image")
        body = _FakeAsyncClient.calls[0][2]["json"]
        self.assertEqual(body["positivePrompt"], "1girl")
        self.assertEqual(body["negativePrompt"], "bad")
        self.assertEqual(body["width"], 1024)
        self.assertEqual(body["height"], 768)
        self.assertEqual(_FakeAsyncClient.calls[-1][1], "https://api.example/api/get_result/abc")

    def test_idlecloud_passes_v4_character_control_fields_through(self) -> None:
        import gen_image_via_api.provider_backends.idlecloud as idle_mod

        original_httpx = idle_mod._httpx
        idle_mod._httpx = lambda: _FakeHttpx
        try:
            _FakeAsyncClient.queue = [
                _FakeResponse(json_data={"job_id": "abc"}),
                _FakeResponse(json_data={"status": "completed", "image_base64": MOCK_PNG_BASE64}),
            ]
            provider = ProviderConfig(id="idle", type="idlecloud", model="nai-diffusion-4-5-full", base_url="https://api.example/api")
            images = asyncio.run(
                call_idlecloud(
                    _config(provider),
                    provider,
                    ProviderKeyConfig(id="k", api_key="secret"),
                    _job(
                        params={
                            "use_coords": True,
                            "characterPrompts": [{"prompt": "left", "uc": "bad left", "center": {"x": 0.2, "y": 0.3}}],
                            "v4_prompt_char_captions": [{"char_caption": "left", "centers": [{"x": 0.2, "y": 0.3}]}],
                        }
                    ),
                    1,
                )
            )
        finally:
            idle_mod._httpx = original_httpx

        self.assertEqual(len(images), 1)
        body = _FakeAsyncClient.calls[0][2]["json"]
        self.assertTrue(body["use_coords"])
        self.assertEqual(body["characterPrompts"][0]["prompt"], "left")
        self.assertEqual(body["v4_prompt_char_captions"][0]["centers"][0]["x"], 0.2)


if __name__ == "__main__":
    unittest.main()

