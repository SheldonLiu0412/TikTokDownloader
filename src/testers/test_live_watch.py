from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.module.live_watch import (
    anchor_session_consumed,
    anchor_session_window,
    build_ffmpeg_live_command,
    mark_anchor_session_consumed,
    seconds_until_next_anchor_session,
    seconds_until_monitor_window,
    should_monitor,
)


BEIJING = ZoneInfo("Asia/Shanghai")


def test_should_monitor_between_8_and_24_beijing_time():
    assert should_monitor(datetime(2026, 5, 16, 8, 0, tzinfo=BEIJING))
    assert should_monitor(datetime(2026, 5, 16, 23, 59, tzinfo=BEIJING))
    assert not should_monitor(datetime(2026, 5, 16, 0, 0, tzinfo=BEIJING))
    assert not should_monitor(datetime(2026, 5, 16, 7, 59, tzinfo=BEIJING))


def test_seconds_until_monitor_window_waits_until_next_8am():
    assert seconds_until_monitor_window(
        datetime(2026, 5, 16, 7, 30, tzinfo=BEIJING)
    ) == 30 * 60
    assert seconds_until_monitor_window(
        datetime(2026, 5, 16, 23, 0, tzinfo=BEIJING)
    ) == 0
    assert seconds_until_monitor_window(
        datetime(2026, 5, 16, 0, 30, tzinfo=BEIJING)
    ) == 7 * 60 * 60 + 30 * 60


def test_anchor_session_window_splits_day_into_two_recording_slots():
    assert anchor_session_window(datetime(2026, 5, 17, 8, 0, tzinfo=BEIJING)) == "morning"
    assert anchor_session_window(datetime(2026, 5, 17, 12, 59, tzinfo=BEIJING)) == "morning"
    assert anchor_session_window(datetime(2026, 5, 17, 13, 0, tzinfo=BEIJING)) == "afternoon"
    assert anchor_session_window(datetime(2026, 5, 17, 23, 59, tzinfo=BEIJING)) == "afternoon"
    assert anchor_session_window(datetime(2026, 5, 17, 7, 59, tzinfo=BEIJING)) is None
    assert anchor_session_window(datetime(2026, 5, 18, 0, 0, tzinfo=BEIJING)) is None


def test_seconds_until_next_anchor_session_jumps_from_consumed_slot_to_next_slot():
    assert seconds_until_next_anchor_session(
        datetime(2026, 5, 17, 10, 30, tzinfo=BEIJING)
    ) == 2 * 60 * 60 + 30 * 60
    assert seconds_until_next_anchor_session(
        datetime(2026, 5, 17, 22, 15, tzinfo=BEIJING)
    ) == 9 * 60 * 60 + 45 * 60
    assert seconds_until_next_anchor_session(
        datetime(2026, 5, 17, 7, 30, tzinfo=BEIJING)
    ) == 30 * 60


def test_anchor_session_limit_file_tracks_anchor_date_and_slot(tmp_path):
    path = tmp_path / "limits.json"
    morning = datetime(2026, 5, 17, 10, 30, tzinfo=BEIJING)
    afternoon = datetime(2026, 5, 17, 19, 51, tzinfo=BEIJING)

    assert not anchor_session_consumed(path, "股海领航", morning)

    mark_anchor_session_consumed(path, "股海领航", morning, title="早盘")

    assert anchor_session_consumed(path, "股海领航", morning)
    assert not anchor_session_consumed(path, "股海领航", afternoon)
    assert not anchor_session_consumed(path, "超短先锋", morning)


def test_build_ffmpeg_live_command_uses_copy_mode_and_output_path():
    command = build_ffmpeg_live_command(
        "/opt/homebrew/bin/ffmpeg",
        "https://example.test/live.flv",
        Path("/tmp/out.mp4"),
        "UA",
    )

    assert command[0] == "/opt/homebrew/bin/ffmpeg"
    assert command[-1] == "/tmp/out.mp4"
    assert command[command.index("-i") + 1] == "https://example.test/live.flv"
    assert command[command.index("-user_agent") + 1] == "UA"
    assert command[command.index("-c:v") + 1] == "copy"
    assert command[command.index("-c:a") + 1] == "copy"


def test_build_ffmpeg_live_command_includes_proxy_when_set():
    command = build_ffmpeg_live_command(
        "ffmpeg",
        "https://example.test/live.flv",
        Path("/tmp/out.mp4"),
        "UA",
        proxy="http://127.0.0.1:7890",
    )

    assert command[2:4] == ["-http_proxy", "http://127.0.0.1:7890"]
