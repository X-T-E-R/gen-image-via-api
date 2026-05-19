from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import shlex
import sys
import time
from typing import Any

from .config import DEFAULT_CONFIG_NAME, ConfigError, load_config, resolve_config_path, write_example_config
from .delivery import SendError, send_paths
from .generation import (
    apply_prompt_template,
    normalize_image_size,
    size_from_aspect_ratio,
)
from .provider_support import COMMON_DIRECT_CLI_PARAMS, provider_parameter_support
from .queue import ImageQueue, JobRecord
from .service import ensure_worker, serve_forever, stop_worker, worker_status
from .worker import Worker, run_queue
from .webui import serve_webui


def _read_prompt(prompt: str | None, prompt_file: str | None) -> str:
    if prompt and prompt_file:
        raise SystemExit("Use --prompt or --prompt-file, not both.")
    if prompt_file:
        return Path(prompt_file).read_text(encoding="utf-8").strip()
    if prompt:
        return prompt.strip()
    raise SystemExit("Missing prompt. Use --prompt or --prompt-file.")


def _parse_param(items: list[str] | None, *, flag_name: str = "--param") -> dict[str, Any]:
    params: dict[str, Any] = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"Invalid {flag_name} value (expected key=value): {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise SystemExit(f"Invalid {flag_name}: empty key")
        try:
            parsed: Any = json.loads(value)
        except json.JSONDecodeError:
            parsed = value
        params[key] = parsed
    return params


def _params_from_job_args(args: argparse.Namespace) -> dict[str, Any]:
    params = _parse_param(getattr(args, "param", None))
    if getattr(args, "size", None):
        params["size"] = _normalize_image_size(str(args.size))
    elif getattr(args, "aspect_ratio", None):
        params["size"] = _size_from_aspect_ratio(args.aspect_ratio, getattr(args, "size_tier", "1K"))

    direct_keys = (
        "quality",
        "background",
        "moderation",
        "model",
        "action",
        "output_compression",
    )
    for key in direct_keys:
        value = getattr(args, key, None)
        if value is not None:
            params[key] = value

    output_format = getattr(args, "output_format", None)
    if output_format:
        params["output_format"] = "jpeg" if output_format == "jpg" else output_format

    stream = getattr(args, "stream", None)
    if stream is not None:
        params["stream"] = bool(stream)

    return params


def _render_prompt_with_template(
    config,
    raw_prompt: str,
    params: dict[str, Any],
    count: int,
    *,
    template_id: str | None,
    no_template: bool = False,
) -> str:
    if no_template:
        return raw_prompt
    try:
        return apply_prompt_template(config, raw_prompt, params, count, template_id=template_id)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _prompt_from_job_args(config, args: argparse.Namespace, params: dict[str, Any]) -> str:
    prompt = _read_prompt(args.prompt, args.prompt_file)
    return _render_prompt_with_template(
        config,
        prompt,
        params,
        int(args.count),
        template_id=getattr(args, "prompt_template", None),
        no_template=bool(getattr(args, "no_template", False)),
    )


def _size_from_aspect_ratio(value: str, tier: str) -> str:
    try:
        return size_from_aspect_ratio(value, tier)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _normalize_image_size(value: str) -> str:
    return normalize_image_size(value)


def _params_from_batch_item(item: dict[str, Any]) -> dict[str, Any]:
    params = dict(item.get("params") or {})
    if item.get("size"):
        params["size"] = _normalize_image_size(str(item["size"]))
    elif item.get("aspect_ratio") or item.get("aspect-ratio"):
        params["size"] = _size_from_aspect_ratio(
            str(item.get("aspect_ratio") or item.get("aspect-ratio")),
            str(item.get("size_tier") or item.get("size-tier") or "1K"),
        )
    for key in (
        "quality",
        "background",
        "moderation",
        "model",
        "action",
        "output_compression",
        "stream",
    ):
        if key in item:
            params[key] = item[key]
    if item.get("output_format") or item.get("format"):
        output_format = str(item.get("output_format") or item.get("format"))
        params["output_format"] = "jpeg" if output_format == "jpg" else output_format
    return params


def _load_app(args: argparse.Namespace):
    config_path = resolve_config_path(getattr(args, "config", None))
    if not config_path.exists():
        write_example_config(config_path, force=False)
        raise ConfigError(
            f"No config was found, so a template was created at {config_path}. "
            "Fill provider keys/settings, then run `doctor` again."
        )
    return load_config(config_path)


def _queue_for(config) -> ImageQueue:
    return ImageQueue(config.queue.db)


def _enqueue_from_args(config, args: argparse.Namespace) -> tuple[str, str, dict[str, int]]:
    kind = "edit" if args.image else "generate"
    params = _params_from_job_args(args)
    prompt = _prompt_from_job_args(config, args, params)
    queue = _queue_for(config)
    try:
        job_id = queue.enqueue(
            kind=kind,
            prompt=prompt,
            input_images=args.image or [],
            mask=args.mask,
            params=params,
            desired_count=args.count,
            provider_id=args.provider,
            out_dir=args.out_dir,
            out_prefix=args.out_prefix,
            max_attempts=args.max_attempts or config.queue.max_attempts,
            priority=args.priority,
        )
        summary = queue.summary()
    finally:
        queue.close()
    return job_id, kind, summary


