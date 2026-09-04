"""Apple Music chart fetcher (official free RSS feed + iTunes lookup for album)."""

from common import ChartSong, get_with_retry, now_iso

PLATFORM = "apple_music"
CHARTS = {"us": "欧美/主榜", "cn": "国语", "hk": "粤语"}
_RSS_URL_TMPL = "https://rss.applemarketingtools.com/api/v2/{storefront}/music/most-played/{limit}/songs.json"
_LOOKUP_URL = "https://itunes.apple.com/lookup"


def fetch_chart(chart_key: str, limit: int = 50) -> list[ChartSong]:
    if chart_key not in CHARTS:
        raise ValueError(f"unknown apple_music storefront: {chart_key}")
    chart_name = CHARTS[chart_key]

    url = _RSS_URL_TMPL.format(storefront=chart_key, limit=limit)
    resp = get_with_retry(url, timeout=10)
    results = resp.json()["feed"]["results"]

    albums = _lookup_albums([r["id"] for r in results], chart_key)

    songs = []
    for rank, r in enumerate(results, start=1):
        songs.append(ChartSong(
            platform=PLATFORM,
            chart=chart_name,
            rank=rank,
            title=r["name"],
            artist=r["artistName"],
            album=albums.get(r["id"]),
            play_count=None,
            comment_count=None,
            popularity=None,
            source_url=r["url"],
            fetched_at=now_iso(),
        ))
    return songs


def _lookup_albums(track_ids: list, storefront: str) -> dict:
    if not track_ids:
        return {}
    resp = get_with_retry(_LOOKUP_URL, params={
        "id": ",".join(track_ids), "country": storefront,
    }, timeout=10)
    albums = {}
    for item in resp.json().get("results", []):
        track_id = str(item.get("trackId"))
        if track_id and track_id != "None":
            albums[track_id] = item.get("collectionName")
    return albums
