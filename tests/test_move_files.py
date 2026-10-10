"""Exercise real moves in temporary directories without using the user's Trash."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'MoveFiles.sh'


class MoveFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / 'source'
        self.dst = self.root / 'target'
        self.src.mkdir()
        self.dst.mkdir()

    def run_move(self, **settings):
        env = dict(os.environ, DEDUP_MODE='none', TRASH_OTHERS='0')
        env.update(settings)
        result = subprocess.run(['bash', str(SCRIPT), str(self.src), str(self.dst)],
                                env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_existing_numbered_files_are_preserved(self):
        for name in ('movie.mp4', 'notes.txt'):
            with self.subTest(name=name):
                (self.src / name).write_text('incoming')
                (self.dst / name).write_text('original')
                (self.dst / (name + '_1')).write_text('previous')
                self.run_move()
                self.assertEqual((self.dst / name).read_text(), 'original')
                self.assertEqual((self.dst / (name + '_1')).read_text(), 'previous')
                self.assertEqual((self.dst / (name + '_2')).read_text(), 'incoming')
                self.assertFalse((self.src / name).exists())

    def test_duplicate_rename_preserves_existing_suffix(self):
        (self.src / 'movie.mp4').write_text('same content')
        (self.dst / 'movie.mp4').write_text('same content')
        (self.dst / 'movie.mp4_dup').write_text('preserve me')
        self.run_move(DEDUP_MODE='hash', DEDUP_ACTION='rename')
        self.assertEqual((self.dst / 'movie.mp4_dup').read_text(), 'preserve me')
        self.assertEqual((self.dst / 'movie.mp4_dup_1').read_text(), 'same content')

    def test_directory_and_dangling_symlink_names_are_preserved(self):
        (self.src / 'movie.mp4').write_text('incoming')
        (self.dst / 'movie.mp4').mkdir()
        (self.dst / 'movie.mp4_1').symlink_to('missing')
        self.run_move()
        self.assertEqual(list((self.dst / 'movie.mp4').iterdir()), [])
        self.assertTrue((self.dst / 'movie.mp4_1').is_symlink())
        self.assertEqual((self.dst / 'movie.mp4_2').read_text(), 'incoming')

    def test_missing_target_is_created(self):
        self.dst.rmdir()
        (self.src / 'movie.mp4').write_text('incoming')
        self.run_move()
        self.assertEqual((self.dst / 'movie.mp4').read_text(), 'incoming')


if __name__ == '__main__':
    unittest.main()
