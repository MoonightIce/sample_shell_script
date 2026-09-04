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
