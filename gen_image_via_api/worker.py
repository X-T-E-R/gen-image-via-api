from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .config import AppConfig, ProviderConfig, ProviderKeyConfig
from .providers import ProviderCallError, call_provider, enabled_keys
from .queue import ImageQueue, JobRecord
from .utils import ensure_parent, extension_for_mime, safe_prefix


class Worker:
    def __init__(self, config: AppConfig, queue: ImageQueue):
        self.config = config
        self.queue = queue
        self.lock = asyncio.Lock()
        self.active_routes: dict[tuple[str, str], int] = {}

    async def run(
        self,
        *,
        watch: bool = False,
        target_job_id: str | None = None,
        recover: bool = True,
    ) -> dict[str, Any]:
        if recover:
            self.queue.recover_running()
        worker_count = self._effective_worker_count(target_job_id)
        workers = [
            asyncio.create_task(self._loop(watch=watch, target_job_id=target_job_id))
            for _ in range(worker_count)
        ]
        counts = await asyncio.gather(*workers)
        return {
            "worker_count": worker_count,
            "processed": sum(item["processed"] for item in counts),
            "succeeded": sum(item["succeeded"] for item in counts),
            "failed": sum(item["failed"] for item in counts),
        }

    async def run_until_done(self, job_id: str) -> JobRecord:
        while True:
            job = self.queue.get_job(job_id)
            if job is None:
                raise RuntimeError(f"Unknown job: {job_id}")
            if job.status in {"succeeded", "failed", "cancelled"}:
                return job
            await self.run(watch=False, target_job_id=job_id, recover=False)
            await asyncio.sleep(0.05)

    async def _loop(self, *, watch: bool, target_job_id: str | None) -> dict[str, int]:
        stats = {"processed": 0, "succeeded": 0, "failed": 0}
        while True:
            claimed = await self._claim_next_available(target_job_id=target_job_id)
            if claimed is None:
                if watch and not target_job_id:
                    await asyncio.sleep(1)
                    continue
                return stats
            job, provider, key = claimed
            stats["processed"] += 1
            try:
                await self._execute_job(job, initial_route=(provider, key))
                async with self.lock:
                    self.queue.set_job_status(job.id, "succeeded")
                stats["succeeded"] += 1
            except Exception as exc:
                message = str(exc)
                async with self.lock:
                    requeued = self.queue.requeue_if_possible(job, message)
                if not requeued:
                    stats["failed"] += 1

    def _effective_worker_count(self, target_job_id: str | None = None) -> int:
        target_provider = self.config.defaults.provider
        if target_job_id:
            job = self.queue.get_job(target_job_id)
            if job and job.provider_id:
                target_provider = job.provider_id

        capacities = [
            _provider_capacity(provider)
            for provider in self.config.providers
            if provider.enabled and (target_provider is None or provider.id == target_provider)
        ]
        capacity = max(1, sum(capacities))
        configured = int(self.config.queue.concurrency)
        if configured <= 0:
            return capacity
        return max(1, min(configured, capacity))

    async def _claim_next_available(
        self,
        *,
        target_job_id: str | None,
    ) -> tuple[JobRecord, ProviderConfig, ProviderKeyConfig] | None:
        async with self.lock:
            for candidate in self.queue.queued_jobs(target_job_id=target_job_id):
                route = self._select_route_locked(candidate, set(), reserve=True)
                if route is None:
                    continue
                provider, key = route
                job = self.queue.claim_job(candidate.id)
                if job is None:
                    self._release_route_locked(provider, key)
                    continue
                return job, provider, key
        return None

    async def _execute_job(
        self,
        job: JobRecord,
        *,
        initial_route: tuple[ProviderConfig, ProviderKeyConfig] | None = None,
    ) -> None:
        desired = max(1, int(job.desired_count))
        produced = len(self.queue.results_for_job(job.id))
        if produced >= desired:
            if initial_route is not None:
                provider, key = initial_route
                async with self.lock:
                    self._release_route_locked(provider, key)
            return
        route_capacity = max(1, self._route_capacity_for_job(job))
        worker_count = min(desired - produced, route_capacity)
        remaining_requests = desired - produced
        next_result_index = produced
        index_lock = asyncio.Lock()

        async with self.lock:
            self.queue.event(
                job.id,
                "info",
                "fanout started",
                {
                    "desired_count": desired,
                    "existing_results": produced,
                    "parallel_requests": worker_count,
                    "route_capacity": route_capacity,
                },
            )

        async def produce(route: tuple[ProviderConfig, ProviderKeyConfig] | None = None) -> None:
            nonlocal remaining_requests, next_result_index
            reserved_route = route
            while True:
                async with index_lock:
                    if remaining_requests <= 0:
                        break
                    remaining_requests -= 1
                image, provider_id, key_id = await self._request_one_image(job, reserved_route)
                reserved_route = None
                async with index_lock:
                    next_result_index += 1
                    result_index = next_result_index
                path = self._output_path(job, result_index, image.mime)
                ensure_parent(path)
                path.write_bytes(image.data)
                async with self.lock:
                    self.queue.add_result(
                        job_id=job.id,
                        result_index=result_index,
                        path=str(path),
                        raw_url=image.raw_url,
                        metadata={
                            "provider": provider_id,
                            "key": key_id,
                            **(image.metadata or {}),
                        },
                    )
            if reserved_route is not None:
                provider, key = reserved_route
                async with self.lock:
                    self._release_route_locked(provider, key)

        tasks = [
            asyncio.create_task(produce(initial_route if slot == 0 else None))
            for slot in range(worker_count)
        ]
        try:
            await asyncio.gather(*tasks)
        except Exception:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _request_one_image(
        self,
        job: JobRecord,
        reserved_route: tuple[ProviderConfig, ProviderKeyConfig] | None,
    ):
        route_failures = 0
        max_route_failures = max(1, self._route_count_for_job(job) * 2)
        excluded_for_round: set[tuple[str, str]] = set()

        while True:
            if reserved_route is not None:
                provider, key = reserved_route
                reserved_route = None
            else:
                provider, key = await self._wait_for_route(job, excluded_for_round)

            try:
                images = await call_provider(self.config, provider, key, job, 1)
                if not images:
                    raise RuntimeError(f"Provider '{provider.id}' returned no images")
                return images[0], provider.id, key.id
            except ProviderCallError as exc:
                route_failures += 1
                async with self.lock:
                    self.queue.event(
                        job.id,
                        "warning" if exc.retryable else "error",
                        "provider request failed",
                        {
                            "provider": provider.id,
                            "key": key.id,
                            "retryable": exc.retryable,
                            "status_code": exc.status_code,
                            "error": str(exc),
                        },
                    )
                if exc.retryable and route_failures < max_route_failures:
                    excluded_for_round.add((provider.id, key.id))
                    continue
                raise
            finally:
                async with self.lock:
                    self._release_route_locked(provider, key)

    async def _wait_for_route(
        self,
        job: JobRecord,
        excluded: set[tuple[str, str]],
    ) -> tuple[ProviderConfig, ProviderKeyConfig]:
        while True:
            async with self.lock:
                if not self._has_ready_route_locked(job, excluded) and excluded:
                    excluded.clear()
                if not self._has_ready_route_locked(job, excluded):
                    raise RuntimeError(f"No enabled provider/key route is available for job {job.id}")
                route = self._select_route_locked(job, excluded, reserve=True)
                if route is not None:
                    return route
            await asyncio.sleep(0.25)

    def _select_route_locked(
        self,
        job: JobRecord,
        excluded: set[tuple[str, str]],
        *,
        reserve: bool,
    ) -> tuple[ProviderConfig, ProviderKeyConfig] | None:
        target_provider = job.provider_id or self.config.defaults.provider
        providers = sorted(
            [
                provider
                for provider in self.config.providers
                if provider.supports(job.kind)
                and (target_provider is None or provider.id == target_provider)
            ],
            key=lambda item: (item.priority, item.id),
        )
        for provider in providers:
            keys = [key for key in enabled_keys(provider) if (provider.id, key.id) not in excluded]
            if not keys:
                continue
            keys = sorted(keys, key=lambda item: item.id)
            last_key_id = self.queue.get_last_key_id(provider.id)
            for offset in range(len(keys)):
                last_index = next((i for i, key in enumerate(keys) if key.id == last_key_id), -1)
                selected = keys[(last_index + 1 + offset + len(keys)) % len(keys)]
                route_id = (provider.id, selected.id)
                if self.active_routes.get(route_id, 0) >= max(1, int(selected.max_concurrent_requests)):
                    continue
                secret = selected.resolve_secret()
                if provider.type != "mock" and not secret:
                    self.queue.event(
                        None,
                        "warning",
                        "provider key has no secret",
                        {"provider": provider.id, "key": selected.id, "secret": selected.secret_label()},
                    )
                    excluded.add((provider.id, selected.id))
                    continue
                self.queue.set_last_key_id(provider.id, selected.id)
                if reserve:
                    self.active_routes[route_id] = self.active_routes.get(route_id, 0) + 1
                return provider, selected
        return None

    def _release_route_locked(self, provider: ProviderConfig, key: ProviderKeyConfig) -> None:
        route_id = (provider.id, key.id)
        current = self.active_routes.get(route_id, 0)
        if current <= 1:
            self.active_routes.pop(route_id, None)
        else:
            self.active_routes[route_id] = current - 1

    def _route_count_for_job(self, job: JobRecord) -> int:
        return sum(
            1
            for provider in self.config.providers
            if provider.supports(job.kind)
            and ((job.provider_id or self.config.defaults.provider) is None or provider.id == (job.provider_id or self.config.defaults.provider))
            for key in enabled_keys(provider)
            if provider.type == "mock" or key.resolve_secret()
        )

    def _route_capacity_for_job(self, job: JobRecord) -> int:
        target_provider = job.provider_id or self.config.defaults.provider
        return sum(
            _provider_capacity(provider)
            for provider in self.config.providers
            if provider.supports(job.kind) and (target_provider is None or provider.id == target_provider)
        )

    def _has_ready_route_locked(self, job: JobRecord, excluded: set[tuple[str, str]]) -> bool:
        target_provider = job.provider_id or self.config.defaults.provider
        for provider in self.config.providers:
            if not provider.supports(job.kind):
                continue
            if target_provider is not None and provider.id != target_provider:
                continue
            for key in enabled_keys(provider):
                if (provider.id, key.id) in excluded:
                    continue
                if provider.type == "mock" or key.resolve_secret():
                    return True
        return False

    def _output_path(self, job: JobRecord, index: int, mime: str) -> Path:
        out_dir = Path(job.out_dir).expanduser() if job.out_dir else self.config.queue.output_dir
        if not out_dir.is_absolute():
            out_dir = self.config.base_dir / out_dir
        prefix = safe_prefix(job.out_prefix or job.id, fallback=job.id)
        ext = extension_for_mime(mime, fallback=str(job.params.get("output_format") or self.config.defaults.output_format))
        if job.desired_count == 1:
            filename = f"{prefix}.{ext}"
        else:
            filename = f"{prefix}-{index}.{ext}"
        path = out_dir / filename
        if not path.exists():
            return path
        stem = path.stem
        for suffix in range(2, 10_000):
            candidate = path.with_name(f"{stem}-{suffix}{path.suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Could not find available output path for {path}")


def _provider_capacity(provider: ProviderConfig) -> int:
    key_capacity = sum(
        max(1, int(key.max_concurrent_requests))
        for key in enabled_keys(provider)
        if provider.type == "mock" or key.resolve_secret()
    )
    if provider.max_concurrent_requests is not None:
        key_capacity = min(key_capacity, max(1, int(provider.max_concurrent_requests)))
    return max(0, key_capacity)


async def run_queue(config: AppConfig, *, watch: bool = False, target_job_id: str | None = None) -> dict[str, Any]:
    queue = ImageQueue(config.queue.db)
    try:
        return await Worker(config, queue).run(watch=watch, target_job_id=target_job_id)
    finally:
        queue.close()
