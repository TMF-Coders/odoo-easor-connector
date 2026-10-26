import copy
import unittest
import requests
from support import Session, Response, source, snapshot, document
from easor_test.core import Client, ContractError, RemoteError, build_payload, cents, decimal, fingerprint, nif, verify_document


class PayloadTests(unittest.TestCase):
    def test_units_and_snapshot(self):
        payload = build_payload(source())
        self.assertEqual(payload["lines"][0]["price"], 10000)
        self.assertEqual(payload["lines"][0]["vat"], 2100)
        self.assertEqual(payload["paymentMethod"], "cash")
        self.assertFalse(payload["paid"])

    def test_half_up_money(self):
        self.assertEqual(cents("1.005"), 101)
        self.assertEqual(cents("-1.005"), -101)
        with self.assertRaises(ContractError):
            cents("1.001", exact=True)

    def test_non_finite_and_boolean_rejected(self):
        for value in (True, False, "nan", "inf", "-inf", None, "abc"):
            with self.subTest(value=value), self.assertRaises(ContractError):
                decimal(value)

    def test_supported_vat(self):
        for rate in (0, 4, 10, 21):
            s = source(); s["lines"][0]["vat"] = rate
            self.assertEqual(build_payload(s)["lines"][0]["vat"], rate * 100)

    def test_unsupported_semantics(self):
        for key, val in (("currency", "USD"), ("country", "FR"), ("discount", 1),
                         ("multiple_due_dates", True), ("payment_method", "bankTransfer"),
                         ("date", "2026-02-30"), ("due_date", "2026-01-01"), ("activity_id", True),
                         ("activity_id", 0), ("merchant_id", "../bad")):
            with self.subTest(key=key):
                s = source(); s[key] = val
                with self.assertRaises(ContractError): build_payload(s)

    def test_unsupported_lines(self):
        for key, val in (("quantity", 0), ("price", 0), ("price", -10), ("price", 1.001),
                         ("quantity", "nan"), ("vat", -5), ("vat", 2), ("special_tax", "N2"),
                         ("discount", 5), ("price_included", True)):
            with self.subTest(key=key, value=val):
                s = source(); s["lines"][0][key] = val
                with self.assertRaises(ContractError): build_payload(s)

    def test_line_limits(self):
        for size in (0, 51):
            s = source(); s["lines"] *= size
            with self.assertRaises(ContractError): build_payload(s)
        s = source(); s["lines"] *= 50
        self.assertEqual(len(build_payload(s)["lines"]), 50)

    def test_fingerprint_and_nif(self):
        self.assertEqual(fingerprint({"a": 1, "b": 2}), fingerprint({"b": 2, "a": 1}))
        self.assertNotEqual(fingerprint({"a": 1}), fingerprint({"a": 2}))
        self.assertEqual(nif("ES B-00000000"), "B00000000")


