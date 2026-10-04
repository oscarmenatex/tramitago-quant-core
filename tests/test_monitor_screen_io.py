"""What the screen reads: a window applied while reading, and nothing read as a zero."""

import tempfile
import unittest
from pathlib import Path

from tramitago_quant_core.research.monitor_screen_io import load_exposure_returns

WINDOW = {"start_utc": "2018-03-01T00:00:00Z", "end_exclusive_utc": "2018-03-06T00:00:00Z"}
ROWS = [
    ("2018-02-28T00:00:00Z", "0.0100"),
    ("2018-03-01T00:00:00Z", "0.0200"),
    ("2018-03-02T00:00:00Z", "-0.0300"),
    ("2018-03-05T00:00:00Z", ""),
    ("2018-03-06T00:00:00Z", "0.0500"),
    ("2018-03-07T00:00:00Z", "0.0600"),
]


def _dataset(rows=ROWS, column="forward_return_1d"):
    root = Path(tempfile.mkdtemp())
    lines = [f"instrument,timestamp,close,{column}"] + [f"X,{ts},10,{value}" for ts, value in rows]
    (root / "dataset.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


class LoadExposureReturnsTests(unittest.TestCase):
    def test_only_rows_inside_the_window_are_ever_held(self):
        out = load_exposure_returns(_dataset(), "forward_return_1d", WINDOW)
        self.assertEqual(set(out), {"2018-03-01", "2018-03-02"})

    def test_the_window_is_half_open_so_the_end_day_belongs_to_the_next_window(self):
        out = load_exposure_returns(_dataset(), "forward_return_1d", WINDOW)
        self.assertNotIn("2018-03-06", out)
        holdout = {"start_utc": "2018-03-06T00:00:00Z", "end_exclusive_utc": "2018-03-08T00:00:00Z"}
        later = load_exposure_returns(_dataset(), "forward_return_1d", holdout)
        self.assertEqual(set(later), {"2018-03-06", "2018-03-07"})
        self.assertFalse(set(out) & set(later))

    def test_a_row_with_no_value_is_skipped_and_never_read_as_zero(self):
        out = load_exposure_returns(_dataset(), "forward_return_1d", WINDOW)
        self.assertNotIn("2018-03-05", out)
        self.assertEqual(out["2018-03-01"], 0.02)
        self.assertEqual(out["2018-03-02"], -0.03)

    def test_the_named_column_is_the_one_read(self):
        root = _dataset(column="forward_spread_return_1d")
        self.assertEqual(len(load_exposure_returns(root, "forward_spread_return_1d", WINDOW)), 2)
        self.assertEqual(load_exposure_returns(root, "forward_return_1d", WINDOW), {})


if __name__ == "__main__":
    unittest.main()
