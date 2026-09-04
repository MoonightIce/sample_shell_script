"""Billboard Hot 100 fetcher (HTML scrape, no official free API)."""

from bs4 import BeautifulSoup

from common import ChartSong, get_with_retry, now_iso

PLATFORM = "billboard"
CHARTS = {"hot100": "Hot 100"}
_URL = "https://www.billboard.com/charts/hot-100/"


def fetch_chart(chart_key: str, limit: int = 50) -> list[ChartSong]:
    if chart_key not in CHARTS:
        raise ValueError(f"unknown billboard chart: {chart_key}")
    resp = get_with_retry(_URL, timeout=15)
    return parse_hot100(resp.text, limit, CHARTS[chart_key])


def parse_hot100(html: str, limit: int, chart_name: str) -> list[ChartSong]:
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("ul.o-chart-results-list-row")
    if not rows:
        raise RuntimeError("billboard hot-100 页面结构解析失败,可能改版")

    songs = []
    for row in rows[:limit]:
        rank_el = row.select_one("span.c-label.a-font-basic")
        title_el = row.select_one("h3.c-title")
        artist_el = row.select_one("span.c-label.a-no-trucate")
        if rank_el is None or title_el is None or artist_el is None:
            continue
        artist_link = artist_el.find("a")
        artist = artist_link.get_text(strip=True) if artist_link else artist_el.get_text(strip=True)
        songs.append(ChartSong(
            platform=PLATFORM,
            chart=chart_name,
            rank=int(rank_el.get_text(strip=True)),
            title=title_el.get_text(strip=True),
            artist=artist,
            album=None,
            play_count=None,
            comment_count=None,
            popularity=None,
            source_url=_URL,
            fetched_at=now_iso(),
        ))
    return songs
