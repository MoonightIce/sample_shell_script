# music_charts/scripts/common.py
"""Shared schema, JSON writer, and HTTP helper for all chart fetchers."""

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

_CN_TZ = timezone(timedelta(hours=8))

COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


@dataclass
class ChartSong:
    platform: str
    chart: str
    rank: int
    title: str
    artist: str
    album: Optional[str]
    play_count: Optional[int]
    comment_count: Optional[int]
    popularity: Optional[float]
    source_url: str
    fetched_at: str


def now_iso() -> str:
    return datetime.now(_CN_TZ).isoformat(timespec="seconds")


def get_with_retry(url: str, **kwargs) -> requests.Response:
    headers = {**COMMON_HEADERS, **kwargs.pop("headers", {})}
    try:
        resp = requests.get(url, headers=headers, **kwargs)
        resp.raise_for_status()
        return resp
    except Exception:
        time.sleep(2)
        resp = requests.get(url, headers=headers, **kwargs)
        resp.raise_for_status()
        return resp


def write_chart_json(
    songs: list[ChartSong], platform: str, chart_key: str, output_dir: Path
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(_CN_TZ).strftime("%Y%m%d")
    path = output_dir / f"{platform}_{chart_key}_{date_str}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump([asdict(s) for s in songs], f, ensure_ascii=False, indent=2)
    return path
