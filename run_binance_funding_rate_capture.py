"""One-off operational script (Etapa 2.8): captures Binance's real public
funding-rate history for BTCUSDT over the exact window the FUNDING_RATE_SIGN
Hypothesis Dataset needs, and writes the sealed capture to disk so it can be
picked up by create_hypothesis_dataset(..., auxiliary_sources=...) without
needing network access again.

Run this only from a machine/network that Binance does not geoblock (the
sandboxed environment this was authored in gets HTTP 451 from
fapi.binance.com). Delete this file after running it; it is not part of the
application.
"""

from datetime import datetime, timezone
from pathlib import Path

import pipeline as p

SYMBOL = "BTCUSDT"
START_UTC = "2024-12-31T00:00:00Z"
END_EXCLUSIVE_UTC = "2026-01-02T00:00:00Z"
OUTPUT = Path("artifacts/research/funding_rate_capture_btc")

acquired_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
series, capture, raw = p.capture_binance_funding_rate(
    SYMBOL, START_UTC, END_EXCLUSIVE_UTC, acquired_at, transport=None)

OUTPUT.mkdir(parents=True, exist_ok=True)
(OUTPUT / "series.json").write_bytes(p.encoded(series))
(OUTPUT / "capture.json").write_bytes(p.encoded(capture))
(OUTPUT / "raw.json").write_bytes(raw)

print("capture_id:", capture["capture_id"])
print("days captured:", len(series))
print("wrote:", OUTPUT / "series.json", OUTPUT / "capture.json", OUTPUT / "raw.json")
