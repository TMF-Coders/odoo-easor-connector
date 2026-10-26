"""Reject indirect attempts to assign a fabricated official invoice PDF."""
from odoo import api, models, _
from odoo.exceptions import UserError

from .models import trusted


class AttachmentAssignmentGuard(models.Model):
    _inherit = "ir.attachment"

    def _check_easor_pdf_target(self, vals=None):
        vals = vals or {}
        for attachment in self:
            model = vals.get("res_model", attachment.res_model)
            field = vals.get("res_field", attachment.res_field)
            target = vals.get("res_id", attachment.res_id)
            if model == "account.move" and field == "invoice_pdf_report_file" and target:
                if self.env["account.move"].sudo().browse(target).easor_managed:
                    raise UserError(_("No se asigna un PDF alternativo a una factura gestionada por Easor."))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if not trusted(self.env):
            # default_* values may be supplied through context instead of vals.
            records._check_easor_pdf_target()
        return records

    def write(self, vals):
        if not trusted(self.env) and {"res_model", "res_field", "res_id"} & vals.keys():
            self._check_easor_pdf_target(vals)
        return super().write(vals)
