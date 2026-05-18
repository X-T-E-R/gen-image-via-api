from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from .config import AppConfig
from .provider_support import provider_parameter_support
from .queue import ImageQueue, JobRecord
from .service import ensure_worker, worker_status


MISSING_WEBUI_DEPS_MESSAGE = (
    "WebUI dependencies are not installed. Install them with `pip install -e .[webui]` "
    "or `pip install gen-image-via-api[webui]`."
)


def serve_webui(
    config: AppConfig,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
    share: bool = False,
) -> int:
    """Start the optional Gradio WebUI for the existing queue-backed CLI."""

    try:
        import gradio as gr
    except ImportError:
        print(MISSING_WEBUI_DEPS_MESSAGE, file=sys.stderr)
        return 2

    demo = build_webui(config, gr)
    demo.queue()
    demo.launch(
        server_name=host,
        server_port=int(port),
        inbrowser=bool(open_browser),
        share=bool(share),
        show_api=False,
    )
    return 0


def build_webui(config: AppConfig, gr):
    """Build a Gradio Blocks app without importing Gradio at module import time."""

    with gr.Blocks(title="Gen Image WebUI", theme=gr.themes.Soft()) as demo:
        gr.Markdown(
            """
# Gen Image WebUI
Local control panel for the existing `gen-image` queue. Provider differences stay visible below; provider-only knobs go into **Extra params JSON**.
""".strip()
        )
        worker_state = gr.Markdown(_worker_markdown(config))

        with gr.Row(equal_height=False):
            with gr.Column(scale=6, min_width=380):
                provider = gr.Dropdown(
                    label="Provider",
                    choices=_provider_choices(config),
                    value="",
                    allow_custom_value=False,
                )
                provider_info = gr.Markdown(_provider_markdown(config, ""))
                prompt = gr.Textbox(label="Prompt", lines=6, placeholder="Describe the image to generate or edit...")
                with gr.Row():
                    count = gr.Number(label="Count", value=1, precision=0, minimum=1)
                    out_prefix = gr.Textbox(label="Output prefix", placeholder="optional")
                with gr.Row():
                    size = gr.Textbox(label="Size", placeholder=config.defaults.size or "1024x1024")
                    aspect_ratio = gr.Textbox(label="Aspect ratio", placeholder="16:9, 1:1, portrait...")
                    size_tier = gr.Dropdown(label="Size tier", choices=["1K", "2K", "4K"], value="1K")
                with gr.Row():
                    model = gr.Textbox(label="Model override", placeholder="optional")
                    output_format = gr.Dropdown(
                        label="Output format",
                        choices=["", "png", "jpeg", "webp"],
                        value="",
                    )
                with gr.Row():
                    quality = gr.Dropdown(label="Quality", choices=["", "auto", "low", "medium", "high"], value="")
                    background = gr.Dropdown(label="Background", choices=["", "auto", "transparent", "opaque"], value="")
                input_images = gr.Textbox(
                    label="Input image paths",
                    lines=3,
                    placeholder="One local path per line. Non-empty paths switch the job to edit/image-to-image.",
                )
                mask = gr.Textbox(label="Mask path", placeholder="optional")
                out_dir = gr.Textbox(label="Output directory", placeholder=str(config.queue.output_dir))
                extra_params = gr.Code(
                    label="Extra params JSON",
                    language="json",
                    value="{}",
                    lines=8,
                    interactive=True,
                )
                submit = gr.Button("Submit job", variant="primary")
                notice = gr.Markdown()

            with gr.Column(scale=7, min_width=460):
                gr.Markdown("## Queue")
                with gr.Row():
                    refresh = gr.Button("Refresh")
                    status_filter = gr.Dropdown(
                        label="Status filter",
                        choices=["", "queued", "running", "succeeded", "failed", "cancelled"],
                        value="",
                    )
                    limit = gr.Number(label="Limit", value=20, precision=0, minimum=1)
                jobs_table = gr.Dataframe(
                    label="Recent jobs",
                    headers=["id", "status", "kind", "provider", "results", "attempts", "created", "prompt"],
                    datatype=["str", "str", "str", "str", "number", "str", "str", "str"],
                    value=_job_rows(config, limit=20),
                    interactive=False,
                    wrap=True,
                )
                gallery = gr.Gallery(
                    label="Recent outputs",
                    value=_recent_output_paths(config, limit=20),
                    columns=3,
                    object_fit="contain",
                    height=420,
                )
                job_id = gr.Textbox(label="Job id", placeholder="Paste a job id for details, retry, or cancel")
                with gr.Row():
                    inspect = gr.Button("Inspect")
                    retry = gr.Button("Retry failed job")
                    cancel = gr.Button("Cancel queued/running job")
                job_detail = gr.JSON(label="Job detail", value={})

        provider.change(_provider_markdown_callback(config), provider, provider_info)
        refresh.click(
            _refresh_callback(config),
            inputs=[limit, status_filter],
            outputs=[jobs_table, gallery, worker_state],
        )
        submit.click(
            _submit_callback(config, gr),
            inputs=[
                prompt,
                provider,
                count,
                out_prefix,
                size,
                aspect_ratio,
                size_tier,
                model,
                output_format,
                quality,
                background,
                input_images,
                mask,
                out_dir,
                extra_params,
            ],
            outputs=[notice, jobs_table, gallery, worker_state, job_detail],
        )
        inspect.click(_inspect_callback(config), inputs=[job_id], outputs=[job_detail])
        retry.click(_retry_callback(config), inputs=[job_id, limit, status_filter], outputs=[notice, jobs_table, gallery, worker_state, job_detail])
        cancel.click(_cancel_callback(config), inputs=[job_id, limit, status_filter], outputs=[notice, jobs_table, gallery, worker_state, job_detail])
    return demo


