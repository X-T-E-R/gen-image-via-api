# Response Parsing

Image providers return generated files in different shapes. The provider layer normalizes URLs, data URLs, and base64 payloads into local image files.

## OpenAI-Compatible Images

The `openai-images` provider reads `data[].b64_json` or `data[].url`:

```json
{
  "data": [
    {
      "b64_json": "iVBORw0KGgo..."
    }
  ]
}
```

## Responses Image: Non-Streaming

Responses-style image routers often place the final base64 image in an `image_generation_call` item:

```json
{
  "status": "completed",
  "output": [
    {
      "type": "image_generation_call",
      "status": "completed",
      "result": "iVBORw0KGgo..."
    }
  ]
}
```

The parser walks nested response objects and looks for image-like fields such as `result`, `b64_json`, `base64`, and `image`.

## Responses Image: Streaming

Streaming providers can send image data through SSE events:

```text
event: response.image_generation_call.partial_image
data: {"type":"response.image_generation_call.partial_image","partial_image_b64":"iVBORw0KGgo..."}

event: response.output_item.done
data: {"type":"response.output_item.done","item":{"result":"iVBORw0KGgo..."}}
```

Parser behavior:

1. Parse all `data:` SSE lines as JSON.
2. Prefer final image values found in completed output items.
3. Fall back to the last `partial_image_b64` value when no final image value is present.


## NAI / IdleCloud Payloads

The `nai` provider accepts zip or direct image responses from `/ai/generate-image`. Zip archives are unpacked and image files inside the archive become normal job results.

The `idlecloud` provider polls `/get_result/{job_id}` and reads:

```json
{
  "status": "completed",
  "image_base64": "iVBORw0KGgo...",
  "image_url": "https://example/result.zip"
}
```

`image_base64` is decoded directly. `image_url` is downloaded; if the download is a zip archive, images inside it are unpacked.

## Custom HTTP Providers

Custom providers use configured result paths:

```toml
[providers.submit.result]
image_url_paths = ["data.images.*.url"]
b64_json_paths = ["data.images.*.b64_json"]
```

For async task APIs, put the same mapping under `[providers.poll.result]`.

## Debugging Empty Outputs

If a provider returns HTTP 200 but no file is written:

- Run the same job with `--verbose` and inspect job events.
- Confirm `stream` settings match the provider's actual API behavior.
- For `custom-http`, verify result paths against the provider's JSON shape.
- For Responses routers, try both `type = "responses-image"` and `type = "any"` when the router rejects tool fields.
