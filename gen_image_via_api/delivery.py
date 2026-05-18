from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
import os
from pathlib import Path
import shlex
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
        resolved.append(str(path.resolve()))
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
        rendered = template.format_map(values)
    except KeyError as exc:
        raise SendError(f"Unknown send message placeholder: {exc}") from exc
    return rendered.replace("\\n", "\n")


def _dispatch(config: AppConfig, *, path: str, target: str, message: str) -> Any:
    method = config.send.method.strip().lower()
    preset = config.send.preset.strip().lower()
    if preset or method in {"preset", "hermes", "openclaw"}:
        return _dispatch_preset(config, path=path, target=target, message=message)
    if method in {"python", "python-call", "callable"}:
        return _dispatch_python(config, path=path, target=target, message=message)
    if method in {"command", "subprocess"}:
        return _dispatch_command(config, path=path, target=target, message=message)
    raise SendError(f"Unsupported [send].method: {config.send.method}")


def _dispatch_python(config: AppConfig, *, path: str, target: str, message: str) -> Any:
    if not config.send.module or not config.send.function:
        raise SendError("[send].module and [send].function are required for method='python-call'.")

    func = _import_callable(config.send.module, config.send.function, [config.base_dir])
    payload = _payload_for_call(config, path=path, target=target, message=message)
    return _decode_result(func(payload))


def _payload_for_call(config: AppConfig, *, path: str, target: str, message: str) -> dict[str, Any]:
    payload = dict(config.send.args)
    if config.send.action_arg:
        payload[config.send.action_arg] = config.send.action
    if config.send.target_arg:
        payload[config.send.target_arg] = target
    if config.send.message_arg:
        payload[config.send.message_arg] = message
    if config.send.path_arg:
        payload[config.send.path_arg] = path
    return payload


def _dispatch_preset(config: AppConfig, *, path: str, target: str, message: str) -> Any:
    preset = (config.send.preset or config.send.method).strip().lower()
    if preset == "hermes":
        return _dispatch_hermes_preset(config, path=path, target=target, message=message)
    if preset == "openclaw":
        return _dispatch_openclaw_preset(config, path=path, target=target, message=message)
    raise SendError(f"Unsupported [send].preset: {preset}")


def _dispatch_hermes_preset(config: AppConfig, *, path: str, target: str, message: str) -> Any:
    roots = _candidate_hermes_agent_roots(config.send)
    _load_hermes_environment(roots, hermes_home=config.send.hermes.home)
    module = config.send.hermes.module or "tools.send_message_tool"
    function = config.send.hermes.function or "send_message_tool"
    func = _import_callable(module, function, roots)
    return _decode_result(func(_payload_for_call(config, path=path, target=target, message=message)))


def _dispatch_openclaw_preset(config: AppConfig, *, path: str, target: str, message: str) -> Any:
    send = config.send.openclaw
    module = send.module or os.getenv("OPENCLAW_SEND_MODULE", "").strip()
    function = send.function or os.getenv("OPENCLAW_SEND_FUNCTION", "").strip()
    roots = _candidate_openclaw_roots(config.send)
    if module and function:
        func = _import_callable(module, function, roots)
        return _decode_result(func(_payload_for_call(config, path=path, target=target, message=message)))

    command = send.command
    env_command = os.getenv("OPENCLAW_SEND_COMMAND", "").strip()
    if not command and env_command:
        command = _split_command(env_command)
    if command:
        return _run_command(
            _format_command(command, path=path, target=target, message=message),
            cwd=config.base_dir,
            timeout=config.send.timeout_seconds,
        )

    return _dispatch_openclaw_cli_preset(config, path=path, target=target, message=message)


def _dispatch_openclaw_cli_preset(config: AppConfig, *, path: str, target: str, message: str) -> dict[str, Any]:
    argv = [
        *_openclaw_cli_argv(config),
        "message",
        "send",
    ]
    channel = _send_env_or_arg(config.send, "OPENCLAW_SEND_CHANNEL", "channel")
    if channel:
        argv.extend(["--channel", channel])
    account = _send_env_or_arg(config.send, "OPENCLAW_SEND_ACCOUNT", "account")
    if account:
        argv.extend(["--account", account])
    argv.extend(["--target", target, "--media", path])

    caption = _caption_from_media_message(message, path)
    if caption:
        argv.extend(["--message", caption])
    reply_to = _send_env_or_arg(config.send, "OPENCLAW_SEND_REPLY_TO", "reply_to")
    if reply_to:
        argv.extend(["--reply-to", reply_to])
    thread_id = _send_env_or_arg(config.send, "OPENCLAW_SEND_THREAD_ID", "thread_id")
    if thread_id:
        argv.extend(["--thread-id", thread_id])
    if _send_env_or_bool_arg(config.send, "OPENCLAW_SEND_FORCE_DOCUMENT", "force_document"):
        argv.append("--force-document")
    if _send_env_or_bool_arg(config.send, "OPENCLAW_SEND_SILENT", "silent"):
        argv.append("--silent")
    if _send_env_or_bool_arg(config.send, "OPENCLAW_SEND_PIN", "pin"):
        argv.append("--pin")
    if _send_env_or_bool_arg(config.send, "OPENCLAW_SEND_DRY_RUN", "dry_run"):
        argv.append("--dry-run")
    argv.append("--json")

    return _run_command(
        argv,
        cwd=config.base_dir,
        timeout=config.send.timeout_seconds,
    )


def _openclaw_cli_argv(config: AppConfig) -> list[str]:
    raw = os.getenv("OPENCLAW_SEND_CLI", "").strip()
    if raw:
        return list(_split_command(raw))

    configured = config.send.args.get("openclaw_cli")
    if isinstance(configured, (list, tuple)):
        argv = [str(item) for item in configured if str(item).strip()]
        if argv:
            return argv
    if configured:
        argv = list(_split_command(str(configured)))
        if argv:
            return argv

    return ["openclaw"]


