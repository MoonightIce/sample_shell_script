---
name: music-search-cdp
description: Connect to a local Chrome instance over the Chrome DevTools Protocol (CDP) to search 1music.cc for a song, let the user pick and confirm a result, download it (FLAC/MP3) into /Users/admin/Documents/Music, then run it through the LyricFlow docker tool for lyrics/cover art and file it by album. Use when the user wants to search and download music from 1music.cc via browser automation.
---

# 1music.cc search + download + library pipeline via local Chrome CDP

Three scripts, run in sequence:

1. [scripts/search_1music.js](scripts/search_1music.js) — search, list results. Never
   downloads anything.
2. [scripts/download_1music.js](scripts/download_1music.js) — given a result index the
   **user has explicitly confirmed**, click through the download modal and save the
   file into a staging folder.
3. [scripts/organize_with_lyricflow.py](scripts/organize_with_lyricflow.py) — run the
   downloaded file through the `laoning666/lyricflow` docker tool (lyrics + cover art),
   then file it under `/Users/admin/Documents/Music/<album>/`.

**Never skip the confirmation step between 1 and 2.** Search results often include
same-titled covers/lives/remixes by different artists — show the numbered list to the
user and get an explicit index (and format: flac/mp3) before running
`download_1music.js`. Steps 2 and 3 can then run back to back without further
confirmation — they're mechanical once the choice is made.

`scripts/submit_to_metube.py` (converts a 1music.cc download URL's `videoId` into a
YouTube URL and POSTs it to metube for a flac download) is a separate, unused-by-default
path kept at the user's request. It is not part of the pipeline above.

## Why CDP instead of a sandboxed browser tool

1music.cc gates its search input behind Cloudflare Turnstile. A sandboxed/automated
browser context is easy for Turnstile to flag, and the input stays disabled
indefinitely (`TurnstileError: 600010` in the console). A real, regular Chrome window
passes the same check like it would for any normal user. So: drive the user's actual
Chrome, not an embedded headless-like browser.

## Setup: launch a CDP-enabled Chrome

Never relaunch the user's main Chrome with debugging flags — it's already running
without them, and existing instances can't have the flag added retroactively without
closing all their windows. Instead start a second, independent instance with its own
profile so nothing about the user's normal browsing is disturbed:

```bash
mkdir -p /tmp/chrome-cdp-profile
open -na "Google Chrome" --args --remote-debugging-port=9223 --user-data-dir=/tmp/chrome-cdp-profile
```

Verify it came up before doing anything else:

```bash
curl -s http://127.0.0.1:9223/json/version
```

A JSON body with `webSocketDebuggerUrl` means it's ready. Empty output means the
port is stale or occupied by something else — pick a different port and retry.

Dependencies live in this skill directory:

```bash
cd music_search_cdp && npm install puppeteer-core   # only needed once
```

## Step 1 — search

```bash
node scripts/search_1music.js "海阔天空 beyond" 10
```

Reuses an already-open 1music.cc tab if there is one (never opens a fresh tab per call —
that's how a slow, chatty tab accumulates). Prints `{query, results: [{index, text}]}`.
`text` is `"<title><artist> · <album>"` — the only reliable disambiguator; multiple
covers/lives/remixes commonly share a title.

On a transient failure (Turnstile re-triggering, a stuck page state, a slow backend) it
waits 15s, reloads the page, and retries the search once before giving up. It does not
retry indefinitely — a repeated failure after that means something needs a human look
(e.g. an interactive Turnstile challenge showing in the visible Chrome window).

**Show the numbered list to the user and get their pick (index + format) before step 2.**

## Step 2 — download

```bash
node scripts/download_1music.js "海阔天空 beyond" 0 flac
```

Args: `<query> <index> [format=flac|mp3] [downloadDir] [cdpPort]`. Re-runs the same
search (same retry behavior as step 1) to reach the same result list deterministically,
clicks that card's download icon, picks the format radio in the modal, and confirms
"下载". That navigates to 1music.cc's own `/download` page (new tab or same tab), which
does its own fetch/transcode server/browser-side — this can take anywhere from tens of
seconds to a few minutes. Don't assume a quiet stretch means something is wrong.

By default the file is saved under
`/Users/admin/Documents/Music/.music-workflow/incoming-downloads/<run-id>/` (a Chrome
download-behavior override via CDP), not the browser's default Downloads folder.
Completion is detected by **polling that directory for a file whose size has stopped
changing** — not by trusting the CDP `Page.downloadProgress` event, because 1music.cc's
own `/download` tab can close itself right after the file lands, which tears down the
CDP session before a `completed` event is guaranteed to arrive. (Observed once: the file
finished writing correctly but the script hung forever waiting on that event.)

Prints `{query, index, format, title, savedTo}` on success.

## Step 3 — lyrics/cover + file by album

```bash
python3 scripts/organize_with_lyricflow.py --source "<savedTo from step 2>"
```

Moves the file into a run-local staging dir, runs
`docker run --rm laoning666/lyricflow` against it (fetches lyrics as a sidecar `.lrc`
and a cover image via LrcApi, writes basic tags), reads the resulting `album` tag with
`ffprobe`, and moves the audio + lyrics + cover into
`/Users/admin/Documents/Music/<album>/` — deduplicating by sha256 if an identical file
is already there. Writes a manifest under
`/Users/admin/Documents/Music/.music-workflow/manifests/`.

Requires `ffprobe` (Homebrew ffmpeg) and a running Docker daemon. `--dry-run` prints the
plan without touching files or contacting anything. `--skip-lyricflow` organizes by
album only, no lyrics/cover.

**This step always moves (not copies) whatever `--source` points at** — don't point it
at a file you want to keep where it is.

## Reference

Read [references/site-flow.md](references/site-flow.md) for the page's structure
(search request, download modal, `/download` URL shape) and what NOT to try to
shortcut (e.g. don't call the search/download endpoints directly, outside the browser).

## Screenshotting/selecting the right tab

`browser.pages()` can return multiple tabs (a blank new-tab page from the fresh
profile, leftover `/download` tabs from earlier runs, the actual 1music.cc search tab).
Always filter with `url.includes('1music.cc') && !url.includes('/download')` — a
`/download` URL also contains `1music.cc` and will get mistaken for the search tab if
you don't exclude it explicitly (this caused a real failure: the script attached to a
stale `/download` tab that has no search input, and hung waiting for Turnstile to
clear). Close stray `/download` tabs left over from prior runs before starting a new
session if they start piling up.
