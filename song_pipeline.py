#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
song_pipeline.py — 歌曲下载 + 歌词下载流水线
==============================================
数据源：
  - 音频：1music.cc（搜索 → 拿 song_hash → POST /download/ 得 webm 直链 → 下载 → 本地 ffmpeg 转 flac）
  - 歌词：laoning666/lyricflow（扫描本地 flac，自动匹配网易云/酷我/QQ 歌词，落盘 .lrc）

用法示例：
  # 浏览器模式（自动打开浏览器，等 Turnstile 验证通过后搜索；headless 下验证不过时需人工在浏览器窗口完成）
  python3 song_pipeline.py -q "晴天" --artist "周杰伦"

  # API 模式（token 从浏览器 DevTools 搜索请求 URL 中复制，有效期约 5 分钟）
  python3 song_pipeline.py -q "晴天" --artist "周杰伦" --token "0.xxxxx..."

  # 参数
  -q, --query      搜索关键词（必填）
  --artist         歌手（辅助过滤）
  --format         输出格式：flac（默认）/ webm / mp3
  --out            输出目录（默认 ./music_download）
  --top N          展示前 N 个搜索结果并自动选第一个（默认 5）
  --index N        直接选第 N 个结果下载（默认 1）
  --token          搜索 API token（从浏览器拿）
  --no-browser     禁用浏览器辅助（仅 API 模式）
  --no-lyrics      跳过歌词下载
  --keep-webm      保留中间 webm 文件
  --lyricflow-dir  lyricflow 项目目录（默认 ./lyricflow）
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

import httpx

BACKEND = "https://backend.1music.cc"
API = "https://api.1music.cc"
SITE = "https://1music.cc"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


# ---------------------------------------------------------------------------
# 1music.cc 客户端
# ---------------------------------------------------------------------------
class OneMusicClient:
    def __init__(self, timeout: float = 30.0):
        self.http = httpx.Client(
            headers={"User-Agent": UA, "Origin": SITE, "Referer": f"{SITE}/"},
            timeout=timeout,
            follow_redirects=True,
        )

    # ---- 搜索 ----
    def search(self, query: str, token: str) -> list[dict]:
        """GET api.1music.cc/search?songs=<query>&token=<token>"""
        r = self.http.get(f"{API}/search", params={"songs": query, "token": token})
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            raise RuntimeError(f"搜索返回异常结构: {str(data)[:200]}")
        return data

    # ---- 下载 ----
    def request_download(self, song: dict, fmt: str = "flac") -> str:
        """POST backend/download/ → 返回 download_url（webm 直链）

        注意（2026-09 接口更新）：body 必须携带 song_hash + exp + thumbnail，
        三者均来自搜索/推荐列表返回；缺任一（尤其 thumbnail）返回 404
        {"detail": "音乐不存在"}。exp 为签名过期时间戳。
        """
        body = {
            "title": song.get("title", ""),
            "album": song.get("album", ""),
            "artist": song.get("artist", ""),
            "videoId": song.get("videoId", "id"),
            "request_format": fmt,
            "song_hash": song.get("song_hash", ""),
            "exp": song.get("exp", ""),
            "thumbnail": song.get("thumbnail", ""),
        }
        body = {k: v for k, v in body.items() if v not in ("", None)}
        if "exp" in body:
            try:
                body["exp"] = int(body["exp"])  # 前端用数字时间戳，字符串会被拒
            except (TypeError, ValueError):
                body.pop("exp", None)
        # backend 偶发断连（RemoteProtocolError / 504），重试 3 次
        last_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                r = self.http.post(f"{BACKEND}/download/", json=body)
                r.raise_for_status()
                break
            except Exception as e:
                last_err = e
                if attempt < 3:
                    time.sleep(1.5 * attempt)
        else:
            raise last_err if last_err else RuntimeError("download 请求失败")
        data = r.json()
        url = data.get("download_url")
        if not url:
            raise RuntimeError(f"下载任务未返回链接: {data}")
        return url

    def close(self):
        self.http.close()


