from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from .utils import json_dumps, json_loads, utc_now


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


@dataclass
class JobRecord:
    id: str
    kind: str
    status: str
    provider_id: str | None
    prompt: str
    input_images: list[str]
    mask: str | None
    params: dict[str, Any]
    desired_count: int
    out_dir: str | None
    out_prefix: str | None
    attempts: int
    max_attempts: int
    priority: int
    error: str | None
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None


class ImageQueue:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              status TEXT NOT NULL,
              provider_id TEXT,
              prompt TEXT NOT NULL,
              input_images TEXT NOT NULL,
              mask TEXT,
              params TEXT NOT NULL,
              desired_count INTEGER NOT NULL,
              out_dir TEXT,
              out_prefix TEXT,
              attempts INTEGER NOT NULL DEFAULT 0,
              max_attempts INTEGER NOT NULL DEFAULT 3,
              priority INTEGER NOT NULL DEFAULT 0,
              error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              started_at TEXT,
              finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS results (
              id TEXT PRIMARY KEY,
              job_id TEXT NOT NULL,
              result_index INTEGER NOT NULL,
              path TEXT NOT NULL,
              raw_url TEXT,
              metadata TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(job_id) REFERENCES jobs(id)
            );
            CREATE TABLE IF NOT EXISTS provider_state (
              provider_id TEXT PRIMARY KEY,
              last_key_id TEXT,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
              id TEXT PRIMARY KEY,
              job_id TEXT,
              level TEXT NOT NULL,
              message TEXT NOT NULL,
              metadata TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def enqueue(
        self,
        *,
        kind: str,
        prompt: str,
        input_images: list[str] | None = None,
        mask: str | None = None,
        params: dict[str, Any] | None = None,
        desired_count: int = 1,
        provider_id: str | None = None,
        out_dir: str | None = None,
        out_prefix: str | None = None,
        max_attempts: int = 3,
        priority: int = 0,
    ) -> str:
        job_id = uuid4().hex
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO jobs (
              id, kind, status, provider_id, prompt, input_images, mask, params,
              desired_count, out_dir, out_prefix, attempts, max_attempts, priority,
              error, created_at, updated_at
            )
            VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, NULL, ?, ?)
            """,
            (
                job_id,
                kind,
                provider_id,
                prompt,
                json_dumps(input_images or []),
                mask,
                json_dumps(params or {}),
                max(1, int(desired_count)),
                out_dir,
                out_prefix,
                max(1, int(max_attempts)),
                int(priority),
                now,
                now,
            ),
        )
        self.conn.commit()
        self.event(job_id, "info", "queued", {"kind": kind, "provider_id": provider_id})
        return job_id

    def recover_running(self) -> int:
        now = utc_now()
        cur = self.conn.execute(
            """
            UPDATE jobs
            SET status = 'queued',
                error = 'Recovered from interrupted worker',
                updated_at = ?
            WHERE status = 'running'
            """,
            (now,),
        )
        self.conn.commit()
        return int(cur.rowcount)

    def claim_next(self, *, target_job_id: str | None = None) -> JobRecord | None:
        where = "status = 'queued'"
        params: list[Any] = []
        if target_job_id:
            where += " AND id = ?"
            params.append(target_job_id)
        row = self.conn.execute(
            f"""
            SELECT * FROM jobs
            WHERE {where}
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            """,
            params,
        ).fetchone()
        if row is None:
            return None
        now = utc_now()
        self.conn.execute(
            """
            UPDATE jobs
            SET status = 'running',
                attempts = attempts + 1,
                started_at = COALESCE(started_at, ?),
                updated_at = ?,
                error = NULL
            WHERE id = ? AND status = 'queued'
            """,
            (now, now, row["id"]),
        )
        self.conn.commit()
        claimed = self.get_job(row["id"])
        if claimed:
            self.event(claimed.id, "info", "claimed", {"attempt": claimed.attempts})
        return claimed

    def queued_jobs(self, *, target_job_id: str | None = None, limit: int = 100) -> list[JobRecord]:
        where = "status = 'queued'"
        params: list[Any] = []
        if target_job_id:
            where += " AND id = ?"
            params.append(target_job_id)
        rows = self.conn.execute(
            f"""
            SELECT * FROM jobs
            WHERE {where}
            ORDER BY priority DESC, created_at ASC
            LIMIT ?
            """,
            [*params, max(1, int(limit))],
        ).fetchall()
        return [_row_to_job(row) for row in rows]

    def claim_job(self, job_id: str) -> JobRecord | None:
        now = utc_now()
        cur = self.conn.execute(
            """
            UPDATE jobs
            SET status = 'running',
                attempts = attempts + 1,
                started_at = COALESCE(started_at, ?),
                updated_at = ?,
                error = NULL
            WHERE id = ? AND status = 'queued'
            """,
            (now, now, job_id),
        )
        self.conn.commit()
        if cur.rowcount <= 0:
            return None
        claimed = self.get_job(job_id)
        if claimed:
            self.event(claimed.id, "info", "claimed", {"attempt": claimed.attempts})
        return claimed

    def set_job_status(self, job_id: str, status: str, *, error: str | None = None) -> None:
        now = utc_now()
        finished_at = now if status in TERMINAL_STATUSES else None
        self.conn.execute(
            """
            UPDATE jobs
            SET status = ?, error = ?, updated_at = ?, finished_at = COALESCE(?, finished_at)
            WHERE id = ?
            """,
            (status, error, now, finished_at, job_id),
        )
        self.conn.commit()
        self.event(job_id, "error" if status == "failed" else "info", status, {"error": error})

    def requeue_if_possible(self, job: JobRecord, error: str) -> bool:
        if job.attempts >= job.max_attempts:
            self.set_job_status(job.id, "failed", error=error)
            return False
        now = utc_now()
        self.conn.execute(
            """
            UPDATE jobs
            SET status = 'queued', error = ?, updated_at = ?
            WHERE id = ?
            """,
            (error, now, job.id),
        )
        self.conn.commit()
        self.event(job.id, "warning", "requeued", {"error": error, "attempt": job.attempts})
        return True

    def add_result(
        self,
        *,
        job_id: str,
        result_index: int,
        path: str,
        raw_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        result_id = uuid4().hex
        self.conn.execute(
            """
            INSERT INTO results (id, job_id, result_index, path, raw_url, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result_id,
                job_id,
                int(result_index),
                path,
                raw_url,
                json_dumps(metadata or {}),
                utc_now(),
            ),
        )
        self.conn.commit()
        return result_id

    def event(
        self,
        job_id: str | None,
        level: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO events (id, job_id, level, message, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (uuid4().hex, job_id, level, message, json_dumps(metadata or {}), utc_now()),
        )
        self.conn.commit()

    def get_job(self, job_id: str) -> JobRecord | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def results_for_job(self, job_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM results WHERE job_id = ? ORDER BY result_index ASC",
            (job_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "job_id": row["job_id"],
                "index": row["result_index"],
                "path": row["path"],
                "raw_url": row["raw_url"],
                "metadata": json_loads(row["metadata"], {}),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def events_for_job(self, job_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM events
            WHERE job_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (job_id, max(1, int(limit))),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "job_id": row["job_id"],
                "level": row["level"],
                "message": row["message"],
                "metadata": json_loads(row["metadata"], {}),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def queued_position(self, job_id: str) -> int | None:
        rows = self.conn.execute(
            """
            SELECT id FROM jobs
            WHERE status = 'queued'
            ORDER BY priority DESC, created_at ASC
            """
        ).fetchall()
        for index, row in enumerate(rows, start=1):
            if row["id"] == job_id:
                return index
        return None

    def list_jobs(self, *, limit: int = 20, status: str | None = None) -> list[JobRecord]:
        params: list[Any] = []
        where = ""
        if status:
            where = "WHERE status = ?"
            params.append(status)
        rows = self.conn.execute(
            f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ?",
            [*params, max(1, int(limit))],
        ).fetchall()
        return [_row_to_job(row) for row in rows]

    def summary(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) AS count FROM jobs GROUP BY status").fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def retry(self, job_id: str) -> bool:
        row = self.conn.execute(
            "UPDATE jobs SET status = 'queued', error = NULL, updated_at = ? WHERE id = ? AND status = 'failed'",
            (utc_now(), job_id),
        )
        self.conn.commit()
        return row.rowcount > 0

    def cancel(self, job_id: str) -> bool:
        row = self.conn.execute(
            "UPDATE jobs SET status = 'cancelled', updated_at = ?, finished_at = ? WHERE id = ? AND status IN ('queued', 'running')",
            (utc_now(), utc_now(), job_id),
        )
        self.conn.commit()
        return row.rowcount > 0

    def get_last_key_id(self, provider_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT last_key_id FROM provider_state WHERE provider_id = ?",
            (provider_id,),
        ).fetchone()
        return str(row["last_key_id"]) if row and row["last_key_id"] else None

    def set_last_key_id(self, provider_id: str, key_id: str) -> None:
        self.conn.execute(
            """
            INSERT INTO provider_state (provider_id, last_key_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(provider_id) DO UPDATE SET
              last_key_id = excluded.last_key_id,
              updated_at = excluded.updated_at
            """,
            (provider_id, key_id, utc_now()),
        )
        self.conn.commit()


def _row_to_job(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        id=str(row["id"]),
        kind=str(row["kind"]),
        status=str(row["status"]),
        provider_id=str(row["provider_id"]) if row["provider_id"] else None,
        prompt=str(row["prompt"]),
        input_images=list(json_loads(row["input_images"], [])),
        mask=str(row["mask"]) if row["mask"] else None,
        params=dict(json_loads(row["params"], {})),
        desired_count=int(row["desired_count"]),
        out_dir=str(row["out_dir"]) if row["out_dir"] else None,
        out_prefix=str(row["out_prefix"]) if row["out_prefix"] else None,
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        priority=int(row["priority"]),
        error=str(row["error"]) if row["error"] else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        started_at=str(row["started_at"]) if row["started_at"] else None,
        finished_at=str(row["finished_at"]) if row["finished_at"] else None,
    )
