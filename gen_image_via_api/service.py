from __future__ import annotations

import asyncio
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any
from uuid import uuid4

from .config import AppConfig
from .queue import ImageQueue
from .utils import json_dumps, json_loads, utc_now
from .worker import Worker


HEARTBEAT_SECONDS = 2.0
STALE_AFTER_SECONDS = 30.0


def service_paths(config: AppConfig) -> dict[str, Path]:
    root = config.queue.db.parent
    return {
        "lock": root / "worker.lock.json",
        "log": root / "worker.log",
    }


def worker_status(config: AppConfig) -> dict[str, Any]:
    paths = service_paths(config)
    lock = _read_lock(paths["lock"])
    running = False
    stale = False
    heartbeat_age_seconds: float | None = None
    if lock:
        heartbeat = float(lock.get("heartbeat_ts") or lock.get("started_ts") or 0)
        heartbeat_age_seconds = max(0.0, time.time() - heartbeat) if heartbeat else None
        running = heartbeat_age_seconds is not None and heartbeat_age_seconds <= STALE_AFTER_SECONDS
        stale = not running
    return {
        "running": running,
        "stale": stale,
        "pid": lock.get("pid") if lock else None,
        "started_at": lock.get("started_at") if lock else None,
        "heartbeat_at": lock.get("heartbeat_at") if lock else None,
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "lock_path": str(paths["lock"]),
        "log_path": str(paths["log"]),
    }


def ensure_worker(config: AppConfig, *, wait_seconds: float = 5.0) -> dict[str, Any]:
    status = worker_status(config)
    if status["running"]:
        return {**status, "started": False, "message": "worker already running"}

    paths = service_paths(config)
    if status["stale"]:
        _safe_unlink(paths["lock"])

    paths["lock"].parent.mkdir(parents=True, exist_ok=True)
    paths["log"].parent.mkdir(parents=True, exist_ok=True)
    project_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    command = [
        sys.executable,
        "-m",
        "gen_image_via_api",
        "serve",
        "--config",
        str(config.path),
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)

    try:
        with paths["log"].open("ab") as log:
            process = subprocess.Popen(
                command,
                cwd=str(project_root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
    except Exception as exc:
        return {
            **worker_status(config),
            "started": False,
            "error": str(exc),
            "message": "failed to start worker",
        }

    deadline = time.time() + max(0.0, wait_seconds)
    latest = worker_status(config)
    while time.time() < deadline:
        latest = worker_status(config)
        if latest["running"]:
            return {**latest, "started": True, "launcher_pid": process.pid, "message": "worker started"}
        if process.poll() is not None:
            return {
                **latest,
                "started": False,
                "launcher_pid": process.pid,
                "exit_code": process.returncode,
                "message": "worker exited before heartbeat",
            }
        time.sleep(0.1)
    return {
        **latest,
        "started": True,
        "launcher_pid": process.pid,
        "message": "worker launch requested; heartbeat not observed yet",
    }


def stop_worker(config: AppConfig, *, wait_seconds: float = 5.0) -> dict[str, Any]:
    status = worker_status(config)
    paths = service_paths(config)
    if status["stale"]:
        _safe_unlink(paths["lock"])
        return {**worker_status(config), "stopped": True, "message": "removed stale worker lock"}
    if not status["running"] or not status.get("pid"):
        return {**status, "stopped": False, "message": "worker is not running"}

    pid = int(status["pid"])
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        return {**status, "stopped": False, "error": str(exc), "message": "failed to stop worker"}

    time.sleep(min(max(0.0, wait_seconds), 0.5))
    _safe_unlink(paths["lock"])
    latest = worker_status(config)
    return {**latest, "stopped": True, "message": "stop signal sent"}


async def serve_forever(config: AppConfig) -> dict[str, Any]:
    lock = _acquire_lock(config)
    if not lock["acquired"]:
        return {**worker_status(config), "started": False, "message": lock["message"]}

    queue = ImageQueue(config.queue.db)
    heartbeat = asyncio.create_task(_heartbeat_loop(lock["lock_path"], lock["payload"]))
    try:
        result = await Worker(config, queue).run(watch=True)
        return {**result, "started": True}
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        queue.close()
        _release_lock(lock["lock_path"], lock["payload"]["token"])


def _acquire_lock(config: AppConfig) -> dict[str, Any]:
    paths = service_paths(config)
    status = worker_status(config)
    if status["running"]:
        return {"acquired": False, "message": "worker already running"}
    if status["stale"]:
        _safe_unlink(paths["lock"])
    paths["lock"].parent.mkdir(parents=True, exist_ok=True)
    payload = _lock_payload(config)
    try:
        fd = os.open(str(paths["lock"]), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return {"acquired": False, "message": "worker lock already exists"}
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json_dumps(payload))
    return {"acquired": True, "lock_path": paths["lock"], "payload": payload}


async def _heartbeat_loop(lock_path: Path, payload: dict[str, Any]) -> None:
    while True:
        payload["heartbeat_ts"] = time.time()
        payload["heartbeat_at"] = utc_now()
        lock_path.write_text(json_dumps(payload), encoding="utf-8")
        await asyncio.sleep(HEARTBEAT_SECONDS)


def _lock_payload(config: AppConfig) -> dict[str, Any]:
    now = time.time()
    return {
        "pid": os.getpid(),
        "token": uuid4().hex,
        "config": str(config.path),
        "started_ts": now,
        "heartbeat_ts": now,
        "started_at": utc_now(),
        "heartbeat_at": utc_now(),
    }


def _read_lock(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return dict(json_loads(path.read_text(encoding="utf-8"), {}))
    except OSError:
        return {}


def _release_lock(path: Path, token: str) -> None:
    payload = _read_lock(path)
    if payload.get("token") == token:
        _safe_unlink(path)


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
