from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "gen_image_cli.py"


class MockCliTests(unittest.TestCase):
    def run_cli(self, *args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=cwd,
            text=True,
            capture_output=True,
        )
        if check and result.returncode != 0:
            self.fail(f"CLI failed: {result.args}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        return result

    def test_mock_once_generation_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cwd = Path(raw)
            config = cwd / "gen-image.toml"
            self.run_cli("init-config", "--out", str(config), cwd=cwd)
            result = self.run_cli(
                "once",
                "--config",
                str(config),
                "--prompt",
                "smoke image",
                "--count",
                "2",
                "--out-prefix",
                "smoke",
                cwd=cwd,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "succeeded")
            self.assertEqual(len(payload["results"]), 2)
            self.assertNotIn("events", payload)
            for item in payload["results"]:
                self.assertEqual(set(item), {"index", "path"})
                self.assertTrue(Path(item["path"]).exists())

    def test_mock_edit_accepts_input_image(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cwd = Path(raw)
            config = cwd / "gen-image.toml"
            self.run_cli("init-config", "--out", str(config), cwd=cwd)
            image = cwd / "input.png"
            image.write_bytes(
                bytes.fromhex(
                    "89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c020000000b49444154789c63fcff1f0003030200efbfa7db0000000049454e44ae426082"
                )
            )
            result = self.run_cli(
                "once",
                "--config",
                str(config),
                "--prompt",
                "edit smoke",
                "--image",
                str(image),
                "--out-prefix",
                "edit",
                cwd=cwd,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["kind"], "edit")
            self.assertEqual(payload["status"], "succeeded")
            self.assertNotIn("events", payload)
            self.assertTrue(Path(payload["results"][0]["path"]).exists())

    def test_once_verbose_keeps_full_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cwd = Path(raw)
            config = cwd / "gen-image.toml"
            self.run_cli("init-config", "--out", str(config), cwd=cwd)
            result = self.run_cli(
                "once",
                "--config",
                str(config),
                "--prompt",
                "verbose smoke",
                "--out-prefix",
                "verbose",
                "--verbose",
                cwd=cwd,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "succeeded")
            self.assertIn("prompt", payload)
            self.assertIn("events", payload)
            self.assertIn("metadata", payload["results"][0])

    def test_generate_json_is_compact_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cwd = Path(raw)
            config = cwd / "gen-image.toml"
            self.run_cli("init-config", "--out", str(config), cwd=cwd)
            try:
                result = self.run_cli(
                    "generate",
                    "--config",
                    str(config),
                    "--prompt",
                    "compact generate",
                    "--count",
                    "2",
                    "--out-prefix",
                    "compact",
                    "--json",
                    "--poll-interval",
                    "0.1",
                    cwd=ROOT,
                )
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "succeeded")
                self.assertEqual(payload["kind"], "generate")
                self.assertEqual(len(payload["results"]), 2)
                self.assertNotIn("job", payload)
                self.assertNotIn("runtime", payload)
                self.assertNotIn("worker", payload)
                self.assertLess(len(result.stdout), 900)
                for item in payload["results"]:
                    self.assertEqual(set(item), {"index", "path"})
                    self.assertTrue(Path(item["path"]).exists())
            finally:
                self.run_cli("stop-worker", "--config", str(config), cwd=ROOT, check=False)

    def test_providers_reports_round_robin_key_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cwd = Path(raw)
            config = cwd / "gen-image.toml"
            self.run_cli("init-config", "--out", str(config), cwd=cwd)
            result = self.run_cli("providers", "--config", str(config), cwd=cwd)
            payload = json.loads(result.stdout)
            mock = next(item for item in payload["providers"] if item["id"] == "mock-local")
            self.assertEqual(mock["keys"][0]["images_per_request"], 1)
            self.assertEqual(mock["parameter_support"]["direct_cli_params"], [])
            self.assertEqual(mock["parameter_support"]["common_cli_params"], [])
            self.assertIn("mock provider", mock["parameter_support"]["notes"][0])

    def test_submit_persists_direct_flags_and_param_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cwd = Path(raw)
            config = cwd / "gen-image.toml"
            self.run_cli("init-config", "--out", str(config), cwd=cwd)
            result = self.run_cli(
                "submit",
                "--config",
                str(config),
                "--prompt",
                "parameter smoke",
                "--no-start-worker",
                "--aspect-ratio",
                "16:9",
                "--size-tier",
                "2K",
                "--quality",
                "high",
                "--format",
                "jpg",
                "--background",
                "transparent",
                "--moderation",
                "low",
                "--output-compression",
                "80",
                "--model",
                "gpt-image-2",
                "--action",
                "generate",
                "--no-stream",
                "--param",
                "response_format=b64_json",
                "--param",
                "seed=123",
                "--json",
                cwd=cwd,
            )
            job_id = json.loads(result.stdout)["job_id"]
            status = self.run_cli("status", "--config", str(config), job_id, "--json", cwd=cwd)
            params = json.loads(status.stdout)["job"]["params"]
            self.assertEqual(params["size"], "2048x1152")
            self.assertEqual(params["quality"], "high")
            self.assertEqual(params["output_format"], "jpeg")
            self.assertEqual(params["background"], "transparent")
            self.assertEqual(params["moderation"], "low")
            self.assertEqual(params["output_compression"], 80)
            self.assertEqual(params["model"], "gpt-image-2")
            self.assertEqual(params["action"], "generate")
            self.assertFalse(params["stream"])
            self.assertEqual(params["response_format"], "b64_json")
            self.assertEqual(params["seed"], 123)

    def test_submit_applies_prompt_template_before_enqueue(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cwd = Path(raw)
            config = cwd / "gen-image.toml"
            self.run_cli("init-config", "--out", str(config), cwd=cwd)
            text = config.read_text(encoding="utf-8")
            text = text.replace('prompt_template = ""', 'prompt_template = "wrap"')
            text += '\n[[prompt_templates]]\nid = "wrap"\nenabled = true\nbody = "Style: {{quality}} / {{size}} / {{ratio}} / {{n}}\\n\\n{{prompt}}"\n'
            config.write_text(text, encoding="utf-8")

            result = self.run_cli(
                "submit",
                "--config",
                str(config),
                "--prompt",
                "template smoke",
                "--no-start-worker",
                "--size",
                "1536x1024",
                "--quality",
                "high",
                "--count",
                "2",
                "--json",
                cwd=cwd,
            )
            job_id = json.loads(result.stdout)["job_id"]
            status = self.run_cli("status", "--config", str(config), job_id, "--json", cwd=cwd)
            prompt = json.loads(status.stdout)["job"]["prompt"]
            self.assertEqual(prompt, "Style: high / 1536x1024 / 3:2 / 2\n\ntemplate smoke")

    def test_submit_autostarts_worker(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cwd = Path(raw)
            config = cwd / "gen-image.toml"
            self.run_cli("init-config", "--out", str(config), cwd=cwd)
            try:
                result = self.run_cli(
                    "submit",
                    "--config",
                    str(config),
                    "--prompt",
                    "auto worker",
                    "--count",
                    "2",
                    "--out-prefix",
                    "auto",
                    "--json",
                    cwd=ROOT,
                )
                payload = json.loads(result.stdout)
                self.assertTrue(payload["worker"]["running"])
                job_id = payload["job_id"]
                final = None
                for _ in range(30):
                    status = self.run_cli("status", "--config", str(config), job_id, "--json", cwd=ROOT)
                    final = json.loads(status.stdout)["job"]
                    if final["status"] in {"succeeded", "failed", "cancelled"}:
                        break
                    time.sleep(0.25)
                self.assertIsNotNone(final)
                self.assertEqual(final["status"], "succeeded")
                self.assertEqual(len(final["results"]), 2)
            finally:
                self.run_cli("stop-worker", "--config", str(config), cwd=ROOT, check=False)

    def test_generate_can_send_outputs_with_python_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cwd = Path(raw)
            config = cwd / "gen-image.toml"
            sender = cwd / "fake_sender.py"
            sender.write_text(
                "\n".join(
                    [
                        "import json",
                        "from pathlib import Path",
                        "",
                        "def send_file(args):",
                        "    log = Path(__file__).with_name('send-log.jsonl')",
                        "    with log.open('a', encoding='utf-8') as fh:",
                        "        fh.write(json.dumps(args, sort_keys=True) + '\\n')",
                        "    return {'ok': True, 'target': args.get('target')}",
                    ]
                ),
                encoding="utf-8",
            )
            self.run_cli("init-config", "--out", str(config), cwd=cwd)
            text = config.read_text(encoding="utf-8")
            text = text.replace('module = ""', 'module = "fake_sender"')
            text = text.replace('function = ""', 'function = "send_file"')
            text = text.replace("retry_delays = [2, 5, 10]", "retry_delays = []")
            config.write_text(text, encoding="utf-8")
            try:
                result = self.run_cli(
                    "generate",
                    "--config",
                    str(config),
                    "--prompt",
                    "send smoke",
                    "--out-prefix",
                    "send-smoke",
                    "--send",
                    "--send-target",
                    "telegram",
                    "--json",
                    "--poll-interval",
                    "0.1",
                    cwd=ROOT,
                )
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "succeeded")
                self.assertTrue(payload["send"]["ok"])
                self.assertEqual(payload["send"]["targets"], ["telegram"])
                self.assertEqual(payload["send"]["count"], 1)
                log_lines = (cwd / "send-log.jsonl").read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(log_lines), 1)
                sent = json.loads(log_lines[0])
                self.assertEqual(sent["action"], "send")
                self.assertEqual(sent["target"], "telegram")
                self.assertTrue(sent["message"].startswith("MEDIA:"))
                self.assertTrue(Path(sent["path"]).exists())
            finally:
                self.run_cli("stop-worker", "--config", str(config), cwd=ROOT, check=False)

    def test_send_command_sends_existing_file_to_multiple_targets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cwd = Path(raw)
            config = cwd / "gen-image.toml"
            sender = cwd / "fake_sender.py"
            sender.write_text(
                "\n".join(
                    [
                        "import json",
                        "from pathlib import Path",
                        "",
                        "def send_file(args):",
                        "    log = Path(__file__).with_name('send-log.jsonl')",
                        "    with log.open('a', encoding='utf-8') as fh:",
                        "        fh.write(json.dumps(args, sort_keys=True) + '\\n')",
                        "    return json.dumps({'ok': True})",
                    ]
                ),
                encoding="utf-8",
            )
            image = cwd / "ready.png"
            image.write_bytes(b"image")
            self.run_cli("init-config", "--out", str(config), cwd=cwd)
            text = config.read_text(encoding="utf-8")
            text = text.replace('module = ""', 'module = "fake_sender"')
            text = text.replace('function = ""', 'function = "send_file"')
            text = text.replace("retry_delays = [2, 5, 10]", "retry_delays = []")
            config.write_text(text, encoding="utf-8")

            result = self.run_cli(
                "send",
                "--config",
                str(config),
                "--path",
                str(image),
                "--target",
                "telegram",
                "--target",
                "weixin",
                "--json",
                cwd=ROOT,
            )

            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["targets"], ["telegram", "weixin"])
            self.assertEqual(payload["count"], 2)
            sent_targets = [
                json.loads(line)["target"]
                for line in (cwd / "send-log.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(sent_targets, ["telegram", "weixin"])


if __name__ == "__main__":
    unittest.main()
