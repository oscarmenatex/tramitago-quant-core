"""Offline validation of the PAPER-only read-only connectivity boundary."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pipeline as p


class CredentialProvider:
    def __init__(self, injector):
        self.injector = injector
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if isinstance(self.injector, BaseException):
            raise self.injector
        return self.injector


class ReadOnlyTransport:
    def __init__(self, result, token=None):
        self.result = result
        self.token = token
        self.calls = []

    def __call__(self, host, path, timeout, injector):
        self.calls.append((host, path, timeout))
        if self.token is not None:
            self.assert_injection(injector)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    def assert_injection(self, injector):
        headers = injector({})
        if headers.get("opaque") is not self.token:
            raise AssertionError("opaque credential injector was not passed")


class PaperConnectivityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.checked_at = "2024-01-03T17:00:00Z"

    def injector(self):
        token = object()

        def inject(headers):
            return {**headers, "opaque": token}

        return token, inject

    def call(self, name, provider, transport, environment="PAPER",
             endpoint=p.ALPACA_PAPER_ACCOUNT_ENDPOINT):
        return p.validate_alpaca_paper_connectivity(
            self.root / name, environment, endpoint, self.checked_at, 5.0,
            provider, transport)

    def valid_response(self):
        return {
            "status_code": 200,
            "content_type": "application/json; charset=utf-8",
            "location": None,
            "body": json.dumps({
                "id": "synthetic-account-identifier", "status": "ACTIVE",
                "currency": "USD", "trading_blocked": False,
                "unnecessary_private_field": "discarded",
            }).encode(),
        }

    def test_valid_paper_get_persists_sanitized_deterministic_evidence(self):
        token, injector = self.injector()
        provider = CredentialProvider(injector)
        transport = ReadOnlyTransport(self.valid_response(), token)
        evidence = self.call("first", provider, transport)
        self.assertEqual(evidence["status"], "PAPER_CONNECTION_VERIFIED")
        self.assertEqual(transport.calls,
                         [("paper-api.alpaca.markets", "/v2/account", 5.0)])
        self.assertEqual(evidence["method"], "GET")
        self.assertFalse(evidence["order_payload_present"])
        self.assertFalse(evidence["orders_sent"])
        self.assertFalse(evidence["orders_cancelled"])
        raw = (self.root / "first" / "paper-connectivity.json").read_bytes()
        self.assertNotIn(b"synthetic-account-identifier", raw)
        self.assertNotIn(b"unnecessary_private_field", raw)
        self.assertEqual(len(evidence["account_identifier_sha256"]), 64)

        _, second_injector = self.injector()
        second = self.call("second", CredentialProvider(second_injector),
                           ReadOnlyTransport(self.valid_response()))
        self.assertEqual(evidence, second)
        self.assertEqual(raw, (self.root / "second" / "paper-connectivity.json").read_bytes())

    def test_live_unknown_and_ambiguous_endpoints_block_before_credentials(self):
        cases = (
            ("LIVE", p.ALPACA_LIVE_ACCOUNT_ENDPOINT),
            (None, p.ALPACA_PAPER_ACCOUNT_ENDPOINT),
            ("PAPER", p.ALPACA_LIVE_ACCOUNT_ENDPOINT),
            ("PAPER", "https://example.invalid/v2/account"),
            ("PAPER", p.ALPACA_PAPER_ACCOUNT_ENDPOINT + "?redirect=true"),
        )
        for index, (environment, endpoint) in enumerate(cases):
            with self.subTest(environment=environment, endpoint=endpoint):
                provider = CredentialProvider(AssertionError("must not read credentials"))
                transport = ReadOnlyTransport(AssertionError("must not use network"))
                evidence = self.call(str(index), provider, transport,
                                     environment=environment, endpoint=endpoint)
                self.assertEqual(evidence["status"], "BLOCKED_ENVIRONMENT")
                self.assertEqual(provider.calls, 0)
                self.assertEqual(transport.calls, [])

    def test_missing_or_invalid_credentials_block_without_transport_or_exposure(self):
        cases = (None, ValueError("sensitive value must not persist"))
        for index, credential_result in enumerate(cases):
            provider = CredentialProvider(credential_result)
            transport = ReadOnlyTransport(AssertionError("transport forbidden"))
            evidence = self.call(f"credentials-{index}", provider, transport)
            self.assertEqual(evidence["status"], "BLOCKED_CREDENTIALS")
            self.assertEqual(transport.calls, [])
            self.assertNotIn(b"sensitive value must not persist",
                             (self.root / f"credentials-{index}" /
                              "paper-connectivity.json").read_bytes())

    def test_rejection_timeout_exception_and_ambiguous_response_fail_closed(self):
        rejected = {"status_code": 403, "content_type": "application/json",
                    "location": None, "body": b'{}'}
        cases = (
            (rejected, "PAPER_CONNECTION_REJECTED"),
            (TimeoutError("sensitive timeout detail"), "PAPER_CONNECTION_UNKNOWN"),
            (RuntimeError("sensitive transport detail"), "PAPER_CONNECTION_UNKNOWN"),
            ({"status_code": 200}, "PAPER_CONNECTION_UNKNOWN"),
            ({"status_code": 200, "content_type": "text/html", "location": None,
              "body": b'<html></html>'}, "PAPER_CONNECTION_UNKNOWN"),
        )
        for index, (transport_result, expected) in enumerate(cases):
            _, injector = self.injector()
            evidence = self.call(f"failure-{index}", CredentialProvider(injector),
                                 ReadOnlyTransport(transport_result))
            self.assertEqual(evidence["status"], expected)
            raw = (self.root / f"failure-{index}" / "paper-connectivity.json").read_bytes()
            self.assertNotIn(b"sensitive", raw)

    def test_redirect_is_recorded_blocked_and_never_followed(self):
        redirect = {
            "status_code": 302, "content_type": "text/html",
            "location": p.ALPACA_LIVE_ACCOUNT_ENDPOINT, "body": b'',
        }
        _, injector = self.injector()
        transport = ReadOnlyTransport(redirect)
        evidence = self.call("redirect", CredentialProvider(injector), transport)
        self.assertEqual(evidence["status"], "BLOCKED_ENVIRONMENT")
        self.assertEqual(evidence["redirect_to_unapproved_host"], "BLOCKED")
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn(p.ALPACA_LIVE_ACCOUNT_ENDPOINT.encode(),
                         (self.root / "redirect" / "paper-connectivity.json").read_bytes())

    def test_https_transport_is_one_get_with_timeout_and_no_body(self):
        class Response:
            status = 200

            def getheader(self, name):
                return {"Content-Type": "application/json", "Location": None}.get(name)

            def read(self, limit):
                self.limit = limit
                return b'{}'

        class Connection:
            def __init__(self, host, timeout):
                self.host = host
                self.timeout = timeout
                self.response = Response()
                self.closed = False

            def request(self, method, path, body, headers):
                self.request_args = (method, path, body, headers)

            def getresponse(self):
                return self.response

            def close(self):
                self.closed = True

        connections = []

        def construct(host, timeout):
            connection = Connection(host, timeout)
            connections.append(connection)
            return connection

        marker = object()
        with patch("pipeline.HTTPSConnection", side_effect=construct):
            result = p.alpaca_paper_account_https_get(
                "paper-api.alpaca.markets", "/v2/account", 5.0,
                lambda headers: {**headers, "opaque": marker})
        connection = connections[0]
        self.assertEqual((connection.host, connection.timeout),
                         ("paper-api.alpaca.markets", 5.0))
        method, path, body, headers = connection.request_args
        self.assertEqual((method, path, body), ("GET", "/v2/account", None))
        self.assertIs(headers["opaque"], marker)
        self.assertTrue(connection.closed)
        self.assertEqual(result["status_code"], 200)


if __name__ == "__main__":
    unittest.main()
