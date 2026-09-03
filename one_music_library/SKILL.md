---
name: one-music-library
description: Download user-authorized music from 1music.cc in a browser, enrich it with the laoning666/lyricflow Docker image, and file new tracks into album folders. Use for 1music downloads, LyricFlow lyric or cover processing, and album-based local library organization.
---

# 1Music library workflow

Use the browser for 1music.cc and the bundled script for local processing. The site requires Cloudflare Turnstile and short-lived signed download data, so do not replace its browser flow with direct API calls or scrape credentials from the page.

Only download music the user is authorized to obtain. Treat a requested title, artist, album, URL, and format as the selection contract. Default to FLAC and `/Users/admin/Documents/Music` when they are omitted.

## Download

Read [references/site-flow.md](references/site-flow.md) before operating 1music.cc.

1. Create a timestamp marker before starting the browser download.
2. Open `https://1music.cc/zh-CN/` in the available browser and wait for automatic verification to finish.
3. Search using the title plus artist when available. Match the visible title, artist, and album. Ask the user only when multiple results remain materially ambiguous.
4. Select the requested format, or FLAC by default, and trigger the site's Download action. Wait until the browser download finishes. The download page performs its own media fetch and may transcode in the browser.
5. Identify only the completed audio file created after the marker. Ignore `.crdownload`, `.part`, and temporary files. Search the browser download directory and the requested library root; use the visible result metadata and extension to disambiguate.

If Turnstile presents an interactive CAPTCHA, ask the user to complete it in the browser. Do not bypass it. Ads or unrelated buttons are not part of the workflow.

## Enrich and organize

Run the bundled launcher with the exact newly downloaded file:

```bash
./bin/music-workflow --source "/absolute/path/to/Artist - Title.flac"
```

Use `--library /absolute/path` for a non-default destination. Add `--move-source` only when the source is the exact file created by this run; this moves it into recoverable staging instead of leaving a duplicate in Downloads.

The script:

- stages only the supplied files under `<library>/.music-workflow/runs/`;
- runs `ghcr.io/laoning666/lyricflow:latest` once with a read-write mount of that staging directory;
- downloads lyrics and cover art without overwriting existing sidecars;
- asks LyricFlow to update basic tags and use album folders;
- reads the resulting `album` tag with `ffprobe` and moves each track plus its `.lrc` and cover into `<library>/<album>/`;
- writes a JSON manifest under `<library>/.music-workflow/manifests/`.

Default to `API_PROVIDER=lrcapi` and `https://api.lrc.cx`; metadata text such as artist, title, and album is sent to that service. If the user requires private metadata handling, use `--lrcapi-url` for their self-hosted LrcApi endpoint. Use `--provider tunehub` only when requested or when the user accepts that fallback.

Run `--dry-run` to show the Docker command and source selection without copying, moving, or contacting metadata services. If Docker or LyricFlow fails, report the retained run directory; do not organize partial output or scan the rest of the library.

## Verify

Report the manifest path, final album directory, audio file, `.lrc` presence, and cover presence. Distinguish a successful browser download, LyricFlow completion, and album organization; one does not prove the others.
