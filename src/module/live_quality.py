from dataclasses import dataclass
from math import inf
from re import compile


@dataclass(frozen=True)
class LiveStreamSelection:
    quality: str
    flv_url: str
    play_url: str


QUALITY_RANK = {
    "ld": 0,
    "sd": 10,
    "hd": 20,
    "full_hd": 30,
    "uhd": 40,
    "origin": 50,
    "orig": 50,
}
QUALITY_PATTERN = compile(r"^(full_hd|origin|orig|uhd|hd|sd|ld)(\d*)$")


def select_lowest_live_stream(
    flv_items: dict[str, str],
    hls_items: dict[str, str] | None = None,
) -> LiveStreamSelection:
    if not flv_items:
        raise ValueError("No live stream URLs are available.")
    hls_items = hls_items or {}
    quality, flv_url = min(
        enumerate(flv_items.items()),
        key=lambda item: _quality_sort_key(item[1][0], item[0]),
    )[1]
    return LiveStreamSelection(
        quality=quality,
        flv_url=flv_url,
        play_url=hls_items.get(quality) or flv_url,
    )


def _quality_sort_key(quality: str, index: int) -> tuple[float, int]:
    normalized = quality.lower().replace("-", "_")
    if match := QUALITY_PATTERN.match(normalized):
        marker, suffix = match.groups()
        suffix_number = int(suffix or 0)
        return QUALITY_RANK[marker], -suffix_number
    return inf, -index
