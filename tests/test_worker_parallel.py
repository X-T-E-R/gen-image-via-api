from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

from gen_image_via_api.config import AppConfig, DefaultsConfig, ProviderConfig, ProviderKeyConfig, QueueConfig
from gen_image_via_api.providers import ImagePayload
from gen_image_via_api.queue import ImageQueue
import gen_image_via_api.worker as worker_mod


class WorkerParallelTests(unittest.TestCase):
    def test_single_count_job_fans_out_to_route_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = AppConfig(
                path=root / "gen-image.toml",
                base_dir=root,
                queue=QueueConfig(
                    db=root / "queue.sqlite3",
                    output_dir=root / "out",
                    concurrency=0,
                ),
                defaults=DefaultsConfig(provider=None),
                providers=(
                    ProviderConfig(
                        id="p1",
                        type="mock",
                        priority=1,
                        keys=(
                            ProviderKeyConfig(id="p1-a", api_key="mock", max_concurrent_requests=1),
                            ProviderKeyConfig(id="p1-b", api_key="mock", max_concurrent_requests=1),
                        ),
                    ),
                    ProviderConfig(
                        id="p2",
                        type="mock",
                        priority=2,
                        keys=(
                            ProviderKeyConfig(id="p2-a", api_key="mock", max_concurrent_requests=2),
                        ),
                    ),
                ),
            )
            queue = ImageQueue(config.queue.db)
            active = 0
            peak = 0
            lock = asyncio.Lock()

            async def fake_call_provider(config, provider, key, job, request_count):  # noqa: ANN001
                nonlocal active, peak
                async with lock:
                    active += 1
                    peak = max(peak, active)
                await asyncio.sleep(0.05)
                async with lock:
                    active -= 1
                return [
                    ImagePayload(
                        data=b"fake-image",
                        mime="image/png",
                        metadata={"provider": provider.id, "key": key.id},
                    )
                ]

            original = worker_mod.call_provider
            worker_mod.call_provider = fake_call_provider
            try:
                job_id = queue.enqueue(kind="generate", prompt="parallel", desired_count=8, out_prefix="parallel")
                final_job = asyncio.run(worker_mod.Worker(config, queue).run_until_done(job_id))
                results = queue.results_for_job(job_id)
            finally:
                worker_mod.call_provider = original
                queue.close()

            self.assertEqual(final_job.status, "succeeded")
            self.assertEqual(len(results), 8)
            self.assertEqual(peak, 4)


if __name__ == "__main__":
    unittest.main()
