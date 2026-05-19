from __future__ import annotations

import html
import json
from pathlib import Path
import sys
from typing import Any

from .config import AppConfig
from .prompting import render_prompt_template
from .provider_support import provider_parameter_support
from .queue import ImageQueue, JobRecord
from .service import ensure_worker, worker_status


MISSING_WEBUI_DEPS_MESSAGE = (
    "WebUI dependencies are not installed. Install them with `pip install -e .[webui]` "
    "or `pip install gen-image-via-api[webui]`."
)

LANGUAGES = (("中文", "zh"), ("English", "en"))
TEMPLATE_DEFAULT = "__default__"
TEMPLATE_NONE = "__none__"

TEXT: dict[str, dict[str, str]] = {
    "zh": {
        "app_title": "Gen Image Studio",
        "app_subtitle": "一个本地图片生成控制台，复用 CLI 配置、队列、worker 和 provider 路由。",
        "config": "配置",
        "queue_db": "队列",
        "worker": "Worker",
        "worker_pid": "pid",
        "worker_age": "心跳",
        "language": "语言",
        "provider": "Provider",
        "provider_status": "Provider 状态",
        "create_tab": "创作",
        "templates_tab": "模板",
        "queue_tab": "队列",
        "gallery_tab": "画廊",
        "providers_tab": "Providers",
        "template": "提示词模板",
        "prompt": "提示词",
        "prompt_placeholder": "写下要生成或编辑的画面描述...",
        "prompt_info": "模板会在提交前渲染；下方可以预览最终入队 prompt。",
        "template_body": "模板正文",
        "rendered_prompt": "最终 Prompt 预览",
        "template_default": "使用配置默认模板",
        "template_none": "不使用模板",
        "no_template_body": "未选择模板；将直接使用原始提示词。",
        "default_no_template": "配置没有默认模板；将直接使用原始提示词。",
        "disabled_template": "模板不可用或已禁用。",
        "provider_info_auto": "自动路由：按 CLI 顺序选择 provider：任务指定、配置默认、启用 provider 的 priority。",
        "provider_direct": "直接控件",
        "provider_extra": "Extra params JSON",
        "provider_ignored": "此 provider 会忽略",
        "provider_notes": "说明",
        "generation": "生成设置",
        "count": "数量",
        "out_prefix": "输出前缀",
        "out_dir": "输出目录",
        "size": "尺寸",
        "aspect_ratio": "比例",
        "size_tier": "尺寸档位",
        "model": "模型覆盖",
        "output_format": "输出格式",
        "quality": "质量",
        "background": "背景",
        "input_images": "输入图片路径",
        "input_images_info": "每行一个本地路径。非空时自动按编辑/图生图任务提交。",
        "mask": "蒙版路径",
        "advanced": "高级参数",
        "extra_params": "Extra params JSON",
        "extra_params_info": "provider 专属字段放这里，例如 seed、negativePrompt、sampler、characterPrompts。",
        "submit": "提交任务",
        "preview": "预览 Prompt",
        "refresh": "刷新",
        "all_status": "全部状态",
        "queue": "队列",
        "gallery": "输出画廊",
        "jobs": "最近任务",
        "status_filter": "状态过滤",
        "limit": "数量上限",
        "job_id": "任务 ID",
        "job_id_placeholder": "粘贴任务 ID 用于查看、重试或取消",
        "inspect": "查看",
        "retry": "重试失败任务",
        "cancel": "取消任务",
        "detail": "任务详情",
        "template_list": "模板列表",
        "template_sample": "模板预览输入",
        "template_sample_placeholder": "输入一段示例提示词，单独测试模板渲染效果...",
        "render_template": "渲染模板",
        "provider_matrix": "Provider 能力表",
        "config_path": "配置文件",
        "output_dir": "输出目录",
        "review_reference": "视觉参考",
        "notice_missing_prompt": "提示词不能为空。",
        "notice_invalid_json": "Extra params 必须是 JSON 对象",
        "notice_queued": "已入队",
        "notice_missing_job": "缺少任务 ID。",
        "notice_not_found": "找不到任务。",
        "notice_retried": "已重试",
        "notice_not_failed": "这个任务不是失败状态，不能重试。",
        "notice_cancelled": "已取消",
        "notice_not_cancellable": "这个任务不是 queued/running，不能取消。",
        "auto_route": "自动路由",
        "disabled": "已禁用",
        "default_provider": "默认 provider",
        "enabled": "启用",
        "capabilities": "能力",
        "results": "结果数",
        "attempts": "尝试",
        "created": "创建时间",
        "kind": "类型",
        "status": "状态",
        "prompt_column": "提示词",
    },
    "en": {
        "app_title": "Gen Image Studio",
        "app_subtitle": "A local image-generation console powered by the same CLI config, queue, worker, and provider routing.",
        "config": "Config",
        "queue_db": "Queue",
        "worker": "Worker",
        "worker_pid": "pid",
        "worker_age": "heartbeat",
        "language": "Language",
        "provider": "Provider",
        "provider_status": "Provider status",
        "create_tab": "Create",
        "templates_tab": "Templates",
        "queue_tab": "Queue",
        "gallery_tab": "Gallery",
        "providers_tab": "Providers",
        "template": "Prompt template",
        "prompt": "Prompt",
        "prompt_placeholder": "Describe the image to generate or edit...",
        "prompt_info": "Templates are rendered before submit; preview the final queued prompt below.",
        "template_body": "Template body",
        "rendered_prompt": "Rendered prompt preview",
        "template_default": "Use config default template",
        "template_none": "No template",
        "no_template_body": "No template selected; the raw prompt will be queued.",
        "default_no_template": "No default template is configured; the raw prompt will be queued.",
        "disabled_template": "Template is unavailable or disabled.",
        "provider_info_auto": "Auto route: provider selection follows CLI order: job override, config default, enabled providers by priority.",
        "provider_direct": "Direct controls",
        "provider_extra": "Extra params JSON",
        "provider_ignored": "Ignored by this provider",
        "provider_notes": "Notes",
        "generation": "Generation",
        "count": "Count",
        "out_prefix": "Output prefix",
        "out_dir": "Output directory",
        "size": "Size",
        "aspect_ratio": "Aspect ratio",
        "size_tier": "Size tier",
        "model": "Model override",
        "output_format": "Output format",
        "quality": "Quality",
        "background": "Background",
        "input_images": "Input image paths",
        "input_images_info": "One local path per line. Non-empty paths submit an edit/image-to-image job.",
        "mask": "Mask path",
        "advanced": "Advanced params",
        "extra_params": "Extra params JSON",
        "extra_params_info": "Provider-specific fields go here, for example seed, negativePrompt, sampler, characterPrompts.",
        "submit": "Submit job",
        "preview": "Preview prompt",
        "refresh": "Refresh",
        "all_status": "All statuses",
        "queue": "Queue",
        "gallery": "Output gallery",
        "jobs": "Recent jobs",
        "status_filter": "Status filter",
        "limit": "Limit",
        "job_id": "Job id",
        "job_id_placeholder": "Paste a job id for details, retry, or cancel",
        "inspect": "Inspect",
        "retry": "Retry failed job",
        "cancel": "Cancel job",
        "detail": "Job detail",
        "template_list": "Template list",
        "template_sample": "Template preview input",
        "template_sample_placeholder": "Enter a sample prompt to test how this template renders...",
        "render_template": "Render template",
        "provider_matrix": "Provider capability matrix",
        "config_path": "Config file",
        "output_dir": "Output directory",
        "review_reference": "Visual reference",
        "notice_missing_prompt": "Prompt is required.",
        "notice_invalid_json": "Extra params must be a JSON object",
        "notice_queued": "Queued",
        "notice_missing_job": "Missing job id.",
        "notice_not_found": "Job not found.",
        "notice_retried": "Retried",
        "notice_not_failed": "This job is not failed, so it cannot be retried.",
        "notice_cancelled": "Cancelled",
        "notice_not_cancellable": "This job is not queued/running, so it cannot be cancelled.",
        "auto_route": "Auto route",
        "disabled": "disabled",
        "default_provider": "Default provider",
        "enabled": "enabled",
        "capabilities": "capabilities",
        "results": "results",
        "attempts": "attempts",
        "created": "created",
        "kind": "kind",
        "status": "status",
        "prompt_column": "prompt",
    },
}


