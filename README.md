# Gen Image Via API

Queue-backed image generation and editing through configurable API providers.

This project is packaged as a Codex skill and also exposes a Python CLI. It is useful when image jobs should run through local TOML configuration, provider/key rotation, and a resumable SQLite queue instead of a one-off built-in image tool.

## Features

- Prompt-to-image and image edit/image-to-image jobs.
- Async SQLite queue with managed background worker autostart.
- Multiple provider types:
  - `mock` for offline validation.
  - `openai-images` for OpenAI-compatible `/images/generations` and `/images/edits`.
  - `responses-image` for `/responses` image-generation-tool routers.
  - `any` for minimal browser-style Responses routers.
  - `custom-http` for configurable sync or async HTTP APIs.
- Provider/key round-robin and per-key concurrency limits.
- Prompt templates with `{{prompt}}`, `{{size}}`, `{{ratio}}`, `{{quality}}`, `{{output_format}}`, and `{{n}}`.
- Provider compatibility switches such as `append_size_to_prompt`, `codex_cli`, `response_format_b64_json`, `force_responses_stream`, and `responses_stream_partial_images`.

## Quick Start

```bash
python scripts/gen_image_cli.py doctor
```

If no config exists, `doctor` creates `gen-image.toml` in the skill directory next to `SKILL.md`. Fill provider keys/settings once; the CLI discovers config in this order:

1. `GEN_IMAGE_CONFIG`
2. `./gen-image.toml`
3. skill directory `gen-image.toml`
4. legacy user config path

Generate and wait for output:

```bash
python scripts/gen_image_cli.py generate \
  --prompt "A clean product hero image of a ceramic mug" \
  --out-prefix mug-hero \
  --json
```

Submit and return immediately:

```bash
python scripts/gen_image_cli.py submit \
  --prompt "A clean product hero image of a ceramic mug" \
  --out-prefix mug-hero \
  --json
```

Generate and send outputs through a configured delivery adapter:

```bash
python scripts/gen_image_cli.py generate \
  --prompt "A clean product hero image of a ceramic mug" \
  --out-prefix mug-hero \
  --send \
  --send-target telegram \
  --json
```

Send existing files:

```bash
python scripts/gen_image_cli.py send \
  --path output/imagegen/mug-hero.png \
  --target telegram \
  --target weixin \
  --json
```

For Hermes Agent delivery, configure:

```toml
[send]
preset = "hermes"
targets = ["telegram", "weixin"]
message_template = "MEDIA:{path}"
```

Edit from an input image:

```bash
python scripts/gen_image_cli.py generate \
  --prompt "Change only the background to warm studio lighting" \
  --image input/product.png \
  --out-prefix product-edit \
  --json
```

Use ratio-based size calculation:

```bash
python scripts/gen_image_cli.py generate \
  --prompt "A polished landing-page hero image" \
  --aspect-ratio 16:9 \
  --size-tier 2K \
  --output-format png \
  --json
```

## Configuration

Start from [`examples/config.example.toml`](examples/config.example.toml) or run:

```bash
python scripts/gen_image_cli.py init-config --out gen-image.toml
```

Keep real secrets out of Git. Prefer `api_key_env` in TOML, or put local-only config in `gen-image.toml` which is ignored by this repository.

For full schema details, see:

- [`references/config.md`](references/config.md)
- [`references/cli.md`](references/cli.md)
- [`references/delivery.md`](references/delivery.md)
- [`references/api-config-examples.md`](references/api-config-examples.md)
- [`references/response-parsing.md`](references/response-parsing.md)

## Development

```bash
python -m pytest
```

The test suite uses the offline `mock` provider and does not require API keys.
