from __future__ import annotations

import argparse
import random
import traceback
from asyncio import create_subprocess_exec, run, sleep
from asyncio.subprocess import PIPE
from datetime import datetime
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.application.TikTokDownloader import TikTokDownloader
from src.application.main_terminal import TikTok
from src.module.live_quality import select_lowest_live_stream
from src.module.live_watch import (
    BEIJING,
    anchor_session_consumed,
    build_ffmpeg_live_command,
    mark_anchor_session_consumed,
    release_anchor_session_reservation,
    reserve_anchor_session,
    seconds_until_next_anchor_session,
    seconds_until_monitor_window,
    should_monitor,
)


async def fetch_live_tasks(terminal: TikTok, url: str) -> list[tuple[dict, str, str]]:
    ids = await terminal.links.run(url, type_="live")
    if not ids:
        raise RuntimeError("未能从链接中提取直播间 ID。")

    live_data = [await terminal.get_live_data(i) for i in ids]
    live_data = await terminal.extractor.run(live_data, None, "live")
    tasks = []
    for item in live_data:
        if not item:
            continue
        if item.get("status") == 4:
            continue
        selected = select_lowest_live_stream(
            item.get("flv_pull_url", {}),
            item.get("hls_pull_url_map", {}),
        )
        tasks.append((item, selected.flv_url, selected.play_url))
    return tasks


def live_task_key(item: dict) -> str:
    for key in ("room_id", "web_rid", "id", "owner_user_id"):
        value = item.get(key)
        if value:
            return f"{key}:{value}"
    return f"{item.get('nickname', '')}:{item.get('title', '')}"


