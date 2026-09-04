# music_charts/scripts/fetchers/netease.py
"""NetEase Cloud Music (网易云音乐) chart fetcher."""

import json
import time

from common import ChartSong, get_with_retry, now_iso

PLATFORM = "netease"
CHARTS = {"hot": "热歌榜", "oumei": "欧美热歌榜"}
_PLAYLIST_IDS = {"hot": 3778678, "oumei": 2809513713}
_DETAIL_URL = "https://music.163.com/api/v6/playlist/detail"
_SONG_DETAIL_URL = "https://music.163.com/api/v3/song/detail"
_COMMENT_URL_TMPL = "https://music.163.com/api/v1/resource/comments/R_SO_4_{song_id}"
_BATCH_SIZE = 100


def fetch_chart(chart_key: str, limit: int = 50) -> list[ChartSong]:
    if chart_key not in _PLAYLIST_IDS:
        raise ValueError(f"unknown netease chart: {chart_key}")
    chart_name = CHARTS[chart_key]

    track_ids = _fetch_track_ids(_PLAYLIST_IDS[chart_key])
    details = _fetch_song_details(track_ids)

    songs = []
    for rank, song_id in enumerate(track_ids, start=1):
        detail = details.get(song_id)
        if detail is None:
            continue
        comment_count = None
        if rank <= limit:
            comment_count = _fetch_comment_count(song_id)
            time.sleep(0.3)
        songs.append(ChartSong(
            platform=PLATFORM,
            chart=chart_name,
            rank=rank,
            title=detail["name"],
            artist="/".join(a["name"] for a in detail["ar"]),
            album=detail["al"]["name"],
            play_count=None,
            comment_count=comment_count,
            popularity=detail.get("pop"),
            source_url=f"https://music.163.com/#/song?id={song_id}",
            fetched_at=now_iso(),
        ))
    return songs


def _fetch_track_ids(playlist_id: int) -> list[int]:
    resp = get_with_retry(_DETAIL_URL, params={"id": playlist_id}, timeout=10)
    return [t["id"] for t in resp.json()["playlist"]["trackIds"]]


def _fetch_song_details(song_ids: list[int]) -> dict:
    details = {}
    for i in range(0, len(song_ids), _BATCH_SIZE):
        batch = song_ids[i:i + _BATCH_SIZE]
        c_param = json.dumps([{"id": sid} for sid in batch])
        resp = get_with_retry(_SONG_DETAIL_URL, params={"c": c_param}, timeout=10)
        for song in resp.json()["songs"]:
            details[song["id"]] = song
    return details


def _fetch_comment_count(song_id: int):
    url = _COMMENT_URL_TMPL.format(song_id=song_id)
    resp = get_with_retry(url, params={"limit": 1, "offset": 0}, timeout=10)
    return resp.json().get("total")
