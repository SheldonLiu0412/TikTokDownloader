from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo


BEIJING = ZoneInfo("Asia/Shanghai")
CONSUMED_ANCHOR_SESSION_STATUSES = {"recording", "recorded"}


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


def _write_limit_state(file, state: dict) -> None:
    file.seek(0)
    file.truncate()
    json.dump(state, file, ensure_ascii=False, indent=2, sort_keys=True)
    file.write("\n")


def _anchor_session_entry_consumed(entry: object) -> bool:
    if not entry:
        return False
    if not isinstance(entry, dict):
        return True
    status = entry.get("status")
    if status:
        return status in CONSUMED_ANCHOR_SESSION_STATUSES
    return bool(entry)


def _find_recording_reservation(
    state: dict,
    anchor: str,
    token: str,
) -> tuple[dict, str, dict] | None:
    anchor_state = state.get("anchors", {}).get(anchor, {})
    if not isinstance(anchor_state, dict):
        return None
    for day_state in anchor_state.values():
        if not isinstance(day_state, dict):
            continue
        for slot, entry in day_state.items():
            if (
                isinstance(entry, dict)
                and entry.get("status") == "recording"
                and entry.get("token") == token
            ):
                return day_state, slot, entry
    return None


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
            entry = state.get("anchors", {}).get(name, {}).get(date_key, {}).get(slot)
            return _anchor_session_entry_consumed(entry)
        finally:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def reserve_anchor_session(
    path: Path,
    anchor: str,
    now: datetime | None = None,
    *,
    title: str = "",
) -> str | None:
    key = _anchor_session_key(anchor, now)
    if not key:
        return None
    name, date_key, slot = key
    path.parent.mkdir(parents=True, exist_ok=True)
    reserved_at = _beijing_time(now).isoformat(timespec="seconds")
    token = uuid4().hex
    with path.open("a+", encoding="utf-8") as file:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        try:
            state = _read_limit_state(file)
            anchors = state.setdefault("anchors", {})
            anchor_state = anchors.setdefault(name, {})
            day_state = anchor_state.setdefault(date_key, {})
            if _anchor_session_entry_consumed(day_state.get(slot)):
                return None
            day_state[slot] = {
                "pid": os.getpid(),
                "reserved_at": reserved_at,
                "status": "recording",
                "title": title,
                "token": token,
            }
            _write_limit_state(file, state)
            return token
        finally:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def mark_anchor_session_consumed(
    path: Path,
    anchor: str,
    now: datetime | None = None,
    *,
    title: str = "",
    reservation_token: str | None = None,
) -> None:
    key = _anchor_session_key(anchor, now)
    name = anchor.strip() or "unknown"
    if not key and not reservation_token:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    recorded_at = _beijing_time(now).isoformat(timespec="seconds")
    with path.open("a+", encoding="utf-8") as file:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        try:
            state = _read_limit_state(file)
            anchors = state.setdefault("anchors", {})
            anchor_state = anchors.setdefault(name, {})
            if reservation_token:
                reservation = _find_recording_reservation(state, name, reservation_token)
                if reservation:
                    _day_state, _slot, entry = reservation
                    entry.update(
                        {
                            "recorded_at": recorded_at,
                            "status": "recorded",
                            "title": title,
                        }
                    )
                    _write_limit_state(file, state)
                    return
            if not key:
                return
            _name, date_key, slot = key
            day_state = anchor_state.setdefault(date_key, {})
            entry = day_state.get(slot)
            if isinstance(entry, dict):
                recorded_entry = dict(entry)
                recorded_entry.update(
                    {
                        "recorded_at": recorded_at,
                        "status": "recorded",
                        "title": title,
                    }
                )
            else:
                recorded_entry = {
                    "recorded_at": recorded_at,
                    "status": "recorded",
                    "title": title,
                }
            day_state[slot] = recorded_entry
            _write_limit_state(file, state)
        finally:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def release_anchor_session_reservation(path: Path, anchor: str, token: str) -> bool:
    if not token or not path.exists():
        return False
    name = anchor.strip() or "unknown"
    with path.open("a+", encoding="utf-8") as file:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        try:
            state = _read_limit_state(file)
            reservation = _find_recording_reservation(state, name, token)
            if not reservation:
                return False
            day_state, slot, _entry = reservation
            del day_state[slot]
            _write_limit_state(file, state)
            return True
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
