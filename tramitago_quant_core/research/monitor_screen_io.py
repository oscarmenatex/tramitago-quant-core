"""What the monitor screen reads from disk, kept apart from the scanner so the scanner reads nothing.

Two things only: the return column of a SEALED exposure dataset, cut to a window AT LOAD TIME, and
a FRED series through the same sealed capture the premium engine uses, so a series is fetched once,
stored with its hash, and re-verifiable offline afterwards.

THE WINDOW IS APPLIED WHILE READING, not after. A loader asked for the discovery window never holds
a holdout row in memory, so the scan cannot be handed one by mistake and the holdout loader is a
different call that is only made after its marker is sealed.
"""

import csv
from pathlib import Path

from tramitago_quant_core.research.premium_runner import _sealed_fred


def load_exposure_returns(dataset_dir, column, window):
    """{date: return} for the rows of a sealed dataset that fall inside `window`.

    A row with no value in the column (a warmup row, or the last row, which has no next day) is
    skipped, never read as zero.
    """
    start, end = window["start_utc"][:10], window["end_exclusive_utc"][:10]
    path = Path(dataset_dir) / "dataset.csv"
    out = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            day = row["timestamp"][:10]
            if not (start <= day < end):
                continue
            value = row.get(column)
            if value in (None, ""):
                continue
            out[day] = float(value)
    return out


def load_series(checks_dir, series_id, relay_fred, start, end, now):
    """A FRED series as {date: value or None}, from its sealed capture, fetching it only once."""
    return _sealed_fred(Path(checks_dir), series_id, relay_fred, start, end, now)
