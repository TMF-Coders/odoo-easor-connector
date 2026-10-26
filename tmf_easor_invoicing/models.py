"""Odoo adapter. Only the private cron owns/commits its cursor across HTTP fences."""
import base64
from datetime import timedelta
import hashlib

from odoo import api, fields, models, SUPERUSER_ID, _
from odoo.exceptions import AccessError, UserError
from odoo.tools import html2plaintext

from odoo.addons.tmf_easor_base.core import (
    ConnectorError, ContractError, build_payload, cents, fingerprint, remote_id,
    verify_document,
)
from odoo.addons.tmf_easor_base.engine import READ_STATES, RUNNABLE, WRITE_STATES, step

_TOKEN = object()  # Python identity cannot be supplied through an RPC JSON context.
STATES = [(v, label) for v, label in (
    ("queued", "Pendiente de crear"), ("creating", "Creación iniciada"),
    ("draft_sync", "Verificando borrador"), ("review", "Pendiente de aprobación"),
    ("issue_queued", "Emisión autorizada"), ("issuing", "Emisión iniciada"),
    ("issued_sync", "Verificando emisión y PDF"), ("done", "Emisión verificada"),
    ("unknown", "Resultado remoto desconocido"), ("blocked", "Bloqueado"),
)]
PAYMENTS = [(v, label) for v, label in (
    ("unspecified", "Sin especificar (elección explícita)"), ("cash", "Efectivo"),
    ("bankCard", "Tarjeta"), ("directDebit", "Domiciliación"), ("paypal", "PayPal"),
    ("bankCheck", "Cheque"), ("factoring", "Factoring"), ("bizum", "Bizum"),
)]
FINANCIAL = {
    "name", "state", "company_id", "journal_id", "partner_id", "currency_id", "date",
    "invoice_date", "invoice_date_due", "invoice_payment_term_id", "invoice_line_ids",
    "line_ids", "fiscal_position_id", "invoice_cash_rounding_id", "auto_post", "move_type",
    "easor_payment_method", "easor_language", "ref", "invoice_origin", "narration", "partner_bank_id",
}
TECHNICAL = {"easor_job_id", "invoice_pdf_report_file", "invoice_pdf_report_id"}


def internal(records):
    return records.with_context(_tmf_easor_token=_TOKEN)


def trusted(env):
    return env.context.get("_tmf_easor_token") is _TOKEN


def require_role(env, role="group_user"):
    if not env.user.has_group("tmf_easor_base." + role):
        raise AccessError(_("No tiene el permiso de Easor necesario."))


class Journal(models.Model):
    _inherit = "account.journal"
    easor_backend_id = fields.Many2one("tmf.easor.backend", check_company=True, ondelete="restrict", string="Facturación delegada en Easor")

    @api.constrains("easor_backend_id", "type")
    def _check_easor(self):
        for journal in self:
            if journal.easor_backend_id and journal.type != "sale":
                raise UserError(_("Esta versión solo admite diarios de ventas."))

    def write(self, vals):
        if "easor_backend_id" in vals:
            require_role(self.env, "group_manager")
            if self.env["account.move"].sudo().search_count([("journal_id", "in", self.ids), "|", ("state", "=", "posted"), ("easor_job_id", "!=", False)]):
                raise UserError(_("Use un diario dedicado nuevo; no cambie un diario con historial."))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if any(v.get("easor_backend_id") for v in vals_list):
            require_role(self.env, "group_manager")
        records = super().create(vals_list)
        if records.filtered("easor_backend_id"):
            require_role(self.env, "group_manager")
        return records


class Tax(models.Model):
    _inherit = "account.tax"
    easor_ordinary_vat = fields.Boolean(string="IVA ordinario revisado para Easor", default=False)

    def write(self, vals):
        if "easor_ordinary_vat" in vals:
            require_role(self.env, "group_manager")
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if any(v.get("easor_ordinary_vat") for v in vals_list):
            require_role(self.env, "group_manager")
        records = super().create(vals_list)
        if records.filtered("easor_ordinary_vat"):
            require_role(self.env, "group_manager")
        return records


