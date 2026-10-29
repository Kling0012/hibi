"""Issue #19 reproduction using real capture, route, and monitor implementations.

From the repository root (Python/Node caches remain outside the worktree):
    uv run --no-project --python 3.12 --with pillow --with fastapi --with pyyaml --with loguru python tests/repro_issue_19_excluded_capture.py

Node >=22.13 is required. The companion Node script executes the unmodified
TypeScript service with Electron/get-windows/axios adapters. Its captured POST
query and generated JPEG are passed to the actual Python route and monitor.
The provider, configuration accessor, image hash, and response wrapper are
in-memory adapters. No OS capture, network, persistence, or threads are used.
Exit 1 means a safety assertion failed, not that the reproduction failed to run.
"""

import asyncio
import base64
import importlib
import io
import json
import logging
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import urlencode

from fastapi import Request
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
GENERATION = "issue-19-generation"


def module(name, **attributes):
    result = types.ModuleType(name)
    result.__dict__.update(attributes)
    return result


def synthetic_jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (40, 80, 120)).save(buffer, format="JPEG")
    return buffer.getvalue()


class ExcludedCaptureReproduction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jpeg = synthetic_jpeg()
        completed = subprocess.run(
            ["node", "--experimental-vm-modules", str(
                ROOT / "tests/repro_issue_19_excluded_capture.cjs")],
            cwd=ROOT,
            input=json.dumps({"jpeg": base64.b64encode(cls.jpeg).decode("ascii")}),
            text=True, capture_output=True, timeout=30,
        )
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        if completed.returncode != 0:
            raise RuntimeError(f"Capture harness exited {completed.returncode}: {completed.stdout}")
        cls.observations = {item["name"]: item for item in json.loads(completed.stdout)}

    def setUp(self):
        self.capture = {"paused": False, "exclude_apps": ["1Password"]}
        self.router = Mock()
        self.router.has_live_provider.return_value = True
        self.router.live_stream_frame = AsyncMock()
        manager = types.SimpleNamespace(get_capture_generation=lambda: GENERATION)
        config = types.SimpleNamespace(get_config_manager=lambda: manager)
        adapters = {
            "opencontext.utils.logging_utils": module(
                "opencontext.utils.logging_utils", get_logger=logging.getLogger),
            "opencontext.utils.image": module(
                "opencontext.utils.image", calculate_bytes2phash=lambda jpeg: "0000"),
            "opencontext.config.global_config": module(
                "opencontext.config.global_config",
                get_config=lambda key: self.capture if key == "capture" else None,
                get_global_config=lambda: config,
                get_prompt=lambda *args: "",
                GlobalConfig=types.SimpleNamespace(get_instance=lambda: config)),
            "opencontext.llm.provider_router": module(
                "opencontext.llm.provider_router", get_provider_router=lambda: self.router),
            "opencontext.server.middleware.auth": module(
                "opencontext.server.middleware.auth", auth_dependency="synthetic-auth"),
            "opencontext.server.utils": module(
                "opencontext.server.utils", convert_resp=lambda **kwargs: kwargs),
        }
        self.adapter_patch = patch.dict(sys.modules, adapters)
        self.adapter_patch.start()
        self.addCleanup(self.adapter_patch.stop)
        for name in ("opencontext.screen_record.live_monitor", "opencontext.server.routes.live"):
            sys.modules.pop(name, None)
        self.live = importlib.import_module("opencontext.screen_record.live_monitor")
        self.route = importlib.import_module("opencontext.server.routes.live")
        self.monitor = self.live.ScreenLiveMonitor()
        # Dispatch queued frames synchronously, without starting real worker threads.
        self.monitor._started = True
        singleton = patch.object(self.live, "get_live_monitor", return_value=self.monitor)
        singleton.start()
        self.addCleanup(singleton.stop)

    def deliver(self, query, jpeg):
        async def receive():
            return {"type": "http.request", "body": jpeg, "more_body": False}

        request = Request({
            "type": "http", "method": "POST", "path": "/api/live/frame",
            "query_string": urlencode(query).encode("ascii"),
            "headers": [(b"content-type", b"image/jpeg")],
            "client": ("127.0.0.1", 12345),
        }, receive)
        response = asyncio.run(self.route.post_live_frame(request, _auth="synthetic-auth"))
        self.assertIn("data", response, "Route must reach the monitor rather than fail in an adapter")
        decision = response["data"]
        self.assertNotEqual(decision.get("reason"), "error")
        while not self.monitor._frame_queue.empty():
            self.monitor._handle_frame(self.monitor._frame_queue.get_nowait())
        self.assertEqual(self.router.live_stream_frame.call_count,
                         self.router.live_stream_frame.await_count,
                         "Provider coroutine must actually run")
        return decision

    def check_scenario(self, name):
        observation = self.observations[name]
        self.capture["exclude_apps"] = observation["exclude"]
        decisions = []
        for upload in observation["uploads"]:
            self.assertEqual(upload["query"]["capture_generation"], GENERATION)
            jpeg = base64.b64decode(upload["jpeg"])
            self.assertEqual(jpeg, self.jpeg)
            decisions.append(self.deliver(upload["query"], jpeg))
        actual = {
            "uploads": len(observation["uploads"]),
            "accepted": sum(decision.get("accepted") is True for decision in decisions),
            "provider_calls": self.router.live_stream_frame.await_count,
        }
        print(json.dumps({"scenario": name, "meta": observation["meta"],
                          "decisions": decisions, **actual}), flush=True)
        if observation["blocked"]:
            self.assertEqual(actual, {"uploads": 0, "accepted": 0, "provider_calls": 0},
                             "Excluded or unidentified frames with exclusions must never be sent")
        else:
            self.assertEqual(actual, {"uploads": 1, "accepted": 1, "provider_calls": 1},
                             "Ordinary capture must continue")
            self.router.live_stream_frame.assert_awaited_once_with(self.jpeg)
            if observation["suppress"]:
                self.assertEqual(observation["meta"]["title"], "")
                self.assertEqual(observation["meta"]["url"], "")
                self.assertEqual(observation["meta"]["app"], "Synthetic Editor",
                                 "Spanning-window metadata suppression must retain app identity")
            if name == "normal_contained":
                self.assertEqual(observation["meta"], {
                    "app": "Synthetic Editor", "title": "Synthetic window title",
                    "url": "https://example.invalid/issue-19"})

    def test_backend_known_excluded_app_control(self):
        decision = self.deliver({"app": "1Password", "capture_generation": GENERATION}, self.jpeg)
        self.assertEqual(decision, {"accepted": False, "reason": "excluded_app"})
        self.router.live_stream_frame.assert_not_called()

    def test_backend_unknown_app_with_exclusions(self):
        decision = self.deliver({"app": "", "capture_generation": GENERATION}, self.jpeg)
        print(json.dumps({"scenario": "backend_unknown_with_exclusions", "decision": decision,
                          "provider_calls": self.router.live_stream_frame.await_count}), flush=True)
        self.assertEqual((decision.get("accepted"), self.router.live_stream_frame.await_count),
                         (False, 0), "Backend must reject unknown app when exclusions are configured")

    def test_backend_unknown_app_without_exclusions_control(self):
        self.capture["exclude_apps"] = []
        decision = self.deliver({"app": "", "capture_generation": GENERATION}, self.jpeg)
        self.assertEqual(decision, {"accepted": True})
        self.router.live_stream_frame.assert_awaited_once_with(self.jpeg)

    def test_backend_exclusion_rechecked_for_dequeued_frame(self):
        # A dequeued frame can arrive independently of admission; use the real
        # generation and current timestamp so only the exclusion is under test.
        import time

        frame = self.live._Frame(self.jpeg, "1Password", "", "", time.time(), self.monitor._generation)
        self.monitor._handle_frame(frame)
        print(json.dumps({"scenario": "backend_dequeued_excluded_app",
                          "provider_calls": self.router.live_stream_frame.await_count}), flush=True)
        self.router.live_stream_frame.assert_not_called()


def scenario_test(name):
    def run(self):
        self.check_scenario(name)
    return run


for scenario in (
    "excluded_spanning", "excluded_maximized", "excluded_maximized_overhang",
    "excluded_negative_spanning", "metadata_error_with_exclusions",
    "metadata_missing_with_exclusions", "normal_spanning", "normal_contained",
    "unknown_without_exclusions",
):
    setattr(ExcludedCaptureReproduction, f"test_capture_{scenario}", scenario_test(scenario))


if __name__ == "__main__":
    unittest.main(verbosity=2)
