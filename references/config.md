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
body = "Style: cinematic.\\n\\n${prompt}\\n\\nRequested size: ${size} (${ratio})."

[[prompt_templates]]
id = "nai-negative"
name = "NAI negative prompt"
enabled = true
body = "${prompt}"
[prompt_templates.params]
negative_prompt = "blurry, lowres, error, film grain, scan artifacts, worst quality, bad quality, jpeg artifacts, very displeasing, chromatic aberration, multiple views, logo, too many watermarks, tiara, coat, futa, poorly drawn, lowers, blurry, bokeh, worst quality, low quality, out of focus, ugly, error, jpeg artifacts, {{{{censor, bar censor}}}}, mosaic censorship, puffy nipples, extra digits, POV hands, hutanari, worst quality, low quality:1.4), lowres, text, bad anatomy, text, logo, watermark, extra fingers, missing fingers, extra arms, missing arms, extra legs, extra legs, counter, body writing"
```

Templates are applied before jobs are written to the queue. CLI `--template <id>` overrides the default for one job, and `--no-template` bypasses the default. Batch JSONL items can use `template` or `prompt_template`.

Prompt `body` and `[prompt_templates.params]` string values share the same placeholder renderer. Prefer `${name}` for new templates because NovelAI uses plain braces in positive prompts for weighting. Legacy `{{name}}` still works when it is exactly two braces, while NAI weight braces such as `{{{tag}}}` are preserved.

Supported built-in placeholders:

- `${prompt}` / `{{prompt}}`
- `${size}` / `{{size}}`
- `${ratio}` / `{{ratio}}`
- `${quality}` / `{{quality}}`
- `${output_format}` / `{{output_format}}`
- `${n}` / `{{n}}`

Any job parameter can also be referenced by name, such as `${negative_prompt}`, `${model}`, `${steps}`, `${scale}`, or `${sampler}`.

Unknown placeholders are left unchanged. If neither `${prompt}` nor `{{prompt}}` is present, the raw prompt is appended after a blank line.

## Send Adapter

The optional `[send]` table powers `generate --send`, `once --send`, and the standalone `send` command.

Preset form:

```toml
[send]
preset = "hermes"
targets = ["telegram", "weixin"]
message_template = "MEDIA:{path}"
```

The Hermes preset imports the local Hermes Agent messaging tool when `HERMES_HOME` or `HERMES_AGENT_PATH` points to an installed Hermes environment.

The OpenClaw preset uses, in order:

1. `[send.openclaw].module` / `[send.openclaw].function`, or `OPENCLAW_SEND_MODULE` / `OPENCLAW_SEND_FUNCTION`, for custom Python adapters.
2. `[send.openclaw].command`, or `OPENCLAW_SEND_COMMAND`, for a custom command.
3. OpenClaw's native CLI route: `openclaw message send --target <target> --media <path> --json`.

When the native route is used, set the OpenClaw channel with `[send.args].channel`
or `OPENCLAW_SEND_CHANNEL` unless your OpenClaw config has exactly one usable
message channel. `targets` should be OpenClaw message targets such as a Telegram
chat id/username, Slack channel id, or Teams conversation id.

```toml
[send]
preset = "openclaw"
targets = ["@mychat"]
message_template = "Generated {filename}\nMEDIA:{path}"

[send.args]
channel = "telegram"
# openclaw_cli = "openclaw"
# force_document = true
```

OpenClaw's MCP `messages_send` tool is text-only for an existing conversation
route, so it is not used as the media delivery preset. Use `preset = "hermes"`
explicitly if Hermes Agent is the intended destination.

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

Command entries also support `{caption}` / `{message_without_media}`, which is
the rendered `message_template` after removing `MEDIA:{path}`.

See `references/delivery.md` for examples and return handling.

## Provider Fields

```toml
[[providers]]
id = "openai-main"
type = "openai-images"
base_url = "https://api.openai.com/v1"
model = "gpt-image-2"
models = ["gpt-image-2"]
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
- `nai`: NovelAI-compatible image provider. It posts to `{base_url}/ai/generate-image` and extracts images from the returned zip payload.
- `idlecloud`: IdleCloud image job provider. It posts to `{base_url}/generate_image`, polls `{base_url}/get_result/{job_id}`, and reads `image_base64` or `image_url`.