class Move(models.Model):
    _inherit = "account.move"
    easor_job_id = fields.Many2one("tmf.easor.job", readonly=True, copy=False, ondelete="restrict")
    easor_state = fields.Selection(related="easor_job_id.state")
    easor_managed = fields.Boolean(compute="_compute_easor_managed")
    easor_payment_method = fields.Selection(PAYMENTS, copy=False, string="Forma de pago Easor")

    easor_language = fields.Selection([(v, v) for v in ("es", "ca", "en", "gl", "it")], default="es", required=True, string="Idioma Easor")

    @api.depends("journal_id.easor_backend_id", "easor_job_id")
    def _compute_easor_managed(self):
        for move in self:
            move.easor_managed = bool(move.journal_id.easor_backend_id or move.easor_job_id)

    def _easor_snapshot(self):
        self.ensure_one()
        backend = self.journal_id.easor_backend_id
        if not backend or self.move_type != "out_invoice" or self.auto_post != "no":
            raise UserError(_("Solo facturas ordinarias de venta en un diario Easor, sin autopost."))
        if self.invoice_cash_rounding_id or not self.invoice_date or not self.partner_id.vat:
            raise UserError(_("Indique fecha y NIF; no se admite redondeo de caja."))
        if self.company_id.country_id.code != "ES":
            raise UserError(_("Solo compañías españolas en esta versión."))
        if self.env["ir.module.module"].sudo().search_count([("state", "=", "installed"), "|", ("name", "=", "account_edi"), ("name", "=like", "l10n_es_edi%")]):
            raise UserError(_("Este piloto no está habilitado junto a módulos emisores EDI de Odoo."))
        bindings = self.env["tmf.easor.binding"].search([("backend_id", "=", backend.id)])
        client = bindings.filtered(lambda b: b.kind == "merchant" and b.partner_id == self.partner_id)
        if len(client) != 1:
            raise UserError(_("Falta una equivalencia revisada para este cliente."))
        if not all((self.partner_id.name, self.partner_id.street, self.partner_id.zip, self.partner_id.city)):
            raise UserError(_("Complete nombre y domicilio fiscal del cliente."))
        lines, descriptions = [], []
        for line in self.invoice_line_ids:
            if line.display_type != "product" or not line.product_id:
                raise UserError(_("No se admiten secciones, notas ni líneas sin producto; no se omitirán silenciosamente."))
            binding = bindings.filtered(lambda b: b.kind == "product" and b.product_id == line.product_id)
            taxes = line.tax_ids
            if len(binding) != 1 or len(taxes) != 1:
                raise UserError(_("Cada línea requiere una equivalencia y exactamente un IVA ordinario."))
            tax = taxes[0]
            if (not tax.easor_ordinary_vat or tax.amount_type != "percent" or tax.price_include
                    or tax.include_base_amount or tax.tax_exigibility != "on_invoice"
                    or line.product_uom_id != line.product_id.uom_id):
                raise UserError(_("Impuesto o unidad no compatible con este alcance."))
            lines.append({"product_id": binding.remote_id, "quantity": line.quantity, "price": line.price_unit,
                          "vat": tax.amount, "discount": line.discount, "price_included": tax.price_include})
            descriptions.append({"id": binding.remote_id, "label": line.name, "unit": binding.unit,
                                 "item_type": "service" if line.product_id.type == "service" else "product"})
        source = {
            "currency": self.currency_id.name, "country": self.partner_id.country_id.code,
            "merchant_id": client.remote_id, "series_id": backend.series_id, "activity_id": backend.activity_id,
            "date": str(self.invoice_date), "due_date": str(self.invoice_date_due) if self.invoice_date_due else None,
            "payment_method": self.easor_payment_method, "language": self.easor_language,
            "notes": "\n".join(filter(None, ["Referencia: " + self.ref if self.ref else "", html2plaintext(self.narration or "").strip()])),
            "multiple_due_dates": len(self.invoice_payment_term_id.line_ids) > 1, "lines": lines,
        }
        return {
            "payload": build_payload(source), "company_vat": self.company_id.vat,
            "merchant": {"name": self.partner_id.name, "vatId": self.partner_id.vat,
                         "address": " ".join(filter(None, [self.partner_id.street, self.partner_id.street2])),
                         "postalCode": self.partner_id.zip or "", "city": self.partner_id.city or ""},
            "descriptions": descriptions, "netAmount": cents(self.amount_untaxed), "totalAmount": cents(self.amount_total),
        }

    def action_easor_prepare(self):
        self.ensure_one()
        require_role(self.env)
        self.check_access("write")
        self.env.cr.execute("SELECT id FROM account_move WHERE id=%s FOR UPDATE", [self.id])
        self.invalidate_recordset()
        if self.easor_job_id:
            return self.action_easor_job()
        if self.state != "draft":
            raise UserError(_("La factura debe estar en borrador."))
        self.journal_id.easor_backend_id._check_writes()
        try:
            snapshot = self._easor_snapshot()
        except ConnectorError as exc:
            raise UserError(str(exc)) from None
        job = internal(self.env["tmf.easor.job"].sudo()).create({
            "move_id": self.id, "backend_id": self.journal_id.easor_backend_id.id,
            "snapshot": snapshot, "fingerprint": fingerprint(snapshot), "requested_by": self.env.uid,
        })
        internal(self).write({"easor_job_id": job.id})
        job._event("queued", self.env.uid)
        return self.action_easor_job()

    def action_easor_job(self):
        self.ensure_one()
        self.check_access("read")
        return {"type": "ir.actions.act_window", "res_model": "tmf.easor.job", "res_id": self.easor_job_id.id, "view_mode": "form"}

    def action_easor_post_mirror(self):
        self.ensure_one()
        require_role(self.env, "group_issuer")
        self.check_access("write")
        self.env.cr.execute("SELECT id FROM account_move WHERE id=%s FOR UPDATE", [self.id])
        self.invalidate_recordset()
        job = self.easor_job_id
        if job.state != "done" or not job.pdf_id or not job.number:
            raise UserError(_("Primero debe verificarse la emisión y el PDF original."))
        if self.state == "posted":
            return True
        if fingerprint(self._easor_snapshot()) != job.fingerprint:
            raise UserError(_("El documento local ya no coincide con la instantánea aprobada."))
        safe = internal(self)
        safe.write({"name": job.number})
        safe._post(soft=False)
        job._event("local_mirror_posted", self.env.uid)
        return True

    def _post(self, soft=True):
        if not trusted(self.env) and self.filtered("easor_managed"):
            raise UserError(_("Emita en Easor y confirme después la copia operativa. No se permite emisión local independiente."))
        return super()._post(soft=soft)

    @api.model_create_multi
    def create(self, vals_list):
        if not trusted(self.env) and any(TECHNICAL & v.keys() for v in vals_list):
            raise AccessError(_("Metadatos remotos protegidos."))
        records = super().create(vals_list)
        if not trusted(self.env) and records.filtered("easor_job_id"):
            raise AccessError(_("No se inyectan operaciones Easor mediante valores por defecto."))
        if not trusted(self.env) and records.filtered(lambda m: m.easor_managed and m.state != "draft"):
            raise UserError(_("Una factura Easor debe nacer en borrador."))
        return records

    def write(self, vals):
        if not trusted(self.env):
            if "easor_job_id" in vals or (TECHNICAL & vals.keys() and self.filtered("easor_managed")):
                raise AccessError(_("Metadatos y PDF remotos protegidos."))
            if FINANCIAL & vals.keys() and self.filtered("easor_job_id"):
                raise UserError(_("La instantánea enviada es inmutable. Rectifique en Easor; no sobrescriba."))
            if vals.get("state") == "posted" and self.filtered("easor_managed"):
                raise UserError(_("Use el circuito de emisión Easor."))
        return super().write(vals)

    def unlink(self):
        if self.filtered("easor_job_id"):
            raise UserError(_("No se elimina un documento con trazabilidad Easor."))
        return super().unlink()

    def _reverse_moves(self, default_values_list=None, cancel=False):
        if self.filtered("easor_managed"):
            raise UserError(_("Las rectificativas requieren el circuito Easor; no están habilitadas en esta entrega."))
        return super()._reverse_moves(default_values_list=default_values_list, cancel=cancel)


