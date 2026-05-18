# Output Delivery

The CLI can deliver generated files after a successful run. Delivery is opt-in:

```bash
python scripts/gen_image_cli.py generate \
  --prompt "A product hero image" \
  --json \
  --send \
  --send-target telegram
```

You can also send existing files:

```bash
python scripts/gen_image_cli.py send \
  --path output/imagegen/example.png \
  --target telegram \
  --target weixin \
  --json
```

## Configuration

Delivery is configured in the optional `[send]` table.

## Presets

### Hermes Agent

Hermes Agent exposes a compatible messaging tool. When `HERMES_HOME` or `HERMES_AGENT_PATH` is set, the preset can import it automatically:

```toml
[send]
preset = "hermes"
targets = ["telegram", "weixin"]
message_template = "MEDIA:{path}"
retry_delays = [2, 5, 10]
delay_seconds = 5
```

Then run:

```bash
python scripts/gen_image_cli.py generate \
  --prompt "A product hero image" \
  --send \
  --send-target weixin \
  --json
```

The Hermes preset sends the same payload shape as a normal Hermes messaging tool call:

```json
{
  "action": "send",
  "target": "weixin",
  "message": "MEDIA:/absolute/path/to/image.png",
  "path": "/absolute/path/to/image.png"
}
```

### OpenClaw

OpenClaw's durable outbound interface is its message command:
`openclaw message send --channel <channel> --target <target> --media <path>`.
Its MCP channel bridge also exposes `messages_send`, but that MCP tool currently
sends text back through an existing conversation route and is not a portable
file/media upload API.

Use the `openclaw` preset when you want this CLI route, or when you have a local
custom callable/command adapter.

```toml
[send]
preset = "openclaw"
targets = ["@mychat"]
message_template = "Generated {filename}\nMEDIA:{path}"

[send.args]
channel = "telegram"
# Optional. Defaults to "openclaw"; use an array when a wrapper needs arguments.
# openclaw_cli = ["python", "C:/path/to/openclaw-wrapper.py"]
# force_document = true
```

Then run:

```bash
python scripts/gen_image_cli.py send \
  --path output/imagegen/example.png \
  --target @mychat \
  --json
```

The preset calls:

```bash
openclaw message send --channel telegram --target @mychat --media output/imagegen/example.png --message "Generated example.png" --json
```

The media token is removed before building the CLI `--message` caption. If
`message_template` is the default `MEDIA:{path}`, the OpenClaw CLI receives
`--media <path>` with no message body.

OpenClaw preset environment overrides:

```bash
set OPENCLAW_SEND_CLI=openclaw
set OPENCLAW_SEND_CHANNEL=telegram
set OPENCLAW_SEND_ACCOUNT=default
set OPENCLAW_SEND_FORCE_DOCUMENT=true
```

For installations that expose their own Python adapter, keep using the explicit
callable route:

```bash
set OPENCLAW_AGENT_PATH=C:\path\to\openclaw-or-skill-root
set OPENCLAW_SEND_MODULE=my_sender
set OPENCLAW_SEND_FUNCTION=send
```

Or route through a fully custom command:

```bash
set OPENCLAW_SEND_COMMAND=openclaw message send --channel telegram --target {target} --media {path} --message "{caption}" --json
```

`preset = "openclaw"` does not fall back to Hermes Agent. Use
`preset = "hermes"` explicitly when Hermes Agent's `send_message_tool` is the
intended delivery surface.

### Python Callable Adapter

Use this when another local package already exposes a send function. The function is called once per file/target and receives a dictionary.

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

The default payload shape is:

```json
{
  "action": "send",
  "target": "telegram",
  "message": "MEDIA:/absolute/path/to/image.png",
  "path": "/absolute/path/to/image.png"
}
```

If the callable uses different key names, configure them:

```toml
[send]
method = "python-call"
module = "my_sender"
function = "send_message"
target_arg = "platform"
message_arg = "text"
path_arg = "file"
action_arg = ""
```

### Command Adapter

Use this when delivery is done by an executable or script.

```toml
[send]
method = "command"
command = ["python", "send_file.py", "--target", "{target}", "--message", "{message}", "--file", "{path}"]
targets = ["telegram"]
message_template = "MEDIA:{path}"
timeout_seconds = 60
```

Placeholders available in `message_template` and `command` entries:

- `{path}`: full output file path
- `{filename}`: output filename
- `{target}`: selected send target
- `{caption}` / `{message_without_media}`: `message_template` with `MEDIA:{path}` removed

## Return Handling

Python adapters may return a dict, JSON string, plain string, or `None`.

- `{"ok": false}`, `{"success": false}`, any non-zero `returncode`, or a non-empty `error` field is treated as a failed send.
- Plain strings and `None` are treated as success unless an exception is raised.
- Failed sends are retried with `[send].retry_delays`.

## Notes

- Real credentials should stay in the adapter's own environment or config, not in this repository.
- Use `delay_seconds` when sending multiple files to rate-limited chat platforms.
- `generate --send` and `once --send` only attempt delivery after the image job succeeds.