def _job_to_dict(queue: ImageQueue, job: JobRecord) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "provider_id": job.provider_id,
        "prompt": job.prompt,
        "input_images": job.input_images,
        "mask": job.mask,
        "params": job.params,
        "desired_count": job.desired_count,
        "out_dir": job.out_dir,
        "out_prefix": job.out_prefix,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "finished_at": job.finished_at,
        "queue_position": queue.queued_position(job.id),
        "results": queue.results_for_job(job.id),
        "events": queue.events_for_job(job.id, limit=8),
    }


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": result["index"],
        "path": result["path"],
    }


def _job_summary(job: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "job_id": job["id"],
        "status": job["status"],
        "kind": job["kind"],
        "desired_count": job["desired_count"],
        "provider_id": job.get("provider_id"),
        "results": [_result_summary(item) for item in job.get("results") or []],
    }
    if job.get("queue_position"):
        payload["queue_position"] = job["queue_position"]
    if job.get("attempts"):
        payload["attempts"] = job["attempts"]
    if job.get("error"):
        payload["error"] = job["error"]
    return payload


def _paths_from_job_payload(job: dict[str, Any]) -> list[str]:
    return [str(item["path"]) for item in job.get("results") or [] if item.get("path")]


def _send_job_results(config, args: argparse.Namespace, job: dict[str, Any]) -> dict[str, Any]:
    paths = _paths_from_job_payload(job)
    send_config = _config_with_send_overrides(config, args)
    return send_paths(
        send_config,
        paths,
        targets=getattr(args, "send_target", None),
        message_template=getattr(args, "send_message", None),
    )


def _split_send_command(value: str, *, flag_name: str) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(value, posix=sys.platform != "win32"))
    except ValueError as exc:
        raise SystemExit(f"Invalid {flag_name}: {exc}") from exc


def _config_with_send_overrides(config, args: argparse.Namespace):
    send = config.send
    updates: dict[str, Any] = {}
    string_fields = {
        "send_method": "method",
        "send_preset": "preset",
        "send_module": "module",
        "send_function": "function",
        "send_action": "action",
        "send_target_arg": "target_arg",
        "send_message_arg": "message_arg",
        "send_path_arg": "path_arg",
        "send_action_arg": "action_arg",
    }
    for arg_name, field_name in string_fields.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            updates[field_name] = str(value)

    if getattr(args, "send_command", None):
        updates["command"] = _split_send_command(args.send_command, flag_name="--send-command")
    if getattr(args, "send_retry_delay", None) is not None:
        updates["retry_delays"] = tuple(max(0.0, float(item)) for item in args.send_retry_delay)
    if getattr(args, "send_delay_seconds", None) is not None:
        updates["delay_seconds"] = max(0.0, float(args.send_delay_seconds))
    if getattr(args, "send_timeout_seconds", None) is not None:
        updates["timeout_seconds"] = max(0.1, float(args.send_timeout_seconds))

    args_updates = dict(send.args)
    args_updates.update(_parse_param(getattr(args, "send_arg", None), flag_name="--send-arg"))
    openclaw_cli = getattr(args, "send_openclaw_cli", None)
    if openclaw_cli:
        args_updates["openclaw_cli"] = list(_split_send_command(openclaw_cli, flag_name="--send-openclaw-cli"))
    openclaw_arg_fields = {
        "send_openclaw_channel": "channel",
        "send_openclaw_account": "account",
        "send_openclaw_reply_to": "reply_to",
        "send_openclaw_thread_id": "thread_id",
    }
    for arg_name, field_name in openclaw_arg_fields.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            args_updates[field_name] = str(value)
    openclaw_bool_fields = {
        "send_openclaw_force_document": "force_document",
        "send_openclaw_silent": "silent",
        "send_openclaw_pin": "pin",
        "send_openclaw_dry_run": "dry_run",
    }
    for arg_name, field_name in openclaw_bool_fields.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            args_updates[field_name] = bool(value)
    if args_updates != send.args:
        updates["args"] = args_updates

    hermes_updates: dict[str, Any] = {}
    for arg_name, field_name in {
        "send_hermes_agent_path": "agent_path",
        "send_hermes_home": "home",
        "send_hermes_module": "module",
        "send_hermes_function": "function",
    }.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            hermes_updates[field_name] = str(value)
    if hermes_updates:
        updates["hermes"] = replace(send.hermes, **hermes_updates)

    openclaw_updates: dict[str, Any] = {}
    for arg_name, field_name in {
        "send_openclaw_agent_path": "agent_path",
        "send_openclaw_module": "module",
        "send_openclaw_function": "function",
    }.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            openclaw_updates[field_name] = str(value)
    if getattr(args, "send_openclaw_command", None):
        openclaw_updates["command"] = _split_send_command(
            args.send_openclaw_command,
            flag_name="--send-openclaw-command",
        )
    if openclaw_updates:
        updates["openclaw"] = replace(send.openclaw, **openclaw_updates)

    if not updates:
        return config
    return replace(config, send=replace(send, **updates))


def _worker_summary(worker: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"running": bool(worker.get("running"))}
    for key in ("started", "stale"):
        if key in worker:
            payload[key] = bool(worker.get(key))
    if worker.get("message"):
        payload["message"] = worker["message"]
    return payload


def _submit_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "job_id": payload["job_id"],
        "status": payload["status"],
        "kind": payload["kind"],
        "desired_count": payload["desired_count"],
        "provider_id": payload["provider_id"] or payload["default_provider"] or "auto",
        "queue_position": payload["queue_position"],
    }
    if payload.get("worker"):
        summary["worker"] = _worker_summary(payload["worker"])
    return summary