def _dispatch_command(config: AppConfig, *, path: str, target: str, message: str) -> dict[str, Any]:
    if not config.send.command:
        raise SendError("[send].command is required for method='command'.")
    return _run_command(
        _format_command(config.send.command, path=path, target=target, message=message),
        cwd=config.base_dir,
        timeout=config.send.timeout_seconds,
    )


def _format_command(command: tuple[str, ...] | list[str], *, path: str, target: str, message: str) -> list[str]:
    caption = _caption_from_media_message(message, path)
    values = {
        "path": path,
        "target": target,
        "message": message,
        "caption": caption,
        "message_without_media": caption,
        "filename": Path(path).name,
    }
    try:
        return [item.format_map(values) for item in command]
    except KeyError as exc:
        raise SendError(f"Unknown send command placeholder: {exc}") from exc


def _caption_from_media_message(message: str, path: str) -> str:
    marker = f"MEDIA:{path}"
    text = message.replace(marker, "")
    normalized_marker = f"MEDIA:{Path(path)}"
    if normalized_marker != marker:
        text = text.replace(normalized_marker, "")
    return text.replace("\r\n", "\n").strip()


def _send_env_or_arg(send: SendConfig, env_name: str, arg_name: str) -> str:
    value = os.getenv(env_name, "").strip()
    if value:
        return value
    raw = send.args.get(arg_name)
    if raw is None:
        return ""
    return str(raw).strip()


def _send_env_or_bool_arg(send: SendConfig, env_name: str, arg_name: str) -> bool:
    value = os.getenv(env_name, "").strip()
    if value:
        return _as_bool(value)
    raw = send.args.get(arg_name)
    return _as_bool(raw)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _run_command(argv: list[str], *, cwd: Path, timeout: float) -> dict[str, Any]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _split_command(command: str) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(command, posix=os.name != "nt"))
    except ValueError as exc:
        raise SendError(f"Invalid OPENCLAW_SEND_COMMAND: {exc}") from exc


def _import_callable(module_name: str, function_name: str, roots: list[Path | str]) -> Any:
    preferred_roots: list[str] = []
    for root in roots:
        if not root:
            continue
        root_path = Path(root).expanduser()
        if not root_path.exists():
            continue
        root_str = str(root_path)
        preferred_roots.append(root_str)
    for root_str in reversed(preferred_roots):
        if root_str not in sys.path:
            sys.path.insert(0, root_str)
        else:
            sys.path.remove(root_str)
            sys.path.insert(0, root_str)
    if preferred_roots:
        _purge_loaded_module(module_name)
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise SendError(f"Could not import send module {module_name!r}: {exc}") from exc

    func = getattr(module, function_name, None)
    if not callable(func):
        raise SendError(f"Configured send function is not callable: {module_name}.{function_name}")
    return func


def _purge_loaded_module(module_name: str) -> None:
    top_level = module_name.split(".", 1)[0]
    prefixes = (top_level, f"{top_level}.")
    for name in list(sys.modules):
        if name == module_name or name.startswith(f"{module_name}.") or name == top_level or name.startswith(prefixes[1]):
            sys.modules.pop(name, None)


def _candidate_hermes_agent_roots(send: SendConfig | None = None) -> list[Path]:
    roots: list[Path] = []
    if send and send.hermes.agent_path:
        roots.append(Path(send.hermes.agent_path).expanduser())
    for env_name in ("HERMES_AGENT_PATH", "HERMES_AGENT_ROOT"):
        value = os.getenv(env_name, "").strip()
        if value:
            roots.append(Path(value).expanduser())
    home = (send.hermes.home if send else "") or os.getenv("HERMES_HOME", "").strip()
    if home:
        roots.append(Path(home).expanduser() / "hermes-agent")
    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    if local_appdata:
        roots.append(Path(local_appdata) / "hermes" / "hermes-agent")
    roots.append(Path.home() / ".hermes" / "hermes-agent")
    return _unique_existing_or_candidate_paths(roots)


def _candidate_openclaw_roots(send: SendConfig | None = None) -> list[Path]:
    roots: list[Path] = []
    if send and send.openclaw.agent_path:
        roots.append(Path(send.openclaw.agent_path).expanduser())
    for env_name in ("OPENCLAW_AGENT_PATH", "OPENCLAW_AGENT_ROOT", "OPENCLAW_HOME", "OPENCLAW_WORKSPACE"):
        value = os.getenv(env_name, "").strip()
        if value:
            roots.append(Path(value).expanduser())
    roots.extend(
        [
            Path.home() / ".openclaw" / "workspace",
            Path.home() / ".openclaw",
        ]
    )
    return _unique_existing_or_candidate_paths(roots)


def _unique_existing_or_candidate_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        normalized = str(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(path)
    return out


def _load_hermes_environment(roots: list[Path], *, hermes_home: str = "") -> None:
    resolved_home = hermes_home or os.getenv("HERMES_HOME", "")
    hermes_home_path = Path(resolved_home).expanduser() if resolved_home else None
    for root in roots:
        if not root.exists():
            continue
        try:
            func = _import_callable("hermes_cli.env_loader", "load_hermes_dotenv", [root])
            kwargs: dict[str, Any] = {}
            if hermes_home_path:
                kwargs["hermes_home"] = hermes_home_path
            project_env = root / ".env"
            if project_env.exists():
                kwargs["project_env"] = project_env
            func(**kwargs)
            return
        except Exception:
            continue


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

