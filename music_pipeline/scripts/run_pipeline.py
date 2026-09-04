"""排行榜 -> 搜索 -> 下载 -> 歌词/专辑归档 总调度脚本。

调用 music_search_cdp 现有的三个脚本（search_1music.js / download_1music.js /
organize_with_lyricflow.py），把一组目标歌曲（歌名 + 歌手）自动跑完整条流水线。

自动匹配规则：候选结果去掉 " · 专辑名" 后缀后，如果同时包含目标歌名和目标歌手名
（子串匹配），视为命中；如果命中的候选不是恰好一个（0 个或多于 1 个），不自动下载，
打印候选列表，标记这首歌需要人工确认，继续处理下一首 —— 遵循 music_search_cdp 自身
SKILL.md 的规则：同名歌曲很常见，不能跳过确认直接下载。

用法：
    python3 run_pipeline.py "恋人|李荣浩" "我不难过|孙燕姿"
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SEARCH_JS = REPO_ROOT / "music_search_cdp" / "scripts" / "search_1music.js"
DOWNLOAD_JS = REPO_ROOT / "music_search_cdp" / "scripts" / "download_1music.js"
ORGANIZE_PY = REPO_ROOT / "music_search_cdp" / "scripts" / "organize_with_lyricflow.py"

DEFAULT_FORMAT = "flac"
DEFAULT_CDP_PORT = "9223"


def normalize(s: str) -> str:
    return s.replace(" ", "").lower()


def find_unique_match(results: list, title: str, artist: str):
    """在候选列表里找唯一命中的索引；命中数不为 1 时返回 None。"""
    title_n = normalize(title)
    artist_n = normalize(artist)
    matches = []
    for r in results:
        text = r.get("text", "")
        blob = text.split(" · ")[0]
        blob_n = normalize(blob)
        if title_n in blob_n and artist_n in blob_n:
            matches.append(r["index"])
    if len(matches) == 1:
        return matches[0]
    return None


def run_search(query: str, limit: int, cdp_port: str) -> dict:
    proc = subprocess.run(
        ["node", str(SEARCH_JS), query, str(limit), cdp_port],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"搜索失败: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def run_download(query: str, index: int, fmt: str, cdp_port: str) -> dict:
    proc = subprocess.run(
        ["node", str(DOWNLOAD_JS), query, str(index), fmt, "", cdp_port],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"下载失败: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def run_organize(source_path: str) -> str:
    proc = subprocess.run(
        ["python3", str(ORGANIZE_PY), "--source", source_path],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"歌词/专辑归档失败: {proc.stderr.strip()}")
    for line in proc.stdout.splitlines():
        if line.startswith("organized:"):
            return line[len("organized:"):].strip()
    raise RuntimeError(f"歌词/专辑归档未产出归档路径,原始输出:\n{proc.stdout}")


def check_cdp_chrome(cdp_port: str) -> bool:
    proc = subprocess.run(
        ["curl", "-s", "-m", "3", f"http://127.0.0.1:{cdp_port}/json/version"],
        capture_output=True, text=True,
    )
    return proc.returncode == 0 and "webSocketDebuggerUrl" in proc.stdout


def process_song(title: str, artist: str, fmt: str, cdp_port: str) -> dict:
    query = f"{title} {artist}"
    print(f"\n=== {title} - {artist} ===")

    search_result = run_search(query, 10, cdp_port)
    results = search_result.get("results", [])
    if not results:
        print("  没有搜到任何候选。")
        return {"title": title, "artist": artist, "status": "not_found"}

    index = find_unique_match(results, title, artist)
    if index is None:
        print("  未能唯一匹配歌手名,候选列表如下,需要你手动确认:")
        for r in results:
            print(f"    [{r['index']}] {r['text']}")
        return {"title": title, "artist": artist, "status": "needs_manual_selection", "candidates": results}

    matched_text = next(r["text"] for r in results if r["index"] == index)
    print(f"  自动匹配到候选 [{index}]: {matched_text}")

    download_result = run_download(query, index, fmt, cdp_port)
    saved_to = download_result["savedTo"]
    print(f"  下载完成: {saved_to}")

    organized_path = run_organize(saved_to)
    print(f"  已归档到: {organized_path}")

    return {
        "title": title, "artist": artist, "status": "done",
        "matched_index": index, "matched_text": matched_text,
        "saved_to": saved_to, "organized_path": organized_path,
    }


def parse_songs(raw_songs: list) -> list:
    songs = []
    for raw in raw_songs:
        if "|" not in raw:
            raise ValueError(f"歌曲参数格式错误(应为 '歌名|歌手'): {raw!r}")
        title, artist = raw.split("|", 1)
        songs.append((title.strip(), artist.strip()))
    return songs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("songs", nargs="+", help="格式: '歌名|歌手'，可传多个")
    parser.add_argument("--format", default=DEFAULT_FORMAT, choices=("flac", "mp3"))
    parser.add_argument("--cdp-port", default=DEFAULT_CDP_PORT)
    args = parser.parse_args(argv)

    if not check_cdp_chrome(args.cdp_port):
        print(
            f"没有检测到监听 {args.cdp_port} 端口的 CDP Chrome。请先启动:\n"
            f"  mkdir -p /tmp/chrome-cdp-profile\n"
            f'  open -na "Google Chrome" --args --remote-debugging-port={args.cdp_port} '
            f"--user-data-dir=/tmp/chrome-cdp-profile",
            file=sys.stderr,
        )
        return 1

    songs = parse_songs(args.songs)
    results = []
    for title, artist in songs:
        try:
            results.append(process_song(title, artist, args.format, args.cdp_port))
        except Exception as exc:
            print(f"  出错: {exc}", file=sys.stderr)
            results.append({"title": title, "artist": artist, "status": "failed", "error": str(exc)})

    print("\n汇总:")
    for r in results:
        print(f"  {r['title']} - {r['artist']}: {r['status']}")

    return 0 if all(r["status"] == "done" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
