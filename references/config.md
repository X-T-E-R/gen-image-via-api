# TOML Configuration

The CLI reads one TOML file. Default name: `gen-image.toml`.

Discovery order:

1. `GEN_IMAGE_CONFIG`
2. `./gen-image.toml`
3. the skill directory (`gen-image.toml` next to `SKILL.md`)

New first-use templates are created in the skill directory so the skill remains self-contained across platforms and Codex installations.

Older user-config files are not auto-loaded. Point to them explicitly with `--config` or `GEN_IMAGE_CONFIG` if you need to migrate values.

## Queue

```toml
[queue]
db = ".gen-image-queue/queue.sqlite3"
output_dir = "output/imagegen"
# 0 = auto. Auto derives worker count from provider/key capacity.
concurrency = 0
poll_interval_seconds = 5
request_timeout_seconds = 180
max_attempts = 3
```

- `db`: persistent SQLite queue path.
- `output_dir`: default image output directory.
- `concurrency`: async worker concurrency inside one process. Use `0` for auto.
- `max_attempts`: job-level retries. Running jobs are recovered to `queued` on worker start.

## Defaults

```toml
[defaults]
# Empty means automatic routing across all enabled providers.
provider = ""
size = "1024x1024"
quality = "auto"
output_format = "png"
moderation = "auto"
images_per_request = 1
prompt_template = ""
```

- `provider`: optional default provider id. Omit it or set `""` to route by provider priority across all enabled providers. Set it only when every default request should be pinned to one provider.
- `images_per_request`: default number of images requested in one API call. Individual keys can override it.
- `prompt_template`: optional default prompt template id from `[[prompt_templates]]`.

## Prompt Templates

```toml
[[prompt_templates]]
id = "cinematic"
name = "Cinematic wrapper"
enabled = true
body = "Style: cinematic.\\n\\n{{prompt}}\\n\\nRequested size: {{size}} ({{ratio}})."
```

Templates are applied before jobs are written to the queue. CLI `--template <id>` overrides the default for one job, and `--no-template` bypasses the default. Batch JSONL items can use `template` or `prompt_template`.

Supported placeholders:

- `{{prompt}}`
- `{{size}}`
- `{{ratio}}`
- `{{quality}}`
- `{{output_format}}`
- `{{n}}`

Unknown placeholders are left unchanged. If `{{prompt}}` is absent, the raw prompt is appended after a blank line.

## Send Adapter

The optional `[send]` table powers `generate --send`, `once --send`, and the standalone `send` command.

Preset form:

```toml
[send]
preset = "hermes"   # or "openclaw"
targets = ["telegram", "weixin"]
message_template = "MEDIA:{path}"
```

The Hermes preset imports the local Hermes Agent messaging tool when `HERMES_HOME` or `HERMES_AGENT_PATH` points to an installed Hermes environment. The OpenClaw preset uses `OPENCLAW_SEND_MODULE` / `OPENCLAW_SEND_FUNCTION` or `OPENCLAW_SEND_COMMAND`, and falls back to the Hermes-compatible route when available.

Explicit Python callable form:

```toml
[send]
method = "python-call"
module = "my_sender"
function = "send_message"
targets = ["telegram", "weixin"]
message_template = "MEDIA:{path}"
retry_delays = [2, 5, 10]
delay_seconds = 5
```

- `method`: `python-call` or `command`.
- `targets`: default delivery targets when CLI `--send-target` / `--target` is omitted.
- `message_template`: rendered once per output path. Supports `{path}`, `{filename}`, and `{target}`.
- `retry_delays`: seconds to wait before retry attempts after a failed send.
- `delay_seconds`: pause between file/target deliveries. Useful for rate-limited chat platforms.

For `python-call`, `module.function` receives a dict with `action`, `target`, `message`, and `path` by default. The key names can be changed with `action_arg`, `target_arg`, `message_arg`, and `path_arg`.

For `command`, configure argv entries:

```toml
[send]
method = "command"
command = ["python", "send_file.py", "--target", "{target}", "--message", "{message}", "--file", "{path}"]
targets = ["telegram"]
```

See `references/delivery.md` for examples and return handling.

## Provider Fields

```toml
[[providers]]
id = "openai-main"
type = "openai-images"
base_url = "https://api.openai.com/v1"
model = "gpt-image-2"
enabled = true
priority = 10
capabilities = ["generate", "edit"]
images_per_request = 1
codex_cli = false
response_format_b64_json = false
append_size_to_prompt = false
force_responses_stream = false
responses_stream_partial_images = 0
```

Supported first-version provider types:

- `mock`: offline 1x1 PNG output for validation.
- `openai-images`: OpenAI-compatible `/images/generations` and `/images/edits`.
- `responses-image`: OpenAI-compatible `/responses` with `image_generation` tool; supports stream/SSE-style routers like the provided HTML example.
- `any`: browser-style `/responses` image router compatible with the provided any HTML sample. It behaves like `responses-image`, but omits `action`, `size`, and `tool_choice` by default.
- `custom-http`: configurable submit/poll/result mapping for sync or async HTTP providers.

Provider compatibility switches:

- `codex_cli`: prefixes prompts with a no-rewrite guard and omits `quality` for OpenAI Images requests. Responses providers also treat it as a codex-cli-style router for quality omission.
- `response_format_b64_json`: for `openai-images`, adds `response_format = "b64_json"` unless a job already sets `response_format`.
- `append_size_to_prompt`: appends `Output size instruction: use size ... and aspect ratio ...` to the submitted prompt. This helps routers that ignore structured `size`.
- `force_responses_stream`: for `responses-image` / `any`, forces `stream = true` even when a job passes `--no-stream`.
- `responses_stream_partial_images`: for `responses-image` / `any`, adds `partial_images` to the image generation tool. Values are clamped to `0..3`.

Provider selection:

1. If a job has `--provider`, use only that provider.
2. Else if `[defaults].provider` is set, use that provider.
3. Else use enabled providers sorted by `priority ASC`, then `id`.

## Capacity, Keys, And Round-Robin

```toml
keys_file = "C:/path/to/provider-keys.txt"
keys_file_images_per_request = 1
keys_file_max_concurrent_requests = 1

[[providers.keys]]
id = "openai-key-a"
api_key_env = "OPENAI_API_KEY"
images_per_request = 1
max_concurrent_requests = 1

[[providers.keys]]
id = "openai-key-b"
api_key_env = "OPENAI_API_KEY_2"
images_per_request = 2
max_concurrent_requests = 1
```

- `api_key_env` is preferred.
- `api_key` is supported but should be avoided for secrets.
- `keys_file` is supported for local secret files where line 1 is the site/base URL and later lines are keys. If `base_url` is omitted, line 1 becomes the provider base URL.
- Key state is persisted in SQLite and selected round-robin per provider.
- `images_per_request` defaults to provider/default value, ultimately `1`.
- `max_concurrent_requests` defaults to `1` per key. It means how many simultaneous API requests that key should handle.
- Provider-level `max_concurrent_requests` caps the sum of its key capacities.

If a job requests 20 images and selected routes have 11 total concurrent request slots, the worker starts up to 11 image requests immediately and continues the rest as slots finish. If selected keys allow multiple images per request, the worker may still split the job into multiple API requests until it records the requested count.

If many jobs are submitted and the count is greater than configured capacity, extra jobs remain in the SQLite queue. `submit`, `submit-batch`, and `generate` auto-start or attach to the managed worker; `serve` / `worker` are only needed for foreground debugging or operations.

## OpenAI-Compatible Images Provider

`openai-images` sends:

- generate: `POST {base_url}/images/generations` JSON body.
- edit: `POST {base_url}/images/edits` multipart body with repeated `image[]` files and optional `mask`.

Common params can be passed by CLI `--param` or provider `[providers.params]`:

```toml
[providers.params]
size = "1024x1024"
quality = "high"
output_format = "png"
moderation = "auto"
```

## Responses Image Provider

Use `responses-image` for routers that generate images through `/v1/responses` with an `image_generation` tool.

```toml
[[providers]]
id = "any"
type = "responses-image"
keys_file = "C:/Programs/gen-image-via-api/.tmp/any.txt"
model = "gpt-5.3-codex"
enabled = true
priority = 1
capabilities = ["generate", "edit"]
images_per_request = 1
keys_file_max_concurrent_requests = 1

[providers.headers]
User-Agent = "Mozilla/5.0"
Origin = "null"
```

For this provider:

- generate jobs send system/user text input plus `tools = [{type = "image_generation"}]`;
- edit jobs send input images as data URLs plus an `input_text` instruction;
- stream/SSE and plain JSON response bodies are both scanned for image base64/URLs.
- Some browser-style routers only support a minimal `image_generation` tool shape. Set
  `omit_action = true`, `omit_size = true`, and/or `omit_tool_choice = true` in
  `[providers.params]`, or pass them with repeated `--param`, when a router rejects or
  ignores those fields.
- For the any browser sample, prefer `type = "any"` instead of repeating those params.

## Custom HTTP Provider

Use `custom-http` when the provider has a non-OpenAI JSON shape or an async task API.

Synchronous example:

```toml
[[providers]]
id = "custom-sync"
type = "custom-http"
base_url = "https://api.example.com/v1"
model = "image-model"
capabilities = ["generate"]

[[providers.keys]]
id = "custom-key"
api_key_env = "CUSTOM_IMAGE_API_KEY"

[providers.submit]
path = "images/generations"
method = "POST"
content_type = "json"

[providers.submit.body]
model = "$model"
prompt = "$prompt"
n = "$n"
size = "$params.size"

[providers.submit.result]
image_url_paths = ["data.*.url"]
b64_json_paths = ["data.*.b64_json"]
```

Async example:

```toml
[providers.submit]
path = "images/generations"
method = "POST"
content_type = "json"
task_id_path = "data.task_id"

[providers.poll]
path = "tasks/{task_id}"
method = "GET"
interval_seconds = 5
status_path = "data.status"
success_values = ["completed"]
failure_values = ["failed", "cancelled"]
error_path = "data.error.message"

[providers.poll.result]
image_url_paths = ["data.result.images.*.url"]
b64_json_paths = ["data.result.images.*.b64_json"]
```

Template variables:

- `$prompt`
- `$model`
- `$n`
- `$params.size`, `$params.quality`, `$params.output_format`, etc.
- `$input_images.paths`
- `$input_images.count`
- `$mask.path`
- `$job.id`
- `$key.id`

For multipart providers, set `content_type = "multipart"` and add:

```toml
[[providers.submit.files]]
field = "image[]"
source = "inputImages"

[[providers.submit.files]]
field = "mask"
source = "mask"
```