def _batch_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "job_ids": payload["job_ids"],
        "count": payload["count"],
        "status": payload["status"],
    }
    if payload.get("worker"):
        summary["worker"] = _worker_summary(payload["worker"])
    return summary


def _generate_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = _job_summary(payload["job"])
    if payload.get("timed_out"):
        summary["timed_out"] = True
    if payload.get("send"):
        summary["send"] = payload["send"]
    return summary


def _print_json(value: Any, *, pretty: bool = False) -> None:
    if pretty:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def _enabled_key_count(provider) -> int:
    return sum(1 for key in provider.keys if key.enabled and (provider.type == "mock" or key.resolve_secret()))


def _provider_capacity(provider) -> int:
    if not provider.enabled:
        return 0
    capacity = sum(
        max(1, int(key.max_concurrent_requests))
        for key in provider.keys
        if key.enabled and (provider.type == "mock" or key.resolve_secret())
    )
    if provider.max_concurrent_requests is not None:
        capacity = min(capacity, max(1, int(provider.max_concurrent_requests)))
    return max(0, capacity)


def _provider_parameter_support(provider) -> dict[str, Any]:
    return provider_parameter_support(provider)


def _capacity_report(config) -> dict[str, Any]:
    providers = []
    total = 0
    route_total = 0
    default_provider = config.defaults.provider
    for provider in sorted(config.providers, key=lambda item: (item.priority, item.id)):
        capacity = _provider_capacity(provider)
        total += capacity
        if default_provider is None or provider.id == default_provider:
            route_total += capacity
        providers.append(
            {
                "id": provider.id,
                "type": provider.type,
                "enabled": provider.enabled,
                "priority": provider.priority,
                "model": provider.model,
                "base_url": provider.base_url,
                "capabilities": list(provider.capabilities),
                "keys_total": len(provider.keys),
                "keys_ready": _enabled_key_count(provider),
                "request_capacity": capacity,
                "images_per_request": provider.images_per_request,
                "max_concurrent_requests": provider.max_concurrent_requests,
                "codex_cli": provider.codex_cli,
                "response_format_b64_json": provider.response_format_b64_json,
                "append_size_to_prompt": provider.append_size_to_prompt,
                "force_responses_stream": provider.force_responses_stream,
                "responses_stream_partial_images": provider.responses_stream_partial_images,
                "parameter_support": _provider_parameter_support(provider),
            }
        )
    effective_capacity = route_total if default_provider is not None else total
    return {
        "configured_concurrency": config.queue.concurrency,
        "auto_capacity": total,
        "default_provider": default_provider,
        "route_capacity": effective_capacity,
        "effective_worker_count": (
            effective_capacity
            if config.queue.concurrency <= 0
            else min(config.queue.concurrency, max(1, effective_capacity))
        ),
        "providers": providers,
    }


def _runtime_report(config, queue: ImageQueue) -> dict[str, Any]:
    summary = queue.summary()
    hints = []
    if summary.get("running", 0):
        hints.append("There are running jobs. If no worker is active, `run` will recover them to queued before processing.")
    return {
        "config": str(config.path),
        "queue_db": str(config.queue.db),
        "output_dir": str(config.queue.output_dir),
        "queue": summary,
        "capacity": _capacity_report(config),
        "worker": worker_status(config),
        "hints": hints,
    }


def _print_human_submit(payload: dict[str, Any]) -> None:
    provider = payload["provider_id"] or payload["default_provider"] or "auto"
    print(f"Queued: {payload['job_id']} | {payload['kind']} x{payload['desired_count']} | provider={provider}")
    print(f"Queue position: {payload['queue_position'] or 'n/a'}")
    worker = payload.get("worker") or {}
    if worker:
        print(f"Worker: running={worker.get('running')} | {worker.get('message', 'unknown')}")
    print(f"Check: python {payload['cli']} status {payload['job_id']}")


def _print_human_job_brief(job: dict[str, Any]) -> None:
    print(f"Job: {job['id']} | {job['status']} | {job['kind']} x{job['desired_count']}")
    if job.get("error"):
        print(f"Error: {job['error']}")
    results = job.get("results") or []
    if results:
        print("Outputs:")
        for item in results:
            print(f"  [{item['index']}] {item['path']}")


def _print_human_send_report(report: dict[str, Any]) -> None:
    state = "OK" if report.get("ok") else "FAILED"
    print(f"Send: {state} | deliveries={report.get('count', 0)}")
    for item in report.get("deliveries") or []:
        marker = "ok" if item.get("ok") else "failed"
        print(f"  - {marker}: {item.get('target')} <- {item.get('path')}")
        if item.get("error"):
            print(f"    error: {item['error']}")


