# CLI Reference

Use the CLI from the skill/project root:

```bash
python scripts/gen_image_cli.py --help
```

If installed with `pip install -e .`, use `gen-image` instead of `python scripts/gen_image_cli.py`.

## Setup

```bash
python scripts/gen_image_cli.py doctor
```

`doctor` searches config in this order:

1. `GEN_IMAGE_CONFIG`
2. `./gen-image.toml`
3. the skill directory (`gen-image.toml` next to `SKILL.md`)

If no config exists, `doctor` creates a template in the skill directory and returns `needs_configuration=true`. Fill the template once, then normal commands can run without `--config`.

Older user-config files are not auto-loaded. Point to them explicitly with `--config` or `GEN_IMAGE_CONFIG` if you need to migrate values.

## Normal Commands

Use these two commands for normal workflows:

- `generate`: submit, auto-start/attach worker, wait for this job, print outputs.
- `submit`: submit, auto-start/attach worker, return immediately with a job id.

Prompt-to-image:

```bash
python scripts/gen_image_cli.py generate \
  --prompt "A clean product hero image of a ceramic mug" \
  --count 2 \
  --out-prefix mug-hero \
  --json
```

Non-blocking submit:

```bash
python scripts/gen_image_cli.py submit \
  --prompt "A clean product hero image of a ceramic mug" \
  --count 2 \
  --out-prefix mug-hero \
  --json
```

`submit` returns immediately with a job id. It does not wait for image generation. It still ensures the managed worker is running, so manual `run --watch` startup is unnecessary.

Generate and deliver after success:

```bash
python scripts/gen_image_cli.py generate \
  --prompt "A clean product hero image of a ceramic mug" \
  --out-prefix mug-hero \
  --send \
  --send-target telegram \
  --json
```

Send existing outputs:

```bash
python scripts/gen_image_cli.py send \
  --path output/imagegen/mug-hero.png \
  --target telegram \
  --target weixin \
  --json
```

The `--send` flag and `send` command use the optional `[send]` adapter. See `references/delivery.md`.

## Output Shape

Default command output is bounded and intended for the common path:

- Human output prints only the job id/status and output paths.
- `--json` prints compact machine-readable JSON on one line.
- `submit --json` returns the queued job id, kind/count, provider routing, queue position, and a minimal worker state.
- `generate --json` and `once` return the final job id, status, kind/count, attempts, and output paths.
- When `--send` is used, JSON output includes a `send` report with target/path delivery status.

Use `--verbose` with `submit --json`, `generate --json`, `enqueue-batch`, `submit-batch`, or `once` only when debugging. Verbose output restores the full diagnostic payload: prompt, params, runtime/worker state, capacity reports, recent events, result metadata, and queue summaries.

Image-to-image/edit:

```bash
python scripts/gen_image_cli.py generate \
  --prompt "Change only the background to warm studio lighting" \
  --image input/product.png \
  --out-prefix product-edit \
  --json
```

Batch JSONL:

```jsonl
{"prompt":"A compact spaceship in a misty hangar","count":1,"out_prefix":"ship"}
{"prompt":"Turn the product background blue","images":["input/product.png"],"out_prefix":"product-blue"}
```

```bash
python scripts/gen_image_cli.py submit-batch --input jobs.jsonl
```

`submit-batch` also auto-starts/attaches the managed worker.

## Worker Operations

```bash
python scripts/gen_image_cli.py serve
```

`serve` runs the same managed worker in the foreground for debugging. Normal workflows use `submit` or `generate`, which auto-start the worker. Use `stop-worker` to terminate the managed worker.

A single job with `--count N` is split into parallel API requests up to the selected route capacity. If capacity is 11 and the job asks for 20 images, the worker starts up to 11 image requests immediately and queues the remaining 9 inside the same job until slots free up.

```bash
python scripts/gen_image_cli.py stop-worker
```

Legacy foreground drain:

```bash
python scripts/gen_image_cli.py run
```

`run` prints processed/succeeded/failed counts, worker count, queue summary before/after, and recent output paths. It is retained for debugging and scripts that explicitly want foreground queue draining.

## Inspect And Repair

```bash
python scripts/gen_image_cli.py status
python scripts/gen_image_cli.py list --limit 10
python scripts/gen_image_cli.py status <job_id>
python scripts/gen_image_cli.py retry <job_id>
python scripts/gen_image_cli.py cancel <job_id>
python scripts/gen_image_cli.py providers
python scripts/gen_image_cli.py stop-worker
```

`status` without a job id shows config path, queue DB, output directory, queue summary, worker heartbeat status, provider capacity, and stale-running hints. `status <job_id>` shows attempts, error, output paths, and recent job events.

## Per-Request Parameters

Common image-generation parameters have direct flags:

- `--size 2048x1152` or `--size auto`
- `--aspect-ratio 16:9 --size-tier 2K` to calculate a normalized `size`
- `--output-format png|jpeg|jpg|webp`
- `--background auto|transparent|opaque`
- `--output-compression 0-100`
- `--template <id>` / `--prompt-template <id>` to render a configured prompt template before enqueueing
- `--no-template` to bypass `[defaults].prompt_template`

Provider-specific parameters also have direct flags so common command snippets do not fail argument parsing:

- `--quality auto|low|medium|high`
- `--moderation auto|low`
- `--model <model-id>`
- `--action auto|generate|edit`
- `--stream` / `--no-stream`

Check `providers` or `doctor` before relying on provider-specific flags. For example, `quality` can be ignored or intentionally omitted by some Responses routers, `moderation` is not sent to the Responses image tool, and `action` only applies to Responses image tool providers.

```bash
python scripts/gen_image_cli.py generate \
  --prompt "A high detail architectural render" \
  --aspect-ratio 16:9 \
  --size-tier 2K \
  --output-format png \
  --json
```

Values passed through `--param` are parsed as JSON when possible, so `--param seed=123` becomes a number and `--param response_format=b64_json` remains a string. Direct flags win over earlier `--param` values for the same key.

Run `providers` or `doctor` to see each configured provider's parameter support. The report distinguishes common direct flags, provider-specific direct flags, `--param`-only extras, and values intentionally ignored by that provider type.

Prompt templates are rendered before jobs enter the queue. Supported placeholders are `{{prompt}}`, `{{size}}`, `{{ratio}}`, `{{quality}}`, `{{output_format}}`, and `{{n}}`. Unknown placeholders are preserved. If a template omits `{{prompt}}`, the raw prompt is appended after a blank line.

## Queue Capacity

`submit` can add any number of jobs. The managed worker consumes only as much work at a time as capacity allows:

- `queue.concurrency = 0` means auto.
- Auto capacity is derived from enabled providers and enabled keys.
- Each key has `max_concurrent_requests` (default `1`).
- A provider can cap aggregate concurrency with `max_concurrent_requests`.
- `--provider` and `[defaults].provider` pin routing to one provider; omit them or set `provider = ""` to use all enabled providers by priority.

Extra jobs remain queued in SQLite and extra images inside a large job wait for a free provider/key route. Use `status` / `list` to inspect queue state.
