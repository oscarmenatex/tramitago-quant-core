"""Focused proof for okx_funding_rate.py (Etapa 2.8, provider substitution
2026-09-29): OKX's public funding-rate history is reachable where Binance's
own is geoblocked, but paginates backward via a content-dependent "after"
cursor rather than Binance's fixed time windows. This proves the capture/
verify round trip and that verification independently re-derives each
page's cursor from the PRECEDING page's own parsed content -- never from
persisted metadata -- with synthetic data, not the real market capture
(that is a separate, real run already smoke-tested against OKX's live API).
"""

import json
import unittest

import pipeline as p


def _funding_response(day_start, count=3):
    events = []
    for index in range(count):
        funding_time_ms = (day_start + index * p.OKX_FUNDING_RATE_INTERVAL_SECONDS) * 1000
        rate = 0.0001 if index % 2 == 0 else -0.00005
        events.append({
            "formulaType": "withRate", "fundingRate": f"{rate:.8f}",
            "fundingTime": str(funding_time_ms), "instId": "BTC-USDT-SWAP",
            "instType": "SWAP", "method": "current_period", "realizedRate": f"{rate:.8f}",
        })
    return {"code": "0", "data": events, "msg": ""}


class OkxFundingRateTests(unittest.TestCase):
    def _paginated_transport(self, day_starts, page_size):
        """Simulates OKX's backward cursor pagination: each call returns up
        to `page_size` events strictly older than the requested after_ms,
        newest first, drawn from the full synthetic day grid."""
        all_events = []
        for day in day_starts:
            all_events.extend(json.loads(json.dumps(_funding_response(day)))["data"])
        all_events.sort(key=lambda item: int(item["fundingTime"]), reverse=True)

        def transport(url, _headers, _timeout):
            from urllib.parse import parse_qs, urlsplit
            query = parse_qs(urlsplit(url).query)
            after_ms = int(query["after"][0])
            page = [item for item in all_events if int(item["fundingTime"]) < after_ms][:page_size]
            return json.dumps({"code": "0", "data": page, "msg": ""}).encode("utf-8"), {"Date": "fixture"}
        return transport

    def test_single_page_round_trip(self):
        day_starts = list(range(p.epoch("2026-06-23T00:00:00Z"), p.epoch("2026-06-30T00:00:00Z"), 86400))
        transport = self._paginated_transport(day_starts, page_size=100)
        series, capture, raw = p.capture_okx_funding_rate(
            "BTC-USDT-SWAP", "2026-06-23T00:00:00Z", "2026-06-30T00:00:00Z",
            "2026-09-29T00:00:00Z", transport=transport)
        self.assertEqual(len(series), 7)
        self.assertEqual(len(capture["responses"]), 1)
        reloaded = p.verified_okx_funding_rate_capture(raw, capture)
        self.assertEqual(reloaded, series)

    def test_multi_page_round_trip_and_tamper_detection(self):
        day_starts = list(range(p.epoch("2026-06-23T00:00:00Z"), p.epoch("2026-09-29T00:00:00Z"), 86400))
        transport = self._paginated_transport(day_starts, page_size=9)
        series, capture, raw = p.capture_okx_funding_rate(
            "BTC-USDT-SWAP", "2026-06-23T00:00:00Z", "2026-09-29T00:00:00Z",
            "2026-09-29T00:00:00Z", transport=transport)
        self.assertEqual(len(series), 98)
        self.assertGreater(len(capture["responses"]), 1)
        reloaded = p.verified_okx_funding_rate_capture(raw, capture)
        self.assertEqual(reloaded, series)

        tampered_capture = json.loads(json.dumps(capture))
        tampered_capture["responses"][0]["after_ms"] += 1000
        with self.assertRaises(ValueError):
            p.verified_okx_funding_rate_capture(raw, tampered_capture)

        raw_payload = json.loads(raw)
        raw_payload["captures"][-1]["response_base64"] = raw_payload["captures"][0]["response_base64"]
        with self.assertRaises(ValueError):
            p.verified_okx_funding_rate_capture(json.dumps(raw_payload).encode("utf-8"), capture)

    def test_incomplete_day_coverage_fails_closed(self):
        all_days = list(range(p.epoch("2026-06-23T00:00:00Z"), p.epoch("2026-06-30T00:00:00Z"), 86400))
        day_starts = [day for day in all_days if day != all_days[3]]  # drop one day mid-range
        transport = self._paginated_transport(day_starts, page_size=100)

        with self.assertRaises(ValueError):
            p.capture_okx_funding_rate(
                "BTC-USDT-SWAP", "2026-06-23T00:00:00Z", "2026-06-30T00:00:00Z",
                "2026-09-29T00:00:00Z", transport=transport)


if __name__ == "__main__":
    unittest.main()
