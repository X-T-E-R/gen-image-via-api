from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import textwrap
import tomllib
from typing import Any


DEFAULT_CONFIG_NAME = "gen-image.toml"
APP_DIR_NAME = "gen-image-via-api"


@dataclass(frozen=True)
class QueueConfig:
    db: Path
    output_dir: Path
    # 0 means auto: derive worker count from enabled provider/key capacity.
    concurrency: int = 0
    poll_interval_seconds: float = 5.0
    request_timeout_seconds: float = 180.0
    max_attempts: int = 3


@dataclass(frozen=True)
class DefaultsConfig:
    provider: str | None = None
    size: str = "1024x1024"
    quality: str = "auto"
    output_format: str = "png"
    moderation: str = "auto"
    images_per_request: int = 1
    prompt_template: str | None = None


@dataclass(frozen=True)
class PromptTemplateConfig:
    id: str
    name: str = ""
    body: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass(frozen=True)
class SendPresetConfig:
    agent_path: str = ""
    home: str = ""
    module: str = ""
    function: str = ""
    command: tuple[str, ...] = ()


@dataclass(frozen=True)
class SendConfig:
    method: str = "python-call"
    preset: str = ""
    targets: tuple[str, ...] = ()
    default_target: str | None = None
    message_template: str = "MEDIA:{path}"
    module: str = ""
    function: str = ""
    command: tuple[str, ...] = ()
    target_arg: str = "target"
    message_arg: str = "message"
    path_arg: str = "path"
    action_arg: str = "action"
    action: str = "send"
    retry_delays: tuple[float, ...] = (2.0, 5.0, 10.0)
    delay_seconds: float = 0.0
    timeout_seconds: float = 60.0
    args: dict[str, Any] = field(default_factory=dict)
    hermes: SendPresetConfig = field(default_factory=SendPresetConfig)
    openclaw: SendPresetConfig = field(default_factory=SendPresetConfig)


@dataclass(frozen=True)
class ProviderKeyConfig:
    id: str
    api_key: str | None = None
    api_key_env: str | None = None
    enabled: bool = True
    images_per_request: int | None = None
    max_concurrent_requests: int = 1

    def resolve_secret(self) -> str:
        if self.api_key is not None:
            return self.api_key
        if self.api_key_env:
            return os.getenv(self.api_key_env, "")
        return ""

    def secret_label(self) -> str:
        if self.api_key_env:
            return f"env:{self.api_key_env}"
        if self.api_key:
            return "inline:****"
        return "none"


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    type: str
    base_url: str = ""
    model: str = ""
    models: tuple[str, ...] = ()
    enabled: bool = True
    priority: int = 100
    capabilities: tuple[str, ...] = ("generate", "edit")
    timeout_seconds: float | None = None
    images_per_request: int = 1
    max_concurrent_requests: int | None = None
    codex_cli: bool = False
    response_format_b64_json: bool = False
    append_size_to_prompt: bool = False
    force_responses_stream: bool = False
    responses_stream_partial_images: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    keys: tuple[ProviderKeyConfig, ...] = ()
    submit: dict[str, Any] = field(default_factory=dict)
    edit_submit: dict[str, Any] = field(default_factory=dict)
    poll: dict[str, Any] = field(default_factory=dict)

    def supports(self, kind: str) -> bool:
        return self.enabled and kind in self.capabilities


@dataclass(frozen=True)
class AppConfig:
    path: Path
    base_dir: Path
    queue: QueueConfig
    defaults: DefaultsConfig
    providers: tuple[ProviderConfig, ...]
    prompt_templates: tuple[PromptTemplateConfig, ...] = ()
    send: SendConfig = field(default_factory=SendConfig)

    def provider_map(self) -> dict[str, ProviderConfig]:
        return {provider.id: provider for provider in self.providers}

    def prompt_template_map(self) -> dict[str, PromptTemplateConfig]:
        return {template.id: template for template in self.prompt_templates}


class ConfigError(ValueError):
    """Raised when the TOML config is invalid."""


def _as_int(value: Any, fallback: int, *, minimum: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, number)


def _as_float(value: Any, fallback: float, *, minimum: float = 0.1) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, number)


def _as_str_tuple(value: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return fallback
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        items = tuple(str(item).strip() for item in value if str(item).strip())
        return items or fallback
    raise ConfigError(f"Expected string/list value, got {type(value).__name__}")


def _as_float_tuple(value: Any, fallback: tuple[float, ...]) -> tuple[float, ...]:
    if value is None:
        return fallback
    if isinstance(value, (int, float, str)):
        values = (value,)
    elif isinstance(value, list):
        values = tuple(value)
    else:
        raise ConfigError(f"Expected number/list value, got {type(value).__name__}")
    parsed: list[float] = []
    for item in values:
        try:
            parsed.append(max(0.0, float(item)))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"Invalid float value: {item}") from exc
    return tuple(parsed)


