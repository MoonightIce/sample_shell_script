import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fetch_charts  # noqa: E402


class TestResolvePlatforms(unittest.TestCase):
    def test_all_returns_every_platform(self):
        result = fetch_charts.resolve_platforms("all")
        self.assertEqual(set(result), {"netease", "qqmusic", "apple_music", "billboard"})

    def test_comma_separated_list(self):
        result = fetch_charts.resolve_platforms("netease, qqmusic")
        self.assertEqual(result, ["netease", "qqmusic"])

    def test_unknown_platform_raises(self):
        with self.assertRaises(ValueError):
            fetch_charts.resolve_platforms("spotify")


class TestMain(unittest.TestCase):
    @patch("fetch_charts.write_chart_json")
    @patch("fetch_charts._FETCHERS")
    def test_one_platform_failure_does_not_abort_others(self, mock_fetchers, mock_write):
        import types

        good = types.SimpleNamespace(
            CHARTS={"a": "chart-a"},
            fetch_chart=lambda key, limit: [],
        )
        bad = types.SimpleNamespace(
            CHARTS={"b": "chart-b"},
            fetch_chart=lambda key, limit: (_ for _ in ()).throw(RuntimeError("network down")),
        )
        mock_fetchers.__getitem__.side_effect = lambda k: {"good": good, "bad": bad}[k]
        mock_fetchers.keys.return_value = ["good", "bad"]
        mock_write.return_value = Path("/tmp/fake.json")

        exit_code = fetch_charts.main(["--platform", "good,bad"])

        self.assertEqual(exit_code, 1)
        mock_write.assert_called_once()


if __name__ == "__main__":
    unittest.main()
