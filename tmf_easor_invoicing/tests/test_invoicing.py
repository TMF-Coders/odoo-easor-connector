"""Real Odoo ORM tests; all Easor network operations are replaced by a fake.

The pure-engine tests verify fence order/crash semantics. These ORM tests suppress
commit() to retain TransactionCase isolation; hard-kill/UAT remains a release gate.
"""
import copy
import os
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.tmf_easor_invoicing.models import internal


class FakeEasor:
    def __init__(self, snapshot, business_id):
        self.snapshot, self.business_id, self.issued = snapshot, business_id, False
        self.posts = []

    def preflight(self, *args):
        return True

    def income(self, business_id, identifier, invoice_date, draft):
        s = self.snapshot
        products = []
        for line, description in zip(s["payload"]["lines"], s["descriptions"]):
            product = copy.deepcopy(line)
            product["id"] = product.pop("productId")
            product.update(concept=description["label"], unit=description["unit"], itemType=description["item_type"])
            products.append(product)
        return {"id": identifier, "businessId": business_id, "type": "invoice", "date": invoice_date,
                "dueDate": s["payload"].get("dueDate"), "currency": "EUR", "originalCurrency": "EUR", "vatType": "iva",
                "merchant": {**s["merchant"], "id": s["payload"]["merchantId"], "country": "ES"},
                "netAmount": s["netAmount"], "totalAmount": s["totalAmount"], "originalProducts": products,
                "isDraft": not self.issued, "issued": self.issued, "number": "E2026/0001" if self.issued else "",
                "paymentMethod": "2", "paymentStatus": "pending", "language": s["payload"]["language"], "notes": s["payload"].get("notes")}

    def request(self, method, path, **kwargs):
        if method == "POST": self.posts.append(path)
        if path.endswith("bulk/issue"): self.issued = True; return {"data": {"issuedIds": ["INVOICE001"]}}
        if kwargs.get("pdf"): return b"%PDF-1.4\nsynthetic-test-only"
        return {"data": {"id": "INVOICE001"}}


