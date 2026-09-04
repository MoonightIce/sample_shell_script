import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fetchers import billboard  # noqa: E402

_ROW_TEMPLATE = """
<ul class="o-chart-results-list-row // lrv-a-unstyle-list">
  <li class="o-chart-results-list__item">
    <span class="c-label  a-font-basic u-font-size-33@desktop">
      {rank}
    </span>
  </li>
  <li class="lrv-u-width-100p a-chart-result-item-container">
    <ul>
      <li class="o-chart-results-list__item">
        <h3 id="title-of-a-story" class="c-title  a-font-basic u-letter-spacing-0010">
          {title}
        </h3>
        <span class="c-label a-no-trucate a-font-secondary">
          <a href="https://www.billboard.com/artist/x/">{artist}</a>
        </span>
      </li>
    </ul>
  </li>
</ul>
"""

_SAMPLE_HTML = "<html><body>" + _ROW_TEMPLATE.format(
    rank=1, title="Choosin&#039; Texas", artist="Ella Langley"
) + _ROW_TEMPLATE.format(
    rank=2, title="BbY WOW", artist="KAROL G"
) + "</body></html>"


class TestParseHot100(unittest.TestCase):
    def test_parses_rank_title_artist(self):
        songs = billboard.parse_hot100(_SAMPLE_HTML, limit=50, chart_name="Hot 100")

        self.assertEqual(len(songs), 2)
        self.assertEqual(songs[0].rank, 1)
        self.assertEqual(songs[0].title, "Choosin' Texas")
        self.assertEqual(songs[0].artist, "Ella Langley")
        self.assertEqual(songs[0].platform, "billboard")
        self.assertIsNone(songs[0].album)
        self.assertIsNone(songs[0].play_count)

    def test_limit_truncates(self):
        songs = billboard.parse_hot100(_SAMPLE_HTML, limit=1, chart_name="Hot 100")
        self.assertEqual(len(songs), 1)

    def test_no_rows_raises(self):
        with self.assertRaises(RuntimeError):
            billboard.parse_hot100("<html><body>nothing here</body></html>", limit=50, chart_name="Hot 100")


class TestFetchChart(unittest.TestCase):
    def test_unknown_chart_raises(self):
        with self.assertRaises(ValueError):
            billboard.fetch_chart("does-not-exist")

    @patch("fetchers.billboard.get_with_retry")
    def test_fetch_chart_delegates_to_parse(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = _SAMPLE_HTML
        mock_get.return_value = mock_resp

        songs = billboard.fetch_chart("hot100", limit=50)

        self.assertEqual(len(songs), 2)


if __name__ == "__main__":
    unittest.main()
