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
    unknown = [p for p in requested if p not in _FETCHERS.keys()]
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