CSS = """
:root {
  --studio-bg: #f8f7f3;
  --studio-ink: #1f2521;
  --studio-muted: #697169;
  --studio-line: rgba(39, 45, 39, .14);
  --studio-surface: rgba(255, 255, 252, .92);
  --studio-accent: #315f4b;
}
.gradio-container {
  background: var(--studio-bg) !important;
}
.studio-shell {
  max-width: 1480px;
  margin: 0 auto;
  padding: 10px 14px 24px;
}
.studio-topbar {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) minmax(320px, 2fr);
  gap: 14px;
  align-items: center;
  border: 1px solid var(--studio-line);
  border-radius: 22px;
  padding: 14px 16px;
  background: rgba(255,255,255,.78);
  box-shadow: 0 18px 44px rgba(38, 43, 38, .07);
  margin-bottom: 12px;
}
.studio-brand {
  display: flex;
  align-items: center;
  gap: 12px;
}
.studio-mark {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  display: grid;
  place-items: center;
  color: white;
  background: linear-gradient(135deg, #1f7a55, #2c5f4d);
  font-weight: 800;
  letter-spacing: -.05em;
}
.studio-title {
  font-size: 22px;
  font-weight: 780;
  margin: 0 0 2px;
  color: var(--studio-ink);
}
.studio-subtitle {
  margin: 0;
  color: var(--studio-muted);
  font-size: 13px;
}
.studio-badges {
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 9px;
}
.studio-badge {
  border: 1px solid var(--studio-line);
  border-radius: 999px;
  padding: 8px 11px;
  background: rgba(255,255,255,.82);
  color: var(--studio-ink);
  font-size: 12px;
  white-space: nowrap;
}
.studio-badge strong {
  color: var(--studio-accent);
  margin-left: 4px;
}
.studio-tabs {
  border: 1px solid var(--studio-line);
  border-radius: 24px;
  background: rgba(255,255,255,.58);
  padding: 8px;
}
.studio-language {
  border: 1px solid var(--studio-line) !important;
  border-radius: 22px !important;
  background: rgba(255,255,255,.78) !important;
  box-shadow: 0 18px 44px rgba(38, 43, 38, .07) !important;
}
.studio-language .wrap {
  gap: 6px !important;
}
.studio-panel {
  border: 1px solid var(--studio-line) !important;
  border-radius: 22px !important;
  background: var(--studio-surface) !important;
  box-shadow: 0 15px 36px rgba(53, 45, 34, .06) !important;
}
.studio-card {
  border: 1px solid var(--studio-line) !important;
  border-radius: 18px !important;
  background: rgba(255,255,255,.72) !important;
}
.studio-section-title h2, .studio-section-title h3 {
  margin-top: 0 !important;
}
.studio-template-list textarea {
  font-size: 12px !important;
}
.studio-code textarea, .studio-code pre {
  font-family: "IBM Plex Mono", "Cascadia Code", Consolas, monospace !important;
}
.studio-action button {
  border-radius: 999px !important;
  font-weight: 760 !important;
}
.studio-queue table {
  font-size: 12px !important;
}
@media (max-width: 860px) {
  .studio-topbar { grid-template-columns: 1fr; }
  .studio-badges { justify-content: flex-start; }
}
"""


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
    """Build a styled Gradio Blocks app without importing Gradio at module import time."""

    lang0 = "zh"
    theme = gr.themes.Soft(
        primary_hue="emerald",
        secondary_hue="orange",
        neutral_hue="stone",
        radius_size="lg",
        font=("Satoshi", "Geist", "Segoe UI", "ui-sans-serif", "system-ui", "sans-serif"),
        font_mono=("IBM Plex Mono", "Cascadia Code", "Consolas", "ui-monospace", "monospace"),
    )

    with gr.Blocks(title="Gen Image Studio", theme=theme, css=CSS, fill_width=True) as demo:
        with gr.Column(elem_classes=["studio-shell"]):
            with gr.Row(equal_height=True):
                with gr.Column(scale=9, min_width=520):
                    header = gr.HTML(_topbar_html(config, lang0))
                with gr.Column(scale=2, min_width=180, variant="panel", elem_classes=["studio-language"]):
                    language = gr.Radio(
                        label=f"{_t('zh', 'language')} / {_t('en', 'language')}",
                        choices=list(LANGUAGES),
                        value=lang0,
                        interactive=True,
                    )
            worker_state = gr.Markdown(_worker_markdown(config, lang0), visible=False)
            status_filter = gr.Dropdown(
                label=_t(lang0, "status_filter"),
                choices=_status_choices(lang0),
                value="",
                visible=False,
            )
            limit = gr.Number(label=_t(lang0, "limit"), value=20, precision=0, minimum=1, visible=False)

            with gr.Tabs(elem_classes=["studio-tabs"]):
                with gr.Tab(_tab_label("create_tab")):
                    with gr.Row(equal_height=False):
                        with gr.Column(scale=7, min_width=500, variant="panel", elem_classes=["studio-panel"]):
                            create_heading = gr.Markdown(f"## {_t(lang0, 'create_tab')}", elem_classes=["studio-section-title"])
                            with gr.Row():
                                provider = gr.Dropdown(
                                    label=_t(lang0, "provider"),
                                    choices=_provider_choices(config, lang0),
                                    value="",
                                    allow_custom_value=False,
                                    scale=2,
                                )
                                template = gr.Dropdown(
                                    label=_t(lang0, "template"),
                                    choices=_template_choices(config, lang0),
                                    value=TEMPLATE_DEFAULT,
                                    allow_custom_value=False,
                                    scale=2,
                                )
                            prompt = gr.Textbox(
                                label=_t(lang0, "prompt"),
                                info=_t(lang0, "prompt_info"),
                                lines=10,
                                placeholder=_t(lang0, "prompt_placeholder"),
                                autofocus=True,
                                show_copy_button=True,
                            )
                            with gr.Row():
                                preview = gr.Button(_t(lang0, "preview"), variant="secondary", elem_classes=["studio-action"])
                                submit = gr.Button(_t(lang0, "submit"), variant="primary", elem_classes=["studio-action"])
                            notice = gr.Markdown()
                            rendered_prompt = gr.Code(
                                label=_t(lang0, "rendered_prompt"),
                                language="markdown",
                                value="",
                                lines=8,
                                interactive=False,
                                elem_classes=["studio-code"],
                            )
                        with gr.Column(scale=4, min_width=360, variant="panel", elem_classes=["studio-panel"]):
                            generation_heading = gr.Markdown(f"## {_t(lang0, 'generation')}", elem_classes=["studio-section-title"])
                            with gr.Row():
                                count = gr.Number(label=_t(lang0, "count"), value=1, precision=0, minimum=1)
                                size_tier = gr.Dropdown(label=_t(lang0, "size_tier"), choices=["1K", "2K", "4K"], value="1K")
                            with gr.Row():
                                size = gr.Textbox(label=_t(lang0, "size"), placeholder=config.defaults.size or "1024x1024")
                                aspect_ratio = gr.Textbox(label=_t(lang0, "aspect_ratio"), placeholder="16:9, 1:1, portrait")
                            with gr.Row():
                                model = gr.Textbox(label=_t(lang0, "model"), placeholder="optional")
                                output_format = gr.Dropdown(
                                    label=_t(lang0, "output_format"),
                                    choices=["", "png", "jpeg", "webp"],
                                    value="",
                                )
                            with gr.Row():
                                quality = gr.Dropdown(label=_t(lang0, "quality"), choices=["", "auto", "low", "medium", "high"], value="")
                                background = gr.Dropdown(label=_t(lang0, "background"), choices=["", "auto", "transparent", "opaque"], value="")
                            with gr.Row():
                                out_prefix = gr.Textbox(label=_t(lang0, "out_prefix"), placeholder="hero-shot")
                                out_dir = gr.Textbox(label=_t(lang0, "out_dir"), placeholder=str(config.queue.output_dir))
                            with gr.Accordion(_bilingual_label("input_images"), open=False):
                                input_images = gr.Textbox(
                                    label=_t(lang0, "input_images"),
                                    info=_t(lang0, "input_images_info"),
                                    lines=4,
                                    placeholder="C:/path/to/input.png",
                                )
                                mask = gr.Textbox(label=_t(lang0, "mask"), placeholder="optional")
                            with gr.Accordion(_bilingual_label("advanced"), open=False):
                                extra_params = gr.Code(
                                    label=_t(lang0, "extra_params"),
                                    language="json",
                                    value="{}",
                                    lines=10,
                                    interactive=True,
                                    elem_classes=["studio-code"],
                                )
                                gr.Markdown(_t(lang0, "extra_params_info"))

                with gr.Tab(_tab_label("templates_tab")):
                    with gr.Row(equal_height=False):
                        with gr.Column(scale=4, min_width=360, variant="panel", elem_classes=["studio-panel"]):
                            templates_heading = gr.Markdown(f"## {_t(lang0, 'template_list')}")
                            template_catalog = gr.Dropdown(
                                label=_t(lang0, "template"),
                                choices=_template_choices(config, lang0),
                                value=TEMPLATE_DEFAULT,
                                allow_custom_value=False,
                            )
                            template_table = gr.Dataframe(
                                label=_t(lang0, "template_list"),
                                headers=["id", "name", "enabled"],
                                datatype=["str", "str", "bool"],
                                value=_template_rows(config),
                                interactive=False,
                                max_height=420,
                                elem_classes=["studio-template-list"],
                            )
                            template_sample = gr.Textbox(
                                label=_t(lang0, "template_sample"),
                                lines=4,
                                placeholder=_t(lang0, "template_sample_placeholder"),
                            )
                            template_preview_button = gr.Button(
                                _t(lang0, "render_template"),
                                variant="secondary",
                                elem_classes=["studio-action"],
                            )
                        with gr.Column(scale=7, min_width=520, variant="panel", elem_classes=["studio-panel"]):
                            template_body = gr.Code(
                                label=_t(lang0, "template_body"),
                                language="markdown",
                                value=_template_body(config, TEMPLATE_DEFAULT, lang0),
                                lines=14,
                                interactive=False,
                                elem_classes=["studio-code"],
                            )
                            template_preview = gr.Code(
                                label=_t(lang0, "rendered_prompt"),
                                language="markdown",
                                value="",
                                lines=10,
                                interactive=False,
                                elem_classes=["studio-code"],
                            )

                with gr.Tab(_tab_label("queue_tab")):
                    with gr.Column(variant="panel", elem_classes=["studio-panel"]):
                        queue_heading = gr.Markdown(f"## {_t(lang0, 'queue_tab')}")
                        with gr.Row():
                            refresh = gr.Button(_t(lang0, "refresh"), elem_classes=["studio-action"])
                            visible_status_filter = gr.Dropdown(
                                label=_t(lang0, "status_filter"),
                                choices=_status_choices(lang0),
                                value="",
                            )
                            visible_limit = gr.Number(label=_t(lang0, "limit"), value=20, precision=0, minimum=1)
                        jobs_table = gr.Dataframe(
                            label=_t(lang0, "jobs"),
                            headers=_job_headers(lang0),
                            datatype=["str", "str", "str", "str", "number", "str", "str", "str"],
                            value=_job_rows(config, limit=20),
                            interactive=False,
                            wrap=True,
                            max_height=520,
                            show_search="filter",
                            elem_classes=["studio-queue"],
                        )
                        with gr.Row():
                            job_id = gr.Textbox(
                                label=_t(lang0, "job_id"),
                                placeholder=_t(lang0, "job_id_placeholder"),
                                scale=3,
                            )
                            inspect = gr.Button(_t(lang0, "inspect"), elem_classes=["studio-action"])
                        with gr.Row():
                            retry = gr.Button(_t(lang0, "retry"), elem_classes=["studio-action"])
                            cancel = gr.Button(_t(lang0, "cancel"), variant="stop", elem_classes=["studio-action"])
                        job_detail = gr.JSON(label=_t(lang0, "detail"), value={}, open=False)

                with gr.Tab(_tab_label("gallery_tab")):
                    with gr.Column(variant="panel", elem_classes=["studio-panel"]):
                        gallery_heading = gr.Markdown(f"## {_t(lang0, 'gallery_tab')}")
                        gallery = gr.Gallery(
                            label=_t(lang0, "gallery"),
                            value=_recent_output_paths(config, limit=40),
                            columns=4,
                            object_fit="contain",
                            height=640,
                            show_download_button=True,
                            elem_classes=["studio-card"],
                        )

                with gr.Tab(_tab_label("providers_tab")):
                    with gr.Row(equal_height=False):
                        with gr.Column(scale=5, min_width=420, variant="panel", elem_classes=["studio-panel"]):
                            providers_heading = gr.Markdown(f"## {_t(lang0, 'provider_matrix')}")
                            provider_table = gr.Dataframe(
                                label=_t(lang0, "provider_matrix"),
                                headers=["id", "type", "enabled", "priority", "model", "keys"],
                                datatype=["str", "str", "bool", "number", "str", "number"],
                                value=_provider_rows(config),
                                interactive=False,
                                max_height=420,
                            )
                        with gr.Column(scale=6, min_width=500, variant="panel", elem_classes=["studio-panel"]):
                            provider_catalog = gr.Dropdown(
                                label=_t(lang0, "provider"),
                                choices=_provider_choices(config, lang0),
                                value="",
                                allow_custom_value=False,
                            )
                            provider_info = gr.Markdown(_provider_markdown(config, "", lang0), elem_classes=["studio-card"])
                            provider_config_info = gr.Markdown(_config_markdown(config, lang0))

        language.change(
            _language_callback(config, gr),
            inputs=[language, provider, template],
            outputs=[
                header,
                worker_state,
                provider,
                provider_catalog,
                template,
                template_catalog,
                provider_info,
                provider_config_info,
                create_heading,
                generation_heading,
                templates_heading,
                queue_heading,
                gallery_heading,
                providers_heading,
                prompt,
                count,
                size_tier,
                size,
                aspect_ratio,
                model,
                output_format,
                quality,
                background,
                out_prefix,
                out_dir,
                input_images,
                mask,
                extra_params,
                template_body,
                template_sample,
                rendered_prompt,
                template_preview,
                template_preview_button,
                submit,
                preview,
                refresh,
                visible_status_filter,
                visible_limit,
                status_filter,
                limit,
                jobs_table,
                gallery,
                job_id,
                inspect,
                retry,
                cancel,
                job_detail,
                template_table,
                provider_table,
            ],
        )
        provider.change(_provider_change_callback(config, gr), inputs=[provider, language], outputs=[provider_info, provider_catalog])
        provider_catalog.change(_provider_catalog_change_callback(config, gr), inputs=[provider_catalog, language], outputs=[provider, provider_info])
        template.change(_template_change_callback(config, gr), inputs=[template, language], outputs=[template_catalog, template_body, rendered_prompt, template_preview])
        template_catalog.change(_template_catalog_change_callback(config, gr), inputs=[template_catalog, language], outputs=[template, template_body, rendered_prompt, template_preview])
        template_preview_button.click(
            _template_sample_preview_callback(config, gr),
            inputs=[template_catalog, template_sample, language],
            outputs=[template_preview],
        )
        refresh.click(
            _refresh_callback(config),
            inputs=[visible_limit, visible_status_filter, language],
            outputs=[jobs_table, gallery, worker_state],
        )
        preview.click(
            _preview_callback(config, gr),
            inputs=[
                prompt,
                template,
                count,
                size,
                aspect_ratio,
                size_tier,
                output_format,
                quality,
                extra_params,
                language,
            ],
            outputs=[rendered_prompt, template_preview, notice],
        )
        submit.click(
            _submit_callback(config, gr),
            inputs=[
                prompt,
                template,
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
                language,
            ],
            outputs=[notice, jobs_table, gallery, worker_state, job_detail, rendered_prompt, template_preview],
        )
        inspect.click(_inspect_callback(config), inputs=[job_id, language], outputs=[job_detail])
        retry.click(
            _retry_callback(config),
            inputs=[job_id, visible_limit, visible_status_filter, language],
            outputs=[notice, jobs_table, gallery, worker_state, job_detail],
        )
        cancel.click(
            _cancel_callback(config),
            inputs=[job_id, visible_limit, visible_status_filter, language],
            outputs=[notice, jobs_table, gallery, worker_state, job_detail],
        )
    return demo


