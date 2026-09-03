# 1music.cc browser flow

Verified against the public site on 2026-09-03.

- Entry page: `https://1music.cc/zh-CN/`
- The search field stays disabled until Cloudflare Turnstile supplies a token.
- Search modes include Songs; use the title and artist together where possible.
- A result contains title, artist, album, cover, and a short-lived signed value.
- The Download dialog offers MP3 and FLAC. FLAC is the workflow default.
- Download opens a localized `/download` page. That page retrieves the source media, fetches cover art, and uses browser-side FFmpeg for MP3 or FLAC output.
- The resulting filename is normally `Artist - Title.<format>` and is saved through the browser download mechanism.

Do not call the search or download backend directly. Turnstile and expiring signed values make direct requests brittle, and browser-side transcoding is part of the site's current download behavior.

If the page changes, rely on current visible labels and result metadata. Stop if a selected result does not match the requested title and artist.
