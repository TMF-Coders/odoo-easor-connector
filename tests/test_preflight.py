"""Preflight uses the real HTTP adapter with synthetic public-API envelopes."""
import copy
import unittest

from support import Session, Response, snapshot
from easor_test.core import Client, ContractError


def page(rows):
    return {"data": rows, "pagination": {"page": 1, "end": True, "total": len(rows)}}


def replies():
    s = snapshot()
    return [
        page([{"id": 81, "vatId": "ESB00000000", "vatType": "iva", "address": {"country": "ESP"}}]),
        {"data": {"allowedVatRegimes": ["01"]}},
        page([{**s["merchant"], "id": "CLIENT0001", "country": "ES"}]),
        page([{"id": "PRODUCT001", "concept": "Synthetic Service", "description": None,
               "type": ["sale"], "itemType": "service", "unit": "hour", "trackStock": False}]),
    ]


class PreflightTests(unittest.TestCase):
    def run_preflight(self, bodies):
        session = Session(*(Response(body) for body in bodies))
        client = Client("https://api.example.invalid", "synthetic", ["api.example.invalid"], session=session)
        client.preflight(81, snapshot())
        return session

    def test_verified_identity_and_catalog_are_read_only(self):
        session = self.run_preflight(replies())
        self.assertEqual(len(session.calls), 4)
        self.assertTrue(all(args[0] == "GET" for args, _ in session.calls))

    def test_other_company_or_tax_system_is_blocked(self):
        for key, value in (("vatId", "B99999999"), ("vatType", "igic"), ("address", {"country": "FIN"})):
            bodies = replies(); bodies[0]["data"][0][key] = value
            with self.subTest(field=key), self.assertRaises(ContractError):
                self.run_preflight(bodies)

    def test_unavailable_regime_is_blocked(self):
        bodies = replies(); bodies[1]["data"]["allowedVatRegimes"] = ["02"]
        with self.assertRaises(ContractError): self.run_preflight(bodies)

    def test_client_snapshot_drift_is_blocked(self):
        for key, value in (("name", "Different Client"), ("address", "Another street"), ("postalCode", "99999"),
                           ("city", "Another city"), ("vatId", "B99999999"), ("country", "FR")):
            bodies = replies(); bodies[2]["data"][0][key] = value
            with self.subTest(field=key), self.assertRaises(ContractError): self.run_preflight(bodies)

    def test_product_semantics_drift_is_blocked(self):
        for key, value in (("concept", "Different Service"), ("unit", "unit"), ("description", "Not approved"),
                           ("itemType", "product"), ("type", ["purchase"]), ("trackStock", True)):
            bodies = replies(); bodies[3]["data"][0][key] = value
            with self.subTest(field=key), self.assertRaises(ContractError): self.run_preflight(bodies)

    def test_missing_or_truncated_catalog_is_not_accepted(self):
        bodies = replies(); bodies[3] = page([])
        with self.assertRaises(ContractError): self.run_preflight(bodies)
        bodies = replies(); bodies[3]["pagination"]["total"] = 2
        with self.assertRaises(ContractError): self.run_preflight(bodies)
