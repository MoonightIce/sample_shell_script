#!/usr/bin/env python3
"""Stage new audio, run LyricFlow, and organize the result by album."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_LIBRARY = Path("/Users/admin/Documents/Music")
IMAGE = "ghcr.io/laoning666/lyricflow:latest"
AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".ape", ".wma"}
TEMP_SUFFIXES = {".crdownload", ".part", ".download", ".tmp"}
INVALID_COMPONENT = re.compile(r"[/:\\\x00-\x1f]")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Process explicitly supplied downloads with LyricFlow and file them by album."
    )
    parser.add_argument("--source", action="append", required=True, type=Path,
                        help="Downloaded audio file or directory; repeat for multiple sources.")
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY,
                        help=f"Destination library (default: {DEFAULT_LIBRARY}).")
    parser.add_argument("--provider", choices=("lrcapi", "tunehub"), default="lrcapi")
    parser.add_argument("--lrcapi-url", default="https://api.lrc.cx",
                        help="LrcApi base URL when --provider=lrcapi.")
    parser.add_argument("--move-source", action="store_true",
                        help="Move exact source files into staging; directories are never moved.")
    parser.add_argument("--keep-stage", action="store_true",
                        help="Keep the empty or residual run directory after success.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate inputs and print planned actions without writes or network calls.")
    parser.add_argument("--skip-lyricflow", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def fail(message: str) -> None:
    raise RuntimeError(message)


def collect_audio(sources: Sequence[Path]) -> List[Path]:
    found: List[Path] = []
    for raw in sources:
        source = raw.expanduser().resolve()
        if not source.exists():
            fail(f"source does not exist: {source}")
        if source.is_file():
            candidates: Iterable[Path] = (source,)
        elif source.is_dir():
            candidates = sorted(p for p in source.rglob("*") if p.is_file())
        else:
            continue
        for path in candidates:
            suffix = path.suffix.lower()
            if suffix in TEMP_SUFFIXES:
                continue
            if suffix in AUDIO_EXTENSIONS:
                found.append(path)
    unique = list(dict.fromkeys(found))
    if not unique:
        fail("no completed supported audio files found in --source")
    return unique


def safe_component(value: str, fallback: str = "Unknown Album") -> str:
    value = INVALID_COMPONENT.sub("_", value).strip().strip(".")
    value = re.sub(r"\s+", " ", value)
    return value[:180] or fallback


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    fail(f"could not allocate a unique destination for {path}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_files(files: Sequence[Path], incoming: Path, move_source: bool) -> List[Path]:
    incoming.mkdir(parents=True, exist_ok=False)
    staged: List[Path] = []
    for source in files:
        companions = matching_sidecars(source) + nearby_covers(source)
        target = unique_path(incoming / source.name)
        if move_source:
            if not source.is_file():
                fail("--move-source accepts exact files only")
            shutil.move(str(source), str(target))
        else:
            shutil.copy2(source, target)
        staged.append(target)
        for companion in companions:
            companion_target = unique_path(incoming / companion.name)
            shutil.copy2(companion, companion_target)
            staged.append(companion_target)
    return staged


def docker_command(incoming: Path, args: argparse.Namespace) -> List[str]:
    env = {
        "TZ": "Asia/Shanghai",
        "MUSIC_PATH": "/music",
        "SCAN_INTERVAL_DAYS": "0",
        "DOWNLOAD_LYRICS": "true",
        "DOWNLOAD_COVER": "true",
        "OVERWRITE_LYRICS": "false",
        "OVERWRITE_COVER": "false",
        "UPDATE_BASIC_INFO": "true",
        "USE_FOLDER_STRUCTURE": "true",
        "API_PROVIDER": args.provider,
    }
    if args.provider == "lrcapi":
        env["LRCAPI_URL"] = args.lrcapi_url.rstrip("/")
    command = ["docker", "run", "--rm", "--pull=missing"]
    for key, value in env.items():
        command.extend(("-e", f"{key}={value}"))
    command.extend(("-v", f"{incoming.resolve()}:/music:rw", IMAGE))
    return command


def preflight(skip_lyricflow: bool) -> None:
    if shutil.which("ffprobe") is None:
        fail("ffprobe is required; install FFmpeg first")
    if skip_lyricflow:
        return
    if shutil.which("docker") is None:
        fail("docker is required")
    check = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if check.returncode != 0:
        fail("Docker is installed but the Docker daemon is unavailable")


def read_tags(path: Path) -> Dict[str, str]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if result.returncode != 0:
        fail(f"ffprobe failed for {path.name}: {result.stderr.strip()}")
    payload = json.loads(result.stdout or "{}")
    tags = payload.get("format", {}).get("tags", {}) or {}
    return {str(key).casefold(): str(value) for key, value in tags.items()}


def matching_sidecars(audio: Path) -> List[Path]:
    result = []
    for suffix in (".lrc", ".txt"):
        candidate = audio.with_suffix(suffix)
        if candidate.exists():
            result.append(candidate)
    return result


def nearby_covers(audio: Path) -> List[Path]:
    names = {"cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.png"}
    return [p for p in audio.parent.iterdir() if p.is_file() and p.name.casefold() in names]


def move_or_deduplicate(source: Path, destination: Path) -> Tuple[Path, bool]:
    if destination.exists() and sha256(source) == sha256(destination):
        source.unlink()
        return destination, True
    destination = unique_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    return destination, False


def organize(incoming: Path, library: Path) -> Tuple[List[Dict[str, object]], List[str]]:
    records: List[Dict[str, object]] = []
    warnings: List[str] = []
    audio_files = sorted(p for p in incoming.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS)
    if not audio_files:
        fail("LyricFlow completed but no audio files remain in staging")

    for audio in audio_files:
        tags = read_tags(audio)
        album_raw = tags.get("album", "").strip()
        album = safe_component(album_raw)
        if not album_raw:
            warnings.append(f"{audio.name}: missing album tag; filed under {album}")
        album_dir = library / album
        sidecars_before_move = matching_sidecars(audio)
        covers_before_move = nearby_covers(audio)
        target_audio, duplicate = move_or_deduplicate(audio, album_dir / audio.name)

        sidecars: List[str] = []
        for sidecar in sidecars_before_move:
            target_sidecar, _ = move_or_deduplicate(sidecar, target_audio.with_suffix(sidecar.suffix))
            sidecars.append(str(target_sidecar))

        covers: List[str] = []
        for cover in covers_before_move:
            if not cover.exists():
                continue
            target_cover, _ = move_or_deduplicate(cover, album_dir / cover.name)
            covers.append(str(target_cover))

        records.append({
            "source_name": audio.name,
            "album": album_raw or None,
            "destination": str(target_audio),
            "sha256": sha256(target_audio),
            "deduplicated": duplicate,
            "lyrics": sidecars,
            "covers": covers,
        })
    return records, warnings


def write_manifest(library: Path, run_id: str, payload: Dict[str, object]) -> Path:
    manifests = library / ".music-workflow" / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    target = manifests / f"{run_id}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return target


def remove_empty_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    try:
        root.rmdir()
    except OSError:
        pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.move_source and any(path.expanduser().is_dir() for path in args.source):
        fail("--move-source requires exact file sources, not directories")
    library = args.library.expanduser().resolve()
    files = collect_audio(args.source)
    run_id = dt.datetime.now().strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"
    run_dir = library / ".music-workflow" / "runs" / run_id
    incoming = run_dir / "incoming"
    command = docker_command(incoming, args)

    print(f"library: {library}")
    print("sources:")
    for path in files:
        print(f"  - {path}")
    print("docker:", " ".join(command))
    if args.dry_run:
        print("dry-run: no files changed and LyricFlow was not contacted")
        return 0

    library.mkdir(parents=True, exist_ok=True)
    preflight(args.skip_lyricflow)
    staged = stage_files(files, incoming, args.move_source)
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        if not args.skip_lyricflow:
            subprocess.run(command, check=True)
        records, warnings = organize(incoming, library)
        payload: Dict[str, object] = {
            "run_id": run_id,
            "started_at": started_at,
            "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "provider": args.provider,
            "image": IMAGE,
            "sources": [str(path) for path in files],
            "staged": [str(path) for path in staged],
            "tracks": records,
            "warnings": warnings,
        }
        manifest = write_manifest(library, run_id, payload)
        if not args.keep_stage:
            remove_empty_tree(run_dir)
        print(f"manifest: {manifest}")
        for record in records:
            print(f"organized: {record['destination']}")
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
        return 0
    except Exception:
        print(f"workflow failed; staged files retained at: {run_dir}", file=sys.stderr)
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