def _as_bool(value: Any, fallback: bool = False) -> bool:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return fallback


def _resolve_path(raw: Any, base_dir: Path, fallback: str) -> Path:
    value = Path(str(raw or fallback)).expanduser()
    if not value.is_absolute():
        value = base_dir / value
    return value


def _resolve_optional_path(raw: Any, base_dir: Path) -> Path | None:
    if not raw:
        return None
    value = Path(str(raw)).expanduser()
    return value if value.is_absolute() else base_dir / value


def _load_key(raw: dict[str, Any], index: int) -> ProviderKeyConfig:
    key_id = str(raw.get("id") or f"key-{index}")
    images_per_request = raw.get("images_per_request")
    return ProviderKeyConfig(
        id=key_id,
        api_key=str(raw["api_key"]) if raw.get("api_key") is not None else None,
        api_key_env=str(raw["api_key_env"]) if raw.get("api_key_env") else None,
        enabled=bool(raw.get("enabled", True)),
        images_per_request=(
            _as_int(images_per_request, 1) if images_per_request is not None else None
        ),
        max_concurrent_requests=_as_int(raw.get("max_concurrent_requests"), 1),
    )


def _load_keys_file(
    raw: dict[str, Any],
    *,
    base_dir: Path,
    provider_id: str,
) -> tuple[str | None, tuple[ProviderKeyConfig, ...]]:
    path = _resolve_optional_path(raw.get("keys_file"), base_dir)
    if path is None:
        return None, ()
    if not path.exists():
        raise ConfigError(f"Provider '{provider_id}' keys_file not found: {path}")
    lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    if len(lines) < 2:
        raise ConfigError(f"Provider '{provider_id}' keys_file must contain site URL on line 1 and keys after it: {path}")
    site = lines[0].rstrip("/")
    images_per_request = raw.get("keys_file_images_per_request")
    max_concurrent_requests = raw.get("keys_file_max_concurrent_requests")
    keys = tuple(
        ProviderKeyConfig(
            id=f"{provider_id}-{i}",
            api_key=key,
            enabled=True,
            images_per_request=(
                _as_int(images_per_request, 1) if images_per_request is not None else None
            ),
            max_concurrent_requests=_as_int(max_concurrent_requests, 1),
        )
        for i, key in enumerate(lines[1:], start=1)
    )
    return site, keys


def _load_provider(raw: dict[str, Any], index: int, defaults: DefaultsConfig, base_dir: Path) -> ProviderConfig:
    provider_id = str(raw.get("id") or f"provider-{index}")
    provider_type = str(raw.get("type") or "openai-images")
    file_site, file_keys = _load_keys_file(raw, base_dir=base_dir, provider_id=provider_id)
    keys = (
        *file_keys,
        *tuple(_load_key(item, i) for i, item in enumerate(raw.get("keys") or [], start=1)),
    )
    if provider_type == "mock" and not keys:
        keys = (ProviderKeyConfig(id="mock-key", api_key="mock"),)
    if provider_type != "mock" and not keys:
        raise ConfigError(f"Provider '{provider_id}' must define at least one key")

    return ProviderConfig(
        id=provider_id,
        type=provider_type,
        base_url=str(raw.get("base_url") or file_site or ""),
        model=str(raw.get("model") or ""),
        models=_as_str_tuple(raw.get("models"), ()),
        enabled=bool(raw.get("enabled", True)),
        priority=_as_int(raw.get("priority"), 100, minimum=-1_000_000),
        capabilities=_as_str_tuple(raw.get("capabilities"), ("generate", "edit")),
        timeout_seconds=(
            _as_float(raw.get("timeout_seconds"), 180.0)
            if raw.get("timeout_seconds") is not None
            else None
        ),
        images_per_request=_as_int(
            raw.get("images_per_request"),
            defaults.images_per_request,
        ),
        max_concurrent_requests=(
            _as_int(raw.get("max_concurrent_requests"), 1)
            if raw.get("max_concurrent_requests") is not None
            else None
        ),
        codex_cli=_as_bool(raw.get("codex_cli"), False),
        response_format_b64_json=_as_bool(raw.get("response_format_b64_json"), False),
        append_size_to_prompt=_as_bool(raw.get("append_size_to_prompt"), False),
        force_responses_stream=_as_bool(raw.get("force_responses_stream"), False),
        responses_stream_partial_images=min(
            3,
            _as_int(raw.get("responses_stream_partial_images"), 0, minimum=0),
        ),
        headers={str(k): str(v) for k, v in dict(raw.get("headers") or {}).items()},
        params=dict(raw.get("params") or {}),
        keys=keys,
        submit=dict(raw.get("submit") or {}),
        edit_submit=dict(raw.get("edit_submit") or {}),
        poll=dict(raw.get("poll") or {}),
    )


