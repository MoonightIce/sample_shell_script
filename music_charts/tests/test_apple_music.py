import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fetchers import apple_music  # noqa: E402


def _resp(json_data):
    m = MagicMock()
    m.json.return_value = json_data
    return m


class TestAppleMusicFetchChart(unittest.TestCase):
    def test_unknown_chart_raises(self):
        with self.assertRaises(ValueError):
            apple_music.fetch_chart("does-not-exist")

    @patch("fetchers.apple_music.get_with_retry")
    def test_builds_songs_and_fills_album_from_lookup(self, mock_get):
        rss_resp = _resp({
            "feed": {"results": [
                {
                    "id": "1844932150", "name": "Choosin' Texas",
                    "artistName": "Ella Langley",
                    "url": "https://music.apple.com/us/album/choosin-texas/1844932149?i=1844932150",
                },
                {
                    "id": "6796864754", "name": "BbY WOW",
                    "artistName": "KAROL G",
                    "url": "https://music.apple.com/us/album/bby-wow/6796864741?i=6796864754",
                },
            ]}
        })
        lookup_resp = _resp({
            "results": [
                {"trackId": 1844932150, "collectionName": "Choosin' Texas - Single"},
                {"trackId": 6796864754, "collectionName": "NO ME ARREPIENTO DE SENTIR TANTO"},
            ]
        })
        mock_get.side_effect = [rss_resp, lookup_resp]

        songs = apple_music.fetch_chart("us", limit=50)

        self.assertEqual(len(songs), 2)
        self.assertEqual(songs[0].rank, 1)
        self.assertEqual(songs[0].title, "Choosin' Texas")
        self.assertEqual(songs[0].artist, "Ella Langley")
        self.assertEqual(songs[0].album, "Choosin' Texas - Single")
        self.assertIsNone(songs[0].play_count)
        self.assertIsNone(songs[0].comment_count)
        self.assertIsNone(songs[0].popularity)
        self.assertEqual(songs[1].album, "NO ME ARREPIENTO DE SENTIR TANTO")

    @patch("fetchers.apple_music.get_with_retry")
    def test_missing_lookup_entry_leaves_album_none(self, mock_get):
        rss_resp = _resp({
            "feed": {"results": [
                {"id": "1", "name": "T", "artistName": "A", "url": "https://x"},
            ]}
        })
        lookup_resp = _resp({"results": []})
        mock_get.side_effect = [rss_resp, lookup_resp]

        songs = apple_music.fetch_chart("cn", limit=50)

        self.assertIsNone(songs[0].album)


if __name__ == "__main__":
    unittest.main()