class MoveLine(models.Model):
    _inherit = "account.move.line"

    def _guard_easor(self):
        if not trusted(self.env) and self.mapped("move_id.easor_job_id"):
            raise UserError(_("Las líneas enviadas a Easor no se pueden modificar ni conciliar localmente."))

    @api.model_create_multi
    def create(self, vals_list):
        if not trusted(self.env):
            moves = self.env["account.move"].browse([v["move_id"] for v in vals_list if v.get("move_id")])
            if moves.mapped("easor_job_id"):
                raise UserError(_("No se añaden líneas a una instantánea Easor."))
        records = super().create(vals_list)
        records._guard_easor()
        return records

    def write(self, vals):
        self._guard_easor()
        if not trusted(self.env) and vals.get("move_id") and self.env["account.move"].browse(vals["move_id"]).easor_job_id:
            raise UserError(_("No se trasladan líneas a una instantánea Easor."))
        return super().write(vals)

    def unlink(self):
        self._guard_easor()
        return super().unlink()


class Job(models.Model):
    _name = "tmf.easor.job"
    _description = "Operación de facturación Easor"
    _order = "id desc"
    _check_company_auto = True
    _rec_name = "move_id"

    move_id = fields.Many2one("account.move", required=True, ondelete="restrict", check_company=True)
    backend_id = fields.Many2one("tmf.easor.backend", required=True, ondelete="restrict", check_company=True)
    company_id = fields.Many2one(related="backend_id.company_id", store=True)
    state = fields.Selection(STATES, required=True, default="queued", readonly=True, index=True)
    snapshot = fields.Json(required=True, readonly=True)
    fingerprint = fields.Char(required=True, readonly=True)
    requested_by = fields.Many2one("res.users", required=True, readonly=True, ondelete="restrict")
    approved_by = fields.Many2one("res.users", readonly=True, ondelete="restrict")
    remote_id = fields.Char(readonly=True, index=True)
    number = fields.Char(readonly=True)
    payment_status = fields.Char(readonly=True)
    last_write = fields.Selection([("create", "Creación"), ("issue", "Emisión")], readonly=True)
    fenced_at = fields.Datetime(readonly=True)
    attempts = fields.Integer(readonly=True, default=0)
    next_attempt = fields.Datetime(readonly=True, default=fields.Datetime.now)
    error = fields.Text(readonly=True)
    pdf_id = fields.Many2one("ir.attachment", readonly=True, ondelete="restrict")
    pdf_sha256 = fields.Char(readonly=True)
    event_ids = fields.One2many("tmf.easor.event", "job_id", readonly=True)
    _move_unique = models.Constraint("UNIQUE(move_id)", "Ya existe una operación para esta factura.")
    _remote_unique = models.Constraint("UNIQUE(backend_id, remote_id)", "Documento remoto ya asociado a otra factura.")

    @api.model_create_multi
    def create(self, vals_list):
        if not trusted(self.env):
            raise AccessError(_("Use la acción de preparación de la factura."))
        return super().create(vals_list)

    def write(self, vals):
        if not trusted(self.env):
            raise AccessError(_("La operación solo puede cambiar mediante el flujo controlado."))
        return super().write(vals)

    def unlink(self):
        raise AccessError(_("Las operaciones Easor no se eliminan."))

    def _event(self, action, actor=None):
        self.ensure_one()
        internal(self.env["tmf.easor.event"].sudo()).create({"job_id": self.id, "action": action, "actor_id": actor or self.env.uid})

    def _update(self, vals, actor=None):
        internal(self.sudo()).write(vals)
        self._event(vals.get("state", "metadata_updated"), actor)

    def _action_access(self, issuer=False):
        self.ensure_one()
        self.check_access("read")
        require_role(self.env, "group_issuer" if issuer else "group_user")
        self.move_id.check_access("write")
        self.env.cr.execute("SELECT id FROM tmf_easor_job WHERE id=%s FOR UPDATE", [self.id])
        self.invalidate_recordset()

    def action_approve_issue(self):
        self._action_access(issuer=True)
        if self.state != "review":
            raise UserError(_("Solo se autoriza un borrador remoto verificado."))
        self.backend_id._check_writes()
        self._update({"state": "issue_queued", "approved_by": self.env.uid, "attempts": 0, "error": False, "next_attempt": fields.Datetime.now()}, self.env.uid)
        return True

    def action_retry_safe(self):
        self._action_access(issuer=True)
        if self.state not in ("blocked", "unknown", "creating", "issuing", "done"):
            raise UserError(_("La operación no requiere recuperación."))
        if self.state in ("creating", "issuing") and self.fenced_at and fields.Datetime.now() - self.fenced_at < timedelta(minutes=3):
            raise UserError(_("Todavía puede haber una petición en curso."))
        if not self.remote_id and self.fenced_at:
            return {"type": "ir.actions.act_window", "res_model": "tmf.easor.recovery", "view_mode": "form", "target": "new", "context": {"default_job_id": self.id}}
        state = "queued" if not self.fenced_at else "issued_sync" if self.last_write == "issue" or self.state == "done" else "draft_sync"
        self._update({"state": state, "attempts": 0, "error": False, "next_attempt": fields.Datetime.now()}, self.env.uid)
        return True

    def action_pdf(self):
        self.ensure_one()
        self.check_access("read")
        self.move_id.check_access("read")
        if not self.pdf_id:
            raise UserError(_("El PDF original todavía no está disponible."))
        return {"type": "ir.actions.act_url", "url": f"/web/content/{self.pdf_id.id}?download=true", "target": "new"}

    def _persist(self, patch):
        patch = dict(patch)
        raw = patch.pop("pdf_bytes", None)
        if raw is not None:
            digest = hashlib.sha256(raw).hexdigest()
            if self.pdf_id and self.pdf_sha256 != digest:
                raise ContractError("The remote PDF changed; the original will not be overwritten")
            if not self.pdf_id:
                attachment = internal(self.env["ir.attachment"]).create({
                    "name": f"Easor-{self.remote_id}.pdf", "datas": base64.b64encode(raw), "mimetype": "application/pdf",
                    "res_model": "account.move", "res_id": self.move_id.id, "res_field": "invoice_pdf_report_file", "public": False,
                })
                patch.update(pdf_id=attachment.id, pdf_sha256=digest)
                self.move_id.invalidate_recordset(["invoice_pdf_report_file", "invoice_pdf_report_id"])
        if patch.get("state") in ("creating", "issuing"):
            patch.update(fenced_at=fields.Datetime.now(), last_write="create" if patch["state"] == "creating" else "issue")
        last_write = patch.get("last_write", self.last_write)
        self._update(patch, self.approved_by.id if last_write == "issue" and self.approved_by else self.requested_by.id)
        # This method is private and called only by _cron_tick with its OWN cursor.
        self.env.cr.commit()

    def _run_step(self):
        self.ensure_one()
        initial = self.state
        internal(self).write({"attempts": self.attempts + 1})
        try:
            if initial in WRITE_STATES:
                actor = self.approved_by if initial == "issue_queued" else self.requested_by
                if not actor.active or self.company_id not in actor.company_ids:
                    raise AccessError(_("El autorizante ya no tiene acceso a la compañía."))
                actor_move = self.move_id.with_user(actor).with_company(self.company_id)
                require_role(actor_move.env, "group_issuer" if initial == "issue_queued" else "group_user")
                actor_move.check_access("write")
                self.backend_id._check_writes()
                if fingerprint(actor_move._easor_snapshot()) != self.fingerprint:
                    raise ContractError("The approved source snapshot has changed")
            client = self.backend_id._client()
            step({"state": initial, "business_id": self.backend_id.business_id, "snapshot": self.snapshot, "remote_id": self.remote_id}, client, self._persist)
        except (ConnectorError, UserError, AccessError):
            self.invalidate_recordset()
            # A durable fence survives worker failure. Only reads can retry automatically.
            state = self.state
            retry = state in READ_STATES and self.attempts < 8
            safe_error = "No se ha podido verificar el contrato, acceso o respuesta. Revise configuración y el documento remoto; no repita escrituras inciertas."
            self._update({"state": state if state == "unknown" or retry else "blocked", "error": safe_error,
                          "next_attempt": fields.Datetime.now() + timedelta(minutes=min(30, 2 ** min(self.attempts, 5)))})
            self.env.cr.commit()

    @api.model
    def _cron_tick(self):
        # No commits on the caller's transaction, including a manual cron invocation.
        with self.env.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            cr.execute("SELECT id FROM tmf_easor_job WHERE state IN %s AND next_attempt <= NOW() ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1", [tuple(RUNNABLE)])
            row = cr.fetchone()
            if row:
                job = env["tmf.easor.job"].browse(row[0])
                job.with_company(job.company_id)._run_step()
            cr.commit()


