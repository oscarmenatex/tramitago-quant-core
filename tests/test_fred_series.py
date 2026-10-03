"""The provider that decides whether the credit premium has a monitor at all.

The equity risk premium cleared five of six gates and was denied on R3 alone,
because an unconditional premium has no degradation variable that is not its own
P&L. The credit premium was declared instead because its compensation is QUOTED.
If this capture does not work, R3 returns NOT_EVALUABLE and that Hypothesis
lands exactly where the equity one did -- having failed at the one thing it was
chosen for.
"""

import base64
import json
import unittest

from tramitago_quant_core.data.fred_series import (
    capture_fred_series, fred_series_url, parse_fred_csv, verified_fred_series_capture,
    FRED_HIGH_YIELD_OAS, fred_api_url, parse_fred_json, ENDPOINT_GRAPH_CSV,
)

SERIES = FRED_HIGH_YIELD_OAS
CSV = (f"observation_date,{SERIES}\n"
       "2024-01-01,.\n"          # a market holiday, as FRED writes it
       "2024-01-02,3.54\n"
       "2024-01-03,3.71\n"
       "2024-01-04,3.69\n").encode("utf-8")


def _transport(payload=CSV):
    return lambda url: payload


class ParsingTests(unittest.TestCase):
    def test_values_stay_strings_exactly_as_served(self):
        # A float here would make the sealed artifact depend on this process's
        # floating point, which is the one thing a seal exists to prevent.
        rows = parse_fred_csv(CSV, SERIES)
        self.assertEqual(rows[1], {"observation_date": "2024-01-02", "value": "3.54"})
        self.assertIsInstance(rows[1]["value"], str)

    def test_a_missing_observation_is_kept_rather_than_dropped(self):
        # For a MONITOR a gap is the most important thing in the file: it is a
        # day nobody could have been watching. Dropping it would let a gappy
        # series read as complete.
        rows = parse_fred_csv(CSV, SERIES)
        self.assertEqual(len(rows), 4)
        self.assertIsNone(rows[0]["value"])

    def test_a_series_FRED_did_not_serve_is_refused(self):
        # The dangerous case is silence: the numbers would simply be wrong,
        # under the right name, inside a correctly sealed artifact.
        other = CSV.replace(SERIES.encode(), b"BAMLC0A0CM")
        with self.assertRaises(ValueError) as caught:
            parse_fred_csv(other, SERIES)
        self.assertIn("served series", str(caught.exception))

    def test_malformed_responses_are_refused(self):
        for payload in (b"", b"not,a,fred,header\n",
                        f"observation_date,{SERIES}\n2024-01-02\n".encode(),
                        f"observation_date,{SERIES}\n24-01-02,3.5\n".encode()):
            with self.assertRaises(ValueError):
                parse_fred_csv(payload, SERIES)

    def test_duplicate_and_unordered_dates_are_refused(self):
        dup = f"observation_date,{SERIES}\n2024-01-02,3.5\n2024-01-02,3.6\n".encode()
        backwards = f"observation_date,{SERIES}\n2024-01-03,3.6\n2024-01-02,3.5\n".encode()
        for payload in (dup, backwards):
            with self.assertRaises(ValueError):
                parse_fred_csv(payload, SERIES)


class UrlTests(unittest.TestCase):
    def test_the_url_is_deterministic_so_anyone_can_re_fetch_and_compare(self):
        first = fred_series_url(SERIES, "2018-01-02", "2026-10-01")
        self.assertEqual(first, fred_series_url(SERIES, "2018-01-02", "2026-10-01"))
        for part in (SERIES, "cosd=2018-01-02", "coed=2026-10-01"):
            self.assertIn(part, first)

    def test_an_incomplete_request_is_refused(self):
        for args in ((None, "2018-01-02", "2026-10-01"), (SERIES, "", "2026-10-01"),
                     (SERIES, "2018-01-02", None)):
            with self.assertRaises(ValueError):
                fred_series_url(*args)