def _print_human_status(payload: dict[str, Any]) -> None:
    if "job" in payload:
        job = payload["job"]
        print(f"Job: {job['id']}")
        print(f"Status: {job['status']} | kind={job['kind']} | count={job['desired_count']} | attempts={job['attempts']}/{job['max_attempts']}")
        print(f"Provider: {job['provider_id'] or 'default/auto'}")
        if job.get("queue_position"):
            print(f"Queue position: {job['queue_position']}")
        if job.get("error"):
            print(f"Error: {job['error']}")
        results = job.get("results") or []
        if results:
            print("Outputs:")
            for item in results:
                print(f"  [{item['index']}] {item['path']}")
        events = job.get("events") or []
        if events:
            print("Recent events:")
            for event in events[:5]:
                print(f"  - {event['created_at']} {event['level']}: {event['message']}")
        return

    print(f"Config: {payload['config']}")
    print(f"Queue DB: {payload['queue_db']}")
    print(f"Output dir: {payload['output_dir']}")
    print(f"Queue summary: {json.dumps(payload['queue'], ensure_ascii=False, sort_keys=True)}")
    for hint in payload.get("hints", []):
        print(f"Hint: {hint}")
    worker = payload.get("worker") or {}
    if worker:
        print(f"Worker: running={worker.get('running')} stale={worker.get('stale')} pid={worker.get('pid') or 'n/a'} log={worker.get('log_path')}")
    cap = payload["capacity"]
    print(
        f"Capacity: total={cap['auto_capacity']} route={cap['route_capacity']} "
        f"effective_workers={cap['effective_worker_count']} configured={cap['configured_concurrency']} "
        f"default_provider={cap['default_provider'] or 'auto'}"
    )
    for provider in cap["providers"]:
        state = "enabled" if provider["enabled"] else "disabled"
        print(
            f"  - {provider['id']}: {state} ready_keys={provider['keys_ready']}/{provider['keys_total']} "
            f"capacity={provider['request_capacity']} type={provider['type']}"
        )
        support = provider.get("parameter_support") or {}
        common = ", ".join(support.get("common_cli_params") or []) or "none"
        provider_flags = ", ".join(support.get("provider_cli_params") or []) or "none"
        extra = ", ".join(support.get("extra_params_via_param") or []) or "none"
        ignored = ", ".join(support.get("ignored_params") or []) or "none"
        print(f"    params: common={common}; provider_flags={provider_flags}; via --param={extra}; ignored={ignored}")
        for note in support.get("notes") or []:
            print(f"    note: {note}")


def _print_human_run(payload: dict[str, Any]) -> None:
    print("Run complete.")
    print(f"Processed: {payload['result']['processed']} | succeeded={payload['result']['succeeded']} | failed={payload['result']['failed']} | workers={payload['result']['worker_count']}")
    print(f"Before: {json.dumps(payload['before'], ensure_ascii=False, sort_keys=True)}")
    print(f"After:  {json.dumps(payload['after'], ensure_ascii=False, sort_keys=True)}")
    if payload.get("recent_outputs"):
        print("Recent outputs:")
        for item in payload["recent_outputs"]:
            print(f"  - {item}")


def _print_human_doctor(payload: dict[str, Any]) -> None:
    print("Doctor:", "OK" if payload["ok"] else "ISSUES")
    _print_human_status(payload)
    if payload["issues"]:
        print("Issues:")
        for issue in payload["issues"]:
            print(f"  - {issue}")


def cmd_init_config(args: argparse.Namespace) -> int:
    out = write_example_config(args.out, force=args.force)
    print(out)
    return 0


def cmd_enqueue(args: argparse.Namespace) -> int:
    config = _load_app(args)
    job_id, kind, summary = _enqueue_from_args(config, args)
    queue = _queue_for(config)
    try:
        position = queue.queued_position(job_id)
    finally:
        queue.close()
    start_worker = bool(getattr(args, "start_worker", True))
    worker = ensure_worker(config) if start_worker else {"running": False, "started": False, "message": "worker autostart skipped"}
    payload = {
        "job_id": job_id,
        "status": "queued",
        "returns_immediately": True,
        "kind": kind,
        "desired_count": args.count,
        "provider_id": args.provider,
        "default_provider": config.defaults.provider,
        "queue_position": position,
        "queue_summary": summary,
        "config": str(config.path),
        "queue_db": str(config.queue.db),
        "capacity": _capacity_report(config),
        "worker": worker,
        "cli": str(Path(sys.argv[0])),
    }
    if args.json:
        if getattr(args, "verbose", False):
            _print_json(payload, pretty=True)
        else:
            _print_json(_submit_summary(payload))
    else:
        _print_human_submit(payload)
    return 0


