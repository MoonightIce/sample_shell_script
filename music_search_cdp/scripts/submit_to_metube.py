#!/usr/bin/env python3
"""Convert 1music.cc download links to YouTube URLs and submit them to metube.

Input: one 1music.cc download URL per line, e.g.
  https://1music.cc/zh-CN/download?title=海闊天空&album=BEYOND&artist=BEYOND&videoId=PUO36ew8LoE&request_format=flac&...

Each line's `videoId` query param is extracted and turned into
  https://www.youtube.com/watch?v=<videoId>
which is then POSTed to metube's /add endpoint requesting a flac download.

Usage:
  python3 submit_to_metube.py urls.txt
  cat urls.txt | python3 submit_to_metube.py
  python3 submit_to_metube.py urls.txt --dry-run
  python3 submit_to_metube.py urls.txt --metube-url http://localhost:8081
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def extract_video_id(line: str) -> str | None:
    line = line.strip()
    if not line:
        return None
    parsed = urllib.parse.urlparse(line)
    params = urllib.parse.parse_qs(parsed.query)
    video_ids = params.get("videoId")
    return video_ids[0] if video_ids else None


def submit(metube_url: str, video_id: str, title: str, quality: str) -> None:
    payload = json.dumps({
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "format": "flac",
        "quality": quality,
    }).encode()
    req = urllib.request.Request(
        metube_url.rstrip("/") + "/add",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode())
    if not body.get("status") == "ok":
        raise RuntimeError(f"metube rejected {title or video_id}: {body}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", nargs="?", help="file with one 1music.cc download URL per line (default: stdin)")
    parser.add_argument("--metube-url", default="http://localhost:8081", help="metube base URL (default: %(default)s)")
    parser.add_argument("--quality", default="best", help="audio quality to request (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="only print the converted YouTube URLs, don't submit")
    args = parser.parse_args()

    lines = (open(args.input, encoding="utf-8") if args.input else sys.stdin).read().splitlines()

    ok, failed, skipped = 0, 0, 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        video_id = extract_video_id(line)
        if not video_id:
            print(f"[跳过] 未找到 videoId: {line}", file=sys.stderr)
            skipped += 1
            continue

        title_params = urllib.parse.parse_qs(urllib.parse.urlparse(line).query)
        title = urllib.parse.unquote(title_params.get("title", [""])[0])
        yt_url = f"https://www.youtube.com/watch?v={video_id}"

        if args.dry_run:
            print(f"{title or video_id} -> {yt_url}")
            continue

        try:
            submit(args.metube_url, video_id, title, args.quality)
            print(f"[已提交] {title or video_id} -> {yt_url}")
            ok += 1
        except (urllib.error.URLError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"[失败] {title or video_id} -> {yt_url}: {exc}", file=sys.stderr)
            failed += 1

    if not args.dry_run:
        print(f"\n完成: 成功 {ok}, 失败 {failed}, 跳过 {skipped}", file=sys.stderr)


if __name__ == "__main__":
    main()