def _submit_callback(config: AppConfig, gr):
    def submit_job(
        prompt: str,
        template_id: str,
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
        lang: str,
    ):
        lang = _lang(lang)
        text = str(prompt or "").strip()
        if not text:
            raise gr.Error(_t(lang, "notice_missing_prompt"))
        try:
            params = _params_from_form(
                config,
                extra_params=extra_params,
                size=size,
                aspect_ratio=aspect_ratio,
                size_tier=size_tier,
                model=model,
                output_format=output_format,
                quality=quality,
                background=background,
            )
            final_prompt = _render_prompt_for_template(config, text, params, max(1, int(count or 1)), template_id, lang)
        except ValueError as exc:
            raise gr.Error(str(exc)) from exc

        images = _path_lines(input_images)
        queue = ImageQueue(config.queue.db)
        try:
            job_id = queue.enqueue(
                kind="edit" if images else "generate",
                prompt=final_prompt,
                input_images=images,
                mask=str(mask or "").strip() or None,
                params=params,
                desired_count=max(1, int(count or 1)),
                provider_id=str(provider_id or "").strip() or None,
                out_dir=str(out_dir or "").strip() or None,
                out_prefix=str(out_prefix or "").strip() or None,
                max_attempts=config.queue.max_attempts,
            )
            detail = _job_to_dict(queue, queue.get_job(job_id)) or {"id": job_id}
        finally:
            queue.close()
        worker = ensure_worker(config)
        notice = f"**{_t(lang, 'notice_queued')}** `{job_id}` · {worker.get('message') or ''}".strip()
        return (
            notice,
            _job_rows(config, limit=20),
            _recent_output_paths(config, limit=20),
            _worker_markdown(config, lang),
            detail,
            final_prompt,
            final_prompt,
        )

    return submit_job