def cmd_enqueue_batch(args: argparse.Namespace) -> int:
    config = _load_app(args)
    queue = _queue_for(config)
    job_ids: list[str] = []
    try:
        for line_no, raw in enumerate(Path(args.input).read_text(encoding="utf-8").splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            item = json.loads(line)
            prompt = str(item.get("prompt") or "").strip()
            if not prompt:
                raise SystemExit(f"Line {line_no}: missing prompt")
            images = list(item.get("images") or item.get("input_images") or [])
            kind = str(item.get("kind") or ("edit" if images else "generate"))
            params = _params_from_batch_item(item)
            desired_count = int(item.get("count") or item.get("desired_count") or args.count)
            prompt = _render_prompt_with_template(
                config,
                prompt,
                params,
                desired_count,
                template_id=item.get("prompt_template") or item.get("template") or getattr(args, "prompt_template", None),
                no_template=bool(item.get("no_template") or getattr(args, "no_template", False)),
            )
            job_ids.append(
                queue.enqueue(
                    kind=kind,
                    prompt=prompt,
                    input_images=images,
                    mask=item.get("mask"),
                    params=params,
                    desired_count=desired_count,
                    provider_id=item.get("provider") or args.provider,
                    out_dir=item.get("out_dir") or args.out_dir,
                    out_prefix=item.get("out_prefix"),
                    max_attempts=int(item.get("max_attempts") or args.max_attempts or config.queue.max_attempts),
                    priority=int(item.get("priority") or 0),
                )
            )
        summary = queue.summary()
    finally:
        queue.close()
    start_worker = bool(getattr(args, "start_worker", True))
    worker = ensure_worker(config) if start_worker else {"running": False, "started": False, "message": "worker autostart skipped"}
    payload = {
        "job_ids": job_ids,
        "count": len(job_ids),
        "status": "queued",
        "returns_immediately": True,
        "queue_summary": summary,
        "capacity": _capacity_report(config),
        "worker": worker,
        "check": f"python {Path(sys.argv[0])} status",
    }
    if getattr(args, "verbose", False):
        _print_json(payload, pretty=True)
    else:
        _print_json(_batch_summary(payload))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = _load_app(args)
    queue = _queue_for(config)
    try:
        before = queue.summary()
    finally:
        queue.close()
    result = asyncio.run(run_queue(config, watch=args.watch, target_job_id=args.job_id))
    queue = _queue_for(config)
    try:
        after = queue.summary()
        recent_outputs = [
            result_item["path"]
            for job in queue.list_jobs(limit=8, status="succeeded")
            for result_item in queue.results_for_job(job.id)
        ][:8]
    finally:
        queue.close()
    payload = {
        "result": result,
        "before": before,
        "after": after,
        "recent_outputs": recent_outputs,
        "capacity": _capacity_report(config),
    }
    if args.json:
        _print_json(payload)
    else:
        _print_human_run(payload)
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    config = _load_app(args)
    kind = "edit" if args.image else "generate"
    params = _params_from_job_args(args)
    prompt = _prompt_from_job_args(config, args, params)
    queue = _queue_for(config)
    try:
        job_id = queue.enqueue(
            kind=kind,
            prompt=prompt,
            input_images=args.image or [],
            mask=args.mask,
            params=params,
            desired_count=args.count,
            provider_id=args.provider,
            out_dir=args.out_dir,
            out_prefix=args.out_prefix,
            max_attempts=args.max_attempts or config.queue.max_attempts,
            priority=args.priority,
        )
        worker = Worker(config, queue)
        final_job = asyncio.run(worker.run_until_done(job_id))
        full_payload = _job_to_dict(queue, final_job)
        payload = full_payload if getattr(args, "verbose", False) else _job_summary(full_payload)
        if getattr(args, "send", False) and full_payload["status"] == "succeeded":
            payload["send"] = _send_job_results(config, args, full_payload)
    finally:
        queue.close()
    _print_json(payload, pretty=bool(getattr(args, "verbose", False)))
    return 0 if payload["status"] == "succeeded" and payload.get("send", {"ok": True}).get("ok") else 1


def cmd_generate(args: argparse.Namespace) -> int:
    config = _load_app(args)
    job_id, kind, summary = _enqueue_from_args(config, args)
    worker = ensure_worker(config)
    if not worker.get("running"):
        raise SystemExit(f"Worker could not be started. Check log: {worker.get('log_path')}")
    queue = _queue_for(config)
    try:
        deadline = time.time() + float(args.timeout_seconds) if args.timeout_seconds else None
        while True:
            job = queue.get_job(job_id)
            if job is None:
                raise SystemExit(f"Unknown job: {job_id}")
            if job.status in {"succeeded", "failed", "cancelled"}:
                payload = {
                    "job": _job_to_dict(queue, job),
                    "status": job.status,
                    "kind": kind,
                    "queue_summary_at_submit": summary,
                    "worker": worker,
                    "runtime": _runtime_report(config, queue),
                }
                break
            if deadline is not None and time.time() >= deadline:
                payload = {
                    "job": _job_to_dict(queue, job),
                    "status": job.status,
                    "timed_out": True,
                    "worker": worker,
                    "runtime": _runtime_report(config, queue),
                }
                break
            time.sleep(max(0.1, float(args.poll_interval)))
    finally:
        queue.close()
    if getattr(args, "send", False) and payload["status"] == "succeeded":
        payload["send"] = _send_job_results(config, args, payload["job"])
    if args.json:
        if getattr(args, "verbose", False):
            _print_json(payload, pretty=True)
        else:
            _print_json(_generate_summary(payload))
    else:
        if payload.get("timed_out"):
            print(f"Timed out while waiting for job: {job_id}")
        if getattr(args, "verbose", False):
            _print_human_status({"job": payload["job"]})
        else:
            _print_human_job_brief(payload["job"])
        if payload.get("send"):
            _print_human_send_report(payload["send"])
    return 0 if payload["status"] == "succeeded" and payload.get("send", {"ok": True}).get("ok") else 1


def cmd_send(args: argparse.Namespace) -> int:
    config = _load_app(args)
    paths = list(args.path or [])
    if args.job_id:
        queue = _queue_for(config)
        try:
            job = queue.get_job(args.job_id)
            if not job:
                raise SystemExit(f"Unknown job: {args.job_id}")
            paths.extend(_paths_from_job_payload(_job_to_dict(queue, job)))
        finally:
            queue.close()
    if not paths:
        raise SystemExit("Nothing to send. Use --path or --job-id.")
    send_config = _config_with_send_overrides(config, args)
    report = send_paths(
        send_config,
        paths,
        targets=args.send_target,
        message_template=args.send_message,
    )
    if args.json:
        _print_json(report, pretty=bool(getattr(args, "verbose", False)))
    else:
        _print_human_send_report(report)
    return 0 if report["ok"] else 1


def cmd_status(args: argparse.Namespace) -> int:
    config = _load_app(args)
    queue = _queue_for(config)
    try:
        if args.job_id:
            job = queue.get_job(args.job_id)
            if not job:
                raise SystemExit(f"Unknown job: {args.job_id}")
            payload = {"job": _job_to_dict(queue, job), "runtime": _runtime_report(config, queue)}
        else:
            payload = _runtime_report(config, queue)
    finally:
        queue.close()
    if args.json:
        _print_json(payload)
    else:
        _print_human_status(payload)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    config = _load_app(args)
    queue = _queue_for(config)
    try:
        jobs = [_job_to_dict(queue, job) for job in queue.list_jobs(limit=args.limit, status=args.status)]
    finally:
        queue.close()
    _print_json({"jobs": jobs})
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    config = _load_app(args)
    queue = _queue_for(config)
    try:
        changed = queue.retry(args.job_id)
    finally:
        queue.close()
    _print_json({"job_id": args.job_id, "requeued": changed})
    return 0 if changed else 1


def cmd_cancel(args: argparse.Namespace) -> int:
    config = _load_app(args)
    queue = _queue_for(config)
    try:
        changed = queue.cancel(args.job_id)
    finally:
        queue.close()
    _print_json({"job_id": args.job_id, "cancelled": changed})
    return 0 if changed else 1


def cmd_providers(args: argparse.Namespace) -> int:
    config = _load_app(args)
    _print_json(
        {
            "providers": [
                {
                    "id": provider.id,
                    "type": provider.type,
                    "enabled": provider.enabled,
                    "priority": provider.priority,
                    "capabilities": list(provider.capabilities),
                    "model": provider.model,
                    "base_url": provider.base_url,
                    "images_per_request": provider.images_per_request,
                    "max_concurrent_requests": provider.max_concurrent_requests,
                    "codex_cli": provider.codex_cli,
                    "response_format_b64_json": provider.response_format_b64_json,
                    "append_size_to_prompt": provider.append_size_to_prompt,
                    "force_responses_stream": provider.force_responses_stream,
                    "responses_stream_partial_images": provider.responses_stream_partial_images,
                    "parameter_support": _provider_parameter_support(provider),
                    "keys": [
                        {
                            "id": key.id,
                            "enabled": key.enabled,
                            "secret": key.secret_label(),
                            "images_per_request": key.images_per_request or provider.images_per_request,
                            "max_concurrent_requests": key.max_concurrent_requests,
                        }
                        for key in provider.keys
                    ],
                }
                for provider in config.providers
            ]
        }
    )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config_path = resolve_config_path(getattr(args, "config", None))
    if not config_path.exists():
        created = write_example_config(config_path, force=False)
        payload = {
            "ok": False,
            "needs_configuration": True,
            "created_template": str(created),
            "message": "Config template created. Fill provider keys/settings, then run doctor again.",
        }
        if args.json:
            _print_json(payload)
        else:
            print("Doctor: NEEDS CONFIGURATION")
            print(payload["message"])
            print(f"Template: {payload['created_template']}")
        return 1

    config = load_config(config_path)
    issues: list[str] = []
    for provider in config.providers:
        if not provider.enabled:
            continue
        if provider.type != "mock":
            for key in provider.keys:
                if key.enabled and not key.resolve_secret():
                    issues.append(f"{provider.id}/{key.id}: missing secret ({key.secret_label()})")
    queue = _queue_for(config)
    try:
        runtime = _runtime_report(config, queue)
    finally:
        queue.close()
    payload = {
        **runtime,
        "providers": len(config.providers),
        "issues": issues,
        "ok": not issues,
    }
    if args.json:
        _print_json(payload)
    else:
        _print_human_doctor(payload)
    return 0 if not issues else 1


def cmd_webui(args: argparse.Namespace) -> int:
    config = _load_app(args)
    return serve_webui(
        config,
        host=args.host,
        port=args.port,
        open_browser=bool(args.open),
        share=bool(args.share),
    )


def cmd_serve(args: argparse.Namespace) -> int:
    config = _load_app(args)
    result = asyncio.run(serve_forever(config))
    if args.json:
        _print_json(result)
    else:
        print(result.get("message") or "Worker stopped.")
    return 0


def cmd_stop_worker(args: argparse.Namespace) -> int:
    config = _load_app(args)
    result = stop_worker(config)
    if args.json:
        _print_json(result)
    else:
        print(result.get("message") or "worker stop requested")
    return 0 if result.get("stopped") or not result.get("running") else 1


def add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        help="Path to TOML config. Defaults: GEN_IMAGE_CONFIG, ./gen-image.toml, then the skill directory.",
    )