@tagged("post_install", "-at_install")
class TestEasorInvoicing(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env_patch = patch.dict(os.environ, {"TMF_EASOR_ALLOWED_HOSTS": "api.example.invalid", "TMF_EASOR_ALLOW_WRITES": "1", "TMF_EASOR_KEY_TEST": "synthetic"})
        env_patch.start(); cls.addClassCleanup(env_patch.stop)
        cls.env.user.write({"group_ids": [Command.link(cls.env.ref("tmf_easor_base.group_manager").id), Command.link(cls.env.ref("tmf_easor_base.group_issuer").id)]})
        cls.env.company.with_context(no_vat_validation=True).write({"country_id": cls.env.ref("base.es").id, "currency_id": cls.env.ref("base.EUR").id, "vat": "ESB00000000"})
        cls.partner_a.with_context(no_vat_validation=True).write({"name": "Synthetic Client", "vat": "ESB11111111", "country_id": cls.env.ref("base.es").id, "street": "Test Street 1", "zip": "15001", "city": "Test City"})
        cls.product_a.write({"name": "Synthetic Service", "type": "service"})
        cls.backend = cls.env["tmf.easor.backend"].create({"name": "Synthetic Easor", "company_id": cls.env.company.id, "origin": "https://api.example.invalid", "business_id": 81, "api_key_env": "TMF_EASOR_KEY_TEST", "writes_enabled": True, "series_id": "SERIES0001", "activity_id": 1})
        cls.journal = cls.env["account.journal"].create({"name": "Easor test", "code": "EASR", "type": "sale", "company_id": cls.env.company.id, "default_account_id": cls.company_data["default_account_revenue"].id, "easor_backend_id": cls.backend.id})
        cls.tax = cls.env["account.tax"].create({"name": "Synthetic ordinary VAT", "amount": 21, "amount_type": "percent", "type_tax_use": "sale", "company_id": cls.env.company.id, "easor_ordinary_vat": True})
        cls.env["tmf.easor.binding"].create([
            {"backend_id": cls.backend.id, "kind": "merchant", "partner_id": cls.partner_a.id, "remote_id": "CLIENT0001"},
            {"backend_id": cls.backend.id, "kind": "product", "product_id": cls.product_a.id, "remote_id": "PRODUCT001", "unit": "unit"},
        ])
        cls.operator = new_test_user(cls.env, login="easor_operator_test", groups="account.group_account_invoice,tmf_easor_base.group_user", company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)])

    def invoice(self):
        return self.env["account.move"].create({"move_type": "out_invoice", "journal_id": self.journal.id, "partner_id": self.partner_a.id, "invoice_date": "2026-10-26", "invoice_date_due": "2026-11-26", "easor_payment_method": "cash", "invoice_line_ids": [Command.create({"product_id": self.product_a.id, "name": "Synthetic Service", "quantity": 2, "price_unit": 100, "tax_ids": [Command.set(self.tax.ids)]})]})

    def test_queue_is_idempotent_and_source_is_locked(self):
        move = self.invoice()
        move.action_easor_prepare(); job = move.easor_job_id
        move.action_easor_prepare()
        self.assertEqual(move.easor_job_id, job)
        self.assertEqual(job.state, "queued")
        with self.assertRaises(UserError): move.write({"invoice_date": "2026-10-27"})
        with self.assertRaises(UserError): move.invoice_line_ids.write({"price_unit": 1})
        with self.assertRaises(UserError): move.unlink()
        with self.assertRaises(AccessError): job.write({"state": "done"})
        with self.assertRaises(AccessError): job.with_context(_tmf_easor_token=True).write({"state": "done"})

    def test_native_posting_is_refused(self):
        move = self.invoice()
        with self.assertRaises(UserError): move.action_post()
        with self.assertRaises(UserError): move.write({"state": "posted"})
        with self.assertRaises(UserError): move.with_context(_tmf_easor_token=True).action_post()
        self.assertEqual(move.state, "draft")

    def test_prepare_does_not_call_network(self):
        with patch("requests.Session.request", side_effect=AssertionError("Network not allowed")):
            self.invoice().action_easor_prepare()

    def test_explicit_write_switch(self):
        with patch.dict(os.environ, {"TMF_EASOR_ALLOW_WRITES": "0"}):
            with self.assertRaises(UserError): self.invoice().action_easor_prepare()

    def test_operator_cannot_authorize_issue(self):
        move = self.invoice(); move.action_easor_prepare()
        internal(move.easor_job_id).write({"state": "review", "remote_id": "INVOICE001"})
        with self.assertRaises(AccessError): move.easor_job_id.with_user(self.operator).action_approve_issue()

    def test_direct_metadata_and_default_injection_denied(self):
        move = self.invoice(); move.action_easor_prepare()
        with self.assertRaises(AccessError):
            self.env["account.move"].with_context(default_easor_job_id=move.easor_job_id.id).create({"move_type": "out_invoice", "journal_id": self.journal.id})

    def test_cross_company_access_denied(self):
        move = self.invoice(); move.action_easor_prepare()
        other = self.env["res.company"].create({"name": "Synthetic second company"})
        outsider = new_test_user(self.env, login="easor_other_test", groups="account.group_account_invoice,tmf_easor_base.group_user", company_id=other.id, company_ids=[Command.set(other.ids)])
        with self.assertRaises(AccessError): move.easor_job_id.with_user(outsider).read(["snapshot"])
        with self.assertRaises(AccessError): self.backend.with_user(outsider).read(["business_id"])

    def test_complete_mocked_flow_and_original_pdf(self):
        move = self.invoice(); move.action_easor_prepare(); job = move.easor_job_id
        fake = FakeEasor(job.snapshot, self.backend.business_id)
        with patch.object(type(self.backend), "_client", return_value=fake), patch.object(type(self.env.cr), "commit"):
            job.sudo()._run_step(); self.assertEqual(job.state, "draft_sync")
            job.sudo()._run_step(); self.assertEqual(job.state, "review")
            job.action_approve_issue()
            job.sudo()._run_step(); self.assertEqual(job.state, "issued_sync")
            job.sudo()._run_step(); self.assertEqual(job.state, "done")
        self.assertEqual(len(fake.posts), 2)
        self.assertEqual(job.pdf_id.raw, b"%PDF-1.4\nsynthetic-test-only")
        self.assertFalse(job.pdf_id.public)
        with self.assertRaises(UserError): job.pdf_id.write({"public": True})
        with self.assertRaises(UserError): job.pdf_id.unlink()
        move.action_easor_post_mirror()
        self.assertEqual(move.state, "posted")
        self.assertEqual(move.name, "E2026/0001")
        self.assertEqual(move.amount_total, 242)
        move.action_easor_post_mirror()  # no second posting or remote write
        pdf, kind = self.env["ir.actions.report"]._render_qweb_pdf("account.account_invoices", [move.id])
        self.assertEqual((pdf, kind), (job.pdf_id.raw, "pdf"))
