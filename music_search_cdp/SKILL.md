---
name: music-search-cdp
description: Connect to a local Chrome instance over the Chrome DevTools Protocol (CDP) to open 1music.cc, get past its Cloudflare Turnstile check with a real browser window, search for a song, and list the matching result versions (title/artist/album). Use when the user wants to search 1music.cc via browser automation, or needs a CDP-driven Chrome session for a similar site. Does not perform or complete any file download.
---

# 1music.cc search via local Chrome CDP

This skill covers connecting to the user's own Chrome via CDP and driving a search on
1music.cc. It stops at listing results — it does not click through to a download, and
it does not decide licensing/authorization questions on the user's behalf. Downloading
a specific result is a separate, explicit decision the user makes after seeing the list.

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

If a port is already listed as LISTEN (`lsof -iTCP:9223 -sTCP:LISTEN`) but
`/json/version` returns nothing, it isn't a live CDP endpoint; don't assume the
port number implies anything.

## Driving the browser

Use `puppeteer-core` (no bundled Chromium needed — it only talks CDP) from a scratch
Node project:

```bash
npm init -y && npm install puppeteer-core
```

Run [scripts/search_1music.js](scripts/search_1music.js):

```bash
node scripts/search_1music.js "海阔天空" 10
```

It connects to `http://127.0.0.1:9223`, opens `https://1music.cc/zh-CN`, waits for the
search input's placeholder to stop reading "正在完成验证" (Turnstile clearing), types
the query, and prints the top N result cards as JSON (`title`, `artist`, `album`).

Read [references/site-flow.md](references/site-flow.md) for the page's structure and
what NOT to try to shortcut (e.g. do not call the search/download endpoints directly).

## Verification-wait behavior

If the wait for Turnstile times out (60s default in the script), a real interactive
challenge may be showing (checkbox/puzzle) rather than the usual silent pass. Do not
attempt to solve it — tell the user to complete it in the visible Chrome window, then
re-run.

## Screenshotting the right tab

`browser.pages()` can return multiple tabs (a blank new-tab page from the fresh
profile, the 1music.cc tab, etc). Always filter by URL — e.g.
`pages.find(p => p.url().includes('1music.cc'))` — rather than assuming the last
page in the array is the one you just drove.

## Stopping point: no downloads

This skill's job ends at producing the result list. Clicking a result's download
button opens `1music.cc/zh-CN/download?...&videoId=...&request_format=...` which
performs its own server/browser-side media fetch and transcode — do not automate
past this point. 1music.cc's own positioning is free download of commercial,
copyrighted tracks; treat completing a download as a separate decision that needs
the user's explicit, informed go-ahead (file name, format, and that they accept
responsibility for the copyright status), not something this skill does
automatically after a search.