def add_job_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("--template", "--prompt-template", dest="prompt_template", help="Prompt template id from config")
    parser.add_argument("--no-template", action="store_true", help="Disable the default prompt template for this job")
    parser.add_argument("--image", action="append", help="Input image path. Repeat for multi-image edits.")
    parser.add_argument("--mask", help="Optional mask path for edit jobs")
    parser.add_argument("--count", type=int, default=1, help="Total images desired")
    parser.add_argument("--provider", help="Provider id override")
    parser.add_argument("--out-dir")
    parser.add_argument("--out-prefix")
    parser.add_argument("--size", help="Image size, for example 1024x1024, 1536x1024, 1024x1536, or auto")
    parser.add_argument(
        "--aspect-ratio",
        help="Calculate size from ratio, for example 1:1, 4:3, 3:2, 16:9, 9:16, or 21:9",
    )
    parser.add_argument("--size-tier", choices=["1K", "2K", "4K"], default="1K", help="Used with --aspect-ratio")
    parser.add_argument("--quality", choices=["auto", "low", "medium", "high"])
    parser.add_argument("--output-format", "--format", dest="output_format", choices=["png", "jpeg", "jpg", "webp"])
    parser.add_argument("--background", choices=["auto", "transparent", "opaque"])
    parser.add_argument("--moderation", choices=["auto", "low"])
    parser.add_argument("--output-compression", type=int, help="0-100 compression for jpeg/webp-capable providers")
    parser.add_argument("--model", help="Override provider model for this job when supported")
    parser.add_argument("--action", choices=["auto", "generate", "edit"], help="Responses image tool action when supported")
    parser.add_argument("--stream", dest="stream", action="store_true", default=None)
    parser.add_argument("--no-stream", dest="stream", action="store_false")
    parser.add_argument("--param", action="append", help="Provider/API param as key=value; JSON values allowed")
    parser.add_argument("--max-attempts", type=int)
    parser.add_argument("--priority", type=int, default=0)


