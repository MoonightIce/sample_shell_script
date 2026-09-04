# music_charts/scripts/fetchers/qqmusic.py
"""QQ Music (QQ音乐) chart fetcher."""

from common import ChartSong, get_with_retry, now_iso

PLATFORM = "qqmusic"
CHARTS = {
    "index": "流行指数榜",
    "neidi": "内地榜",
    "hongkong": "香港地区榜",
    "oumei": "欧美榜",
}
_TOPIDS = {"index": 4, "neidi": 5, "hongkong": 59, "oumei": 3}
_TOPLIST_URL = "https://c.y.qq.com/v8/fcg-bin/fcg_v8_toplist_cp.fcg"
_HEADERS = {"Referer": "https://y.qq.com/"}


def fetch_chart(chart_key: str, limit: int = 50) -> list[ChartSong]:
    if chart_key not in _TOPIDS:
        raise ValueError(f"unknown qqmusic chart: {chart_key}")
    chart_name = CHARTS[chart_key]

    resp = get_with_retry(_TOPLIST_URL, headers=_HEADERS, params={
        "format": "json",
        "topid": _TOPIDS[chart_key],
        "type": "top",
        "page": "detail",
        "tpl": 3,
        "needNewCode": 1,
    }, timeout=10)
    songlist = resp.json()["songlist"][:limit]

    songs = []
    for rank, item in enumerate(songlist, start=1):
        data = item["data"]
        popularity = float(item["cur_count"]) if chart_key == "index" else None
        songs.append(ChartSong(
            platform=PLATFORM,
            chart=chart_name,
            rank=rank,
            title=data["songname"],
            artist="/".join(s["name"] for s in data["singer"]),
            album=data.get("albumname") or None,
            play_count=popularity,
            comment_count=None,
            popularity=popularity,
            source_url=f"https://y.qq.com/n/ryqq/songDetail/{data['songmid']}",
            fetched_at=now_iso(),
        ))
    return songs