Provider compatibility switches:

- `codex_cli`: prefixes prompts with a no-rewrite guard and omits `quality` for OpenAI Images requests. Responses providers also treat it as a codex-cli-style router for quality omission.
- `response_format_b64_json`: for `openai-images`, adds `response_format = "b64_json"` unless a job already sets `response_format`.
- `append_size_to_prompt`: appends `Output size instruction: use size ... and aspect ratio ...` to the submitted prompt. This helps routers that ignore structured `size`.
- `force_responses_stream`: for `responses-image` / `any`, forces `stream = true` even when a job passes `--no-stream`.
- `responses_stream_partial_images`: for `responses-image` / `any`, adds `partial_images` to the image generation tool. Values are clamped to `0..3`.
- `models`: optional UI-facing model choices for this provider. The CLI still accepts `--model` or `--param model=...`; WebUI uses this list for the global model selector. NAI and IdleCloud providers also include `nai-diffusion-3`, `nai-diffusion-4-full`, and `nai-diffusion-4-5-full` as documented IdleCloud image models.

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


## NAI-Compatible Provider

Use `type = "nai"` for NovelAI-compatible image endpoints such as IdleCloud's `/api/ai/generate-image` adapter.

```toml
[[providers]]
id = "nai-idlecloud"
type = "nai"
base_url = "https://api.idlecloud.cc/api"
model = "nai-diffusion-4-5-full"
models = ["nai-diffusion-3", "nai-diffusion-4-full", "nai-diffusion-4-5-full"]
enabled = true
priority = 16
capabilities = ["generate", "edit"]
images_per_request = 1
max_concurrent_requests = 1

[providers.params]
negative_prompt = "lowres, bad anatomy"
steps = 28
scale = 5
sampler = "k_euler"
noise_schedule = "karras"
ucPreset = 1

[[providers.keys]]
id = "nai-key"
api_key_env = "IDLECLOUD_API_KEY"
```

For this provider:

- `--prompt` maps to the NAI `prompt` field.
- `--size` / `--aspect-ratio` maps to `parameters.width` and `parameters.height`.
- `--image` maps the first input image to `parameters.image` for image-to-image/edit jobs.
- `--mask` maps to `parameters.mask` for inpaint-style jobs.
- Use `--param` or `[providers.params]` for NAI-specific knobs such as `negative_prompt`, `seed`, `steps`, `scale`, `sampler`, `noise_schedule`, `ucPreset`, `strength`, and `noise`.
- The response is expected to be a zip or image payload. Zip files are unpacked and image files inside the archive are normalized into job results.

## IdleCloud Provider

Use `type = "idlecloud"` for IdleCloud's native async image job endpoint.

```toml
[[providers]]
id = "idlecloud"
type = "idlecloud"
base_url = "https://api.idlecloud.cc/api"
model = "nai-diffusion-4-5-full"
models = ["nai-diffusion-3", "nai-diffusion-4-full", "nai-diffusion-4-5-full"]
enabled = true
priority = 15
capabilities = ["generate", "edit"]
images_per_request = 1
max_concurrent_requests = 1

[providers.params]
negativePrompt = "lowres, bad anatomy"
steps = 28
scale = 5
sampler = "k_euler"
noise_schedule = "karras"
ucPreset = 1

[[providers.keys]]
id = "idlecloud-key"
api_key_env = "IDLECLOUD_API_KEY"
```

For this provider:

- `--prompt` maps to `positivePrompt`.
- `negativePrompt` can be set with `[providers.params]` or `--param negativePrompt=...`; `negative_prompt` is accepted as an alias.
- `--size` / `--aspect-ratio` maps to `width` and `height`.
- `--image` maps the first input image to `image` as base64 and enables image-to-image fields.
- `--mask` maps to `mask` and enables inpaint fields.
- Use `--param` or `[providers.params]` for provider-specific features such as reference image arrays, V4 character captions, `seed`, `steps`, `scale`, `sampler`, `ucPreset`, `strength`, and `noise`.
- The API documents a 20 second request interval and one concurrent task per user, so keep `max_concurrent_requests = 1` unless your account/provider explicitly allows more.

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