def _preview_callback(config: AppConfig, gr):
    def preview_prompt(
        prompt: str,
        template_id: str,
        count: int | float,
        size: str,
        aspect_ratio: str,
        size_tier: str,
        output_format: str,
        quality: str,
        extra_params: str,
        lang: str,
    ):
        lang = _lang(lang)
        text = str(prompt or "").strip()
        if not text:
            raise gr.Error(_t(lang, "notice_missing_prompt"))
        try:
            params = _params_from_form(
                config,
                extra_params=extra_params,
                size=size,
                aspect_ratio=aspect_ratio,
                size_tier=size_tier,
                model="",
                output_format=output_format,
                quality=quality,
                background="",
            )
            final_prompt = _render_prompt_for_template(config, text, params, max(1, int(count or 1)), template_id, lang)
        except ValueError as exc:
            raise gr.Error(str(exc)) from exc
        return final_prompt, final_prompt, ""

    return preview_prompt


def _refresh_callback(config: AppConfig):
    def refresh_jobs(limit: int | float, status: str, lang: str):
        status_value = str(status or "") or None
        return (
            _job_rows(config, limit=_int_value(limit, 20), status=status_value),
            _recent_output_paths(config, limit=_int_value(limit, 20), status=status_value),
            _worker_markdown(config, _lang(lang)),
        )

    return refresh_jobs


