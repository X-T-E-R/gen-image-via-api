---
name: gen-image-via-api
description: Queue image generation/editing jobs through API providers from a local TOML configuration. Use when Codex needs prompt-to-image, image-to-image/editing, batch/async generation, provider/key rotation, or resumable image jobs through OpenAI-compatible or custom HTTP image APIs, especially when the work should be done by CLI rather than the built-in image_gen tool. For simple one-off built-in image_gen requests, vector/SVG editing, or provider admin work, use the dedicated tool/workflow for that task instead.
---

# Gen Image Via API

Use this skill when image generation should run through a configurable API queue rather than a built-in image tool. The bundled CLI is the execution interface.

## Boundary

- Use the CLI for prompt-to-image, image-to-image/edit jobs, batch generation, async queue processing, provider/key rotation, and custom HTTP image APIs.
- Keep secrets out of chat. Prefer `api_key_env` in TOML.
- Normal operation uses only `submit` or `generate`. These commands auto-start or attach to the managed background worker; manual `run --watch` startup is unnecessary.
- Managed worker is intended for one active process; it uses async concurrency inside that process and is guarded by a heartbeat lock.
- Real API calls need configured provider keys. Offline smoke tests can use the `mock` provider.

## Quick Start

From this skill folder:

```bash
python scripts/gen_image_cli.py doctor
```

If no config exists, `doctor` creates a first-use template in the skill directory (`gen-image.toml` next to `SKILL.md`) and returns `needs_configuration=true`. Fill it once; normal commands then discover it automatically through `GEN_IMAGE_CONFIG`, `./gen-image.toml`, or the skill config path.

Generate and wait for outputs:

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

`generate` submits the job, ensures the worker is running, waits for completion, and prints final output paths. `submit` submits the job, ensures the worker is running, and returns immediately with a queued job id. Use `--json` for machine-readable output.
Default `--json` output is intentionally compact for agent and script consumption: job id, status, count, and output paths. Add `--verbose` when you need full diagnostics such as prompt, params, worker/runtime status, events, result metadata, and capacity details.

Edit or image-to-image from existing images:

```bash
python scripts/gen_image_cli.py submit \
  --prompt "Change only the background to warm studio lighting; keep the product unchanged" \
  --image input/product.png \
  --out-prefix product-edit \
  --json
```

## Workflow

1. Create or inspect TOML config:
   - Start from `examples/config.example.toml` or `init-config`.
   - Read `references/config.md` only when provider/key/custom mapping details are needed.
2. Choose mode:
   - No `--image`: prompt-to-image generation.
   - One or more `--image`: image edit / image-to-image.
3. Add jobs:
   - Use `generate --json` when final image paths are needed in the same command.
   - Use `submit --json` / `submit-batch` when fast return is preferred and results can be checked later.
   - Both commands auto-start or attach to the managed worker. Manual `serve`, `worker`, `run`, or `run --watch` startup is unnecessary.
4. Inspect outputs:
   - Use `status` for config path, queue summary, capacity, and stale-running hints.
   - Use `status <job_id>` for output paths, attempts, errors, and recent events.
   - Use `retry <job_id>` only for failed jobs.
5. Worker operations are debug/maintenance only:
   - `serve` / `worker`: foreground managed worker.
   - `stop-worker`: stop the managed worker.
   - `run`: legacy foreground queue drain.

## Provider And Key Rules

- Providers declare `capabilities = ["generate", "edit"]`.
- Use `responses-image` when a router works through `/v1/responses` with an `image_generation` tool.
- Use `any` for browser-style anyrouter-compatible `/v1/responses` image generation; it uses the minimal tool shape from the bundled HTML sample.
- Use `keys_file` when the user has a local secret file whose first line is the site and later lines are API keys.
- Provider selection:
  1. job `--provider`;
  2. `[defaults].provider`;
  3. enabled providers sorted by `priority ASC`, then `id`.
- Keys are selected round-robin per provider and persisted in the queue DB.
- `images_per_request` can be set on a key, provider, or defaults. Missing value resolves to `1`.
- `max_concurrent_requests` can be set on a key and capped on a provider. Missing key value resolves to `1`.
- `append_size_to_prompt` can be set on a provider when a router ignores structured `size`; it appends a size/ratio instruction to the submitted prompt.
- `codex_cli`, `response_format_b64_json`, `force_responses_stream`, and `responses_stream_partial_images` are provider compatibility switches for common OpenAI-compatible router quirks.
- `queue.concurrency = 0` means auto; auto uses enabled provider/key request capacity so excess work remains queued or waits for a free route.
- A single job with `--count N` is also fanned out internally up to the selected route capacity. Example: 6 ready `any` keys at 1 request each plus one `sukaka` key at 5 requests gives `route_capacity = 11`; `--count 20` starts up to 11 requests at once, then continues as slots finish.
- If `[defaults].provider` or `--provider` pins one provider, route capacity is limited to that provider. Leave `[defaults].provider = ""` / omit it to route across all enabled providers.
- Each API call currently records one requested output by default. Increase `images_per_request` only for providers that really return multiple images in one request.

## Important Commands

```bash
python scripts/gen_image_cli.py --help
python scripts/gen_image_cli.py generate --prompt "..." --json
python scripts/gen_image_cli.py submit --prompt "..." --json
python scripts/gen_image_cli.py providers
python scripts/gen_image_cli.py status
python scripts/gen_image_cli.py list --limit 20
python scripts/gen_image_cli.py status <job_id>
python scripts/gen_image_cli.py retry <job_id>
python scripts/gen_image_cli.py cancel <job_id>
python scripts/gen_image_cli.py stop-worker
```

Pass common image parameters without changing config:

```bash
python scripts/gen_image_cli.py generate \
  --prompt "A polished landing-page hero image" \
  --aspect-ratio 16:9 \
  --size-tier 2K \
  --output-format png \
  --json
```

Use full diagnostic JSON only when debugging:

```bash
python scripts/gen_image_cli.py generate \
  --prompt "A polished landing-page hero image" \
  --json \
  --verbose
```

Common direct flags: `--size`, or `--aspect-ratio` plus `--size-tier`, `--output-format`, `--background`, and `--output-compression`. The CLI also accepts provider-specific direct flags such as `--quality`, `--moderation`, `--model`, `--action`, `--stream`, and `--no-stream` so common recipes do not fail at argument parsing. Use `--template <id>` / `--prompt-template <id>` for configured prompt templates and `--no-template` to bypass `[defaults].prompt_template`. Run `providers` or `doctor` before relying on provider-specific flags; the report shows which params are common, provider-specific, `--param`-only, or ignored. Use repeated `--param key=value` for extras such as `response_format=b64_json`, `seed=123`, `tool_choice=required`, or `reasoning={"effort":"low"}`.

## References

- `references/cli.md`: command recipes and JSONL batch shape.
- `references/config.md`: TOML schema, custom HTTP submit/poll/result mappings, key rotation.
- `examples/config.example.toml`: safe starter config with `mock`, disabled OpenAI-compatible provider, and custom async HTTP example.

Read CLI source only when debugging or extending the tool itself.