class Event(models.Model):
    _name = "tmf.easor.event"
    _description = "Trazabilidad inmutable de integración Easor"
    _order = "id desc"
    job_id = fields.Many2one("tmf.easor.job", required=True, ondelete="restrict")
    company_id = fields.Many2one(related="job_id.company_id", store=True)
    action = fields.Char(required=True)
    actor_id = fields.Many2one("res.users", required=True, ondelete="restrict")

    @api.model_create_multi
    def create(self, vals_list):
        if not trusted(self.env):
            raise AccessError(_("Los eventos los genera el flujo de integración."))
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_("El historial no se reescribe."))

    def unlink(self):
        raise AccessError(_("El historial no se elimina."))


class Recovery(models.TransientModel):
    _name = "tmf.easor.recovery"
    _description = "Vinculación manual después de un resultado incierto"
    job_id = fields.Many2one("tmf.easor.job", required=True)
    remote_id = fields.Char(required=True)
    reason = fields.Text(required=True)
    already_issued = fields.Boolean(default=False)

    def action_link(self):
        self.ensure_one()
        job = self.job_id
        job._action_access(issuer=True)
        if job.remote_id or job.state not in ("creating", "unknown", "blocked"):
            raise UserError(_("La operación no admite vinculación manual."))
        if job.fenced_at and fields.Datetime.now() - job.fenced_at < timedelta(minutes=3):
            raise UserError(_("La petición todavía puede estar en curso."))
        if len((self.reason or "").strip()) < 20:
            raise UserError(_("Documente cómo ha comprobado el origen del documento (mínimo 20 caracteres)."))
        try:
            identifier = remote_id(self.remote_id)
            doc = job.backend_id._client().income(job.backend_id.business_id, identifier, job.snapshot["payload"]["date"], draft=not self.already_issued)
            verify_document(doc, job.snapshot, job.backend_id.business_id, issued=self.already_issued)
        except ConnectorError as exc:
            raise UserError(str(exc)) from None
        job._update({"remote_id": identifier, "state": "issued_sync" if self.already_issued else "draft_sync", "last_write": "issue" if self.already_issued else "create",
                     "approved_by": self.env.uid if self.already_issued else False, "attempts": 0, "next_attempt": fields.Datetime.now(), "error": False}, self.env.uid)
        job._event("human_recovery: " + self.reason.strip()[:1000], self.env.uid)
        return {"type": "ir.actions.act_window_close"}