def _provider_change_callback(config: AppConfig, gr):
    def provider_info(provider_id: str, lang: str):
        return _provider_markdown(config, provider_id, _lang(lang)), gr.update(value=provider_id or "")

    return provider_info


def _provider_catalog_change_callback(config: AppConfig, gr):
    def provider_info(provider_id: str, lang: str):
        return gr.update(value=provider_id or ""), _provider_markdown(config, provider_id, _lang(lang))

    return provider_info


def _template_change_callback(config: AppConfig, gr):
    def update_template(template_id: str, lang: str):
        clean_template = template_id or TEMPLATE_DEFAULT
        return gr.update(value=clean_template), _template_body(config, clean_template, _lang(lang)), "", ""

    return update_template


def _template_catalog_change_callback(config: AppConfig, gr):
    def update_template(template_id: str, lang: str):
        clean_template = template_id or TEMPLATE_DEFAULT
        return gr.update(value=clean_template), _template_body(config, clean_template, _lang(lang)), "", ""

    return update_template


def _template_sample_preview_callback(config: AppConfig, gr):
    def preview_template(template_id: str, prompt: str, lang: str) -> str:
        lang = _lang(lang)
        text = str(prompt or "").strip()
        if not text:
            raise gr.Error(_t(lang, "notice_missing_prompt"))
        try:
            return _render_prompt_for_template(config, text, {}, 1, template_id, lang)
        except ValueError as exc:
            raise gr.Error(str(exc)) from exc

    return preview_template


