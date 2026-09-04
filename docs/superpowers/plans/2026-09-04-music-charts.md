# Music Charts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `music_charts/`, a CLI tool that fetches song-level chart data (title,
artist, album, plus whatever play-count/comment-count/popularity each platform
actually exposes) from NetEase Cloud Music, QQ Music, Apple Music, and Billboard, and
writes one JSON file per platform+chart per run.

**Architecture:** One `fetch_chart(chart_key, limit) -> list[ChartSong]` function per
platform, implemented in its own module under `music_charts/scripts/fetchers/`. A
shared `common.py` defines the `ChartSong` record, the JSON writer, and an HTTP-GET
helper with a single retry. A CLI (`fetch_charts.py`) enumerates the requested
platforms' `CHARTS` dicts, calls each fetcher, writes JSON, and never lets one
platform/chart failure abort the others.

**Tech Stack:** Python 3, `requests`, `beautifulsoup4` (Billboard HTML parsing only).
Standard library `unittest` + `unittest.mock` for tests (no network calls in tests).

**Spec:** [docs/superpowers/specs/2026-09-03-music-charts-design.md](../specs/2026-09-03-music-charts-design.md)

## Global Constraints

- Platforms in scope: `netease`, `qqmusic`, `apple_music`, `billboard`. Spotify is
  explicitly out of scope.
- Chart identifiers (exact, verified against live endpoints on 2026-09-03/04):
  - NetEase: `hot` = playlist id `3778678` ("热歌榜"), `oumei` = playlist id
    `2809513713` ("欧美热歌榜"). No Cantonese chart exists — do not add one.
  - QQ Music: `index` = topid `4` ("流行指数榜"), `neidi` = topid `5` ("内地榜"),
    `hongkong` = topid `59` ("香港地区榜"), `oumei` = topid `3` ("欧美榜").
  - Apple Music: storefront `us` ("欧美/主榜"), `cn` ("国语"), `hk` ("粤语").
  - Billboard: `hot100` only.
