"""Reproduce issue #7 without a running app or sync server.

Run from the repository root:
    uv run --no-project --python 3.12 --with httpx --with loguru \
        tests/repro_issue_7_timeline_timezone.py

The production module is loaded directly to avoid unrelated retrieval-tool
imports. Only the server URL, HTTP transport, timezone environment, and
/etc/localtime resolution are mocked; the timezone helper and tool execute
method run unchanged. Windows is simulated by the absence of a zoneinfo
symlink, with an explicit IANA TZ value identifying the intended local zone.
Date ranges are calculated from the captured request using the issue's API
contract, not measured against the real server.
"""

import importlib.util
import json
import os
import sys
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location(
    "issue_7_sync_tools", ROOT / "opencontext/tools/retrieval_tools/sync_tools.py"
)
sync_tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_tools)

CASES = (
    ("Asia/Tokyo", "2026-10-21", "2026-10-20T15:00:00+00:00", "2026-10-21T15:00:00+00:00"),
    ("UTC", "2026-10-21", "2026-10-21T00:00:00+00:00", "2026-10-22T00:00:00+00:00"),
    ("America/New_York", "2026-03-08", "2026-03-08T05:00:00+00:00", "2026-03-09T04:00:00+00:00"),
    ("America/New_York", "2026-11-01", "2026-11-01T04:00:00+00:00", "2026-11-02T05:00:00+00:00"),
)


def utc_bounds(day_string, tz_name):
    day = date.fromisoformat(day_string)
    zone = ZoneInfo(tz_name)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def request_timeline(day_string, localtime_target, explicit_tz):
    original_realpath = os.path.realpath

    def resolve_localtime(path, *args, **kwargs):
        if os.fspath(path) == "/etc/localtime":
            return localtime_target
        return original_realpath(path, *args, **kwargs)

    def respond(url, *, params, timeout):
        request = httpx.Request("GET", url, params=params)
        return httpx.Response(
            200,
            request=request,
            json={"date": params["date"], "tz": params["tz"], "items": []},
        )

    with patch.dict(os.environ):
        os.environ.pop("TZ", None)
        if explicit_tz is not None:
            os.environ["TZ"] = explicit_tz
        with (
            patch.object(sync_tools, "_get_server_url", return_value="https://sync.invalid"),
            patch.object(sync_tools.os.path, "realpath", side_effect=resolve_localtime),
            patch.object(sync_tools.httpx, "get", side_effect=respond) as get,
            patch.object(sync_tools.logger, "warning") as warning,
        ):
            result = sync_tools.GetDayTimelineTool().execute(date=day_string)
            return result, get.call_args_list, warning.call_args_list


class TimelineTimezoneReproduction(unittest.TestCase):
    def check_request(self, label, zone, day_string, start, end, target, explicit_tz):
        result, calls, _ = request_timeline(day_string, target, explicit_tz)
        self.assertNotIn("error", result)
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call.args, ("https://sync.invalid/v1/timeline",))
        params = call.kwargs["params"]
        self.assertEqual(params["date"], day_string)
        actual_bounds = utc_bounds(params["date"], params["tz"])
        actual = (params["tz"], *(bound.isoformat() for bound in actual_bounds))
        local_bounds = tuple(bound.astimezone(ZoneInfo(zone)).isoformat() for bound in actual_bounds)
        print(json.dumps({
            "case": label,
            "expected_zone": zone,
            "request_params": params,
            "inferred_utc_bounds": actual[1:],
            "inferred_local_bounds": local_bounds,
            "inferred_hours": (actual_bounds[1] - actual_bounds[0]).total_seconds() / 3600,
        }), flush=True)
        self.assertEqual(actual, (zone, start, end))

    def test_windows_without_localtime_symlink(self):
        for zone, day_string, start, end in CASES:
            with self.subTest(zone=zone, date=day_string):
                self.check_request(
                    "windows_no_localtime",
                    zone, day_string, start, end,
                    r"C:\etc\localtime", zone,
                )

    def test_existing_zoneinfo_symlink(self):
        for zone, day_string, start, end in CASES:
            with self.subTest(zone=zone, date=day_string):
                self.check_request(
                    "linux_zoneinfo_symlink",
                    zone, day_string, start, end,
                    f"/usr/share/zoneinfo/{zone}", None,
                )

    def test_unknown_timezone_does_not_silently_request_utc(self):
        result, calls, warnings = request_timeline("2026-10-21", r"C:\etc\localtime", None)
        print(json.dumps({
            "case": "unknown_timezone",
            "result": result,
            "request_params": [call.kwargs["params"] for call in calls],
            "warning_count": len(warnings),
        }), flush=True)
        self.assertTrue(
            not calls or warnings or result.get("error"),
            "Timezone detection failed, but the tool requested UTC without a warning or error",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