class ClientTests(unittest.TestCase):
    def client(self, session):
        return Client("https://api.example.invalid", "secret-not-for-logs", ["api.example.invalid"], session=session)

    def test_origin_security(self):
        for origin in ("http://api.example.invalid", "https://evil.invalid", "https://api.example.invalid/api",
                       "https://u:p@api.example.invalid", "https://api.example.invalid?x=1", "https://api.example.invalid:8443",
                       "https://api.example.invalid#x", "https://api.example.invalid:abc"):
            with self.subTest(origin=origin), self.assertRaises(ContractError):
                Client(origin, "secret", ["api.example.invalid"])
        with self.assertRaises(ContractError): Client("https://api.example.invalid", "bad\nkey", ["api.example.invalid"])
        with self.assertRaises(ContractError): Client("https://api.example.invalid", "secret", [])

    def test_no_redirect_no_proxy_explicit_timeouts(self):
        response = Response({"data": {"id": "INVOICE001"}}, status=201)
        session = Session(response)
        reply = self.client(session).request("POST", "/api/v1/business/81/invoice/draft", payload=build_payload(source()))
        self.assertEqual(reply["data"]["id"], "INVOICE001")
        self.assertFalse(session.trust_env)
        options = session.calls[0][1]
        self.assertFalse(options["allow_redirects"])
        self.assertEqual(options["timeout"], (5, 30))
        self.assertEqual(options["headers"]["x-easor-api-key"], "secret-not-for-logs")
        self.assertTrue(response.closed)

    def test_mutations_never_retry(self):
        for error in (Response(status=429), Response(status=409), Response(status=500), Response(status=302), requests.Timeout("secret-not-for-logs")):
            session = Session(error)
            with self.assertRaises(RemoteError) as caught:
                self.client(session).request("POST", "/api/v1/business/81/invoice/draft", payload={})
            self.assertEqual(len(session.calls), 1)
            self.assertNotIn("secret-not-for-logs", str(caught.exception))

    def test_json_contract_and_bounds(self):
        for body in (b"[]", b"{", b'{"value":NaN}', b"x" * (5 * 1024 * 1024 + 1)):
            response = Response(body=body)
            with self.assertRaises(ContractError): self.client(Session(response)).request("GET", "/api/v1/business")
            self.assertTrue(response.closed)

    def test_pdf_requires_original_bytes_and_type(self):
        good = b"%PDF-1.4\nsynthetic"
        self.assertEqual(self.client(Session(Response(body=good, content_type="application/pdf"))).request("GET", "/api/v1/business/81/income/INVOICE001/pdf", pdf=True), good)
        for body, content_type in ((b"<html>error", "application/pdf"), (good, "application/json")):
            with self.assertRaises(ContractError): self.client(Session(Response(body=body, content_type=content_type))).request("GET", "/api/v1/business/81/income/INVOICE001/pdf", pdf=True)

    def test_paths_cannot_escape(self):
        for path in ("https://evil.invalid", "/v2/business", "/api/v1/business/../secrets", "/api/v1/business?x=1"):
            with self.assertRaises(ContractError): self.client(Session()).request("GET", path)

    def test_pagination_is_exhaustive(self):
        session = Session(Response({"data": [{"id": "a"}], "pagination": {"page": 1, "end": False, "total": 2}}),
                          Response({"data": [{"id": "b"}], "pagination": {"page": 2, "end": True, "total": 2}}))
        self.assertEqual([r["id"] for r in self.client(session).rows("/api/v1/business")], ["a", "b"])

    def test_truncated_repeated_or_changing_pages_rejected(self):
        bad_pages = [
            [{"data": [{"id": "a"}], "pagination": {"page": 1, "end": True, "total": 2}}],
            [{"data": [], "pagination": {"page": 1, "end": False, "total": 2}}],
            [{"data": [], "pagination": {"page": 2, "end": True, "total": 0}}],
            [{"data": [{"id": "a"}], "pagination": {"page": 1, "end": False, "total": 2}}, {"data": [{"id": "a"}], "pagination": {"page": 2, "end": True, "total": 2}}],
            [{"data": [{"id": "a"}], "pagination": {"page": 1, "end": False, "total": 2}}, {"data": [{"id": "b"}], "pagination": {"page": 2, "end": True, "total": 3}}],
        ]
        for pages in bad_pages:
            with self.subTest(pages=pages), self.assertRaises(ContractError):
                list(self.client(Session(*(Response(p) for p in pages))).rows("/api/v1/business"))


class VerificationTests(unittest.TestCase):
    def test_draft_and_issued(self):
        for issued in (False, True):
            self.assertTrue(verify_document(document(issued), snapshot(), 81, issued=issued))

    def test_wrong_fiscal_context_blocked(self):
        for key, value in (("businessId", 82), ("netAmount", 19999), ("totalAmount", 24201), ("isDraft", True),
                           ("issued", False), ("number", ""), ("deleted", True), ("voided", True),
                           ("date", "2026-10-27"), ("currency", "USD"), ("paymentMethod", "3"), ("fractions", [{"amount": 10}])):
            d = document(True); d[key] = value
            with self.subTest(key=key), self.assertRaises(ContractError): verify_document(d, snapshot(), 81, issued=True)

    def test_changed_line_or_client_blocked_even_with_equal_totals(self):
        for key, val in (("price", 50), ("quantity", 4), ("concept", "Another service"), ("itemType", "product"),
                         ("vat", -5), ("retention", 1500), ("discount", {"amount": 100}), ("subtype", "supplied"),
                         ("notSubjectCause", "N2")):
            d = document(); d["originalProducts"][0][key] = val
            with self.subTest(key=key), self.assertRaises(ContractError): verify_document(d, snapshot(), 81, issued=False)
        d = document(); d["merchant"]["vatId"] = "B22222222"
        with self.assertRaises(ContractError): verify_document(d, snapshot(), 81, issued=False)