# ---------------------------------------------------------------------------
# 浏览器辅助（获取 Turnstile token / 搜索结果）
# ---------------------------------------------------------------------------
def get_token_via_browser(query: str, max_wait: int = 180) -> str:
    """
    打开真实浏览器 → 等待 Turnstile 验证（自动或人工）→ 触发一次搜索 →
    从网络请求中截获 api.1music.cc/search 的 token 参数并返回。
    headless 模式下 Turnstile 通常无法自动通过，会打印提示等待人工验证。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("需要 playwright：pip install playwright && playwright install chromium")

    token_holder = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=UA,
            locale="zh-CN",
            viewport={"width": 1280, "height": 800},
        )
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
        """)
        page = ctx.new_page()
        page.on("request", lambda r: _capture_search_token(r, token_holder))
        page.on("console", lambda m: None)  # 静默

        print("[browser] 打开 1music.cc ...")
        page.goto(f"{SITE}/zh-CN", wait_until="domcontentloaded", timeout=60000)

        # 轮询等待验证完成（搜索框 placeholder 从"正在完成验证"变为搜索提示）
        print("[browser] 等待 Turnstile 验证 ...（若长时间无反应，请在浏览器窗口手动完成验证）")
        for i in range(max_wait // 5):
            time.sleep(5)
            st = page.evaluate("""() => {
                const inps = [...document.querySelectorAll('input[type=text]')];
                return inps.map(i => ({ph: i.placeholder, dis: i.disabled}))[0] || null;
            }""")
            if st and st["ph"] and "验证" not in st["ph"] and not st["dis"]:
                print(f"[browser] 验证完成（{i*5}s），搜索框可用")
                break
        else:
            raise RuntimeError("等待验证超时，请重试并在浏览器窗口内手动完成人机验证")

        # 触发搜索（此时页面内的 React 状态已拿到 token）
        box = page.query_selector("input[type=text]")
        box.fill(query)
        box.press("Enter")

        # 等待请求被截获
        print("[browser] 触发搜索，截获 token ...")
        for _ in range(20):
            if token_holder.get("token"):
                break
            time.sleep(1)

        # 兜底：直接从 turnstile 对象读 token
        if not token_holder.get("token"):
            tok = page.evaluate("window.turnstile && window.turnstile.getResponse ? window.turnstile.getResponse() : null")
            if tok:
                token_holder["token"] = tok

        browser.close()

    token = token_holder.get("token")
    if not token:
        raise RuntimeError("未能获取搜索 token（网络请求未截获）。请改用 --token 直接传入。")
    return token


def _capture_search_token(request, holder: dict):
    """从 api.1music.cc/search 请求中提取 token 参数"""
    try:
        url = request.url
        if "/search" in url and "api.1music.cc" in url:
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            if params.get("token"):
                holder["token"] = params["token"][0]
                print(f"[browser] 截获搜索请求 token: {holder['token'][:24]}...")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ffmpeg 转码
# ---------------------------------------------------------------------------
def to_flac(src: Path, dst: Path, song: dict):
    """webm → flac，并写入 ID3/FLAC 标签（title/artist/album + 来源标记）"""
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vn", "-c:a", "flac",
        "-metadata", f"title={song.get('title','')}",
        "-metadata", f"artist={song.get('artist','')}",
        "-metadata", f"album={song.get('album','')}",
        "-metadata", "PURL=1music.cc",
        "-metadata", "COMMENT=1music.cc download",
        str(dst),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 转码失败: {r.stderr[-400:]}")


# ---------------------------------------------------------------------------
# lyricflow 歌词下载
# ---------------------------------------------------------------------------
def run_lyricflow(music_dir: Path, lyricflow_dir: Path, provider: str = "tunehub"):
    """
    对 music_dir 下的音频跑 lyricflow，下载 .lrc 歌词到同目录。
    music_dir 即专辑文件夹；flac 已带 title/artist/album 标签，scanner 直接读取。
    """
    env = dict(os.environ)
    env.update({
        "MUSIC_PATH": str(music_dir),
        "DOWNLOAD_LYRICS": "true",
        "DOWNLOAD_COVER": "false",
        "UPDATE_LYRICS": "false",
        "UPDATE_COVER": "false",
        "UPDATE_BASIC_INFO": "false",
        "OVERWRITE_LYRICS": "true",
        "API_PROVIDER": provider,
        "PLATFORMS": "netease,kuwo,qq",
    })
    r = subprocess.run(
        [sys.executable, "-m", "src.main"],
        cwd=str(lyricflow_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        print(f"  [lyricflow] 运行失败 rc={r.returncode}")
        print((r.stdout or "")[-500:])
        print((r.stderr or "")[-500:])


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def safe_name(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]', "_", s).strip()
    return s or "untitled"


def download_file(url: str, dest: Path, timeout: float = 120.0, retries: int = 3) -> int:
    """下载文件（带重试）。

    oss.1music.cc CDN 偶发 504 Gateway Timeout，重试可显著提高成功率；
    带 Origin/Referer 头可走更优节点（实测 15.4s → 6.5s）。
    """
    headers = {"User-Agent": UA, "Origin": SITE, "Referer": f"{SITE}/"}
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with httpx.stream("GET", url, headers=headers, timeout=timeout,
                              follow_redirects=True) as r:
                r.raise_for_status()
                size = 0
                with open(dest, "wb") as f:
                    for chunk in r.iter_bytes(65536):
                        f.write(chunk)
                        size += len(chunk)
            return size
        except Exception as e:
            last_err = e
            if attempt < retries:
                wait = 2 * attempt
                print(f"  [下载重试 {attempt}/{retries - 1}] {type(e).__name__}: {str(e)[:80]} ({wait}s 后重试)")
                time.sleep(wait)
    raise last_err if last_err else RuntimeError("下载失败")


def pick_song(results: list[dict], index: int) -> dict:
    if not results:
        raise RuntimeError("没有搜索结果")
    idx = index - 1
    if idx < 0 or idx >= len(results):
        raise RuntimeError(f"结果只有 {len(results)} 条，无法选第 {index} 条")
    return results[idx]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="1music.cc 歌曲下载 + lyricflow 歌词下载")
    ap.add_argument("-q", "--query", required=True, help="搜索关键词")
    ap.add_argument("--artist", default="", help="歌手（辅助过滤）")
    ap.add_argument("--format", dest="fmt", default="flac", choices=["flac", "webm", "mp3"])
    ap.add_argument("--out", default="/Users/moonightice/GitHub/Music",
                    help="输出目录（默认按 歌手/专辑 分文件夹存到 ~/GitHub/Music）")
    ap.add_argument("--top", type=int, default=5, help="展示前 N 条结果")
    ap.add_argument("--index", type=int, default=1, help="下载第 N 条结果")
    ap.add_argument("--token", default="", help="搜索 API token（从浏览器 DevTools 复制）")
    ap.add_argument("--no-browser", action="store_true", help="禁用浏览器辅助")
    ap.add_argument("--no-lyrics", action="store_true", help="跳过歌词下载")
    ap.add_argument("--lyric-provider", default="tunehub", choices=["tunehub", "lrcapi"],
                    help="歌词 API provider（tunehub 聚合网易云/酷我/QQ；lrcapi 用 api.lrc.cx）")
    ap.add_argument("--keep-webm", action="store_true", help="保留中间 webm")
    ap.add_argument("--lyricflow-dir", default=str(Path(__file__).parent / "lyricflow"))
    args = ap.parse_args()

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    lyricflow_dir = Path(args.lyricflow_dir)
    if not (lyricflow_dir / "src" / "main.py").exists():
        print(f"[!] lyricflow 目录无效: {lyricflow_dir}")
        print("    请先: git clone https://github.com/laoning666/lyricFlow.git lyricflow")

    client = OneMusicClient()

    # ---- 1. 搜索 ----
    token = args.token
    if not token and not args.no_browser:
        print(f"\n=== 搜索: {args.query} ===")
        try:
            token = get_token_via_browser(args.query)
        except Exception as e:
            print(f"[!] 浏览器获取 token 失败: {e}")
            print("    请改用 --token 传入 token，或 --no-browser 跳过")
            client.close()
            sys.exit(1)
    if not token:
        print("[!] 未提供 token，无法搜索（1music.cc 搜索强制要求 Turnstile token）")
        client.close()
        sys.exit(1)

    try:
        results = client.search(args.query, token)
    except Exception as e:
        print(f"[!] 搜索失败: {e}")
        print("    token 可能已过期（有效期约 5 分钟），请重新获取")
        client.close()
        sys.exit(1)

    if args.artist:
        kw = args.artist.lower()
        results = [r for r in results if kw in (r.get("artist") or "").lower()]
    if not results:
        print("[!] 无匹配结果")
        client.close()
        sys.exit(1)

    print(f"找到 {len(results)} 条结果，展示前 {min(args.top, len(results))} 条:")
    for i, r in enumerate(results[: args.top], 1):
        mark = "  <== 将下载" if i == args.index else ""
        print(f"  [{i}] {r.get('title','')} — {r.get('artist','')} | {r.get('album','')}{mark}")

    song = pick_song(results, args.index)
    print(f"\n=== 下载: {song.get('title','')} — {song.get('artist','')} ===")

    # ---- 2. 拿下载链接并下载 webm ----
    dl_url = client.request_download(song, args.fmt)
    print(f"  下载链接: {dl_url[:100]}...")

    tmpdir = Path(tempfile.mkdtemp(prefix="songpipe_"))
    webm_path = tmpdir / "audio.webm"
    size = download_file(dl_url, webm_path)
    print(f"  已下载 webm: {size/1024/1024:.1f} MB")

    # ---- 3. 转码（按 歌手/专辑 分文件夹：<out>/<歌手>/<专辑>/<歌名>.<ext>）----
    artist_name = safe_name(song.get("artist") or "未知歌手")
    album_name = safe_name(song.get("album") or f"{song.get('artist','')} - 单曲")
    album_dir = out_root / artist_name / album_name
    album_dir.mkdir(parents=True, exist_ok=True)

    if args.fmt == "webm":
        final_path = album_dir / f"{safe_name(song.get('title','audio'))}.webm"
        shutil.move(str(webm_path), str(final_path))
        print(f"  保存 webm: {final_path}")
    elif args.fmt == "mp3":
        # webm → mp3 直转
        final_path = album_dir / f"{safe_name(song.get('title','audio'))}.mp3"
        print("  ffmpeg 转码 webm → mp3 ...")
        cmd = ["ffmpeg", "-y", "-i", str(webm_path), "-vn", "-c:a", "libmp3lame", "-b:a", "320k",
               "-metadata", f"title={song.get('title','')}",
               "-metadata", f"artist={song.get('artist','')}",
               "-metadata", f"album={song.get('album','')}",
               "-metadata", "PURL=1music.cc",
               "-metadata", "COMMENT=1music.cc download",
               str(final_path)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg 转码失败: {r.stderr[-400:]}")
        print(f"  保存 mp3: {final_path} ({final_path.stat().st_size/1024/1024:.1f} MB)")
    else:
        final_path = album_dir / f"{safe_name(song.get('title','audio'))}.flac"
        print("  ffmpeg 转码 webm → flac ...")
        to_flac(webm_path, final_path, song)
        print(f"  保存 flac: {final_path} ({final_path.stat().st_size/1024/1024:.1f} MB)")

    if not args.keep_webm:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # ---- 4. 歌词 ----
    if not args.no_lyrics and (lyricflow_dir / "src" / "main.py").exists():
        print("\n=== lyricflow 下载歌词 ===")
        run_lyricflow(album_dir, lyricflow_dir, args.lyric_provider)
        lrcs = list(album_dir.glob("*.lrc"))
        if lrcs:
            print(f"  歌词已保存: {[p.name for p in lrcs]}")
        else:
            print("  [warn] 未匹配到歌词（可稍后对整目录重跑）")
    elif not args.no_lyrics:
        print("\n=== 跳过歌词：lyricflow 未安装 ===")

    print("\n=== 完成 ===")
    print(f"  输出目录: {album_dir}")
    client.close()


if __name__ == "__main__":
    main()
