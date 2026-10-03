"""Capture a published economic series from FRED, sealed and re-verifiable offline.

WHY THIS EXISTS. The equity risk premium cleared five of six admission gates and
was denied on R3 alone: an unconditional premium has no degradation variable that
is not its own P&L, and the monitor contract refuses P&L surveillance whatever
its latency. The credit premium was declared instead because its compensation is
QUOTED -- a third party publishes the high-yield option-adjusted spread every
business day, and would keep publishing it if the position were never opened.
This module is what makes that monitor real rather than asserted. Without it R3
returns NOT_EVALUABLE and the credit premium lands exactly where the equity one
did.

VALUES STAY STRINGS, exactly as served. A spread is a decimal quantity and
turning it into a float on the way in would make the sealed artifact depend on
this process's floating point, which is the one thing a seal is for.

UNITS ARE NOT CONVERTED AND ARE NOT GUESSED. FRED publishes this family in
PERCENT: 3.54 means 3.54%, which is 354 basis points. Nothing here multiplies or
divides, and a caller that wants basis points converts at the point of use where
the choice is visible.

MISSING OBSERVATIONS ARE RECORDED, NOT DROPPED. Silently skipping them would let
a gappy series read as complete, and for a MONITOR a gap is the most important
thing in the file: it is a day nobody could have been watching. They are carried
through as None and counted.

THE TWO ENDPOINTS DISAGREE ABOUT HOW TO WRITE "MISSING", AND THE DOCSTRING HERE
ONCE ASSERTED THE WRONG ONE. The API writes ".", the graph CSV leaves the field
EMPTY -- "2023-12-25," on Christmas. This module was written claiming "." for
both, on a convention nobody had checked, and the first real response caught it:
an empty string sailed through as a VALUE. Both forms are now missing.

AND THE GRAPH CSV IGNORES THE DATES IT IS GIVEN. MEASURED 2026-10-02: asking for
cosd=2018-01-02&coed=2020-12-31 and for cosd=2021-01-01&coed=2023-10-02 returns
the IDENTICAL 794 rows, 2023-10-03 to 2026-10-01 -- the last three years, every
time. It cannot be paginated and it cannot reach a window older than that, which
is why the keyed API exists here at all: a monitor whose history starts in late
2023 has never seen a credit crisis, and the representativeness gate would be
right to refuse it.

THE API KEY NEVER ENTERS THE SEALED URL. FRED takes it as a query parameter, so
building it into the recorded URL would write a credential into an artifact that
is meant to be shared and re-verified. The URL is sealed WITHOUT it and the
transport appends it, exactly as Alpaca's injector adds headers this module
never sees. A later verifier re-fetches that URL with their OWN key, which is
the correct arrangement rather than a limitation.

THE SERIES IDENTITY IS CHECKED, NEVER ASSUMED. The CSV's second column header is
the series FRED actually served. If it does not match what was asked for, this
raises rather than returning somebody else's numbers under the requested name.

REACHABILITY IS NOT UNIVERSAL. Measured 2026-10-02: fred.stlouisfed.org accepts
a TCP connection from the evidence host and then never answers (HTTP 000 after
22 seconds, curl and urllib alike, with and without a browser user agent), while
the same request from the project's Oracle VM returns 200 in 0.4s. That is why
`transport` exists here as it does in every other provider: the bytes may be
fetched anywhere, and the SEALING happens on the evidence host. Which socket
received them is irrelevant to verification, because the URL is deterministic
and anyone with access can re-fetch and compare the hash -- which is the whole
point of storing the response rather than the conclusion.
"""

import base64
import csv
import io
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tramitago_quant_core.shared.util import digest, encoded

FRED_SERIES_CAPTURE_SCHEMA_VERSION = "1"
FRED_SERIES_CAPTURE_KIND = "fred-series-capture"
FRED_SERIES_SOURCE = "Federal Reserve Bank of St. Louis, FRED graph CSV export"
FRED_SERIES_HOST = "fred.stlouisfed.org"
# The API writes ".", the graph CSV leaves the field empty. Both are missing.
FRED_MISSING_FORMS = (".", "")

# The monitor variable declared in HYPOTHESIS|cf55e397: ICE BofA US High Yield
# Index Option-Adjusted Spread, daily, in percent.
FRED_HIGH_YIELD_OAS = "BAMLH0A0HYM2"

FRED_API_HOST = "api.stlouisfed.org"
ENDPOINT_GRAPH_CSV = "GRAPH_CSV"    # free, no key, LAST THREE YEARS ONLY
ENDPOINT_API = "API"                # keyed, full history
FRED_ENDPOINTS = (ENDPOINT_GRAPH_CSV, ENDPOINT_API)