def _load_prompt_template(raw: Any, index: int, fallback_id: str | None = None) -> PromptTemplateConfig | None:
    if isinstance(raw, str):
        template_id = str(fallback_id or f"template-{index}").strip()
        return PromptTemplateConfig(id=template_id, name=template_id, body=raw, enabled=True) if template_id else None
    if not isinstance(raw, dict):
        return None
    template_id = str(raw.get("id") or fallback_id or f"template-{index}").strip()
    if not template_id:
        return None
    return PromptTemplateConfig(
        id=template_id,
        name=str(raw.get("name") or template_id),
        body=str(raw.get("body") or ""),
        params=dict(raw.get("params") or {}),
        enabled=_as_bool(raw.get("enabled"), True),
    )


def _load_prompt_templates(raw: Any) -> tuple[PromptTemplateConfig, ...]:
    loaded: list[PromptTemplateConfig] = []
    if isinstance(raw, list):
        for index, item in enumerate(raw, start=1):
            template = _load_prompt_template(item, index)
            if template:
                loaded.append(template)
    elif isinstance(raw, dict):
        for index, (template_id, item) in enumerate(raw.items(), start=1):
            template = _load_prompt_template(item, index, str(template_id))
            if template:
                loaded.append(template)
    seen: set[str] = set()
    unique: list[PromptTemplateConfig] = []
    for template in loaded:
        if template.id in seen:
            raise ConfigError(f"Duplicate prompt template id: {template.id}")
        seen.add(template.id)
        unique.append(template)
    return tuple(unique)


def _parse_command_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    raise ConfigError(f"{field_name} must be a string or list")


def _load_send_preset_config(raw: Any, *, field_name: str) -> SendPresetConfig:
    if raw is None:
        return SendPresetConfig()
    if not isinstance(raw, dict):
        raise ConfigError(f"{field_name} must be a table")
    return SendPresetConfig(
        agent_path=str(raw.get("agent_path") or ""),
        home=str(raw.get("home") or ""),
        module=str(raw.get("module") or ""),
        function=str(raw.get("function") or ""),
        command=_parse_command_tuple(raw.get("command"), field_name=f"{field_name}.command"),
    )


def _load_send_config(raw: Any) -> SendConfig:
    if raw is None:
        return SendConfig()
    if not isinstance(raw, dict):
        raise ConfigError("[send] must be a table")
    command_tuple = _parse_command_tuple(raw.get("command"), field_name="[send].command")
    targets = _as_str_tuple(raw.get("targets"), ())
    default_target = str(raw["default_target"]) if raw.get("default_target") else None
    if default_target and not targets:
        targets = (default_target,)
    return SendConfig(
        method=str(raw.get("method") or "python-call"),
        preset=str(raw.get("preset") or ""),
        targets=targets,
        default_target=default_target,
        message_template=str(raw.get("message_template") or "MEDIA:{path}"),
        module=str(raw.get("module") or ""),
        function=str(raw.get("function") or ""),
        command=command_tuple,
        target_arg=str(raw.get("target_arg") or "target"),
        message_arg=str(raw.get("message_arg") or "message"),
        path_arg=str(raw.get("path_arg") or "path"),
        action_arg=str(raw.get("action_arg") or "action"),
        action=str(raw.get("action") or "send"),
        retry_delays=_as_float_tuple(raw.get("retry_delays"), (2.0, 5.0, 10.0)),
        delay_seconds=_as_float(raw.get("delay_seconds"), 0.0, minimum=0.0),
        timeout_seconds=_as_float(raw.get("timeout_seconds"), 60.0),
        args=dict(raw.get("args") or {}),
        hermes=_load_send_preset_config(raw.get("hermes"), field_name="[send.hermes]"),
        openclaw=_load_send_preset_config(raw.get("openclaw"), field_name="[send.openclaw]"),
    )


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_skill_config_path() -> Path:
    return skill_root() / DEFAULT_CONFIG_NAME


def default_user_config_path() -> Path:
    if os.name == "nt" and os.getenv("APPDATA"):
        return Path(os.environ["APPDATA"]) / APP_DIR_NAME / "config.toml"
    return Path.home() / ".config" / APP_DIR_NAME / "config.toml"


