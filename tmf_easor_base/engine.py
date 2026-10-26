"""One resumable step, with a durable fence BEFORE each remote mutation.

persist() must commit in a transaction owned by the worker. A crashed creating/
issuing state is NEVER selected for automatic retry. Recovery is read-only.
"""
from .core import ContractError, remote_id, verify_document

WRITE_STATES = {"queued": "creating", "issue_queued": "issuing"}
READ_STATES = {"draft_sync", "issued_sync"}
RUNNABLE = tuple(WRITE_STATES) + tuple(READ_STATES)


def step(job, client, persist):
    state = job["state"]
    business_id = job["business_id"]
    snapshot = job["snapshot"]
    root = f"/api/v1/business/{business_id}"
    if state in WRITE_STATES:
        client.preflight(business_id, snapshot)
        if state == "issue_queued":
            doc = client.income(business_id, job["remote_id"], snapshot["payload"]["date"], draft=True)
            verify_document(doc, snapshot, business_id, issued=False)
        persist({"state": WRITE_STATES[state], "error": False})
        # No automatic retry after this point, even on a 4xx/5xx or malformed reply.
        try:
            if state == "queued":
                reply = client.request("POST", root + "/invoice/draft", payload=snapshot["payload"])
                identifier = remote_id((reply.get("data") or {}).get("id"))
                persist({"remote_id": identifier, "state": "draft_sync"})
            else:
                client.request("POST", root + "/income/draft/bulk/issue", payload={"draftIds": [job["remote_id"]]})
                # A 2xx is not proof of issuance or tax-agency acceptance.
                persist({"state": "issued_sync"})
        except Exception:
            persist({"state": "unknown", "error": "Remote write outcome is uncertain. Reconcile; do not resend."})
            raise
        return
    if state in READ_STATES:
        issued = state == "issued_sync"
        doc = client.income(business_id, job["remote_id"], snapshot["payload"]["date"], draft=not issued)
        verify_document(doc, snapshot, business_id, issued=issued)
        if issued:
            pdf = client.request("GET", root + f"/income/{remote_id(job['remote_id'])}/pdf", pdf=True)
            persist({"state": "done", "number": doc["number"], "payment_status": doc.get("paymentStatus"), "pdf_bytes": pdf, "error": False})
        else:
            persist({"state": "review", "error": False})
        return
    raise ContractError("This state cannot be dispatched automatically")
