from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from .config import AppConfig, SendConfig


class SendError(RuntimeError):
    """Raised when output delivery cannot be configured or completed."""


@dataclass(frozen=True)
class SendAttempt:
    target: str
    path: str
    ok: bool
    attempts: int
    message: str
    result: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "target": self.target,
            "path": self.path,
            "ok": self.ok,
            "attempts": self.attempts,
            "message": self.message,
        }
        if self.error:
            payload["error"] = self.error
        if self.result is not None:
            payload["result"] = self.result
        return payload


def send_paths(
    config: AppConfig,
    paths: list[str],
    *,
    targets: list[str] | None = None,
    message_template: str | None = None,
) -> dict[str, Any]:
    existing_paths = _validate_paths(paths)
    resolved_targets = _resolve_targets(config.send, targets)
    deliveries: list[dict[str, Any]] = []
    for path_index, path in enumerate(existing_paths):
        for target_index, target in enumerate(resolved_targets):
            if deliveries and config.send.delay_seconds > 0:
                time.sleep(config.send.delay_seconds)
            attempt = _send_one(
                config,
                path=path,
                target=target,
                message_template=message_template or config.send.message_template,
            )
            deliveries.append(attempt.to_dict())
    ok = all(item["ok"] for item in deliveries)
    return {
        "ok": ok,
        "targets": resolved_targets,
        "paths": existing_paths,
        "count": len(deliveries),
        "deliveries": deliveries,
    }


def _validate_paths(paths: list[str]) -> list[str]:
    if not paths:
        raise SendError("No output paths to send.")
    resolved: list[str] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if not path.exists():
            raise SendError(f"Output path does not exist: {path}")
        if not path.is_file():
            raise SendError(f"Output path is not a file: {path}")
        resolved.append(str(path))
    return resolved


def _resolve_targets(send: SendConfig, explicit_targets: list[str] | None) -> list[str]:
    targets = [item.strip() for item in explicit_targets or [] if item and item.strip()]
    if not targets:
        targets = [item for item in send.targets if item]
    if not targets and send.default_target:
        targets = [send.default_target]
    if not targets:
        raise SendError("No send target configured. Pass --send-target or set [send].targets.")
    return targets


def _send_one(
    config: AppConfig,
    *,
    path: str,
    target: str,
    message_template: str,
) -> SendAttempt:
    message = _render_message(message_template, path=path, target=target)
    waits = (0.0, *config.send.retry_delays)
    last_error: str | None = None
    last_result: Any = None
    for index, wait_seconds in enumerate(waits, start=1):
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        try:
            result = _dispatch(config, path=path, target=target, message=message)
            last_result = result
            ok, error = _result_status(result)
            if ok:
                return SendAttempt(
                    target=target,
                    path=path,
                    ok=True,
                    attempts=index,
                    message=message,
                    result=result,
                )
            last_error = error or "send adapter returned a failure result"
        except Exception as exc:  # noqa: BLE001 - adapter errors should be reported, not leaked as tracebacks.
            last_error = str(exc)
    return SendAttempt(
        target=target,
        path=path,
        ok=False,
        attempts=len(waits),
        message=message,
        result=last_result,
        error=last_error,
    )


def _render_message(template: str, *, path: str, target: str) -> str:
    values = {
        "path": path,
        "target": target,
        "filename": Path(path).name,
    }
    try:
        return template.format_map(values)
    except KeyError as exc:
        raise SendError(f"Unknown send message placeholder: {exc}") from exc


def _dispatch(config: AppConfig, *, path: str, target: str, message: str) -> Any:
    method = config.send.method.strip().lower()
    if method in {"python", "python-call", "callable"}:
        return _dispatch_python(config, path=path, target=target, message=message)
    if method in {"command", "subprocess"}:
        return _dispatch_command(config, path=path, target=target, message=message)
    raise SendError(f"Unsupported [send].method: {config.send.method}")


def _dispatch_python(config: AppConfig, *, path: str, target: str, message: str) -> Any:
    if not config.send.module or not config.send.function:
        raise SendError("[send].module and [send].function are required for method='python-call'.")

    base_dir = str(config.base_dir)
    added_base_dir = False
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)
        added_base_dir = True
    try:
        module = importlib.import_module(config.send.module)
    finally:
        if added_base_dir:
            try:
                sys.path.remove(base_dir)
            except ValueError:
                pass

    func = getattr(module, config.send.function, None)
    if not callable(func):
        raise SendError(f"Configured send function is not callable: {config.send.module}.{config.send.function}")

    payload = dict(config.send.args)
    if config.send.action_arg:
        payload[config.send.action_arg] = config.send.action
    if config.send.target_arg:
        payload[config.send.target_arg] = target
    if config.send.message_arg:
        payload[config.send.message_arg] = message
    if config.send.path_arg:
        payload[config.send.path_arg] = path
    return _decode_result(func(payload))


def _dispatch_command(config: AppConfig, *, path: str, target: str, message: str) -> dict[str, Any]:
    if not config.send.command:
        raise SendError("[send].command is required for method='command'.")
    values = {
        "path": path,
        "target": target,
        "message": message,
        "filename": Path(path).name,
    }
    argv = [item.format_map(values) for item in config.send.command]
    result = subprocess.run(
        argv,
        cwd=config.base_dir,
        text=True,
        capture_output=True,
        timeout=config.send.timeout_seconds,
        check=False,
    )
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _decode_result(result: Any) -> Any:
    if isinstance(result, str):
        text = result.strip()
        if not text:
            return {"ok": True}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"ok": True, "text": result}
    return result


def _result_status(result: Any) -> tuple[bool, str | None]:
    if isinstance(result, dict):
        if result.get("ok") is False:
            return False, _error_from_result(result)
        if result.get("success") is False:
            return False, _error_from_result(result)
        if result.get("error"):
            return False, _error_from_result(result)
        if result.get("returncode") not in (None, 0):
            return False, _error_from_result(result)
    return True, None


def _error_from_result(result: dict[str, Any]) -> str:
    for key in ("error", "message", "stderr", "text"):
        value = result.get(key)
        if value:
            return str(value)
    return json.dumps(result, ensure_ascii=False, sort_keys=True)
