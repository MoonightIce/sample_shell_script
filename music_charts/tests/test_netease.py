# music_charts/tests/test_netease.py
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fetchers import netease  # noqa: E402


def _resp(json_data):
    m = MagicMock()
    m.json.return_value = json_data
    return m


class TestNeteaseFetchChart(unittest.TestCase):
    def test_unknown_chart_raises(self):
        with self.assertRaises(ValueError):
            netease.fetch_chart("does-not-exist")

    @patch("fetchers.netease.get_with_retry")
    def test_builds_songs_from_playlist_and_details(self, mock_get):
        playlist_resp = _resp({
            "playlist": {"trackIds": [{"id": 111}, {"id": 222}]}
        })
        detail_resp = _resp({
            "songs": [
                {
                    "id": 111, "name": "歌曲A",
                    "ar": [{"name": "歌手A"}],
                    "al": {"name": "专辑A"},
                    "pop": 100.0,
                },
                {
                    "id": 222, "name": "歌曲B",
                    "ar": [{"name": "歌手B1"}, {"name": "歌手B2"}],
                    "al": {"name": "专辑B"},
                    "pop": 60.0,
                },
            ]
        })
        comment_resp_1 = _resp({"total": 37331})
        comment_resp_2 = _resp({"total": 42})
        mock_get.side_effect = [playlist_resp, detail_resp, comment_resp_1, comment_resp_2]

        songs = netease.fetch_chart("hot", limit=50)

        self.assertEqual(len(songs), 2)
        self.assertEqual(songs[0].rank, 1)
        self.assertEqual(songs[0].title, "歌曲A")
        self.assertEqual(songs[0].artist, "歌手A")
        self.assertEqual(songs[0].album, "专辑A")
        self.assertEqual(songs[0].popularity, 100.0)
        self.assertIsNone(songs[0].play_count)
        self.assertEqual(songs[0].comment_count, 37331)
        self.assertEqual(songs[1].artist, "歌手B1/歌手B2")
        self.assertEqual(songs[1].comment_count, 42)

    @patch("fetchers.netease.get_with_retry")
    def test_comment_count_skipped_beyond_limit(self, mock_get):
        playlist_resp = _resp({
            "playlist": {"trackIds": [{"id": 111}, {"id": 222}]}
        })
        detail_resp = _resp({
            "songs": [
                {"id": 111, "name": "A", "ar": [{"name": "x"}], "al": {"name": "y"}, "pop": 1.0},
                {"id": 222, "name": "B", "ar": [{"name": "x"}], "al": {"name": "y"}, "pop": 1.0},
            ]
        })
        comment_resp_1 = _resp({"total": 5})
        mock_get.side_effect = [playlist_resp, detail_resp, comment_resp_1]

        songs = netease.fetch_chart("hot", limit=1)

        self.assertEqual(songs[0].comment_count, 5)
        self.assertIsNone(songs[1].comment_count)
        self.assertEqual(mock_get.call_count, 3)  # no 3rd comment call for rank 2


if __name__ == "__main__":
    unittest.main()
