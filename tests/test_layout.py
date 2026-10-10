"""Validate canonical entrypoints and migration compatibility without network/device I/O."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MUSIC = ('backfill_thumbnail.py', 'chart_download.py', 'chart_download_v2.py',
         'chart_fetch.py', 'filter_original.py', 'lyrics_batch.py',
         'prefetch_hashes.py', 'prefetch_reliable.py', 'prefetch_slow.py',
         'prefetch_via_cdp.py', 'song_pipeline.py')


class LayoutTests(unittest.TestCase):
    def test_legacy_links_resolve_to_canonical_files(self):
        links = {name: 'music_pipeline/scripts/' + name for name in MUSIC}
        links.update({'MoveFiles.sh': 'file_sorter/scripts/MoveFiles.sh',
                      'nas_qbt_archive.sh': 'nas_archive/scripts/nas_qbt_archive.sh',
                      'qbt_hook.sh': 'nas_archive/scripts/qbt_hook.sh',
                      'download_videos.py': 'video_search/scripts/download_videos.py'})
        for old, new in links.items():
            with self.subTest(entry=old):
                self.assertTrue((ROOT / old).is_symlink())
                self.assertTrue((ROOT / new).is_file())
                self.assertEqual((ROOT / old).resolve(), ROOT / new)

    def test_music_help_from_outside_repository(self):
        # filter_original accepts a JSON path directly, so --help is not a safe probe.
        with tempfile.TemporaryDirectory() as cwd:
            for name in MUSIC:
                if name == 'filter_original.py':
                    continue
                for entry in (ROOT / name, ROOT / 'music_pipeline/scripts' / name):
                    with self.subTest(entry=str(entry)):
                        proc = subprocess.run([sys.executable, str(entry), '--help'],
                                              cwd=cwd, capture_output=True, text=True, timeout=15)
                        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_lyricflow_defaults_resolve_outside_repository(self):
        with tempfile.TemporaryDirectory() as cwd:
            for name in ('song_pipeline', 'chart_download', 'chart_download_v2', 'lyrics_batch'):
                script = ROOT / 'music_pipeline/scripts' / (name + '.py')
                # Intercept argparse before the entrypoint performs any real work.
                probe = '''import argparse, runpy, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parent))
def inspect(parser, *args, **kwargs):
    defaults = [a.default for a in parser._actions if a.dest == 'lyricflow_dir']
    if defaults:
        assert Path(defaults[0]) == Path(sys.argv[2]), defaults
    raise SystemExit(0)
argparse.ArgumentParser.parse_args = inspect
scope = runpy.run_path(sys.argv[1])
if 'LYRICFLOW_DIR' in scope:
    assert scope['LYRICFLOW_DIR'] == Path(sys.argv[2])
else:
    scope['main']()
'''
                with self.subTest(script=name):
                    proc = subprocess.run([sys.executable, '-c', probe, str(script), str(ROOT / 'lyricflow')],
                                          cwd=cwd, capture_output=True, text=True, timeout=15)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