def _submit_callback(config: AppConfig, gr):
    def submit_job(
        prompt: str,
        provider_id: str,
        count: int | float,
        out_prefix: str,
        size: str,
        aspect_ratio: str,
        size_tier: str,
        model: str,
        output_format: str,
        quality: str,
        background: str,
        input_images: str,
        mask: str,
        out_dir: str,
        extra_params: str,
    ):
        text = str(prompt or "").strip()
        if not text:
            raise gr.Error("Prompt is required.")
        try:
            params = _parse_extra_params(extra_params)
        except ValueError as exc:
            raise gr.Error(str(exc)) from exc
        if str(size or "").strip():
            params["size"] = str(size).strip()
        elif str(aspect_ratio or "").strip():
            params["size"] = _size_from_aspect_ratio(str(aspect_ratio), str(size_tier or "1K"))
        for key, value in {
            "model": model,
            "output_format": output_format,
            "quality": quality,
            "background": background,
        }.items():
            if str(value or "").strip():
                params[key] = str(value).strip()
        images = _path_lines(input_images)
        queue = ImageQueue(config.queue.db)
        try:
            job_id = queue.enqueue(
                kind="edit" if images else "generate",
                prompt=text,
                input_images=images,
                mask=str(mask or "").strip() or None,
                params=params,
                desired_count=max(1, int(count or 1)),
                provider_id=str(provider_id or "").strip() or None,
                out_dir=str(out_dir or "").strip() or None,
                out_prefix=str(out_prefix or "").strip() or None,
                max_attempts=config.queue.max_attempts,
            )
            job = queue.get_job(job_id)
            detail = _job_to_dict(queue, job) if job else {"id": job_id}
        finally:
            queue.close()
        worker = ensure_worker(config)
        notice = f"Queued `{job_id}`. {worker.get('message') or ''}".strip()
        return notice, _job_rows(config, limit=20), _recent_output_paths(config, limit=20), _worker_markdown(config), detail

    return submit_job


def _refresh_callback(config: AppConfig):
    def refresh_jobs(limit: int | float, status: str):
        return _job_rows(config, limit=_int_value(limit, 20), status=str(status or "") or None), _recent_output_paths(
            config,
            limit=_int_value(limit, 20),
            status=str(status or "") or None,
        ), _worker_markdown(config)

    return refresh_jobs


def _provider_markdown_callback(config: AppConfig):
    def provider_info(provider_id: str) -> str:
        return _provider_markdown(config, provider_id)

    return provider_info


def _inspect_callback(config: AppConfig):
    def inspect_job(job_id: str) -> dict[str, Any]:
        return _load_job_detail(config, job_id) or {"error": "job not found"}

    return inspect_job


def _retry_callback(config: AppConfig):
    def retry_job(job_id: str, limit: int | float, status: str):
        clean_id = str(job_id or "").strip()
        if not clean_id:
            return "Missing job id.", _job_rows(config, limit=_int_value(limit, 20), status=status or None), _recent_output_paths(config), _worker_markdown(config), {}
        queue = ImageQueue(config.queue.db)
        try:
            changed = queue.retry(clean_id)
            detail = _job_to_dict(queue, queue.get_job(clean_id)) if queue.get_job(clean_id) else {"id": clean_id}
        finally:
            queue.close()
        worker = ensure_worker(config) if changed else worker_status(config)
        notice = f"Retried `{clean_id}`." if changed else f"Job `{clean_id}` is not a failed job."
        return notice, _job_rows(config, limit=_int_value(limit, 20), status=status or None), _recent_output_paths(config), _worker_markdown(config), {"worker": worker, "job": detail}

    return retry_job


def _cancel_callback(config: AppConfig):
    def cancel_job(job_id: str, limit: int | float, status: str):
        clean_id = str(job_id or "").strip()
        if not clean_id:
            return "Missing job id.", _job_rows(config, limit=_int_value(limit, 20), status=status or None), _recent_output_paths(config), _worker_markdown(config), {}
        queue = ImageQueue(config.queue.db)
        try:
            changed = queue.cancel(clean_id)
            detail = _job_to_dict(queue, queue.get_job(clean_id)) if queue.get_job(clean_id) else {"id": clean_id}
        finally:
            queue.close()
        notice = f"Cancelled `{clean_id}`." if changed else f"Job `{clean_id}` is not queued/running."
        return notice, _job_rows(config, limit=_int_value(limit, 20), status=status or None), _recent_output_paths(config), _worker_markdown(config), detail

    return cancel_job


