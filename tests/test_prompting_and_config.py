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
from gen_image_via_api.prompting import (
    PROMPT_REWRITE_GUARD_PREFIX,
    append_size_instruction,
    render_prompt_template,
)
from gen_image_via_api.webui import MISSING_WEBUI_DEPS_MESSAGE, serve_webui


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