class CaptureTests(unittest.TestCase):
    def _capture(self):
        return capture_fred_series(SERIES, "2024-01-01", "2024-01-04",
                                   "2026-10-02T00:00:00.000000Z", transport=_transport())

    def test_the_capture_counts_its_own_gaps(self):
        _, capture, _ = self._capture()
        self.assertEqual(capture["missing_observations"], 1)

    def test_the_units_are_recorded_and_nothing_is_rescaled(self):
        rows, capture, _ = self._capture()
        self.assertIn("percent", capture["units"])
        self.assertEqual(rows[1]["value"], "3.54")

    def test_it_reproduces_from_the_raw_bytes_alone(self):
        _, capture, raw = self._capture()
        series = verified_fred_series_capture(raw, capture)
        self.assertEqual(series["2024-01-02"], "3.54")
        self.assertIsNone(series["2024-01-01"])

    def test_altering_a_recorded_value_is_caught(self):
        # The parsed observations are re-derived from the bytes, never trusted.
        _, capture, raw = self._capture()
        tampered = json.loads(json.dumps(capture))
        tampered["observations"][1]["value"] = "0.01"
        with self.assertRaises(ValueError):
            verified_fred_series_capture(raw, tampered)

    def test_altering_the_stored_bytes_is_caught(self):
        _, capture, raw = self._capture()
        stored = json.loads(raw)
        stored["response_base64"] = base64.b64encode(
            CSV.replace(b"3.54", b"9.99")).decode("ascii")
        with self.assertRaises(ValueError):
            verified_fred_series_capture(json.dumps(stored).encode(), capture)

    def test_a_transport_returning_anything_but_bytes_is_refused(self):
        with self.assertRaises(ValueError):
            capture_fred_series(SERIES, "2024-01-01", "2024-01-04",
                                "2026-10-02T00:00:00.000000Z",
                                transport=lambda url: CSV.decode())

    def test_a_capture_must_say_when_it_was_acquired(self):
        with self.assertRaises(ValueError):
            capture_fred_series(SERIES, "2024-01-01", "2024-01-04", "",
                                transport=_transport())


if __name__ == "__main__":
    unittest.main()


class RealResponseTests(unittest.TestCase):
    """What the live endpoints actually did, as opposed to what was assumed.

    Both of these were written only after a real response contradicted this
    module's own docstring.
    """

    def test_the_graph_csv_writes_missing_as_EMPTY_not_as_a_dot(self):
        # Measured: "2023-12-25," on Christmas. The module claimed "." for both
        # endpoints, on a convention nobody had checked, and an empty string
        # sailed through as a VALUE in the first real capture.
        payload = (f"observation_date,{SERIES}\n"
                   "2023-12-22,3.39\n2023-12-25,\n2023-12-26,3.37\n").encode()
        rows = parse_fred_csv(payload, SERIES)
        self.assertIsNone(rows[1]["value"])
        self.assertEqual(rows[0]["value"], "3.39")

    def test_the_api_writes_missing_as_a_dot(self):
        payload = json.dumps({"observations": [
            {"date": "2023-12-22", "value": "3.39"},
            {"date": "2023-12-25", "value": "."}]}).encode()
        rows = parse_fred_json(payload, SERIES)
        self.assertIsNone(rows[1]["value"])

    def test_the_endpoint_is_explicit_because_one_of_them_sees_no_crisis(self):
        # MEASURED: the graph CSV IGNORES cosd and coed entirely -- two
        # different windows returned the identical 794 rows, 2023-10-03 to
        # 2026-10-01. A monitor built on it has never observed a credit crisis,
        # so the choice may not be made by a default.
        with self.assertRaises(ValueError):
            capture_fred_series(SERIES, "2018-01-02", "2026-10-01",
                                "2026-10-02T00:00:00.000000Z",
                                transport=_transport(), endpoint="WHATEVER")

    def test_the_api_key_never_reaches_the_sealed_url(self):
        # FRED takes the key as a QUERY PARAMETER, so building it in would write
        # a credential into an artifact meant to be shared and re-verified.
        url = fred_api_url(SERIES, "2018-01-02", "2026-10-01")
        for secret in ("api_key", "apikey", "key="):
            self.assertNotIn(secret, url)
        self.assertIn(SERIES, url)

    def test_the_endpoint_travels_into_the_seal(self):
        # Otherwise a three-year capture and a full-history one are
        # indistinguishable after the fact.
        _, capture, raw = capture_fred_series(
            SERIES, "2024-01-01", "2024-01-04", "2026-10-02T00:00:00.000000Z",
            transport=_transport(), endpoint=ENDPOINT_GRAPH_CSV)
        self.assertEqual(capture["endpoint"], ENDPOINT_GRAPH_CSV)
        self.assertEqual(len(verified_fred_series_capture(raw, capture)), 4)
