# API Configuration Examples

These examples show reusable provider patterns. Keep real keys outside Git; prefer `api_key_env` or a local ignored `keys_file`.

## OpenAI-Compatible Images

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
max_concurrent_requests = 2
response_format_b64_json = true
append_size_to_prompt = false

[[providers.keys]]
id = "openai-key-a"
api_key_env = "OPENAI_API_KEY"
images_per_request = 1
max_concurrent_requests = 1
```

## Responses Image Router

Use `responses-image` when the provider exposes `/v1/responses` with an `image_generation` tool.

```toml
[[providers]]
id = "responses-router"
type = "responses-image"
base_url = "https://router.example/v1"
model = "image-model"
enabled = true
priority = 5
capabilities = ["generate", "edit"]
images_per_request = 1
max_concurrent_requests = 1
force_responses_stream = true
responses_stream_partial_images = 1

[providers.headers]
User-Agent = "gen-image-via-api"

[[providers.keys]]
id = "responses-key-a"
api_key_env = "RESPONSES_IMAGE_API_KEY"
images_per_request = 1
max_concurrent_requests = 1
```

## Minimal Browser-Style Responses Router

Use `type = "any"` for routers that reject `action`, `size`, or `tool_choice` fields in the image tool.

```toml
[[providers]]
id = "minimal-router"
type = "any"
base_url = "https://router.example/v1"
model = "image-model"
enabled = true
priority = 5
capabilities = ["generate", "edit"]
force_responses_stream = true
responses_stream_partial_images = 1

[[providers.keys]]
id = "minimal-router-key"
api_key_env = "MINIMAL_ROUTER_API_KEY"
```


## IdleCloud Native Image Job

```toml
[[providers]]
id = "idlecloud"
type = "idlecloud"
base_url = "https://api.idlecloud.cc/api"
model = "nai-diffusion-4-5-full"
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

## NAI-Compatible IdleCloud Endpoint

```toml
[[providers]]
id = "nai-idlecloud"
type = "nai"
base_url = "https://api.idlecloud.cc/api"
model = "nai-diffusion-4-5-full"
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

## Custom HTTP Async Provider

```toml
[[providers]]
id = "custom-async-example"
type = "custom-http"
enabled = true
priority = 20
base_url = "https://api.example.com/v1"
model = "example-image-model"
capabilities = ["generate", "edit"]

[[providers.keys]]
id = "custom-key-a"
api_key_env = "CUSTOM_IMAGE_API_KEY"

[providers.submit]
path = "images/generations"
method = "POST"
content_type = "json"
task_id_path = "data.task_id"

[providers.submit.body]
model = "$model"
prompt = "$prompt"
size = "$params.size"
n = "$n"

[providers.poll]
path = "tasks/{task_id}"
method = "GET"
interval_seconds = 5
status_path = "data.status"
success_values = ["completed"]
failure_values = ["failed", "cancelled"]
error_path = "data.error.message"

[providers.poll.result]
image_url_paths = ["data.images.*.url"]
b64_json_paths = ["data.images.*.b64_json"]
```

## Key Rotation

For multiple keys with round-robin rotation:

```toml
[[providers.keys]]
id = "key-1"
api_key_env = "IMAGE_API_KEY_1"
images_per_request = 1
max_concurrent_requests = 1

[[providers.keys]]
id = "key-2"
api_key_env = "IMAGE_API_KEY_2"
images_per_request = 1
max_concurrent_requests = 1
```

## Local Keys File

Use only for ignored local files. The first line is the provider base URL; later lines are API keys.

```text
https://router.example/v1
key-one
key-two
```

```toml
[[providers]]
id = "router-from-file"
type = "responses-image"
model = "image-model"
keys_file = "keys.txt"
```
