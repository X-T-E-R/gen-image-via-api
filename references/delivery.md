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