- Field availability (never invent values beyond what's listed — set the field to
  `None` when a platform/chart doesn't support it):
  - NetEase: `comment_count` real; `play_count` always `None`; `popularity` = the
    song detail API's `pop` field (0-100).
  - QQ Music: `comment_count` always `None`. `popularity` and `play_count` both
    equal `cur_count` from the toplist response, but **only for `index`** — `neidi`,
    `hongkong`, `oumei` always get `None` for both (their `cur_count` is a constant
    placeholder, not real data).
  - Apple Music: `play_count`, `comment_count`, `popularity` always `None`. `album`
    comes from a separate iTunes lookup call (the RSS feed doesn't include it).
  - Billboard: `album`, `play_count`, `comment_count`, `popularity` always `None`.
- Every outbound HTTP GET goes through `common.get_with_retry` (one retry after a 2s
  sleep, then raise) — no fetcher calls `requests.get` directly.
- Every fetcher sets a browser `User-Agent` (`common.COMMON_HEADERS`).
- Output file naming: `music_charts/output/<platform>_<chart_key>_<YYYYMMDD>.json`.
- `--limit` default is `50` everywhere it applies.
- A platform/chart fetch failure must never abort the whole CLI run — catch, log,
  continue, and reflect it in the final summary printed to stdout.

---

## File Structure

```
music_charts/
  SKILL.md                        # usage doc (Task 6)
  requirements.txt                # requests, beautifulsoup4 (Task 6)
  output/                         # run artifacts, gitignored (Task 6)
  scripts/
    common.py                     # ChartSong, write_chart_json, get_with_retry (Task 1)
    fetch_charts.py                # CLI entry point (Task 6)
    fetchers/
      __init__.py                  # empty (Task 1)
      netease.py                    # Task 2
      qqmusic.py                     # Task 3
      apple_music.py                  # Task 4
      billboard.py                     # Task 5
  tests/
    test_common.py                  # Task 1
    test_netease.py                  # Task 2
    test_qqmusic.py                   # Task 3
    test_apple_music.py                # Task 4
    test_billboard.py                   # Task 5
```

All tests run from the repo root with:
```bash
cd music_charts && python3 -m pytest tests/ -v
```
(`pytest` must be available — if not already installed, `pip install pytest` once;
this repo doesn't otherwise use a test runner, so this is a new per-project dependency
listed in `requirements.txt`, see Task 6.)

Because `scripts/` isn't a package importable via `python3 -m pytest` from repo root by
default, `tests/` files add `music_charts/scripts` to `sys.path` at the top (shown in
each test file below) rather than relying on package installation.

---

### Task 1: Shared schema, JSON writer, HTTP retry helper

**Files:**
- Create: `music_charts/scripts/common.py`
- Create: `music_charts/scripts/fetchers/__init__.py` (empty file)
- Test: `music_charts/tests/test_common.py`

**Interfaces:**
- Produces:
  - `ChartSong` — a `dataclasses.dataclass` with fields `platform: str`, `chart: str`,
    `rank: int`, `title: str`, `artist: str`, `album: Optional[str]`,
    `play_count: Optional[int]`, `comment_count: Optional[int]`,
    `popularity: Optional[float]`, `source_url: str`, `fetched_at: str`.
  - `COMMON_HEADERS: dict` — `{"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}`.
  - `now_iso() -> str` — current time in `Asia/Shanghai` (UTC+8), ISO 8601, seconds
    precision.
  - `get_with_retry(url: str, **kwargs) -> requests.Response` — GETs `url` with
    `COMMON_HEADERS` merged into any `headers` kwarg the caller passes, raises via
    `raise_for_status()`; on any `requests.RequestException` or a raised HTTP error,
    sleeps 2s and retries exactly once, then propagates the second attempt's
    exception if it also fails.
  - `write_chart_json(songs: list[ChartSong], platform: str, chart_key: str, output_dir: pathlib.Path) -> pathlib.Path`
    — creates `output_dir` if needed, writes
    `<platform>_<chart_key>_<YYYYMMDD>.json` (today's date, `Asia/Shanghai`) as a
    pretty-printed JSON array of the songs' fields, returns the path written.

- [ ] **Step 1: Write the failing tests**

```python
# music_charts/tests/test_common.py
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from common import ChartSong, now_iso, get_with_retry, write_chart_json  # noqa: E402


class TestNowIso(unittest.TestCase):
    def test_returns_iso_string_with_timezone_offset(self):
        result = now_iso()
        self.assertIn("+08:00", result)


class TestGetWithRetry(unittest.TestCase):
    @patch("common.requests.get")
    def test_succeeds_on_first_try(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        result = get_with_retry("https://example.com")

        self.assertIs(result, mock_resp)
        self.assertEqual(mock_get.call_count, 1)

    @patch("common.time.sleep")
    @patch("common.requests.get")
    def test_retries_once_then_succeeds(self, mock_get, mock_sleep):
        failing_resp = MagicMock()
        failing_resp.raise_for_status.side_effect = Exception("boom")
        ok_resp = MagicMock()
        ok_resp.raise_for_status.return_value = None
        mock_get.side_effect = [failing_resp, ok_resp]

        result = get_with_retry("https://example.com")

        self.assertIs(result, ok_resp)
        self.assertEqual(mock_get.call_count, 2)
        mock_sleep.assert_called_once_with(2)

    @patch("common.time.sleep")
    @patch("common.requests.get")
    def test_raises_after_second_failure(self, mock_get, mock_sleep):
        failing_resp = MagicMock()
        failing_resp.raise_for_status.side_effect = Exception("boom")
        mock_get.return_value = failing_resp

        with self.assertRaises(Exception):
            get_with_retry("https://example.com")

        self.assertEqual(mock_get.call_count, 2)


class TestWriteChartJson(unittest.TestCase):
    def test_writes_expected_filename_and_content(self):
        import tempfile

        songs = [
            ChartSong(
                platform="netease", chart="热歌榜", rank=1, title="歌名",
                artist="歌手", album="专辑", play_count=None,
                comment_count=100, popularity=99.0,
                source_url="https://music.163.com/#/song?id=1",
                fetched_at="2026-09-03T10:00:00+08:00",
            )
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "output"
            path = write_chart_json(songs, "netease", "hot", output_dir)

            self.assertTrue(path.name.startswith("netease_hot_"))
            self.assertTrue(path.name.endswith(".json"))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["title"], "歌名")
            self.assertEqual(data[0]["comment_count"], 100)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd music_charts && python3 -m pytest tests/test_common.py -v`
Expected: FAIL / ERROR — `common.py` does not exist yet, so the import at the top of
the test file fails.

- [ ] **Step 3: Write `common.py`**

```python
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
```

- [ ] **Step 4: Create the empty fetchers package marker**

```bash
mkdir -p music_charts/scripts/fetchers
touch music_charts/scripts/fetchers/__init__.py
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd music_charts && python3 -m pytest tests/test_common.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add music_charts/scripts/common.py music_charts/scripts/fetchers/__init__.py music_charts/tests/test_common.py
git commit -m "music_charts: add shared ChartSong schema, JSON writer, HTTP retry helper"
```

---

### Task 2: NetEase Cloud Music fetcher

**Files:**
- Create: `music_charts/scripts/fetchers/netease.py`
- Test: `music_charts/tests/test_netease.py`

**Interfaces:**
- Consumes: `common.ChartSong`, `common.now_iso`, `common.get_with_retry`.
- Produces:
  - `PLATFORM = "netease"`
  - `CHARTS: dict[str, str]` = `{"hot": "热歌榜", "oumei": "欧美热歌榜"}`
  - `fetch_chart(chart_key: str, limit: int = 50) -> list[common.ChartSong]` — raises
    `ValueError` for an unknown `chart_key`.

- [ ] **Step 1: Write the failing tests**

```python
# music_charts/tests/test_netease.py
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fetchers import netease  # noqa: E402


def _resp(json_data):
    m = MagicMock()
    m.json.return_value = json_data
    return m


class TestNeteaseFetchChart(unittest.TestCase):
    def test_unknown_chart_raises(self):
        with self.assertRaises(ValueError):
            netease.fetch_chart("does-not-exist")

    @patch("fetchers.netease.get_with_retry")
    def test_builds_songs_from_playlist_and_details(self, mock_get):
        playlist_resp = _resp({
            "playlist": {"trackIds": [{"id": 111}, {"id": 222}]}
        })
        detail_resp = _resp({
            "songs": [
                {
                    "id": 111, "name": "歌曲A",
                    "ar": [{"name": "歌手A"}],
                    "al": {"name": "专辑A"},
                    "pop": 100.0,
                },
                {
                    "id": 222, "name": "歌曲B",
                    "ar": [{"name": "歌手B1"}, {"name": "歌手B2"}],
                    "al": {"name": "专辑B"},
                    "pop": 60.0,
                },
            ]
        })
        comment_resp_1 = _resp({"total": 37331})
        comment_resp_2 = _resp({"total": 42})
        mock_get.side_effect = [playlist_resp, detail_resp, comment_resp_1, comment_resp_2]

        songs = netease.fetch_chart("hot", limit=50)

        self.assertEqual(len(songs), 2)
        self.assertEqual(songs[0].rank, 1)
        self.assertEqual(songs[0].title, "歌曲A")
        self.assertEqual(songs[0].artist, "歌手A")
        self.assertEqual(songs[0].album, "专辑A")
        self.assertEqual(songs[0].popularity, 100.0)
        self.assertIsNone(songs[0].play_count)
        self.assertEqual(songs[0].comment_count, 37331)
        self.assertEqual(songs[1].artist, "歌手B1/歌手B2")
        self.assertEqual(songs[1].comment_count, 42)

    @patch("fetchers.netease.get_with_retry")
    def test_comment_count_skipped_beyond_limit(self, mock_get):
        playlist_resp = _resp({
            "playlist": {"trackIds": [{"id": 111}, {"id": 222}]}
        })
        detail_resp = _resp({
            "songs": [
                {"id": 111, "name": "A", "ar": [{"name": "x"}], "al": {"name": "y"}, "pop": 1.0},
                {"id": 222, "name": "B", "ar": [{"name": "x"}], "al": {"name": "y"}, "pop": 1.0},
            ]
        })
        comment_resp_1 = _resp({"total": 5})
        mock_get.side_effect = [playlist_resp, detail_resp, comment_resp_1]

        songs = netease.fetch_chart("hot", limit=1)

        self.assertEqual(songs[0].comment_count, 5)
        self.assertIsNone(songs[1].comment_count)
        self.assertEqual(mock_get.call_count, 3)  # no 3rd comment call for rank 2


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd music_charts && python3 -m pytest tests/test_netease.py -v`
Expected: FAIL — `fetchers.netease` doesn't exist yet.

- [ ] **Step 3: Write `netease.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd music_charts && python3 -m pytest tests/test_netease.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add music_charts/scripts/fetchers/netease.py music_charts/tests/test_netease.py
git commit -m "music_charts: add NetEase Cloud Music chart fetcher"
```

---

### Task 3: QQ Music fetcher

**Files:**
- Create: `music_charts/scripts/fetchers/qqmusic.py`
- Test: `music_charts/tests/test_qqmusic.py`

**Interfaces:**
- Consumes: `common.ChartSong`, `common.now_iso`, `common.get_with_retry`.
- Produces:
  - `PLATFORM = "qqmusic"`
  - `CHARTS: dict[str, str]` = `{"index": "流行指数榜", "neidi": "内地榜", "hongkong": "香港地区榜", "oumei": "欧美榜"}`
  - `fetch_chart(chart_key: str, limit: int = 50) -> list[common.ChartSong]` — raises
    `ValueError` for an unknown `chart_key`.

- [ ] **Step 1: Write the failing tests**

```python
# music_charts/tests/test_qqmusic.py
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fetchers import qqmusic  # noqa: E402


def _resp(json_data):
    m = MagicMock()
    m.json.return_value = json_data
    return m


_SONGLIST = [
    {
        "cur_count": "143759",
        "data": {
            "songname": "LEMONADE", "albumname": "LEMONADE - The 2nd Album",
            "singer": [{"name": "aespa"}], "songmid": "000NVIwc0ezTOD",
        },
    },
    {
        "cur_count": "1",
        "data": {
            "songname": "歌曲B", "albumname": "专辑B",
            "singer": [{"name": "歌手B1"}, {"name": "歌手B2"}],
            "songmid": "abc123",
        },
    },
]


class TestQqmusicFetchChart(unittest.TestCase):
    def test_unknown_chart_raises(self):
        with self.assertRaises(ValueError):
            qqmusic.fetch_chart("does-not-exist")

    @patch("fetchers.qqmusic.get_with_retry")
    def test_index_chart_populates_popularity_and_play_count(self, mock_get):
        mock_get.return_value = _resp({"songlist": _SONGLIST})

        songs = qqmusic.fetch_chart("index", limit=50)

        self.assertEqual(len(songs), 2)
        self.assertEqual(songs[0].rank, 1)
        self.assertEqual(songs[0].title, "LEMONADE")
        self.assertEqual(songs[0].artist, "aespa")
        self.assertEqual(songs[0].album, "LEMONADE - The 2nd Album")
        self.assertEqual(songs[0].popularity, 143759.0)
        self.assertEqual(songs[0].play_count, 143759.0)
        self.assertIsNone(songs[0].comment_count)
        self.assertEqual(songs[1].artist, "歌手B1/歌手B2")

    @patch("fetchers.qqmusic.get_with_retry")
    def test_region_chart_never_populates_popularity(self, mock_get):
        mock_get.return_value = _resp({"songlist": _SONGLIST})

        songs = qqmusic.fetch_chart("neidi", limit=50)

        self.assertIsNone(songs[0].popularity)
        self.assertIsNone(songs[0].play_count)

    @patch("fetchers.qqmusic.get_with_retry")
    def test_limit_truncates_songlist(self, mock_get):
        mock_get.return_value = _resp({"songlist": _SONGLIST})

        songs = qqmusic.fetch_chart("oumei", limit=1)

        self.assertEqual(len(songs), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd music_charts && python3 -m pytest tests/test_qqmusic.py -v`
Expected: FAIL — `fetchers.qqmusic` doesn't exist yet.

- [ ] **Step 3: Write `qqmusic.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd music_charts && python3 -m pytest tests/test_qqmusic.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add music_charts/scripts/fetchers/qqmusic.py music_charts/tests/test_qqmusic.py
git commit -m "music_charts: add QQ Music chart fetcher"
```

---

### Task 4: Apple Music fetcher

**Files:**
- Create: `music_charts/scripts/fetchers/apple_music.py`
- Test: `music_charts/tests/test_apple_music.py`

**Interfaces:**
- Consumes: `common.ChartSong`, `common.now_iso`, `common.get_with_retry`.
- Produces:
  - `PLATFORM = "apple_music"`
  - `CHARTS: dict[str, str]` = `{"us": "欧美/主榜", "cn": "国语", "hk": "粤语"}`
  - `fetch_chart(chart_key: str, limit: int = 50) -> list[common.ChartSong]` — raises
    `ValueError` for an unknown `chart_key`.

- [ ] **Step 1: Write the failing tests**

```python
# music_charts/tests/test_apple_music.py
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fetchers import apple_music  # noqa: E402


def _resp(json_data):
    m = MagicMock()
    m.json.return_value = json_data
    return m


class TestAppleMusicFetchChart(unittest.TestCase):
    def test_unknown_chart_raises(self):
        with self.assertRaises(ValueError):
            apple_music.fetch_chart("does-not-exist")

    @patch("fetchers.apple_music.get_with_retry")
    def test_builds_songs_and_fills_album_from_lookup(self, mock_get):
        rss_resp = _resp({
            "feed": {"results": [
                {
                    "id": "1844932150", "name": "Choosin' Texas",
                    "artistName": "Ella Langley",
                    "url": "https://music.apple.com/us/album/choosin-texas/1844932149?i=1844932150",
                },
                {
                    "id": "6796864754", "name": "BbY WOW",
                    "artistName": "KAROL G",
                    "url": "https://music.apple.com/us/album/bby-wow/6796864741?i=6796864754",
                },
            ]}
        })
        lookup_resp = _resp({
            "results": [
                {"trackId": 1844932150, "collectionName": "Choosin' Texas - Single"},
                {"trackId": 6796864754, "collectionName": "NO ME ARREPIENTO DE SENTIR TANTO"},
            ]
        })
        mock_get.side_effect = [rss_resp, lookup_resp]

        songs = apple_music.fetch_chart("us", limit=50)

        self.assertEqual(len(songs), 2)
        self.assertEqual(songs[0].rank, 1)
        self.assertEqual(songs[0].title, "Choosin' Texas")
        self.assertEqual(songs[0].artist, "Ella Langley")
        self.assertEqual(songs[0].album, "Choosin' Texas - Single")
        self.assertIsNone(songs[0].play_count)
        self.assertIsNone(songs[0].comment_count)
        self.assertIsNone(songs[0].popularity)
        self.assertEqual(songs[1].album, "NO ME ARREPIENTO DE SENTIR TANTO")

    @patch("fetchers.apple_music.get_with_retry")
    def test_missing_lookup_entry_leaves_album_none(self, mock_get):
        rss_resp = _resp({
            "feed": {"results": [
                {"id": "1", "name": "T", "artistName": "A", "url": "https://x"},
            ]}
        })
        lookup_resp = _resp({"results": []})
        mock_get.side_effect = [rss_resp, lookup_resp]

        songs = apple_music.fetch_chart("cn", limit=50)

        self.assertIsNone(songs[0].album)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd music_charts && python3 -m pytest tests/test_apple_music.py -v`
Expected: FAIL — `fetchers.apple_music` doesn't exist yet.

- [ ] **Step 3: Write `apple_music.py`**

```python
# music_charts/scripts/fetchers/apple_music.py
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd music_charts && python3 -m pytest tests/test_apple_music.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add music_charts/scripts/fetchers/apple_music.py music_charts/tests/test_apple_music.py
git commit -m "music_charts: add Apple Music chart fetcher"
```

---

### Task 5: Billboard fetcher

**Files:**
- Create: `music_charts/scripts/fetchers/billboard.py`
- Test: `music_charts/tests/test_billboard.py`

**Interfaces:**
- Consumes: `common.ChartSong`, `common.now_iso`, `common.get_with_retry`.
- Produces:
  - `PLATFORM = "billboard"`
  - `CHARTS: dict[str, str]` = `{"hot100": "Hot 100"}`
  - `fetch_chart(chart_key: str, limit: int = 50) -> list[common.ChartSong]` — raises
    `ValueError` for an unknown `chart_key`.
  - `parse_hot100(html: str, limit: int, chart_name: str) -> list[common.ChartSong]`
    — pure parsing function, no network I/O. Raises `RuntimeError` if no chart rows
    are found in `html`.

This is the one fetcher with a real network-dependent smoke test in addition to the
fixture-based unit tests, because HTML structure drift is the actual long-term risk
here (see spec).

- [ ] **Step 1: Write the failing tests**

```python
# music_charts/tests/test_billboard.py
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fetchers import billboard  # noqa: E402

_ROW_TEMPLATE = """
<ul class="o-chart-results-list-row // lrv-a-unstyle-list">
  <li class="o-chart-results-list__item">
    <span class="c-label  a-font-basic u-font-size-33@desktop">
      {rank}
    </span>
  </li>
  <li class="lrv-u-width-100p a-chart-result-item-container">
    <ul>
      <li class="o-chart-results-list__item">
        <h3 id="title-of-a-story" class="c-title  a-font-basic u-letter-spacing-0010">
          {title}
        </h3>
        <span class="c-label a-no-trucate a-font-secondary">
          <a href="https://www.billboard.com/artist/x/">{artist}</a>
        </span>
      </li>
    </ul>
  </li>
</ul>
"""

_SAMPLE_HTML = "<html><body>" + _ROW_TEMPLATE.format(
    rank=1, title="Choosin&#039; Texas", artist="Ella Langley"
) + _ROW_TEMPLATE.format(
    rank=2, title="BbY WOW", artist="KAROL G"
) + "</body></html>"


class TestParseHot100(unittest.TestCase):
    def test_parses_rank_title_artist(self):
        songs = billboard.parse_hot100(_SAMPLE_HTML, limit=50, chart_name="Hot 100")

        self.assertEqual(len(songs), 2)
        self.assertEqual(songs[0].rank, 1)
        self.assertEqual(songs[0].title, "Choosin' Texas")
        self.assertEqual(songs[0].artist, "Ella Langley")
        self.assertEqual(songs[0].platform, "billboard")
        self.assertIsNone(songs[0].album)
        self.assertIsNone(songs[0].play_count)

    def test_limit_truncates(self):
        songs = billboard.parse_hot100(_SAMPLE_HTML, limit=1, chart_name="Hot 100")
        self.assertEqual(len(songs), 1)

    def test_no_rows_raises(self):
        with self.assertRaises(RuntimeError):
            billboard.parse_hot100("<html><body>nothing here</body></html>", limit=50, chart_name="Hot 100")


class TestFetchChart(unittest.TestCase):
    def test_unknown_chart_raises(self):
        with self.assertRaises(ValueError):
            billboard.fetch_chart("does-not-exist")

    @patch("fetchers.billboard.get_with_retry")
    def test_fetch_chart_delegates_to_parse(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = _SAMPLE_HTML
        mock_get.return_value = mock_resp

        songs = billboard.fetch_chart("hot100", limit=50)

        self.assertEqual(len(songs), 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd music_charts && python3 -m pytest tests/test_billboard.py -v`
Expected: FAIL — `fetchers.billboard` doesn't exist yet.

- [ ] **Step 3: Write `billboard.py`**

```python
# music_charts/scripts/fetchers/billboard.py
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd music_charts && python3 -m pytest tests/test_billboard.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add music_charts/scripts/fetchers/billboard.py music_charts/tests/test_billboard.py
git commit -m "music_charts: add Billboard Hot 100 fetcher"
```

---

### Task 6: CLI entry point, packaging, docs, and manual smoke test

**Files:**
- Create: `music_charts/scripts/fetch_charts.py`
- Create: `music_charts/requirements.txt`
- Create: `music_charts/SKILL.md`
- Modify: `/Users/admin/Documents/Github/sample_shell_script/.gitignore` (add
  `music_charts/output/`)
- Test: `music_charts/tests/test_fetch_charts.py`

**Interfaces:**
- Consumes: `fetchers.netease`, `fetchers.qqmusic`, `fetchers.apple_music`,
  `fetchers.billboard` (each exposing `CHARTS` and `fetch_chart`), `common.write_chart_json`.
- Produces: `resolve_platforms(platform_arg: str) -> list[str]` (raises `ValueError`
  on an unknown platform name) and `main(argv=None) -> int` (0 if every platform/chart
  succeeded, 1 if any failed) — both importable for testing.

- [ ] **Step 1: Write the failing tests**

```python
# music_charts/tests/test_fetch_charts.py
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fetch_charts  # noqa: E402


class TestResolvePlatforms(unittest.TestCase):
    def test_all_returns_every_platform(self):
        result = fetch_charts.resolve_platforms("all")
        self.assertEqual(set(result), {"netease", "qqmusic", "apple_music", "billboard"})

    def test_comma_separated_list(self):
        result = fetch_charts.resolve_platforms("netease, qqmusic")
        self.assertEqual(result, ["netease", "qqmusic"])

    def test_unknown_platform_raises(self):
        with self.assertRaises(ValueError):
            fetch_charts.resolve_platforms("spotify")


class TestMain(unittest.TestCase):
    @patch("fetch_charts.write_chart_json")
    @patch("fetch_charts._FETCHERS")
    def test_one_platform_failure_does_not_abort_others(self, mock_fetchers, mock_write):
        import types

        good = types.SimpleNamespace(
            CHARTS={"a": "chart-a"},
            fetch_chart=lambda key, limit: [],
        )
        bad = types.SimpleNamespace(
            CHARTS={"b": "chart-b"},
            fetch_chart=lambda key, limit: (_ for _ in ()).throw(RuntimeError("network down")),
        )
        mock_fetchers.__getitem__.side_effect = lambda k: {"good": good, "bad": bad}[k]
        mock_fetchers.keys.return_value = ["good", "bad"]
        mock_write.return_value = Path("/tmp/fake.json")

        exit_code = fetch_charts.main(["--platform", "good,bad"])

        self.assertEqual(exit_code, 1)
        mock_write.assert_called_once()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd music_charts && python3 -m pytest tests/test_fetch_charts.py -v`
Expected: FAIL — `fetch_charts` module doesn't exist yet.

- [ ] **Step 3: Write `fetch_charts.py`**

```python
# music_charts/scripts/fetch_charts.py
"""CLI: fetch song charts from netease/qqmusic/apple_music/billboard and write JSON."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import write_chart_json  # noqa: E402
from fetchers import apple_music, billboard, netease, qqmusic  # noqa: E402

_FETCHERS = {
    "netease": netease,
    "qqmusic": qqmusic,
    "apple_music": apple_music,
    "billboard": billboard,
}


def resolve_platforms(platform_arg: str) -> list[str]:
    if platform_arg == "all":
        return list(_FETCHERS.keys())
    requested = [p.strip() for p in platform_arg.split(",") if p.strip()]
    unknown = [p for p in requested if p not in _FETCHERS]
    if unknown:
        raise ValueError(f"unknown platform(s): {', '.join(unknown)}")
    return requested


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="采集各音乐平台排行榜歌曲信息")
    parser.add_argument("--platform", default="all", help="逗号分隔的平台列表,或 all")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent.parent / "output"),
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    platforms = resolve_platforms(args.platform)
    output_dir = Path(args.output_dir)

    results = []
    for platform in platforms:
        module = _FETCHERS[platform]
        for chart_key in module.CHARTS:
            try:
                songs = module.fetch_chart(chart_key, args.limit)
                path = write_chart_json(songs, platform, chart_key, output_dir)
                results.append((platform, chart_key, "ok", str(path)))
                print(f"[OK] {platform}/{chart_key} -> {path}")
            except Exception as exc:
                results.append((platform, chart_key, "failed", str(exc)))
                print(f"[FAILED] {platform}/{chart_key}: {exc}")

    print("\n汇总:")
    for platform, chart_key, status, info in results:
        print(f"  {platform}/{chart_key}: {status} ({info})")

    return 0 if all(r[2] == "ok" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd music_charts && python3 -m pytest tests/test_fetch_charts.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Add `requirements.txt`**

```
requests>=2.31
beautifulsoup4>=4.12
pytest>=7.4
```

Write it to `music_charts/requirements.txt`.

- [ ] **Step 6: Add `.gitignore` entry**

Append to the repo-root `.gitignore` (after the existing `music_search_cdp/node_modules/`
line):

```
# music_charts: run output, not committed
music_charts/output/
```

- [ ] **Step 7: Write `SKILL.md`**

```markdown
---
name: music-charts
description: Fetch song-level chart data (title, artist, album, plus whatever play-count/comment-count/popularity each platform exposes) from NetEase Cloud Music, QQ Music, Apple Music, and Billboard, and write one JSON file per platform+chart. Use when the user wants a snapshot of current music charts across platforms.
---

# Music charts snapshot (netease / qqmusic / apple_music / billboard)

## Setup (once)

```bash
cd music_charts && pip install -r requirements.txt
```

## Run

```bash
python3 scripts/fetch_charts.py --platform all
python3 scripts/fetch_charts.py --platform netease,qqmusic
python3 scripts/fetch_charts.py --platform all --limit 20
```

Each platform+chart writes its own file to
`music_charts/output/<platform>_<chart_key>_<YYYYMMDD>.json`. A failure on one
platform/chart is logged and skipped — it never aborts the rest of the run. The
final summary printed to stdout lists every platform/chart's ok/failed status.

## What each platform actually gives you

| Platform | Charts | play_count | comment_count | popularity | album |
|---|---|---|---|---|---|
| netease | hot (热歌榜), oumei (欧美热歌榜) | always null | real | real (`pop` 0-100) | real |
| qqmusic | index (流行指数榜), neidi (内地榜), hongkong (香港地区榜), oumei (欧美榜) | real only for `index`, else null | always null | real only for `index`, else null | real |
| apple_music | us (欧美/主榜), cn (国语), hk (粤语) | always null | always null | always null | real (via iTunes lookup) |
| billboard | hot100 | always null | always null | always null | always null |

NetEase has no Cantonese ("粤语") chart — don't go looking for one, it isn't there.

## Known fragility

- All four platforms' interfaces are unofficial/undocumented except Apple Music's
  RSS feed and the iTunes lookup API. NetEase, QQ Music, and Billboard can change
  their response shape or HTML structure at any time without notice.
- Billboard's `parse_hot100` raises `RuntimeError` (not a silent empty list) if it
  can't find any chart rows — that's your signal the page structure changed and the
  CSS selectors in `fetchers/billboard.py` need updating.
- Run tests before assuming a fetcher is still working:
  `cd music_charts && python3 -m pytest tests/ -v`. The tests use fixture data, not
  live requests, so they verify parsing logic but not whether the real endpoints
  still return that shape — for that, do a real run and eyeball the output JSON.
```

- [ ] **Step 8: Manual smoke test against the real APIs**

Run the full pipeline for real (this is the only way to confirm the still-live
endpoints and page structure actually match what the fetchers expect):

```bash
cd music_charts && pip install -r requirements.txt
python3 scripts/fetch_charts.py --platform all --limit 5
```

Expected: a `[OK] ...` line per platform/chart (9 total: 2 netease + 4 qqmusic + 3
apple_music + 1 billboard), a summary with no `failed` entries, and 10 JSON files in
`music_charts/output/`. Open two or three of them and confirm: `title`/`artist` are
non-empty, `rank` starts at 1 and is contiguous, and the fields documented as "real"
in the SKILL.md table above actually have non-null values.

If any platform fails here, don't just retry — that platform's public interface has
likely changed shape since 2026-09-03/04 verification; open a browser/curl session
against it directly first to see what changed, then fix the corresponding fetcher.

- [ ] **Step 9: Commit**

```bash
git add music_charts/scripts/fetch_charts.py music_charts/requirements.txt music_charts/SKILL.md music_charts/tests/test_fetch_charts.py .gitignore
git commit -m "music_charts: add CLI entry point, requirements, SKILL.md docs"
```

---

## Self-Review Notes

- **Spec coverage:** every platform/chart/field-availability rule in the Global
  Constraints section traces back to a task (netease → Task 2, qqmusic → Task 3,
  apple_music → Task 4, billboard → Task 5, CLI/output/docs → Task 6, shared
  schema/retry → Task 1). The spec's "不做的事" (no scheduling, no history, no
  Spotify, no audio download) are honored by omission — no task adds any of them.
- **Type/interface consistency:** all four fetchers expose the same
  `fetch_chart(chart_key: str, limit: int = 50) -> list[ChartSong]` signature and a
  `CHARTS: dict[str, str]` constant, which is exactly what `fetch_charts.py` in
  Task 6 assumes when it iterates `module.CHARTS` and calls `module.fetch_chart`.
- **No placeholders:** every code block above is complete, runnable code with real
  verified field names (`trackIds`, `songlist`, `cur_count`, `pop`, `collectionName`,
  `o-chart-results-list-row`, etc.) captured from live API responses on
  2026-09-03/04 — nothing is a "TODO" or "similar to above".