def resolve_config_path(path: str | Path | None = None) -> Path:
    if path:
        config_path = Path(path).expanduser()
        return config_path if config_path.is_absolute() else Path.cwd() / config_path

    env_path = os.getenv("GEN_IMAGE_CONFIG")
    if env_path:
        return resolve_config_path(env_path)

    cwd_config = Path.cwd() / DEFAULT_CONFIG_NAME
    if cwd_config.exists():
        return cwd_config

    skill_config = default_skill_config_path()
    if skill_config.exists():
        return skill_config

    return skill_config


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = resolve_config_path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    base_dir = config_path.parent

    defaults_raw = dict(data.get("defaults") or {})
    defaults = DefaultsConfig(
        provider=str(defaults_raw["provider"]) if defaults_raw.get("provider") else None,
        size=str(defaults_raw.get("size") or "1024x1024"),
        quality=str(defaults_raw.get("quality") or "auto"),
        output_format=str(defaults_raw.get("output_format") or "png"),
        moderation=str(defaults_raw.get("moderation") or "auto"),
        images_per_request=_as_int(defaults_raw.get("images_per_request"), 1),
        prompt_template=str(defaults_raw["prompt_template"]) if defaults_raw.get("prompt_template") else None,
    )

    queue_raw = dict(data.get("queue") or {})
    queue = QueueConfig(
        db=_resolve_path(queue_raw.get("db"), base_dir, ".gen-image-queue/queue.sqlite3"),
        output_dir=_resolve_path(queue_raw.get("output_dir"), base_dir, "output/imagegen"),
        concurrency=_as_int(queue_raw.get("concurrency"), 0, minimum=0),
        poll_interval_seconds=_as_float(queue_raw.get("poll_interval_seconds"), 5.0),
        request_timeout_seconds=_as_float(queue_raw.get("request_timeout_seconds"), 180.0),
        max_attempts=_as_int(queue_raw.get("max_attempts"), 3),
    )

    providers_raw = data.get("providers") or []
    if not isinstance(providers_raw, list) or not providers_raw:
        raise ConfigError("Config must contain at least one [[providers]] entry")
    providers = tuple(_load_provider(item, i, defaults, base_dir) for i, item in enumerate(providers_raw, start=1))

    if defaults.provider and defaults.provider not in {provider.id for provider in providers}:
        raise ConfigError(f"defaults.provider references unknown provider: {defaults.provider}")

    prompt_templates = _load_prompt_templates(data.get("prompt_templates") or [])
    prompt_template_ids = {template.id for template in prompt_templates if template.enabled}
    if defaults.prompt_template and defaults.prompt_template not in prompt_template_ids:
        raise ConfigError(f"defaults.prompt_template references unknown or disabled template: {defaults.prompt_template}")
    send = _load_send_config(data.get("send"))

    return AppConfig(
        path=config_path,
        base_dir=base_dir,
        queue=queue,
        defaults=defaults,
        providers=providers,
        prompt_templates=prompt_templates,
        send=send,
    )


