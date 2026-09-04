import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from common import ChartSong, now_iso, get_with_retry, write_chart_json  # noqa: E402


class TestNowIso(unittest.TestCase):
    def test_returns_iso_string_with_timezone_offset(self):
        result = now_iso()
        self.assertIn("+08:00", result)


class TestGetWithRetry(unittest.TestCase):
    @patch("common.requests.get")
    def test_succeeds_on_first_try(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        result = get_with_retry("https://example.com")

        self.assertIs(result, mock_resp)
        self.assertEqual(mock_get.call_count, 1)

    @patch("common.time.sleep")
    @patch("common.requests.get")
    def test_retries_once_then_succeeds(self, mock_get, mock_sleep):
        failing_resp = MagicMock()
        failing_resp.raise_for_status.side_effect = Exception("boom")
        ok_resp = MagicMock()
        ok_resp.raise_for_status.return_value = None
        mock_get.side_effect = [failing_resp, ok_resp]

        result = get_with_retry("https://example.com")

        self.assertIs(result, ok_resp)
        self.assertEqual(mock_get.call_count, 2)
        mock_sleep.assert_called_once_with(2)

    @patch("common.time.sleep")
    @patch("common.requests.get")
    def test_raises_after_second_failure(self, mock_get, mock_sleep):
        failing_resp = MagicMock()
        failing_resp.raise_for_status.side_effect = Exception("boom")
        mock_get.return_value = failing_resp

        with self.assertRaises(Exception):
            get_with_retry("https://example.com")

        self.assertEqual(mock_get.call_count, 2)


class TestWriteChartJson(unittest.TestCase):
    def test_writes_expected_filename_and_content(self):
        import tempfile

        songs = [
            ChartSong(
                platform="netease", chart="热歌榜", rank=1, title="歌名",
                artist="歌手", album="专辑", play_count=None,
                comment_count=100, popularity=99.0,
                source_url="https://music.163.com/#/song?id=1",
                fetched_at="2026-09-03T10:00:00+08:00",
            )
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "output"
            path = write_chart_json(songs, "netease", "hot", output_dir)

            self.assertTrue(path.name.startswith("netease_hot_"))
            self.assertTrue(path.name.endswith(".json"))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["title"], "歌名")
            self.assertEqual(data[0]["comment_count"], 100)


if __name__ == "__main__":
    unittest.main()
