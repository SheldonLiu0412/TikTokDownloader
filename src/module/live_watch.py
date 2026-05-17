from __future__ import annotations

import json
import fcntl
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


BEIJING = ZoneInfo("Asia/Shanghai")


def _beijing_time(now: datetime | None = None) -> datetime:
    now = now or datetime.now(BEIJING)
    if now.tzinfo is None:
        now = now.replace(tzinfo=BEIJING)
    return now.astimezone(BEIJING)


def should_monitor(
    now: datetime | None = None,
    start_hour: int = 8,
    end_hour: int = 24,
) -> bool:
    hour = _beijing_time(now).hour
    return start_hour <= hour < end_hour


def seconds_until_monitor_window(
    now: datetime | None = None,
    start_hour: int = 8,
    end_hour: int = 24,
) -> int:
    now = _beijing_time(now)
    if should_monitor(now, start_hour, end_hour):
        return 0
    if now.hour < start_hour:
        next_start = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    else:
        next_start = (now + timedelta(days=1)).replace(
            hour=start_hour,
            minute=0,
            second=0,
            microsecond=0,
        )
    return max(1, int((next_start - now).total_seconds()))


def anchor_session_window(now: datetime | None = None) -> str | None:
    hour = _beijing_time(now).hour
    if 8 <= hour < 13:
        return "morning"
    if 13 <= hour < 24:
        return "afternoon"
    return None


def seconds_until_next_anchor_session(now: datetime | None = None) -> int:
    now = _beijing_time(now)
    if now.hour < 8:
        next_start = now.replace(hour=8, minute=0, second=0, microsecond=0)
    elif now.hour < 13:
        next_start = now.replace(hour=13, minute=0, second=0, microsecond=0)
    else:
        next_start = (now + timedelta(days=1)).replace(
            hour=8,
            minute=0,
            second=0,
            microsecond=0,
        )
    return max(1, int((next_start - now).total_seconds()))


def _anchor_session_key(anchor: str, now: datetime | None = None) -> tuple[str, str, str] | None:
    now = _beijing_time(now)
    slot = anchor_session_window(now)
    if not slot:
        return None
    name = anchor.strip() or "unknown"
    return name, now.strftime("%Y-%m-%d"), slot


def _read_limit_state(file) -> dict:
    file.seek(0)
    raw = file.read().strip()
    if not raw:
        return {"anchors": {}}
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        return {"anchors": {}}
    if not isinstance(state, dict):
        return {"anchors": {}}
    anchors = state.get("anchors")
    if not isinstance(anchors, dict):
        state["anchors"] = {}
    return state


def anchor_session_consumed(path: Path, anchor: str, now: datetime | None = None) -> bool:
    key = _anchor_session_key(anchor, now)
    if not key:
        return False
    name, date_key, slot = key
    if not path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as file:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        try:
            state = _read_limit_state(file)
            return bool(state.get("anchors", {}).get(name, {}).get(date_key, {}).get(slot))
        finally:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def mark_anchor_session_consumed(
    path: Path,
    anchor: str,
    now: datetime | None = None,
    *,
    title: str = "",
) -> None:
    key = _anchor_session_key(anchor, now)
    if not key:
        return
    name, date_key, slot = key
    path.parent.mkdir(parents=True, exist_ok=True)
    recorded_at = _beijing_time(now).isoformat(timespec="seconds")
    with path.open("a+", encoding="utf-8") as file:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        try:
            state = _read_limit_state(file)
            anchors = state.setdefault("anchors", {})
            anchor_state = anchors.setdefault(name, {})
            day_state = anchor_state.setdefault(date_key, {})
            day_state[slot] = {"recorded_at": recorded_at, "title": title}
            file.seek(0)
            file.truncate()
            json.dump(state, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
        finally:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def build_ffmpeg_live_command(
    ffmpeg_path: str,
    url: str,
    output: Path,
    user_agent: str,
    proxy: str | None = None,
) -> list[str]:
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-rw_timeout",
        f"{30 * 1000 * 1000}",
        "-loglevel",
        "info",
        "-protocol_whitelist",
        "rtmp,crypto,file,http,https,tcp,tls,udp,rtp,httpproxy",
        "-analyzeduration",
        f"{10 * 1000 * 1000}",
        "-probesize",
        f"{10 * 1000 * 1000}",
        "-fflags",
        "+discardcorrupt",
        "-user_agent",
        user_agent,
        "-i",
        url,
        "-bufsize",
        "10240k",
        "-map",
        "0",
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-sn",
        "-dn",
        "-reconnect_delay_max",
        "60",
        "-reconnect_streamed",
        "-reconnect_at_eof",
        "-max_muxing_queue_size",
        "128",
        "-correct_ts_overflow",
        "1",
        "-f",
        "mp4",
        str(output),
    ]
    if proxy:
        command[2:2] = ["-http_proxy", proxy]
    return command