def _inspect_callback(config: AppConfig):
    def inspect_job(job_id: str, lang: str) -> dict[str, Any]:
        return _load_job_detail(config, job_id) or {"error": _t(_lang(lang), "notice_not_found")}

    return inspect_job


def _retry_callback(config: AppConfig):
    def retry_job(job_id: str, limit: int | float, status: str, lang: str):
        lang = _lang(lang)
        clean_id = str(job_id or "").strip()
        if not clean_id:
            return _t(lang, "notice_missing_job"), _job_rows(config, limit=_int_value(limit, 20), status=status or None), _recent_output_paths(config), _worker_markdown(config, lang), {}
        queue = ImageQueue(config.queue.db)
        try:
            changed = queue.retry(clean_id)
            detail = _job_to_dict(queue, queue.get_job(clean_id)) or {"id": clean_id}
        finally:
            queue.close()
        worker = ensure_worker(config) if changed else worker_status(config)
        notice = f"{_t(lang, 'notice_retried')} `{clean_id}`." if changed else _t(lang, "notice_not_failed")
        return notice, _job_rows(config, limit=_int_value(limit, 20), status=status or None), _recent_output_paths(config), _worker_markdown(config, lang), {"worker": worker, "job": detail}

    return retry_job


def _cancel_callback(config: AppConfig):
    def cancel_job(job_id: str, limit: int | float, status: str, lang: str):
        lang = _lang(lang)
        clean_id = str(job_id or "").strip()
        if not clean_id:
            return _t(lang, "notice_missing_job"), _job_rows(config, limit=_int_value(limit, 20), status=status or None), _recent_output_paths(config), _worker_markdown(config, lang), {}
        queue = ImageQueue(config.queue.db)
        try:
            changed = queue.cancel(clean_id)
            detail = _job_to_dict(queue, queue.get_job(clean_id)) or {"id": clean_id}
        finally:
            queue.close()
        notice = f"{_t(lang, 'notice_cancelled')} `{clean_id}`." if changed else _t(lang, "notice_not_cancellable")
        return notice, _job_rows(config, limit=_int_value(limit, 20), status=status or None), _recent_output_paths(config), _worker_markdown(config, lang), detail

    return cancel_job


