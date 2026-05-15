from src.module.live_quality import select_lowest_live_stream


def test_select_lowest_live_stream_prefers_lowest_known_quality():
    flv = {
        "FULL_HD1": "flv-full",
        "HD1": "flv-hd",
        "SD1": "flv-sd",
        "LD1": "flv-ld",
    }
    hls = {
        "FULL_HD1": "hls-full",
        "HD1": "hls-hd",
        "SD1": "hls-sd",
        "LD1": "hls-ld",
    }

    selected = select_lowest_live_stream(flv, hls)

    assert selected.quality == "LD1"
    assert selected.flv_url == "flv-ld"
    assert selected.play_url == "hls-ld"


def test_select_lowest_live_stream_falls_back_to_last_unknown_quality():
    flv = {
        "QUALITY_A": "flv-a",
        "QUALITY_B": "flv-b",
    }

    selected = select_lowest_live_stream(flv, {})

    assert selected.quality == "QUALITY_B"
    assert selected.flv_url == "flv-b"
    assert selected.play_url == "flv-b"


def test_select_lowest_live_stream_uses_numeric_suffix_within_same_quality():
    flv = {
        "SD1": "flv-sd1",
        "SD2": "flv-sd2",
        "HD1": "flv-hd1",
    }

    selected = select_lowest_live_stream(flv, {})

    assert selected.quality == "SD2"
    assert selected.flv_url == "flv-sd2"


def test_select_lowest_live_stream_matches_full_quality_prefixes():
    flv = {
        "FULL_HD1": "flv-full-hd",
        "HD1": "flv-hd",
    }

    selected = select_lowest_live_stream(flv, {})

    assert selected.quality == "HD1"