def _provider_choices(config: AppConfig) -> list[tuple[str, str]]:
    choices = [("Auto route", "")]
    for provider in sorted(config.providers, key=lambda item: (item.priority, item.id)):
        suffix = "" if provider.enabled else " (disabled)"
        choices.append((f"{provider.id} · {provider.type}{suffix}", provider.id))
    return choices


def _provider_markdown(config: AppConfig, provider_id: str) -> str:
    if not provider_id:
        default = f" Default provider: `{config.defaults.provider}`." if config.defaults.provider else ""
        return f"**Auto route.** Uses the CLI routing order: job provider, defaults provider, then enabled providers by priority.{default}"
    provider = config.provider_map().get(provider_id)
    if provider is None:
        return f"Unknown provider `{provider_id}`."
    support = provider_parameter_support(provider)
    direct = ", ".join(f"`{item}`" for item in support.get("direct_cli_params") or []) or "none"
    extra = ", ".join(f"`{item}`" for item in support.get("extra_params_via_param") or []) or "none"
    ignored = ", ".join(f"`{item}`" for item in support.get("ignored_params") or []) or "none"
    notes = "\n".join(f"- {note}" for note in support.get("notes") or [])
    return (
        f"**{provider.id}** · `{provider.type}` · enabled=`{provider.enabled}` · capabilities={list(provider.capabilities)}\n\n"
        f"Direct controls: {direct}\n\n"
        f"Extra params JSON: {extra}\n\n"
        f"Ignored for this provider: {ignored}\n\n"
        f"{notes}"
    )


def _job_rows(config: AppConfig, *, limit: int = 20, status: str | None = None) -> list[list[Any]]:
    queue = ImageQueue(config.queue.db)
    try:
        rows = []
        for job in queue.list_jobs(limit=limit, status=status):
            results = queue.results_for_job(job.id)
            rows.append(
                [
                    job.id,
                    job.status,
                    job.kind,
                    job.provider_id or "auto",
                    len(results),
                    f"{job.attempts}/{job.max_attempts}",
                    job.created_at,
                    _shorten(job.prompt, 120),
                ]
            )
        return rows
    finally:
        queue.close()


def _recent_output_paths(config: AppConfig, *, limit: int = 20, status: str | None = None) -> list[str]:
    queue = ImageQueue(config.queue.db)
    try:
        paths: list[str] = []
        for job in queue.list_jobs(limit=limit, status=status):
            for result in queue.results_for_job(job.id):
                path = Path(str(result.get("path") or "")).expanduser()
                if path.exists() and path.is_file():
                    paths.append(str(path))
        return paths
    finally:
        queue.close()


def _load_job_detail(config: AppConfig, job_id: str) -> dict[str, Any] | None:
    clean_id = str(job_id or "").strip()
    if not clean_id:
        return None
    queue = ImageQueue(config.queue.db)
    try:
        job = queue.get_job(clean_id)
        return _job_to_dict(queue, job) if job else None
    finally:
        queue.close()


def _job_to_dict(queue: ImageQueue, job: JobRecord | None) -> dict[str, Any] | None:
    if job is None:
        return None
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
        "priority": job.priority,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "finished_at": job.finished_at,
        "queue_position": queue.queued_position(job.id),
        "results": queue.results_for_job(job.id),
        "events": queue.events_for_job(job.id, limit=8),
    }


def _worker_markdown(config: AppConfig) -> str:
    status = worker_status(config)
    state = "running" if status.get("running") else "stale" if status.get("stale") else "stopped"
    pid = status.get("pid") or "-"
    age = status.get("heartbeat_age_seconds")
    age_text = f"{age:.1f}s" if isinstance(age, (int, float)) else "-"
    return f"**Worker:** `{state}` · pid `{pid}` · heartbeat age `{age_text}`"


def _parse_extra_params(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Extra params must be a JSON object: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Extra params must be a JSON object.")
    return dict(parsed)


def _path_lines(value: str) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def _int_value(value: Any, fallback: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return fallback


def _shorten(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _size_from_aspect_ratio(value: str, tier: str) -> str:
    ratio = _parse_ratio(value)
    if not ratio:
        return "1024x1024"
    rw, rh = ratio
    tier = str(tier or "1K").upper()
    if rw == rh:
        side = 1024 if tier == "1K" else 2048 if tier == "2K" else 3840
        return f"{side}x{side}"
    long_side = 1024 if tier == "1K" else 2048 if tier == "2K" else 3840
    if rw > rh:
        return f"{long_side}x{max(64, int(round(long_side * rh / rw)))}"
    return f"{max(64, int(round(long_side * rw / rh)))}x{long_side}"


def _parse_ratio(value: str) -> tuple[float, float] | None:
    text = value.strip().lower().replace(" ", "")
    aliases = {"square": "1:1", "landscape": "3:2", "portrait": "2:3"}
    text = aliases.get(text, text)
    for separator in (":", "x", "×"):
        if separator in text:
            left, right = text.split(separator, 1)
            try:
                rw = float(left)
                rh = float(right)
            except ValueError:
                return None
            if rw > 0 and rh > 0:
                return rw, rh
    return None
