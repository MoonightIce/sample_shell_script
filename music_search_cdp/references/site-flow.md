# 1music.cc browser flow

Verified against the public site on 2026-09-03, using a real Chrome window connected
over CDP (not a sandboxed/headless browser).

- Entry page: `https://1music.cc/zh-CN`
- The search `<input>` keeps the placeholder text "正在完成验证，请稍候..." and stays
  effectively disabled (typed characters don't land) until Cloudflare Turnstile
  finishes. In a real, regular Chrome window this clears on its own within a few
  seconds to tens of seconds. In a sandboxed/automated browser context it can hang
  indefinitely and the console shows `TurnstileError: [Cloudflare Turnstile] Error:
  600010` repeatedly — that error means don't keep retrying in that context, switch
  to a real Chrome window instead.
- Once the placeholder clears, click the input and type the query; results render
  as a grid of cards, each with a thumbnail, title, artist/album line, and a
  download icon button.
- A result card's title and artist/album are the only reliable disambiguators —
  many results share the same song title from different artists/covers/live
  versions. Match what the user actually asked for; ask if it's ambiguous which
  one they mean.
- The download icon opens a modal ("下载 <title>") with format radio buttons
  (MP3 / FLAC) and three actions: 下载 (download), 上传到 WEBDAV, 取消 (cancel).
- Confirming 下载 navigates the current tab (or opens a new one) to
  `1music.cc/zh-CN/download?title=...&album=...&artist=...&videoId=...&request_format=mp3|flac&song_hash=...&exp=...&thumbnail=...`.
  That URL:
  - is a full HTML page (`content-type: text/html`), not a direct file — it does its
    own media fetch/transcode server- or browser-side before a file is actually
    produced. It can hang for a long time; don't assume a quick response means
    something is wrong.
  - carries a `videoId`-shaped parameter and a `song_hash`/`exp` signed, expiring
    pair. Do not read the presence of a YouTube-ID-shaped string as proof of where
    the audio actually comes from — that's an assumption about parameter naming,
    not an observed fact about the backend. Don't state it as fact to the user.
  - Do not call this endpoint directly outside the browser flow (Turnstile and the
    expiring signature make direct requests brittle even if the shape is known).

## What this skill does not verify or do

- It does not confirm 1music.cc's actual source/licensing of the audio.
- It does not click through the download modal or wait out the `/download` page.
- If a caller wants that automated too, that's a distinct, explicit ask — treat it
  as a new decision, not an extension of "search for a song."
