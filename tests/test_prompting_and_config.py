from __future__ import annotations

import builtins
from contextlib import redirect_stderr
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from gen_image_via_api.config import load_config
import gen_image_via_api.config as config_mod
from gen_image_via_api.generation import (
    apply_prompt_template,
    build_character_rows,
    build_job_params,
    build_v4_character_params,
    provider_model_choices,
)
from gen_image_via_api.prompting import (
    PROMPT_REWRITE_GUARD_PREFIX,
    append_size_instruction,
    render_prompt_template,
)
from gen_image_via_api.webui import (
    MISSING_WEBUI_DEPS_MESSAGE,
    TEMPLATE_DEFAULT,
    TEMPLATE_NONE,
    _status_choices,
    _tab_label,
    _template_body,
    _template_choices,
    serve_webui,
)


ROOT = Path(__file__).resolve().parents[1]


class PromptingAndConfigTests(unittest.TestCase):
    def test_prompt_template_renders_known_placeholders_and_appends_prompt(self) -> None:
        rendered = render_prompt_template(
            "Style: cinematic | {{size}} | {{ratio}} | {{quality}} | {{output_format}} | {{n}}",
            prompt="a city",
            params={
                "size": "1536x1024",
                "quality": "high",
                "output_format": "webp",
                "n": 2,
            },
        )

        self.assertEqual(rendered, "Style: cinematic | 1536x1024 | 3:2 | high | webp | 2\n\na city")

    def test_prompt_template_preserves_nai_weight_braces_and_supports_dollar_placeholders(self) -> None:
        rendered = render_prompt_template(
            "{{{best quality}}}, ${prompt}, {{unknown}}",
            prompt="1girl",
            params={},
        )

        self.assertEqual(rendered, "{{{best quality}}}, 1girl, {{unknown}}")

    def test_append_size_instruction_is_idempotent(self) -> None:
        prompt = append_size_instruction("draw", "1536x1024")

        self.assertEqual(
            append_size_instruction(prompt, "1536x1024"),
            "draw\n\nOutput size instruction: use size 1536x1024 and aspect ratio 3:2.",
        )

    def test_config_loads_provider_settings_and_templates(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "gen-image.toml"
            config.write_text(
                """
[queue]
db = "queue.sqlite3"
output_dir = "out"

[defaults]
provider = "p"
prompt_template = "wrap"

[[prompt_templates]]
id = "wrap"
body = "Wrapped: {{prompt}}"

[[providers]]
id = "p"
type = "mock"
codex_cli = true
response_format_b64_json = true
append_size_to_prompt = true
force_responses_stream = true
responses_stream_partial_images = 9
""".strip(),
                encoding="utf-8",
            )

            app = load_config(config)

        provider = app.providers[0]
        self.assertTrue(provider.codex_cli)
        self.assertTrue(provider.response_format_b64_json)
        self.assertTrue(provider.append_size_to_prompt)
        self.assertTrue(provider.force_responses_stream)
        self.assertEqual(provider.responses_stream_partial_images, 3)
        self.assertEqual(app.defaults.prompt_template, "wrap")
        self.assertEqual(app.prompt_templates[0].body, "Wrapped: {{prompt}}")

    def test_config_loads_send_preset_settings(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "gen-image.toml"
            config.write_text(
                """
[[providers]]
id = "p"
type = "mock"

[send]
preset = "openclaw"
targets = ["@mychat"]
message_template = "Generated {filename}\\nMEDIA:{path}"

[send.hermes]
agent_path = "C:/hermes-agent"
home = "C:/hermes-home"
module = "custom_hermes"
function = "send"

[send.openclaw]
agent_path = "C:/openclaw"
module = "openclaw_sender"
function = "send"
command = ["python", "send.py", "--target", "{target}", "--media", "{path}", "--message", "{caption}"]
""".strip(),
                encoding="utf-8",
            )

            app = load_config(config)

        self.assertEqual(app.send.preset, "openclaw")
        self.assertEqual(app.send.hermes.agent_path, "C:/hermes-agent")
        self.assertEqual(app.send.hermes.home, "C:/hermes-home")
        self.assertEqual(app.send.hermes.module, "custom_hermes")
        self.assertEqual(app.send.hermes.function, "send")
        self.assertEqual(app.send.openclaw.agent_path, "C:/openclaw")
        self.assertEqual(app.send.openclaw.module, "openclaw_sender")
        self.assertEqual(app.send.openclaw.function, "send")
        self.assertEqual(app.send.openclaw.command[-1], "{caption}")

    def test_prompt_rewrite_guard_prefix_constant(self) -> None:
        self.assertIn("Do not rewrite", PROMPT_REWRITE_GUARD_PREFIX)

    def test_cli_help_imports_without_optional_webui_dependency(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "gen_image_via_api", "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("webui", result.stdout)

    def test_webui_dependency_error_is_friendly(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "gen-image.toml"
            config.write_text(
                """
[[providers]]
id = "p"
type = "mock"
""".strip(),
                encoding="utf-8",
            )
            app = load_config(config)

            real_import = builtins.__import__

            def fake_import(name: str, *args, **kwargs):
                if name == "gradio":
                    raise ImportError("blocked in test")
                return real_import(name, *args, **kwargs)

            stderr = io.StringIO()
            with patch("builtins.__import__", side_effect=fake_import), redirect_stderr(stderr):
                code = serve_webui(app)

        self.assertEqual(code, 2)
        self.assertIn(MISSING_WEBUI_DEPS_MESSAGE, stderr.getvalue())

    def test_template_helpers_support_default_none_params_models_and_bilingual_labels(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "gen-image.toml"
            config.write_text(
                """
[defaults]
prompt_template = "wrap"

[[prompt_templates]]
id = "wrap"
name = "Wrapper"
body = "Wrap ${n} ${size} ${prompt}"
[prompt_templates.params]
negative_prompt = "blurry, ${prompt}, {{{{censor}}}}"

[[prompt_templates]]
id = "disabled"
enabled = false
body = "disabled"

[[providers]]
id = "p"
type = "idlecloud"
model = "nai-diffusion-4-5-full"
models = ["custom-model"]

[[providers.keys]]
id = "k"
api_key = "secret"
""".strip(),
                encoding="utf-8",
            )
            app = load_config(config)

        zh_choices = _template_choices(app, "zh")
        en_choices = _template_choices(app, "en")

        self.assertEqual(zh_choices[0], ("使用配置默认模板", TEMPLATE_DEFAULT))
        self.assertEqual(en_choices[1], ("No template", TEMPLATE_NONE))
        self.assertIn(("Wrapper · wrap", "wrap"), zh_choices)
        self.assertNotIn(("disabled · disabled", "disabled"), zh_choices)
        self.assertIn("Wrapper", _template_body(app, TEMPLATE_DEFAULT, "zh"))
        params: dict[str, object] = {"size": "1536x1024"}
        self.assertEqual(
            apply_prompt_template(app, "a fox", params, 2, template_id=TEMPLATE_DEFAULT),
            "Wrap 2 1536x1024 a fox",
        )
        self.assertEqual(params["negative_prompt"], "blurry, a fox, {{{{censor}}}}")
        self.assertEqual(app.prompt_templates[0].params["negative_prompt"], "blurry, ${prompt}, {{{{censor}}}}")
        self.assertIn("custom-model", provider_model_choices(app, "p"))
        self.assertIn("nai-diffusion-4-5-full", provider_model_choices(app, "p"))
        self.assertEqual(app.providers[0].models, ("custom-model",))
        self.assertEqual(apply_prompt_template(app, "raw", {}, 1, template_id=TEMPLATE_NONE), "raw")
        self.assertEqual(
            build_job_params(app, provider_id="p", negative_prompt="bad", steps=28, scale=5, seed=0)["negative_prompt"],
            "bad",
        )
        self.assertEqual(_status_choices("en")[0], ("All statuses", ""))
        self.assertEqual(_tab_label("create_tab"), "创作 / Create")

    def test_nai_v4_character_controls_build_first_class_payload(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "gen-image.toml"
            config.write_text(
                """
[[providers]]
id = "p"
type = "idlecloud"
model = "nai-diffusion-4-5-full"

[[providers.keys]]
id = "k"
api_key = "secret"
""".strip(),
                encoding="utf-8",
            )
            app = load_config(config)

        rows = build_character_rows(
            ["girl on left", "girl on right"],
            ["bad left", "bad right"],
            ["0.2,0.3", "0.8,0.3"],
        )
        payload = build_v4_character_params("nai-diffusion-4-5-full", rows, use_coords=True)

        self.assertTrue(payload["use_coords"])
        self.assertEqual(payload["characterPrompts"][0]["center"], {"x": 0.2, "y": 0.3})
        self.assertEqual(payload["v4_prompt_char_captions"][1]["char_caption"], "girl on right")
        self.assertEqual(payload["v4_negative_prompt_char_captions"][0]["char_caption"], "bad left")

        params = build_job_params(
            app,
            provider_id="p",
            model="nai-diffusion-4-5-full",
            characters=rows,
            use_coords=True,
        )
        self.assertIn("characterPrompts", params)
        self.assertEqual(params["characterPrompts"][1]["uc"], "bad right")

    def test_character_controls_require_v4_model(self) -> None:
        rows = build_character_rows(["girl"], ["bad"], ["0.5,0.5"])
        with self.assertRaises(ValueError):
            build_v4_character_params("nai-diffusion-3", rows, use_coords=True)

    def test_resolve_config_path_uses_skill_config_and_ignores_legacy_user_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cwd = root / "cwd"
            skill = root / "skill"
            user = root / "user"
            cwd.mkdir()
            skill.mkdir()
            user.mkdir()
            skill_config = skill / "gen-image.toml"
            user_config = user / "config.toml"
            skill_config.write_text("", encoding="utf-8")
            user_config.write_text("", encoding="utf-8")

            original_skill = config_mod.default_skill_config_path
            original_user = config_mod.default_user_config_path
            original_cwd = Path.cwd()
            try:
                config_mod.default_skill_config_path = lambda: skill_config  # type: ignore[assignment]
                config_mod.default_user_config_path = lambda: user_config  # type: ignore[assignment]
                os.chdir(cwd)
                self.assertEqual(config_mod.resolve_config_path(), skill_config)
                skill_config.unlink()
                self.assertEqual(config_mod.resolve_config_path(), skill_config)
            finally:
                os.chdir(original_cwd)
                config_mod.default_skill_config_path = original_skill  # type: ignore[assignment]
                config_mod.default_user_config_path = original_user  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
