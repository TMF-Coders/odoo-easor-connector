"""Company-scoped configuration; no credentials are stored in Odoo records."""
import os
import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from .core import Client, ConnectorError, nif, remote_id


class Backend(models.Model):
    _name = "tmf.easor.backend"
    _description = "Conexión Easor España"
    _check_company_auto = True

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda s: s.env.company, ondelete="restrict")
    origin = fields.Char(required=True, help="Origen HTTPS confirmado por Easor España, sin /api ni rutas.")
    business_id = fields.Integer(required=True)
    api_key_env = fields.Char(required=True, groups="tmf_easor_base.group_manager", help="Nombre de variable TMF_EASOR_KEY_…; nunca la clave.")
    writes_enabled = fields.Boolean(string="Permitir escrituras remotas", default=False)
    series_id = fields.Char(required=True, string="ID de serie de facturas")
    activity_id = fields.Integer(required=True, string="ID de actividad económica")
    _unique_company = models.Constraint("UNIQUE(company_id)", "Una conexión por compañía en esta versión.")
    _positive_ids = models.Constraint("CHECK(business_id > 0 AND activity_id > 0)", "Los identificadores deben ser positivos.")

    @api.constrains("origin", "api_key_env", "series_id")
    def _check_configuration(self):
        for record in self:
            try:
                Client(record.origin, "configuration-validation", os.getenv("TMF_EASOR_ALLOWED_HOSTS", "").split(","))
                remote_id(record.series_id)
                if not re.fullmatch(r"TMF_EASOR_KEY_[A-Z0-9_]+", record.api_key_env or ""):
                    raise ValidationError(_("Use un nombre de variable TMF_EASOR_KEY_…"))
            except ConnectorError as exc:
                raise ValidationError(str(exc)) from None

    def _client(self):
        self.ensure_one()
        if not self.active:
            raise UserError(_("La conexión está desactivada."))
        return Client(self.origin, os.getenv(self.sudo().api_key_env, ""), os.getenv("TMF_EASOR_ALLOWED_HOSTS", "").split(","))

    def _check_writes(self):
        self.ensure_one()
        if not self.active or not self.writes_enabled or os.getenv("TMF_EASOR_ALLOW_WRITES") != "1":
            raise UserError(_("Escrituras desactivadas: se requieren autorización de la conexión y TMF_EASOR_ALLOW_WRITES=1."))

    def write(self, vals):
        if {"company_id", "business_id", "origin", "series_id", "activity_id"} & vals.keys() and "tmf.easor.job" in self.env:
            if self.env["tmf.easor.job"].sudo().search_count([("backend_id", "in", self.ids)]):
                raise UserError(_("La identidad de una conexión con historial no se puede reescribir."))
        return super().write(vals)

    def action_check(self):
        self.ensure_one()
        self.check_access("read")
        try:
            business = self._client().find("/api/v1/business", self.business_id)
            if not nif(self.company_id.vat) or nif(business.get("vatId")) != nif(self.company_id.vat):
                raise UserError(_("El NIF remoto no coincide con la compañía."))
        except ConnectorError as exc:
            raise UserError(str(exc)) from None
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {"title": "Easor", "message": "Acceso de lectura e identidad verificados. No se ha emitido nada.", "type": "success"}}


class Binding(models.Model):
    _name = "tmf.easor.binding"
    _description = "Equivalencia revisada Odoo–Easor"
    _check_company_auto = True
    _rec_name = "remote_id"

    backend_id = fields.Many2one("tmf.easor.backend", required=True, ondelete="restrict", check_company=True)
    company_id = fields.Many2one(related="backend_id.company_id", store=True)
    kind = fields.Selection([("merchant", "Cliente"), ("product", "Producto")], required=True)
    partner_id = fields.Many2one("res.partner", check_company=True, ondelete="restrict")
    product_id = fields.Many2one("product.product", check_company=True, ondelete="restrict")
    remote_id = fields.Char(required=True)
    unit = fields.Selection([(v, v) for v in ("day", "gram", "hour", "kilogram", "liter", "meter", "minute", "unit")], default="unit", required=True)
    _local_partner = models.Constraint("UNIQUE(backend_id, partner_id)", "Cliente ya vinculado.")
    _local_product = models.Constraint("UNIQUE(backend_id, product_id)", "Producto ya vinculado.")
    _remote = models.Constraint("UNIQUE(backend_id, kind, remote_id)", "Identificador remoto ya vinculado.")
    _target = models.Constraint("CHECK((kind = 'merchant' AND partner_id IS NOT NULL AND product_id IS NULL) OR (kind = 'product' AND product_id IS NOT NULL AND partner_id IS NULL))", "Seleccione exactamente el registro del tipo indicado.")

    @api.constrains("remote_id")
    def _check_remote_id(self):
        for record in self:
            try:
                remote_id(record.remote_id)
            except ConnectorError as exc:
                raise ValidationError(str(exc)) from None
