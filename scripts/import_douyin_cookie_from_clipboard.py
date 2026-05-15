from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "Volume" / "settings.json"


def read_clipboard() -> str:
    return subprocess.check_output(["pbpaste"], text=True)


def main() -> None:
    raw = read_clipboard().strip()
    exported = json.loads(raw)
    if not isinstance(exported, list):
        raise SystemExit("Clipboard content must be a JSON cookie list.")

    cookie = {
        item["name"]: item["value"]
        for item in exported
        if isinstance(item, dict) and item.get("name") and "value" in item
    }
    if "odin_tt" not in cookie or "sessionid_ss" not in cookie:
        raise SystemExit("Cookie is missing odin_tt or sessionid_ss.")

    SETTINGS.parent.mkdir(exist_ok=True)
    data = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
    data["cookie"] = cookie
    data["cookie_tiktok"] = ""
    data["douyin_platform"] = True
    data["tiktok_platform"] = False

    SETTINGS.write_text(
        json.dumps(data, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    print(f"Saved {len(cookie)} Douyin cookies to {SETTINGS}")


if __name__ == "__main__":
    main()
