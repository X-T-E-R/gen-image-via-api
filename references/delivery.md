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

### OpenClaw-Compatible

OpenClaw installations do not all expose the same messaging callable. The preset supports two portable routes:

```toml
[send]
preset = "openclaw"
targets = ["telegram"]
message_template = "MEDIA:{path}"
```

Route through a Python callable:

```bash
set OPENCLAW_AGENT_PATH=C:\path\to\openclaw-or-skill-root
set OPENCLAW_SEND_MODULE=my_sender
set OPENCLAW_SEND_FUNCTION=send
```

Or route through a command:

```bash
set OPENCLAW_SEND_COMMAND=python send_file.py --target {target} --message {message} --file {path}
```

If neither OpenClaw route is configured, the preset automatically tries the Hermes-compatible messaging tool when Hermes is installed.

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

## Return Handling

Python adapters may return a dict, JSON string, plain string, or `None`.

- `{"ok": false}`, `{"success": false}`, any non-zero `returncode`, or a non-empty `error` field is treated as a failed send.
- Plain strings and `None` are treated as success unless an exception is raised.
- Failed sends are retried with `[send].retry_delays`.

## Notes

- Real credentials should stay in the adapter's own environment or config, not in this repository.
- Use `delay_seconds` when sending multiple files to rate-limited chat platforms.
- `generate --send` and `once --send` only attempt delivery after the image job succeeds.