EXAMPLE_CONFIG = """\
[queue]
db = ".gen-image-queue/queue.sqlite3"
output_dir = "output/imagegen"
# 0 = auto. Auto uses enabled provider/key capacity so extra jobs stay queued.
concurrency = 0
poll_interval_seconds = 5
request_timeout_seconds = 180
max_attempts = 3

[defaults]
# Empty means automatic routing across all enabled providers.
# Set a provider id here only when every default request should be pinned.
provider = ""
size = "1024x1024"
quality = "auto"
output_format = "png"
moderation = "auto"
images_per_request = 1
# Optional default prompt template id from [[prompt_templates]].
prompt_template = ""

[[prompt_templates]]
id = "cinematic"
name = "Cinematic wrapper"
enabled = false
body = "Style: cinematic, high detail.\\n\\n{{prompt}}\\n\\nRequested size: {{size}} ({{ratio}})."

[[prompt_templates]]
id = "nai-safe-negative"
name = "NAI safe negative prompt"
enabled = false
body = "${prompt}"
[prompt_templates.params]
negative_prompt = "blurry, lowres, error, film grain, scan artifacts, worst quality, bad quality, jpeg artifacts, very displeasing, chromatic aberration, multiple views, logo, too many watermarks, tiara, coat, futa, poorly drawn, lowers, blurry, bokeh, worst quality, low quality, out of focus, ugly, error, jpeg artifacts, {{{{censor, bar censor}}}}, mosaic censorship, puffy nipples, extra digits, POV hands, hutanari, worst quality, low quality:1.4), lowres, text, bad anatomy, text, logo, watermark, extra fingers, missing fingers, extra arms, missing arms, extra legs, extra legs, counter, body writing"

[send]
# Optional adapter used by `generate --send`, `once --send`, and `send`.
# Set preset = "hermes" for Hermes Agent's messaging tool, or
# preset = "openclaw" for OpenClaw's `message send --media` CLI route.
# `python-call` imports module.function and passes one dict per file/target.
# Use `command` for a subprocess adapter with {path}, {target}, {message},
# and {caption} placeholders in argv items.
method = "python-call"
preset = ""
module = ""
function = ""
targets = []
message_template = "MEDIA:{path}"
retry_delays = [2, 5, 10]
delay_seconds = 0

# OpenClaw preset example:
# preset = "openclaw"
# targets = ["@mychat"]
# message_template = "Generated {filename}\\nMEDIA:{path}"
# [send.args]
# channel = "telegram"
# force_document = true

[send.hermes]
agent_path = ""
home = ""
module = ""
function = ""

[send.openclaw]
agent_path = ""
module = ""
function = ""
command = []

[[providers]]
id = "mock-local"
type = "mock"
enabled = true
priority = 1
capabilities = ["generate", "edit"]
images_per_request = 1
max_concurrent_requests = 1
codex_cli = false
response_format_b64_json = false
append_size_to_prompt = false
force_responses_stream = false
responses_stream_partial_images = 0

[[providers]]
id = "responses-router"
type = "responses-image"
base_url = "https://anyrouter.top/v1"
model = "gpt-5.3-codex"
enabled = false
priority = 5
capabilities = ["generate", "edit"]
images_per_request = 1
max_concurrent_requests = 1
codex_cli = true
append_size_to_prompt = false
force_responses_stream = true
responses_stream_partial_images = 1

[providers.headers]
User-Agent = "Mozilla/5.0"
Origin = "null"

[[providers.keys]]
id = "responses-key-a"
api_key_env = "RESPONSES_IMAGE_API_KEY"
images_per_request = 1
max_concurrent_requests = 1

[[providers]]
id = "openai-main"
type = "openai-images"
base_url = "https://api.openai.com/v1"
model = "gpt-image-2"
enabled = false
priority = 10
capabilities = ["generate", "edit"]
images_per_request = 1
max_concurrent_requests = 2
response_format_b64_json = true
append_size_to_prompt = false

[[providers.keys]]
id = "openai-key-a"
api_key_env = "OPENAI_API_KEY"
images_per_request = 1
max_concurrent_requests = 1

[[providers.keys]]
id = "openai-key-b"
api_key_env = "OPENAI_API_KEY_2"
images_per_request = 1
max_concurrent_requests = 1

[[providers]]
id = "idlecloud"
type = "idlecloud"
base_url = "https://api.idlecloud.cc/api"
model = "nai-diffusion-4-5-full"
models = ["nai-diffusion-3", "nai-diffusion-4-full", "nai-diffusion-4-5-full"]
enabled = false
priority = 15
capabilities = ["generate", "edit"]
images_per_request = 1
max_concurrent_requests = 1

[providers.params]
negativePrompt = ""
steps = 28
scale = 5
sampler = "k_euler"
noise_schedule = "karras"
ucPreset = 1

[[providers.keys]]
id = "idlecloud-key-a"
api_key_env = "IDLECLOUD_API_KEY"
images_per_request = 1
max_concurrent_requests = 1

[[providers]]
id = "nai-idlecloud"
type = "nai"
base_url = "https://api.idlecloud.cc/api"
model = "nai-diffusion-4-5-full"
models = ["nai-diffusion-3", "nai-diffusion-4-full", "nai-diffusion-4-5-full"]
enabled = false
priority = 16
capabilities = ["generate", "edit"]
images_per_request = 1
max_concurrent_requests = 1

[providers.params]
negative_prompt = ""
steps = 28
scale = 5
sampler = "k_euler"
noise_schedule = "karras"
ucPreset = 1

[[providers.keys]]
id = "nai-key-a"
api_key_env = "IDLECLOUD_API_KEY"
images_per_request = 1
max_concurrent_requests = 1
# Custom HTTP providers can model async submit/poll APIs.
# See references/config.md for all mapping fields.
"""


def write_example_config(path: str | Path, *, force: bool = False) -> Path:
    out = Path(path).expanduser()
    if not out.is_absolute():
        out = Path.cwd() / out
    if out.exists() and not force:
        raise ConfigError(f"Refusing to overwrite existing config: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(textwrap.dedent(EXAMPLE_CONFIG), encoding="utf-8")
    return out