async def record_task(
    app: TikTokDownloader,
    terminal: TikTok,
    task: tuple[dict, str, str],
    error_log: Path,
    max_retries: int,
    dry_run: bool,
) -> bool:
    item, flv_url, play_url = task
    title = item.get("title", "")
    nickname = item.get("nickname", "")
    terminal.console.print("直播标题:", title)
    terminal.console.print("主播昵称:", nickname)
    selected = select_lowest_live_stream(
        item.get("flv_pull_url", {}),
        item.get("hls_pull_url_map", {}),
    )
    terminal.console.print("自动选择最低清晰度:", selected.quality)
    if dry_run:
        terminal.console.print("dry-run 模式，不启动 ffmpeg。")
        return True

    output = live_output_path(app, title, nickname)
    output.parent.mkdir(parents=True, exist_ok=True)
    attempts = max_retries + 1
    for attempt in range(1, attempts + 1):
        command = build_ffmpeg_live_command(
            app.parameter.ffmpeg.path,
            play_url or flv_url,
            output,
            app.parameter.headers["User-Agent"],
            app.parameter.proxy,
        )
        terminal.console.print(f"开始录制，第 {attempt}/{attempts} 次尝试:", output)
        process = await create_subprocess_exec(
            *command,
            stdout=PIPE,
            stderr=PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode == 0:
            terminal.console.print("录制进程已正常结束。")
            return True
        await write_error(
            error_log,
            (
                f"ffmpeg exited with code {process.returncode}\n"
                f"title={title}\n"
                f"nickname={nickname}\n"
                f"output={output}\n"
                f"stdout={stdout.decode(errors='replace')}\n"
                f"stderr={stderr.decode(errors='replace')}\n"
            ),
        )
        if attempt < attempts:
            terminal.console.print("拉流失败，准备重试一次。")
    terminal.console.print(f"录制失败，详细错误已写入: {error_log}")
    return False


def live_output_path(app: TikTokDownloader, title: str, nickname: str) -> Path:
    root = app.parameter.root / "Live"
    split = app.parameter.split
    now = datetime.now(BEIJING).strftime("%Y-%m-%d %H.%M.%S")
    name = app.parameter.CLEANER.filter_name(
        f"{title}{split}{nickname}{split}{now}.mp4",
        f"live{split}{now}.mp4",
    )
    return root / name


async def write_error(path: Path, detail: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(BEIJING).isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as file:
        file.write(f"\n[{timestamp}]\n{detail}\n")


async def watch_live(
    url: str,
    interval_seconds: int,
    interval_min_seconds: int | None,
    interval_max_seconds: int | None,
    max_retries: int,
    dry_run: bool,
    anchor_session_limit_file: Path | None,
) -> None:
    def next_interval() -> int:
        if interval_min_seconds is None or interval_max_seconds is None:
            return interval_seconds
        low = min(interval_min_seconds, interval_max_seconds)
        high = max(interval_min_seconds, interval_max_seconds)
        return random.randint(low, high)

    async with TikTokDownloader() as app:
        app.check_config()
        await app.check_settings(False)
        terminal = TikTok(app.parameter, app.database)
        error_log = app.parameter.root / "Live" / "watch_douyin_live_errors.log"
        recorded_live_keys: set[str] = set()
        offline_checks = 0
        while True:
            if wait_seconds := seconds_until_monitor_window():
                terminal.console.print(
                    f"当前不在北京时间 08:00-24:00 监测窗口，"
                    f"{wait_seconds} 秒后继续。"
                )
                await sleep(wait_seconds)
                continue
            if not should_monitor():
                await sleep(next_interval())
                continue
            try:
                tasks = await fetch_live_tasks(terminal, url)
                if not tasks:
                    terminal.console.print("当前未开播。")
                    offline_checks += 1
                    if offline_checks >= 2:
                        recorded_live_keys.clear()
                    if dry_run:
                        return
                    await sleep(next_interval())
                    continue
                offline_checks = 0
                slept_until_next_session = False
                for task in tasks:
                    item = task[0]
                    nickname = item.get("nickname", "")
                    title = item.get("title", "")
                    key = live_task_key(item)
                    if key in recorded_live_keys:
                        terminal.console.print("当前直播场次已录制过，等待下播后再继续监听。")
                        continue
                    reservation_token: str | None = None
                    if anchor_session_limit_file:
                        if dry_run:
                            session_unavailable = anchor_session_consumed(
                                anchor_session_limit_file,
                                nickname,
                            )
                        else:
                            reservation_token = reserve_anchor_session(
                                anchor_session_limit_file,
                                nickname,
                                title=title,
                            )
                            session_unavailable = reservation_token is None
                        if session_unavailable:
                            sleep_seconds = seconds_until_next_anchor_session()
                            terminal.console.print(
                                f"主播 {nickname or 'unknown'} 当前时段已被占用或已录制过，"
                                f"{sleep_seconds} 秒后进入下一个监听时段。"
                            )
                            await sleep(sleep_seconds)
                            slept_until_next_session = True
                            break
                    try:
                        recorded = await record_task(
                            app,
                            terminal,
                            task,
                            error_log,
                            max_retries,
                            dry_run,
                        )
                    except Exception:
                        if anchor_session_limit_file and reservation_token:
                            release_anchor_session_reservation(
                                anchor_session_limit_file,
                                nickname,
                                reservation_token,
                            )
                        raise
                    if recorded:
                        if anchor_session_limit_file and not dry_run:
                            mark_anchor_session_consumed(
                                anchor_session_limit_file,
                                nickname,
                                title=title,
                                reservation_token=reservation_token,
                            )
                            sleep_seconds = seconds_until_next_anchor_session()
                            terminal.console.print(
                                f"主播 {nickname or 'unknown'} 当前时段录制完成，"
                                f"{sleep_seconds} 秒后进入下一个监听时段。"
                            )
                            await sleep(sleep_seconds)
                            slept_until_next_session = True
                        recorded_live_keys.add(key)
                        if slept_until_next_session:
                            break
                    elif anchor_session_limit_file and reservation_token:
                        release_anchor_session_reservation(
                            anchor_session_limit_file,
                            nickname,
                            reservation_token,
                        )
                if slept_until_next_session:
                    continue
            except Exception:
                await write_error(error_log, traceback.format_exc())
                terminal.console.print(f"监听检查失败，详细错误已写入: {error_log}")
            if dry_run:
                return
            await sleep(next_interval())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="按北京时间 08:00-24:00 每小时监听抖音直播，开播后自动录制。"
    )
    parser.add_argument("url", help="抖音直播间链接，例如 https://live.douyin.com/123")
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=60 * 60,
        help="固定检查间隔，默认 3600 秒；设置随机区间时仅作为兼容兜底。",
    )
    parser.add_argument(
        "--interval-min-seconds",
        type=int,
        default=None,
        help="随机检查间隔下限秒数；需和 --interval-max-seconds 一起使用。",
    )
    parser.add_argument(
        "--interval-max-seconds",
        type=int,
        default=None,
        help="随机检查间隔上限秒数；需和 --interval-min-seconds 一起使用。",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=1,
        help="ffmpeg 拉流失败后的重试次数，默认 1。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只检查直播状态和清晰度，不启动 ffmpeg。",
    )
    parser.add_argument(
        "--anchor-session-limit-file",
        type=Path,
        default=None,
        help="按主播和北京时间时段限制每日录制次数的状态文件。",
    )
    args = parser.parse_args()
    run(
        watch_live(
            args.url,
            args.interval_seconds,
            args.interval_min_seconds,
            args.interval_max_seconds,
            args.max_retries,
            args.dry_run,
            args.anchor_session_limit_file,
        )
    )


if __name__ == "__main__":
    main()
