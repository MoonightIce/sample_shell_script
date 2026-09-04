# music_charts/tests/test_qqmusic.py
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fetchers import qqmusic  # noqa: E402


def _resp(json_data):
    m = MagicMock()
    m.json.return_value = json_data
    return m


_SONGLIST = [
    {
        "cur_count": "143759",
        "data": {
            "songname": "LEMONADE", "albumname": "LEMONADE - The 2nd Album",
            "singer": [{"name": "aespa"}], "songmid": "000NVIwc0ezTOD",
        },
    },
    {
        "cur_count": "1",
        "data": {
            "songname": "歌曲B", "albumname": "专辑B",
            "singer": [{"name": "歌手B1"}, {"name": "歌手B2"}],
            "songmid": "abc123",
        },
    },
]


class TestQqmusicFetchChart(unittest.TestCase):
    def test_unknown_chart_raises(self):
        with self.assertRaises(ValueError):
            qqmusic.fetch_chart("does-not-exist")

    @patch("fetchers.qqmusic.get_with_retry")
    def test_index_chart_populates_popularity_and_play_count(self, mock_get):
        mock_get.return_value = _resp({"songlist": _SONGLIST})

        songs = qqmusic.fetch_chart("index", limit=50)

        self.assertEqual(len(songs), 2)
        self.assertEqual(songs[0].rank, 1)
        self.assertEqual(songs[0].title, "LEMONADE")
        self.assertEqual(songs[0].artist, "aespa")
        self.assertEqual(songs[0].album, "LEMONADE - The 2nd Album")
        self.assertEqual(songs[0].popularity, 143759.0)
        self.assertEqual(songs[0].play_count, 143759.0)
        self.assertIsNone(songs[0].comment_count)
        self.assertEqual(songs[1].artist, "歌手B1/歌手B2")

    @patch("fetchers.qqmusic.get_with_retry")
    def test_region_chart_never_populates_popularity(self, mock_get):
        mock_get.return_value = _resp({"songlist": _SONGLIST})

        songs = qqmusic.fetch_chart("neidi", limit=50)

        self.assertIsNone(songs[0].popularity)
        self.assertIsNone(songs[0].play_count)

    @patch("fetchers.qqmusic.get_with_retry")
    def test_limit_truncates_songlist(self, mock_get):
        mock_get.return_value = _resp({"songlist": _SONGLIST})

        songs = qqmusic.fetch_chart("oumei", limit=1)

        self.assertEqual(len(songs), 1)


if __name__ == "__main__":
    unittest.main()
