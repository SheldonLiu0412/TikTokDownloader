from __future__ import annotations

import argparse
from asyncio import run
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.application.TikTokDownloader import TikTokDownloader
from src.application.main_terminal import TikTok
from src.module.live_quality import select_lowest_live_stream


async def record_live(url: str) -> None:
    async with TikTokDownloader() as app:
        app.check_config()
        await app.check_settings(False)
        terminal = TikTok(app.parameter, app.database)
        ids = await terminal.links.run(url, type_="live")
        if not ids:
            raise SystemExit("未能从链接中提取直播间 ID。")

        live_data = [await terminal.get_live_data(i) for i in ids]
        live_data = await terminal.extractor.run(live_data, None, "live")
        tasks = []
        for item in live_data:
            if not item:
                continue
            if item.get("status") == 4:
                terminal.console.print("当前直播已结束，跳过。")
                continue
            selected = select_lowest_live_stream(
                item.get("flv_pull_url", {}),
                item.get("hls_pull_url_map", {}),
            )
            terminal.console.print("直播标题:", item.get("title", ""))
            terminal.console.print("主播昵称:", item.get("nickname", ""))
            terminal.console.print("自动选择最低清晰度:", selected.quality)
            tasks.append((item, selected.flv_url, selected.play_url))

        if not tasks:
            raise SystemExit("没有可录制的直播任务。")
        await terminal.downloader.run(tasks, type_="live")


def main() -> None:
    parser = argparse.ArgumentParser(description="一键录制抖音直播到 settings.root/Live")
    parser.add_argument("url", help="抖音直播间链接，例如 https://live.douyin.com/123")
    args = parser.parse_args()
    run(record_live(args.url))


if __name__ == "__main__":
    main()
