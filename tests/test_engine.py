import unittest
from support import snapshot, document
from easor_test.core import ContractError, RemoteError
from easor_test.engine import step


class KillWorker(BaseException):
    pass


class FakeClient:
    def __init__(self):
        self.calls, self.events, self.fail = [], [], None
        self.issued = False

    def preflight(self, business, snapshot):
        self.events.append("preflight")
        if self.fail == "preflight": raise ContractError("Mismatch")

    def income(self, business, identifier, invoice_date, draft):
        self.events.append("read")
        return document(issued=self.issued if not draft else False)

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs)); self.events.append(method)
        if self.fail == "timeout" and method == "POST": raise RemoteError()
        if self.fail == "crash" and method == "POST": raise KillWorker()
        if kwargs.get("pdf"): return b"%PDF-1.4\nsynthetic evidence"
        if self.fail == "malformed": return {}
        if path.endswith("bulk/issue"): self.issued = True; return {"data": {"issuedIds": ["INVOICE001"], "failedDrafts": []}}
        return {"data": {"id": "INVOICE001"}}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.job = {"state": "queued", "business_id": 81, "snapshot": snapshot(), "remote_id": None}
        self.client = FakeClient()

    def persist(self, patch):
        self.job.update(patch)
        self.client.events.append(patch.get("state", "save"))

    def run_step(self):
        step(self.job, self.client, self.persist)

    def test_full_round_trip_needs_explicit_approval(self):
        self.run_step(); self.assertEqual(self.job["state"], "draft_sync")
        self.run_step(); self.assertEqual(self.job["state"], "review")
        with self.assertRaises(ContractError): self.run_step()
        self.job["state"] = "issue_queued"  # human authorization is outside the pure engine
        self.run_step(); self.assertEqual(self.job["state"], "issued_sync")
        self.run_step(); self.assertEqual(self.job["state"], "done")
        self.assertEqual(self.job["number"], "E2026/0001")
        self.assertTrue(self.job["pdf_bytes"].startswith(b"%PDF-"))
        self.assertEqual(sum(c[0] == "POST" for c in self.client.calls), 2)

    def test_fence_precedes_each_write(self):
        self.run_step()
        self.assertLess(self.client.events.index("creating"), self.client.events.index("POST"))
        self.client.events.clear(); self.job["state"] = "issue_queued"
        self.run_step()
        self.assertLess(self.client.events.index("issuing"), self.client.events.index("POST"))

    def test_preflight_failure_does_not_write_or_fence(self):
        self.client.fail = "preflight"
        with self.assertRaises(ContractError): self.run_step()
        self.assertEqual(self.job["state"], "queued")
        self.assertFalse(self.client.calls)

    def test_timeout_and_malformed_success_are_unknown_not_retryable(self):
        for failure in ("timeout", "malformed"):
            self.setUp(); self.client.fail = failure
            with self.assertRaises((RemoteError, ContractError)): self.run_step()
            self.assertEqual(self.job["state"], "unknown")
            self.client.fail = None
            with self.assertRaises(ContractError): self.run_step()
            self.assertEqual(len(self.client.calls), 1)

    def test_crash_leaves_fence_and_cannot_replay(self):
        for state in ("queued", "issue_queued"):
            self.setUp(); self.job.update(state=state, remote_id="INVOICE001")
            self.client.fail = "crash"
            with self.assertRaises(KillWorker): self.run_step()
            self.assertEqual(self.job["state"], "creating" if state == "queued" else "issuing")
            self.client.fail = None
            with self.assertRaises(ContractError): self.run_step()
            self.assertEqual(len(self.client.calls), 1)

    def test_read_recovery_never_sends_a_write(self):
        self.job.update(state="draft_sync", remote_id="INVOICE001")
        self.run_step()
        self.assertEqual(self.job["state"], "review")
        self.assertFalse(self.client.calls)

    def test_accepted_request_is_not_proof_of_issuance(self):
        self.job.update(state="issued_sync", remote_id="INVOICE001")
        with self.assertRaises(ContractError): self.run_step()
        self.assertEqual(self.job["state"], "issued_sync")
        self.assertFalse(self.client.calls)

    def test_uncertain_issue_cannot_be_reissued(self):
        self.job.update(state="issue_queued", remote_id="INVOICE001")
        self.client.fail = "timeout"
        with self.assertRaises(RemoteError): self.run_step()
        self.assertEqual(self.job["state"], "unknown")
        self.client.fail = None
        with self.assertRaises(ContractError): self.run_step()
        self.assertEqual(len(self.client.calls), 1)