def add_send_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--send", action="store_true", help="Send completed output files using the [send] adapter")
    parser.add_argument("--send-target", action="append", help="Delivery target. Repeat to send to multiple targets.")
    parser.add_argument("--send-message", help="Override [send].message_template for this command")
    add_send_override_args(parser)


def add_send_override_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--send-method", help="Override [send].method for this command")
    parser.add_argument("--send-preset", help="Override [send].preset for this command")
    parser.add_argument("--send-module", help="Override [send].module for method=python-call")
    parser.add_argument("--send-function", help="Override [send].function for method=python-call")
    parser.add_argument("--send-command", help="Override [send].command as a shell-like command template")
    parser.add_argument("--send-action", help="Override [send].action payload value")
    parser.add_argument("--send-target-arg", help="Override [send].target_arg")
    parser.add_argument("--send-message-arg", help="Override [send].message_arg")
    parser.add_argument("--send-path-arg", help="Override [send].path_arg")
    parser.add_argument("--send-action-arg", help="Override [send].action_arg")
    parser.add_argument("--send-arg", action="append", help="Extra send adapter arg as key=value; JSON values allowed")
    parser.add_argument("--send-retry-delay", type=float, action="append", help="Retry delay in seconds. Repeat for multiple retries.")
    parser.add_argument("--send-delay-seconds", type=float, help="Pause between file/target deliveries")
    parser.add_argument("--send-timeout-seconds", type=float, help="Subprocess adapter timeout in seconds")
    parser.add_argument("--send-hermes-agent-path", help="Override [send.hermes].agent_path")
    parser.add_argument("--send-hermes-home", help="Override [send.hermes].home")
    parser.add_argument("--send-hermes-module", help="Override [send.hermes].module")
    parser.add_argument("--send-hermes-function", help="Override [send.hermes].function")
    parser.add_argument("--send-openclaw-agent-path", help="Override [send.openclaw].agent_path")
    parser.add_argument("--send-openclaw-module", help="Override [send.openclaw].module")
    parser.add_argument("--send-openclaw-function", help="Override [send.openclaw].function")
    parser.add_argument("--send-openclaw-command", help="Override [send.openclaw].command as a shell-like command template")
    parser.add_argument("--send-openclaw-cli", help="OpenClaw CLI command for the native message-send preset")
    parser.add_argument("--send-openclaw-channel", help="OpenClaw message channel for the native CLI route")
    parser.add_argument("--send-openclaw-account", help="OpenClaw channel account id")
    parser.add_argument("--send-openclaw-reply-to", help="OpenClaw reply-to message id")
    parser.add_argument("--send-openclaw-thread-id", help="OpenClaw thread id")
    parser.add_argument("--send-openclaw-force-document", action="store_true", default=None, help="Pass --force-document to OpenClaw")
    parser.add_argument("--send-openclaw-silent", action="store_true", default=None, help="Pass --silent to OpenClaw")
    parser.add_argument("--send-openclaw-pin", action="store_true", default=None, help="Pass --pin to OpenClaw")
    parser.add_argument("--send-openclaw-dry-run", action="store_true", default=None, help="Pass --dry-run to OpenClaw")


