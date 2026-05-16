from __future__ import annotations

import asyncio
import json
from pathlib import Path
import unittest

from gen_image_via_api.config import AppConfig, DefaultsConfig, ProviderConfig, QueueConfig
from gen_image_via_api.queue import JobRecord
from gen_image_via_api.providers import _extract_responses_images
from gen_image_via_api.providers import _responses_image_tool
from gen_image_via_api.utils import MOCK_PNG_BASE64


ROOT_PATH = Path(__file__).resolve()


def _sse(*payloads: dict[str, object]) -> str:
    return "\n".join(f"data: {json.dumps(payload)}" for payload in payloads)


class ResponsesImageExtractionTests(unittest.TestCase):
    def test_extracts_partial_image_when_no_final_result_exists(self) -> None:
        images = asyncio.run(
            _extract_responses_images(
                _sse({"type": "response.image_generation_call.partial_image", "partial_image_b64": MOCK_PNG_BASE64}),
                content_type="text/event-stream",
                fallback_mime="image/png",
                timeout=1,
            )
        )

        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].mime, "image/png")
        self.assertTrue(images[0].data.startswith(b"\x89PNG"))

    def test_prefers_final_result_over_partial_image(self) -> None:
        final_data_url = f"data:image/png;base64,{MOCK_PNG_BASE64}"
        images = asyncio.run(
            _extract_responses_images(
                _sse(
                    {"type": "response.image_generation_call.partial_image", "partial_image_b64": MOCK_PNG_BASE64},
                    {"type": "response.output_item.done", "item": {"result": final_data_url}},
                ),
                content_type="text/event-stream",
                fallback_mime="image/png",
                timeout=1,
            )
        )

        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].mime, "image/png")

    def test_any_provider_type_omits_browser_incompatible_tool_fields(self) -> None:
        config = AppConfig(
            path=ROOT_PATH,
            base_dir=ROOT_PATH.parent,
            queue=QueueConfig(db=ROOT_PATH.parent / "queue.sqlite3", output_dir=ROOT_PATH.parent / "out"),
            defaults=DefaultsConfig(size="1024x1024"),
            providers=(),
        )
        job = JobRecord(
            id="job",
            kind="edit",
            status="queued",
            provider_id="any",
            prompt="edit",
            input_images=[],
            mask=None,
            params={},
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
        tool = _responses_image_tool(
            config,
            ProviderConfig(id="any", type="any"),
            job,
            {"size": "1536x1024"},
            "png",
        )

        self.assertEqual(tool, {"type": "image_generation", "output_format": "png"})


if __name__ == "__main__":
    unittest.main()