def fred_series_url(series_id, start_date, end_date_inclusive):
    """Deterministic, so the capture can be re-fetched and compared by anyone."""
    for name, value in (("series id", series_id), ("start date", start_date),
                        ("end date", end_date_inclusive)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"A FRED capture needs a {name}")
    return (f"https://{FRED_SERIES_HOST}/graph/fredgraph.csv?"
            + urlencode({"id": series_id, "cosd": start_date, "coed": end_date_inclusive}))


def fred_api_url(series_id, start_date, end_date_inclusive):
    """The keyed endpoint's URL WITHOUT the key.

    The key is appended by the transport and never appears here, so it never
    reaches the sealed capture. A verifier re-fetches this URL with their own.
    """
    for name, value in (("series id", series_id), ("start date", start_date),
                        ("end date", end_date_inclusive)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"A FRED capture needs a {name}")
    return (f"https://{FRED_API_HOST}/fred/series/observations?"
            + urlencode({"series_id": series_id, "observation_start": start_date,
                         "observation_end": end_date_inclusive, "file_type": "json"}))


def parse_fred_json(raw, series_id):
    """The keyed endpoint's response. Same row shape as the CSV path.

    The JSON carries no series id, so unlike the CSV there is nothing here to
    check it against -- which is a weakness of the endpoint, not of this parser,
    and is stated rather than papered over. The requested id is in the sealed
    URL, which is the only place it can be audited from.
    """
    import json as _json
    try:
        payload = _json.loads(raw)
    except (UnicodeError, _json.JSONDecodeError) as error:
        raise ValueError("FRED API response is not JSON") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("observations"), list):
        raise ValueError("Malformed FRED API response")
    rows, seen = [], set()
    for item in payload["observations"]:
        if not isinstance(item, dict) or "date" not in item or "value" not in item:
            raise ValueError("Malformed FRED API observation")
        date, value = str(item["date"]).strip(), str(item["value"]).strip()
        if len(date) != 10 or date[4] != "-" or date[7] != "-":
            raise ValueError(f"Invalid FRED observation date: {date!r}")
        if date in seen:
            raise ValueError(f"FRED returned a duplicate observation date: {date}")
        seen.add(date)
        rows.append({"observation_date": date,
                     "value": None if value in FRED_MISSING_FORMS else value})
    if not rows:
        raise ValueError("FRED returned no observations")
    if rows != sorted(rows, key=lambda row: row["observation_date"]):
        raise ValueError("FRED observations are not in ascending date order")
    return rows


def _live_get(url, timeout_seconds):
    with urlopen(Request(url, headers={"User-Agent": "tramitago-quant-core"}),
                 timeout=timeout_seconds) as response:
        if response.status != 200:
            raise ValueError(f"FRED responded {response.status}")
        return response.read()


def _response(transport, url, timeout_seconds=30):
    raw = (_live_get(url, timeout_seconds) if transport is None else transport(url))
    if not isinstance(raw, bytes):
        raise ValueError("A FRED transport must return raw bytes")
    return raw


def parse_fred_csv(raw, series_id):
    """Rows exactly as served, with missing observations kept as None.

    Returns [{"observation_date": "2024-01-02", "value": "3.54" | None}, ...].
    """
    try:
        text = raw.decode("utf-8")
    except (UnicodeError, AttributeError) as error:
        raise ValueError("FRED response is not UTF-8 text") from error
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("FRED response is empty")
    if len(header) != 2 or header[0].strip() not in ("observation_date", "DATE"):
        raise ValueError(f"Unexpected FRED header: {header}")
    # The served series, not the requested one. FRED answering with a different
    # id would otherwise be invisible: the numbers would simply be wrong, under
    # the right name, inside a correctly sealed artifact.
    if header[1].strip() != series_id:
        raise ValueError(
            f"FRED served series {header[1].strip()!r} when {series_id!r} was requested")

    rows, seen = [], set()
    for line in reader:
        if not line:
            continue
        if len(line) != 2:
            raise ValueError(f"Malformed FRED row: {line}")
        date, value = line[0].strip(), line[1].strip()
        if len(date) != 10 or date[4] != "-" or date[7] != "-":
            raise ValueError(f"Invalid FRED observation date: {date!r}")
        if date in seen:
            raise ValueError(f"FRED returned a duplicate observation date: {date}")
        seen.add(date)
        rows.append({"observation_date": date,
                     "value": None if value in FRED_MISSING_FORMS else value})
    if not rows:
        raise ValueError("FRED returned no observations")
    if rows != sorted(rows, key=lambda row: row["observation_date"]):
        raise ValueError("FRED observations are not in ascending date order")
    return rows