def add_autostart_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--no-start-worker",
        dest="start_worker",
        action="store_false",
        help="Only enqueue; do not auto-start the background worker.",
    )
    parser.set_defaults(start_worker=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gen-image",
        description="Async queue-backed image generation via API providers",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-config", help="Write an example TOML config")
    init.add_argument("--out", default=DEFAULT_CONFIG_NAME)
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init_config)

    enqueue = sub.add_parser("enqueue", help="Add one generation/edit job to the queue")
    add_config_arg(enqueue)
    add_job_args(enqueue)
    add_autostart_arg(enqueue)
    enqueue.add_argument("--json", action="store_true")
    enqueue.add_argument("--verbose", action="store_true", help="With --json, include full diagnostics.")
    enqueue.set_defaults(func=cmd_enqueue)

    submit = sub.add_parser("submit", help="Submit one job, auto-start worker, and return immediately")
    add_config_arg(submit)
    add_job_args(submit)
    add_autostart_arg(submit)
    submit.add_argument("--json", action="store_true")
    submit.add_argument("--verbose", action="store_true", help="With --json, include full diagnostics.")
    submit.set_defaults(func=cmd_enqueue)

    generate = sub.add_parser("generate", help="Submit one job, auto-start worker, wait, and print outputs")
    add_config_arg(generate)
    add_job_args(generate)
    generate.add_argument("--json", action="store_true")
    generate.add_argument("--verbose", action="store_true", help="Include full diagnostics instead of the compact result.")
    generate.add_argument("--poll-interval", type=float, default=2.0)
    generate.add_argument("--timeout-seconds", type=float, default=0.0, help="0 means wait indefinitely")
    add_send_args(generate)
    generate.set_defaults(func=cmd_generate)

    batch = sub.add_parser("enqueue-batch", help="Add jobs from a JSONL file")
    add_config_arg(batch)
    batch.add_argument("--input", required=True)
    batch.add_argument("--count", type=int, default=1)
    batch.add_argument("--provider")
    batch.add_argument("--out-dir")
    batch.add_argument("--template", "--prompt-template", dest="prompt_template", help="Default prompt template id for batch items")
    batch.add_argument("--no-template", action="store_true", help="Disable config default prompt templates for this batch")
    batch.add_argument("--max-attempts", type=int)
    batch.add_argument("--verbose", action="store_true", help="Include full diagnostics.")
    add_autostart_arg(batch)
    batch.set_defaults(func=cmd_enqueue_batch)

    submit_batch = sub.add_parser("submit-batch", help="Submit jobs from JSONL, auto-start worker, and return job ids immediately")
    add_config_arg(submit_batch)
    submit_batch.add_argument("--input", required=True)
    submit_batch.add_argument("--count", type=int, default=1)
    submit_batch.add_argument("--provider")
    submit_batch.add_argument("--out-dir")
    submit_batch.add_argument("--template", "--prompt-template", dest="prompt_template", help="Default prompt template id for batch items")
    submit_batch.add_argument("--no-template", action="store_true", help="Disable config default prompt templates for this batch")
    submit_batch.add_argument("--max-attempts", type=int)
    submit_batch.add_argument("--verbose", action="store_true", help="Include full diagnostics.")
    add_autostart_arg(submit_batch)
    submit_batch.set_defaults(func=cmd_enqueue_batch)

    run = sub.add_parser("run", help="Run queued jobs asynchronously")
    add_config_arg(run)
    run.add_argument("--watch", action="store_true", help="Keep waiting for new jobs")
    run.add_argument("--job-id", help="Only run a specific queued job")
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=cmd_run)

    once = sub.add_parser("once", help="Enqueue one job, run it, and print final JSON")
    add_config_arg(once)
    add_job_args(once)
    once.add_argument("--verbose", action="store_true", help="Include full diagnostics instead of the compact result.")
    add_send_args(once)
    once.set_defaults(func=cmd_once)

    send = sub.add_parser("send", help="Send existing output files using the configured delivery adapter")
    add_config_arg(send)
    send.add_argument("--path", action="append", help="File path to send. Repeat for multiple files.")
    send.add_argument("--job-id", help="Send all output files recorded for a completed job.")
    send.add_argument("--target", "--send-target", dest="send_target", action="append", help="Delivery target. Repeat for multiple targets.")
    send.add_argument("--message", "--send-message", dest="send_message", help="Override [send].message_template for this command")
    add_send_override_args(send)
    send.add_argument("--json", action="store_true")
    send.add_argument("--verbose", action="store_true", help="Pretty-print JSON output.")
    send.set_defaults(func=cmd_send)

    serve = sub.add_parser("serve", help="Run the managed background worker in the foreground")
    add_config_arg(serve)
    serve.add_argument("--json", action="store_true")
    serve.set_defaults(func=cmd_serve)

    worker = sub.add_parser("worker", help="Alias for serve")
    add_config_arg(worker)
    worker.add_argument("--json", action="store_true")
    worker.set_defaults(func=cmd_serve)

    stop_worker_cmd = sub.add_parser("stop-worker", help="Stop the managed background worker")
    add_config_arg(stop_worker_cmd)
    stop_worker_cmd.add_argument("--json", action="store_true")
    stop_worker_cmd.set_defaults(func=cmd_stop_worker)

    status = sub.add_parser("status", help="Show queue summary or one job")
    add_config_arg(status)
    status.add_argument("job_id", nargs="?")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    list_cmd = sub.add_parser("list", help="List recent jobs")
    add_config_arg(list_cmd)
    list_cmd.add_argument("--limit", type=int, default=20)
    list_cmd.add_argument("--status")
    list_cmd.set_defaults(func=cmd_list)

    retry = sub.add_parser("retry", help="Requeue a failed job")
    add_config_arg(retry)
    retry.add_argument("job_id")
    retry.set_defaults(func=cmd_retry)

    cancel = sub.add_parser("cancel", help="Cancel a queued/running job")
    add_config_arg(cancel)
    cancel.add_argument("job_id")
    cancel.set_defaults(func=cmd_cancel)

    providers = sub.add_parser("providers", help="Show configured providers and keys")
    add_config_arg(providers)
    providers.set_defaults(func=cmd_providers)

    doctor = sub.add_parser("doctor", help="Validate config and required key env vars")
    add_config_arg(doctor)
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    webui = sub.add_parser("webui", help="Start the optional Gradio WebUI")
    add_config_arg(webui)
    webui.add_argument("--host", default="127.0.0.1")
    webui.add_argument("--port", type=int, default=8765)
    webui.add_argument("--open", action="store_true", help="Open the Web UI in the default browser")
    webui.add_argument("--share", action="store_true", help="Ask Gradio to create a temporary public share URL")
    webui.set_defaults(func=cmd_webui)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    except SendError as exc:
        print(f"Send error: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