def _language_callback(config: AppConfig, gr):
    def change_language(lang: str, provider_id: str, template_id: str):
        lang = _lang(lang)
        provider_value = provider_id or ""
        template_value = template_id or TEMPLATE_DEFAULT
        return (
            _topbar_html(config, lang),
            _worker_markdown(config, lang),
            gr.update(label=_t(lang, "provider"), choices=_provider_choices(config, lang), value=provider_value),
            gr.update(label=_t(lang, "provider"), choices=_provider_choices(config, lang), value=provider_value),
            gr.update(label=_t(lang, "template"), choices=_template_choices(config, lang), value=template_value),
            gr.update(label=_t(lang, "template"), choices=_template_choices(config, lang), value=template_value),
            _provider_markdown(config, provider_value, lang),
            _config_markdown(config, lang),
            f"## {_t(lang, 'create_tab')}",
            f"## {_t(lang, 'generation')}",
            f"## {_t(lang, 'template_list')}",
            f"## {_t(lang, 'queue_tab')}",
            f"## {_t(lang, 'gallery_tab')}",
            f"## {_t(lang, 'provider_matrix')}",
            gr.update(label=_t(lang, "prompt"), info=_t(lang, "prompt_info"), placeholder=_t(lang, "prompt_placeholder")),
            gr.update(label=_t(lang, "count")),
            gr.update(label=_t(lang, "size_tier")),
            gr.update(label=_t(lang, "size")),
            gr.update(label=_t(lang, "aspect_ratio")),
            gr.update(label=_t(lang, "model")),
            gr.update(label=_t(lang, "output_format")),
            gr.update(label=_t(lang, "quality")),
            gr.update(label=_t(lang, "background")),
            gr.update(label=_t(lang, "out_prefix")),
            gr.update(label=_t(lang, "out_dir")),
            gr.update(label=_t(lang, "input_images"), info=_t(lang, "input_images_info")),
            gr.update(label=_t(lang, "mask")),
            gr.update(label=_t(lang, "extra_params")),
            gr.update(label=_t(lang, "template_body"), value=_template_body(config, template_value, lang)),
            gr.update(label=_t(lang, "template_sample"), placeholder=_t(lang, "template_sample_placeholder")),
            gr.update(label=_t(lang, "rendered_prompt")),
            gr.update(label=_t(lang, "rendered_prompt")),
            gr.update(value=_t(lang, "render_template")),
            gr.update(value=_t(lang, "submit")),
            gr.update(value=_t(lang, "preview")),
            gr.update(value=_t(lang, "refresh")),
            gr.update(label=_t(lang, "status_filter"), choices=_status_choices(lang)),
            gr.update(label=_t(lang, "limit")),
            gr.update(label=_t(lang, "status_filter"), choices=_status_choices(lang)),
            gr.update(label=_t(lang, "limit")),
            gr.update(label=_t(lang, "jobs"), headers=_job_headers(lang)),
            gr.update(label=_t(lang, "gallery")),
            gr.update(label=_t(lang, "job_id"), placeholder=_t(lang, "job_id_placeholder")),
            gr.update(value=_t(lang, "inspect")),
            gr.update(value=_t(lang, "retry")),
            gr.update(value=_t(lang, "cancel")),
            gr.update(label=_t(lang, "detail")),
            gr.update(label=_t(lang, "template_list"), headers=["id", "name", "enabled"]),
            gr.update(label=_t(lang, "provider_matrix"), headers=["id", "type", "enabled", "priority", "model", "keys"]),
        )

    return change_language


def _topbar_html(config: AppConfig, lang: str) -> str:
    status = worker_status(config)
    state = "running" if status.get("running") else "stale" if status.get("stale") else "stopped"
    provider_count = sum(1 for provider in config.providers if provider.enabled)
    default_provider = config.defaults.provider or _t(lang, "auto_route")
    queue_summary = _queue_summary(config)
    return f"""
<section class="studio-topbar">
  <div class="studio-brand">
    <div class="studio-mark">GI</div>
    <div>
      <h1 class="studio-title">{html.escape(_t(lang, "app_title"))}</h1>
      <p class="studio-subtitle">{html.escape(_t(lang, "app_subtitle"))}</p>
    </div>
  </div>
  <div class="studio-badges">
    <span class="studio-badge">{html.escape(_t(lang, "config"))}<strong>OK</strong></span>
    <span class="studio-badge">{html.escape(_t(lang, "worker"))}<strong>{html.escape(state)}</strong></span>
    <span class="studio-badge">{html.escape(_t(lang, "provider"))}<strong>{provider_count} · {html.escape(default_provider)}</strong></span>
    <span class="studio-badge">{html.escape(_t(lang, "queue"))}<strong>{queue_summary}</strong></span>
  </div>
</section>
""".strip()


def _provider_choices(config: AppConfig, lang: str = "zh") -> list[tuple[str, str]]:
    choices = [(_t(lang, "auto_route"), "")]
    for provider in sorted(config.providers, key=lambda item: (item.priority, item.id)):
        suffix = "" if provider.enabled else f" ({_t(lang, 'disabled')})"
        choices.append((f"{provider.id} · {provider.type}{suffix}", provider.id))
    return choices


def _provider_rows(config: AppConfig) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for provider in sorted(config.providers, key=lambda item: (item.priority, item.id)):
        rows.append(
            [
                provider.id,
                provider.type,
                provider.enabled,
                provider.priority,
                provider.model,
                sum(1 for key in provider.keys if key.enabled),
            ]
        )
    return rows


def _template_choices(config: AppConfig, lang: str = "zh") -> list[tuple[str, str]]:
    choices = [(_t(lang, "template_default"), TEMPLATE_DEFAULT), (_t(lang, "template_none"), TEMPLATE_NONE)]
    for template in config.prompt_templates:
        if template.enabled:
            label = template.name if template.name and template.name != template.id else template.id
            choices.append((f"{label} · {template.id}", template.id))
    return choices


def _tab_label(key: str) -> str:
    return _bilingual_label(key)


def _bilingual_label(key: str) -> str:
    zh = _t("zh", key)
    en = _t("en", key)
    return zh if zh == en else f"{zh} / {en}"


def _template_rows(config: AppConfig) -> list[list[Any]]:
    return [[template.id, template.name or template.id, template.enabled] for template in config.prompt_templates]