class Attachment(models.Model):
    _inherit = "ir.attachment"

    @api.model_create_multi
    def create(self, vals_list):
        if not trusted(self.env):
            for vals in vals_list:
                if vals.get("res_model") == "account.move" and vals.get("res_field") == "invoice_pdf_report_file" and vals.get("res_id"):
                    if self.env["account.move"].sudo().browse(vals["res_id"]).easor_managed:
                        raise UserError(_("El PDF oficial solo puede recuperarse desde Easor."))
        return super().create(vals_list)

    def write(self, vals):
        protected = {"datas", "raw", "db_datas", "store_fname", "res_model", "res_id", "res_field", "company_id", "public", "name", "mimetype"}
        if not trusted(self.env) and protected & vals.keys() and self.env["tmf.easor.job"].sudo().search_count([("pdf_id", "in", self.ids)]):
            raise UserError(_("El PDF original de Easor no se sobrescribe ni se hace público."))
        return super().write(vals)

    def unlink(self):
        if self.env["tmf.easor.job"].sudo().search_count([("pdf_id", "in", self.ids)]):
            raise UserError(_("El PDF original se conserva como evidencia."))
        return super().unlink()


class Report(models.Model):
    _inherit = "ir.actions.report"

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        report = self._get_report(report_ref)
        if report.model == "account.move" and res_ids:
            moves = self.env["account.move"].browse(res_ids)
            moves.check_access("read")
            if moves.filtered("easor_managed"):
                if len(moves) != 1 or not moves.sudo().easor_job_id.pdf_id:
                    raise UserError(_("Descargue individualmente el PDF original verificado en Easor."))
                return moves.sudo().easor_job_id.pdf_id.raw, "pdf"
        return super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)
