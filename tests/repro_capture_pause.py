"""Issue #5 reproduction; no real screenshots, provider calls, or worker threads.

Run from the repository root:
    uv run --no-project --python 3.12 --with pyyaml --with loguru python tests/repro_capture_pause.py

The companion Node check requires Node >=22.13 with VM modules enabled.
Only configuration YAML in a temporary directory is written. The real config
manager persists/reloads it; the global config accessor is an in-memory adapter.
"""

import base64
import importlib
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def module(name, **attributes):
    result = types.ModuleType(name)
    result.__dict__.update(attributes)
    return result


class CapturePauseReproduction(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hibi-pause-repro-")
        self.addCleanup(self.temp.cleanup)
        self.router = Mock()
        self.router.has_live_provider.return_value = True
        self.router.has_route.return_value = True
        self.router.live_stream_frame = AsyncMock()
        self.router.generate = AsyncMock(return_value="")
        self.router.live_ask = AsyncMock(return_value="")
        self.image_hash = Mock(return_value="0000000000000000")
        # Import adapters replace only external dependencies and the global
        # accessor. The monitor/config manager implementations remain unchanged.
        adapters = {
            "opencontext.utils.logging_utils": module(
                "opencontext.utils.logging_utils", get_logger=logging.getLogger
            ),
            "opencontext.utils.image": module(
                "opencontext.utils.image", calculate_bytes2phash=self.image_hash
            ),
            "opencontext.llm.provider_router": module(
                "opencontext.llm.provider_router", get_provider_router=lambda: self.router
            ),
        }
        self.modules = patch.dict(sys.modules, adapters)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        # Ensure these imports use this test's dependency adapters.
        for name in ("opencontext.screen_record.live_monitor", "opencontext.config.config_manager"):
            sys.modules.pop(name, None)
        self.live = importlib.import_module("opencontext.screen_record.live_monitor")
        config_module = importlib.import_module("opencontext.config.config_manager")
        self.config = config_module.ConfigManager()
        accessor = patch(
            "opencontext.config.global_config.get_config",
            side_effect=lambda key: self.config.get_config().get(key),
        )
        accessor.start()
        self.addCleanup(accessor.stop)
        self.config_path = Path(self.temp.name) / "config.yaml"
        self.config_path.write_text(
            f"user_setting_path: {self.temp.name}/user_setting.yaml\n"
            "capture:\n  paused: false\n  capture_min_interval: 20\n",
            encoding="utf-8",
        )
        self.assertTrue(self.config.load_config(str(self.config_path)))
        self.monitor = self.live.ScreenLiveMonitor()
        # submit_frame normally launches threads. Dispatch their real loop
        # bodies synchronously below, after saving the pause setting.
        self.monitor._started = True
        self.monitor._save_screen_summary = Mock()
        self.jpeg = b"synthetic-image-only-issue-5"

    def save_pause(self, paused):
        capture = {**self.config.get_config()["capture"], "paused": paused}
        self.assertTrue(self.config.save_user_settings({"capture": capture}))
        self.assertTrue(self.config.load_config(str(self.config_path)))
        self.assertEqual(self.monitor._capture_config()["paused"], paused)

    def queue_frame(self):
        decision = self.monitor.submit_frame(self.jpeg, app="ReproApp", title="Synthetic screen")
        self.assertEqual(decision, {"accepted": True})
        self.assertEqual(self.monitor._frame_queue.qsize(), 1)

    def queue_exact(self):
        self.queue_frame()
        # Generate a genuine exact work item before pause, with Live disabled
        # so this setup never starts a provider send for the frame.
        self.router.has_live_provider.return_value = False
        self.monitor._handle_frame(self.monitor._frame_queue.get_nowait())
        self.assertEqual(self.monitor._trigger_queue.qsize(), 1)
        self.assertEqual(self.router.live_stream_frame.call_count, 0)
        self.router.has_live_provider.return_value = True

    def queue_interval(self):
        now = time.time()
        self.monitor._enqueue_trigger(self.live._Trigger(
            "interval", interval_start=now - 30, interval_end=now,
            interval_meta=[{"app": "ReproApp", "title": "Synthetic screen", "url": ""}],
        ))

    def run_one(self, queue_name, loop_name):
        pending = getattr(self.monitor, queue_name)
        if pending.empty():
            return

        original_get = pending.get

        def take_one(*args, **kwargs):
            self.monitor._stop.set()
            return original_get(block=False)

        self.monitor._stop.clear()
        with patch.object(pending, "get", side_effect=take_one):
            getattr(self.monitor, loop_name)()
        self.monitor._stop.clear()

    def run_push(self):
        self.run_one("_frame_queue", "_push_loop")

    def run_summarizer(self):
        self.run_one("_trigger_queue", "_summarizer_loop")

    def test_control_paused_admission_is_rejected(self):
        self.save_pause(True)
        self.assertEqual(self.monitor.submit_frame(self.jpeg),
                         {"accepted": False, "reason": "paused"})
        self.assertTrue(self.monitor._frame_queue.empty())

    def test_control_unpaused_live_sends_synthetic_frame(self):
        self.queue_frame()
        self.run_push()
        self.router.live_stream_frame.assert_awaited_once_with(self.jpeg)

    def test_control_unpaused_exact_sends_synthetic_image(self):
        self.queue_exact()
        self.run_summarizer()
        self.router.generate.assert_awaited_once()
        call = self.router.generate.call_args.kwargs
        self.assertEqual(call["route"], "exact")
        expected = "data:image/jpeg;base64," + base64.b64encode(self.jpeg).decode("ascii")
        self.assertEqual(call["messages"][1]["content"][1]["image_url"]["url"], expected)

    def test_control_unpaused_interval_calls_live_ask(self):
        self.queue_interval()
        self.run_summarizer()
        self.router.live_ask.assert_awaited_once()

    def test_queued_frame_must_not_reach_live_after_pause(self):
        self.queue_frame()
        self.save_pause(True)
        self.run_push()
        self.router.live_stream_frame.assert_not_called()

    def test_queued_exact_must_not_send_image_after_pause(self):
        self.queue_exact()
        self.save_pause(True)
        self.run_summarizer()
        self.router.generate.assert_not_called()

    def test_queued_interval_must_not_call_summarizer_after_pause(self):
        self.queue_interval()
        self.save_pause(True)
        self.run_summarizer()
        self.router.live_ask.assert_not_called()

    def test_pause_must_discard_pending_frames_and_triggers(self):
        self.queue_exact()
        self.queue_frame()
        self.save_pause(True)
        self.assertEqual(
            (self.monitor._frame_queue.qsize(), self.monitor._trigger_queue.qsize()),
            (0, 0),
            "Pause must discard both unsent frames and exact triggers",
        )

    def test_resume_must_not_revive_old_live_frame(self):
        self.queue_frame()
        self.save_pause(True)
        self.save_pause(False)
        self.run_push()
        self.router.live_stream_frame.assert_not_called()

    def test_resume_must_not_revive_old_exact_trigger(self):
        self.queue_exact()
        self.save_pause(True)
        self.save_pause(False)
        self.run_summarizer()
        self.router.generate.assert_not_called()

    def test_resume_must_not_revive_old_interval_trigger(self):
        self.queue_interval()
        self.save_pause(True)
        self.save_pause(False)
        self.run_summarizer()
        self.router.live_ask.assert_not_called()

    def test_live_must_recheck_pause_at_provider_boundary(self):
        self.queue_frame()
        # Pause after _handle_frame read its capture config, before the external
        # client call. No external call has begun at this boundary.
        self.image_hash.side_effect = lambda jpeg: (self.save_pause(True), "0000")[1]
        self.run_push()
        self.router.live_stream_frame.assert_not_called()


class ElectronCapturePauseReproduction(unittest.TestCase):
    def test_electron_capture_respects_pause(self):
        completed = subprocess.run(
            ["node", "--experimental-vm-modules", "--test",
             str(ROOT / "tests/repro_capture_pause.cjs")],
            cwd=ROOT, text=True, capture_output=True, timeout=30,
        )
        print(completed.stdout, end="", flush=True)
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr, flush=True)
        self.assertEqual(completed.returncode, 0, "Electron pause assertions failed; see TAP output")


if __name__ == "__main__":
    unittest.main(verbosity=2)