def _status_choices(lang: str = "zh") -> list[tuple[str, str]]:
    return [
        (_t(lang, "all_status"), ""),
        ("queued", "queued"),
        ("running", "running"),
        ("succeeded", "succeeded"),
        ("failed", "failed"),
        ("cancelled", "cancelled"),
    ]


def _provider_markdown(config: AppConfig, provider_id: str, lang: str = "zh") -> str:
    if not provider_id:
        default = f" {_t(lang, 'default_provider')}: `{config.defaults.provider}`." if config.defaults.provider else ""
        return f"**{_t(lang, 'auto_route')}** · {_t(lang, 'provider_info_auto')}{default}"
    provider = config.provider_map().get(provider_id)
    if provider is None:
        return f"Unknown provider `{provider_id}`."
    support = provider_parameter_support(provider)
    direct = ", ".join(f"`{item}`" for item in support.get("direct_cli_params") or []) or "none"
    extra = ", ".join(f"`{item}`" for item in support.get("extra_params_via_param") or []) or "none"
    ignored = ", ".join(f"`{item}`" for item in support.get("ignored_params") or []) or "none"
    notes = "\n".join(f"- {note}" for note in support.get("notes") or [])
    return (
        f"**{provider.id}** · `{provider.type}` · {_t(lang, 'enabled')}=`{provider.enabled}` · "
        f"{_t(lang, 'capabilities')}={list(provider.capabilities)}\n\n"
        f"**{_t(lang, 'provider_direct')}:** {direct}\n\n"
        f"**{_t(lang, 'provider_extra')}:** {extra}\n\n"
        f"**{_t(lang, 'provider_ignored')}:** {ignored}\n\n"
        f"**{_t(lang, 'provider_notes')}**\n{notes}"
    )


def _config_markdown(config: AppConfig, lang: str = "zh") -> str:
    return f"**{_t(lang, 'config_path')}:** `{config.path}`\n\n**{_t(lang, 'output_dir')}:** `{config.queue.output_dir}`"


def _template_body(config: AppConfig, template_id: str, lang: str = "zh") -> str:
    selected = _selected_template_id(config, template_id)
    if selected is None:
        return _t(lang, "default_no_template") if template_id == TEMPLATE_DEFAULT else _t(lang, "no_template_body")
    template = config.prompt_template_map().get(selected)
    if template is None or not template.enabled:
        return _t(lang, "disabled_template")
    header = f"# {template.name or template.id}\n\n" if template.name else ""
    return f"{header}{template.body}"


def _render_prompt_for_template(
    config: AppConfig,
    raw_prompt: str,
    params: dict[str, Any],
    count: int,
    template_id: str,
    lang: str = "zh",
) -> str:
    selected = _selected_template_id(config, template_id)
    if selected is None:
        return raw_prompt
    template = config.prompt_template_map().get(selected)
    if template is None or not template.enabled:
        raise ValueError(_t(lang, "disabled_template"))
    return render_prompt_template(
        template.body,
        prompt=raw_prompt,
        params=_params_for_prompt_render(config, params, count),
    )


def _selected_template_id(config: AppConfig, template_id: str | None) -> str | None:
    value = str(template_id or TEMPLATE_DEFAULT)
    if value == TEMPLATE_NONE:
        return None
    if value == TEMPLATE_DEFAULT:
        return config.defaults.prompt_template
    return value


def _params_for_prompt_render(config: AppConfig, params: dict[str, Any], count: int) -> dict[str, Any]:
    return {
        "size": config.defaults.size,
        "quality": config.defaults.quality,
        "output_format": config.defaults.output_format,
        "moderation": config.defaults.moderation,
        **params,
        "n": count,
    }


def _params_from_form(
    config: AppConfig,
    *,
    extra_params: str,
    size: str,
    aspect_ratio: str,
    size_tier: str,
    model: str,
    output_format: str,
    quality: str,
    background: str,
) -> dict[str, Any]:
    params = _parse_extra_params(extra_params)
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
    return params


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


def _job_headers(lang: str = "zh") -> list[str]:
    return [
        "id",
        _t(lang, "status"),
        _t(lang, "kind"),
        _t(lang, "provider"),
        _t(lang, "results"),
        _t(lang, "attempts"),
        _t(lang, "created"),
        _t(lang, "prompt_column"),
    ]


def _queue_summary(config: AppConfig) -> str:
    queue = ImageQueue(config.queue.db)
    try:
        summary = queue.summary()
    finally:
        queue.close()
    if not summary:
        return "0"
    parts = [f"{key}:{value}" for key, value in sorted(summary.items()) if value]
    return " · ".join(parts) if parts else "0"


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


def _worker_markdown(config: AppConfig, lang: str = "zh") -> str:
    status = worker_status(config)
    state = "running" if status.get("running") else "stale" if status.get("stale") else "stopped"
    pid = status.get("pid") or "-"
    age = status.get("heartbeat_age_seconds")
    age_text = f"{age:.1f}s" if isinstance(age, (int, float)) else "-"
    return f"**{_t(lang, 'worker')}:** `{state}` · {_t(lang, 'worker_pid')} `{pid}` · {_t(lang, 'worker_age')} `{age_text}`"


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


def _lang(lang: str | None) -> str:
    return "en" if str(lang or "").lower().startswith("en") else "zh"


def _t(lang: str | None, key: str) -> str:
    normalized = _lang(lang)
    return TEXT.get(normalized, TEXT["zh"]).get(key, TEXT["en"].get(key, key))