def _capture_content(series_id, endpoint, capture_period, acquired_at,
                     response_meta, observed):
    content = {
        "schema_version": FRED_SERIES_CAPTURE_SCHEMA_VERSION,
        "kind": FRED_SERIES_CAPTURE_KIND,
        "source": FRED_SERIES_SOURCE,
        "series_id": series_id,
        "endpoint": endpoint,
        "units": "percent, as published; nothing here converts or rescales",
        "capture_period": capture_period,
        "acquired_at": acquired_at,
        "response": response_meta,
        "observations": observed,
        "missing_observations": sum(1 for row in observed if row["value"] is None),
    }
    return {**content, "capture_id": "FRED_SERIES_CAPTURE|" + digest(encoded(content))}


def capture_fred_series(series_id, start_date, end_date_inclusive, acquired_at, *,
                        transport=None, endpoint=ENDPOINT_GRAPH_CSV):
    """Capture and seal one FRED series. Returns (rows, capture, raw_bytes).

    `endpoint` is explicit and has no clever default: GRAPH_CSV needs no key and
    serves only the last three years, API needs one and serves the lot. Choosing
    silently would let a three-year monitor stand in for a full-history one,
    which for a tail-detecting monitor is the difference that matters.
    """
    if not isinstance(acquired_at, str) or not acquired_at.strip():
        raise ValueError("A FRED capture must say when it was acquired")
    if endpoint not in FRED_ENDPOINTS:
        raise ValueError(f"Unknown FRED endpoint {endpoint!r}; expected one of "
                         + ", ".join(FRED_ENDPOINTS))
    url = (fred_series_url(series_id, start_date, end_date_inclusive)
           if endpoint == ENDPOINT_GRAPH_CSV
           else fred_api_url(series_id, start_date, end_date_inclusive))
    raw = _response(transport, url)
    rows = (parse_fred_csv(raw, series_id) if endpoint == ENDPOINT_GRAPH_CSV
            else parse_fred_json(raw, series_id))

    capture = _capture_content(
        series_id, endpoint,
        {"start_date": start_date, "end_date_inclusive": end_date_inclusive},
        acquired_at,
        {"url": url, "response_sha256": digest(raw)},
        rows)
    stored = encoded({"schema_version": FRED_SERIES_CAPTURE_SCHEMA_VERSION,
                      "response_sha256": digest(raw),
                      "response_base64": base64.b64encode(raw).decode("ascii")})
    return rows, capture, stored.encode("utf-8") if isinstance(stored, str) else stored


def verified_fred_series_capture(raw_bytes, capture):
    """Reproduce the series from the stored bytes ALONE, trusting none of the
    parsed values recorded alongside them.

    Signature (raw_bytes, capture) -> {date: value} is the generic
    auxiliary-verifier contract every other provider here satisfies.
    """
    if not isinstance(capture, dict):
        raise ValueError("FRED capture is invalid")
    content = {key: capture[key] for key in (
        "schema_version", "kind", "source", "series_id", "endpoint", "units",
        "capture_period", "acquired_at", "response", "observations", "missing_observations")
        if key in capture}
    if (set(capture) != set(content) | {"capture_id"}
            or capture.get("schema_version") != FRED_SERIES_CAPTURE_SCHEMA_VERSION
            or capture.get("kind") != FRED_SERIES_CAPTURE_KIND
            or capture.get("source") != FRED_SERIES_SOURCE
            or capture.get("capture_id") != "FRED_SERIES_CAPTURE|" + digest(encoded(content))):
        raise ValueError("FRED capture metadata is invalid")

    import json
    try:
        stored = json.loads(raw_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("FRED raw capture is invalid") from error
    if (not isinstance(stored, dict)
            or set(stored) != {"schema_version", "response_sha256", "response_base64"}
            or stored["schema_version"] != FRED_SERIES_CAPTURE_SCHEMA_VERSION):
        raise ValueError("FRED raw capture is incomplete")
    raw = base64.b64decode(stored["response_base64"])
    if digest(raw) != stored["response_sha256"] \
            or digest(raw) != capture["response"]["response_sha256"]:
        raise ValueError("FRED raw capture does not match its recorded digest")

    rows = (parse_fred_csv(raw, capture["series_id"])
            if capture["endpoint"] == ENDPOINT_GRAPH_CSV
            else parse_fred_json(raw, capture["series_id"]))
    if rows != capture["observations"]:
        raise ValueError("FRED capture observations do not reproduce from the raw bytes")
    return {row["observation_date"]: row["value"] for row in rows}
