"""Original TMFCoders integration code. No Odoo dependency; no write retries.

Amounts are EUR cents and VAT rates are basis points. This first slice supports
ordinary domestic sales, not arbitrary fiscal transformations.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
import re
from urllib.parse import urlsplit

import requests


class ConnectorError(Exception):
    """Safe diagnostic: never includes credentials or upstream response bodies."""


class ContractError(ConnectorError):
    pass


class RemoteError(ConnectorError):
    def __init__(self, status=None):
        self.status = status
        super().__init__(f"Easor HTTP {status}" if status else "Easor transport failure")


def decimal(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ContractError("Invalid decimal") from None
    if isinstance(value, bool) or not result.is_finite():
        raise ContractError("Invalid decimal")
    return result


def cents(value, *, exact=False):
    scaled = decimal(value) * 100
    rounded = scaled.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if exact and scaled != rounded:
        raise ContractError("Unit prices with fractional cents are outside this version")
    return int(rounded)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def fingerprint(value):
    return sha256(canonical(value).encode()).hexdigest()


def remote_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9]{10}", value):
        raise ContractError("Expected a ten-character Easor identifier")
    return value


def nif(value):
    value = re.sub(r"[\s.-]", "", str(value or "")).upper()
    return value[2:] if value.startswith("ES") else value


def text(value):
    return " ".join(str(value or "").split()).casefold()


def build_payload(source):
    """Fail closed instead of silently dropping unsupported Odoo semantics."""
    from datetime import date
    if source["currency"] != "EUR" or source["country"] != "ES":
        raise ContractError("Only domestic Spanish EUR invoices are supported")
    if source.get("discount") or source.get("multiple_due_dates"):
        raise ContractError("Discounts and installment payment terms are not supported")
    lines = source["lines"]
    if not 1 <= len(lines) <= 50:
        raise ContractError("An invoice must contain 1 to 50 billable lines")
    payload = {
        "merchantId": remote_id(source["merchant_id"]),
        "serialNumberId": remote_id(source["series_id"]),
        "economicActivityId": source["activity_id"],
        "date": source["date"], "paymentMethod": source["payment_method"],
        "vatRegime": "01", "paid": False, "language": source.get("language", "es"), "lines": [],
    }
    if payload["language"] not in ("es", "ca", "en", "gl", "it"):
        raise ContractError("Unsupported document language")
    if source.get("notes"):
        if len(source["notes"]) > 2000:
            raise ContractError("Customer notes exceed the public API limit")
        payload["notes"] = source["notes"]
    if payload["paymentMethod"] not in ("unspecified", "cash", "bankCard", "directDebit", "paypal", "bankCheck", "factoring", "bizum"):
        raise ContractError("Unsupported public API payment method; bank transfer is not currently exposed")
    if type(payload["economicActivityId"]) is not int or payload["economicActivityId"] <= 0:
        raise ContractError("Invalid economic activity")
    try:
        date.fromisoformat(payload["date"])
        if source.get("due_date"):
            date.fromisoformat(source["due_date"])
            if source["due_date"] < source["date"]:
                raise ValueError()
            payload["dueDate"] = source["due_date"]
    except (ValueError, TypeError):
        raise ContractError("Invalid invoice date or due date") from None
    for line in lines:
        qty, price = decimal(line["quantity"]), cents(line["price"], exact=True)
        rate = cents(line["vat"], exact=True)
        if qty < Decimal("0.01") or price <= 0:
            raise ContractError("Positive quantities >= 0.01 and positive prices required")
        if rate not in (0, 400, 1000, 2100) or line.get("special_tax"):
            raise ContractError("Only explicit ordinary VAT 0/4/10/21 is supported")
        if line.get("discount") or line.get("price_included"):
            raise ContractError("Line discounts and tax-inclusive prices are not supported")
        payload["lines"].append({
            "productId": remote_id(line["product_id"]), "quantity": float(qty),
            "price": price, "vat": rate, "retention": 0, "equivalenceSurcharge": 0,
        })
    return payload


class Client:
    """Spanish public API only. An explicit host allow-list prevents secret exfiltration."""
    def __init__(self, origin, api_key, allowed_hosts, session=None):
        try:
            parsed = urlsplit(origin)
            port = parsed.port
        except ValueError:
            raise ContractError("Invalid HTTPS origin") from None
        allowed = {h.strip().lower() for h in allowed_hosts if h.strip()}
        if (parsed.scheme != "https" or not parsed.hostname or parsed.hostname.lower() not in allowed
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/") or port not in (None, 443)):
            raise ContractError("Public API origin must be HTTPS and explicitly allowed")
        if not api_key or "\n" in api_key or "\r" in api_key:
            raise ContractError("Missing or invalid Easor credential")
        self.origin = origin.rstrip("/")
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.key = api_key

    def request(self, method, path, *, payload=None, params=None, pdf=False):
        if method not in ("GET", "POST") or not re.fullmatch(r"/api/v1/business(?:/[A-Za-z0-9/-]+)?", path):
            raise ContractError("Invalid public API request")
        response = None
        try:
            response = self.session.request(
                method, self.origin + path, json=payload, params=params,
                headers={"x-easor-api-key": self.key, "Accept": "application/pdf" if pdf else "application/json"},
                timeout=(5, 30), allow_redirects=False, stream=True,
            )
            if not 200 <= response.status_code < 300:
                raise RemoteError(response.status_code)
            limit = 10 * 1024 * 1024 if pdf else 5 * 1024 * 1024
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > limit:
                    raise ContractError("Easor response exceeded size limit")
                chunks.append(chunk)
            body = b"".join(chunks)
            if pdf:
                if not body.startswith(b"%PDF-") or "application/pdf" not in response.headers.get("Content-Type", ""):
                    raise ContractError("Expected an original PDF")
                return body
            try:
                result = json.loads(body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, UnicodeDecodeError):
                raise ContractError("Invalid Easor JSON response") from None
            if not isinstance(result, dict):
                raise ContractError("Expected a JSON object")
            return result
        except requests.RequestException:
            raise RemoteError() from None
        finally:
            if response is not None:
                response.close()

    def rows(self, path, **filters):
        """Bounded paging; never mistake a partial catalogue for an exhaustive one."""
        seen, expected_total = set(), None
        for page in range(1, 101):
            reply = self.request("GET", path, params={**filters, "page": page, "pageSize": 50})
            data, pagination = reply.get("data"), reply.get("pagination")
            if not isinstance(data, list) or not isinstance(pagination, dict) or pagination.get("page") != page:
                raise ContractError("Invalid paginated response")
            total = pagination.get("total")
            if type(total) is not int or total < 0 or (expected_total is not None and total != expected_total):
                raise ContractError("Invalid or changing pagination total")
            expected_total = total
            for row in data:
                if not isinstance(row, dict) or type(row.get("id")) not in (str, int) or not row["id"] or row["id"] in seen:
                    raise ContractError("Invalid or repeated record in pagination")
                seen.add(row["id"])
                yield row
            if pagination.get("end") is True:
                if len(seen) != total:
                    raise ContractError("Truncated paginated response")
                return
            if not data:
                raise ContractError("Pagination did not advance")
        raise ContractError("Pagination limit exceeded")

    def find(self, path, identifier, **filters):
        for row in self.rows(path, **filters):
            if row["id"] == identifier:
                return row
        raise ContractError("Remote record not found in the requested scope")

    def preflight(self, business_id, snapshot):
        """Read-only validation immediately before the durable write fence."""
        root = f"/api/v1/business/{business_id}"
        business = self.find("/api/v1/business", business_id)
        if (nif(business.get("vatId")) != nif(snapshot["company_vat"])
                or not nif(snapshot["company_vat"]) or business.get("vatType") != "iva"
                or (business.get("address") or {}).get("country") not in ("ES", "ESP")):
            raise ContractError("Business fiscal identity does not match the Odoo company")
        options = self.request("GET", root + "/invoice/options")
        options = options.get("data", options)
        if "01" not in options.get("allowedVatRegimes", []):
            raise ContractError("General VAT regime is not available")
        merchant = self.find(root + "/merchant", snapshot["payload"]["merchantId"])
        for key in ("name", "address", "postalCode", "city"):
            if text(merchant.get(key)) != text(snapshot["merchant"][key]):
                raise ContractError("Client fiscal snapshot differs from Easor")
        if nif(merchant.get("vatId")) != nif(snapshot["merchant"]["vatId"]) or merchant.get("country") not in ("ES", "ESP"):
            raise ContractError("Client tax identifier or country mismatch")
        products = {row["id"]: row for row in self.rows(root + "/product", type="sale")}
        for expected in snapshot["descriptions"]:
            product = products.get(expected["id"])
            if not product or "sale" not in product.get("type", []) or product.get("trackStock"):
                raise ContractError("Missing sales product or remote stock tracking is enabled")
            label = product["concept"] + ("\n" + product["description"] if product.get("description") else "")
            if label != expected["label"] or product.get("unit") != expected["unit"] or product.get("itemType") != expected["item_type"]:
                raise ContractError("Product description or unit differs from the approved line")
        return True

    def income(self, business_id, identifier, invoice_date, *, draft):
        return self.find(f"/api/v1/business/{business_id}/income", identifier,
                         isDraft="true" if draft else "false", startDate=invoice_date, endDate=invoice_date)


def verify_document(document, snapshot, business_id, *, issued):
    """Compare full fiscal/commercial evidence before approval and after issuance."""
    payload = snapshot["payload"]
    merchant = document.get("merchant") or {}
    for field in ("name", "address", "postalCode", "city"):
        if text(merchant.get(field)) != text(snapshot["merchant"][field]):
            raise ContractError("Remote client fiscal snapshot mismatch")
    if nif(merchant.get("vatId")) != nif(snapshot["merchant"]["vatId"]):
        raise ContractError("Remote client tax identifier mismatch")
    if (document.get("businessId") != business_id or document.get("type") != "invoice"
            or document.get("deleted") or document.get("voided") or document.get("isRectification")
            or merchant.get("country") not in ("ES", "ESP")
            or document.get("date") != payload["date"] or document.get("originalCurrency") != "EUR"
            or document.get("currency") != "EUR" or document.get("vatType") != "iva"
            or (document.get("merchant") or {}).get("id") != payload["merchantId"]):
        raise ContractError("Remote document identity or fiscal context mismatch")
    if document.get("isDraft") is not (not issued):
        raise ContractError("Unexpected remote draft state")
    if issued and (document.get("issued") is not True or not document.get("number")):
        raise ContractError("Issuance is not yet confirmed")
    method_codes = {"unspecified": "1", "cash": "2", "bankCard": "4", "directDebit": "5", "paypal": "6", "bankCheck": "7", "factoring": "8", "bizum": "9"}
    if document.get("paymentMethod") != method_codes[payload["paymentMethod"]]:
        raise ContractError("Remote payment method mismatch")
    if text(document.get("notes")) != text(payload.get("notes")) or document.get("language", "es") != payload["language"]:
        raise ContractError("Remote notes or language mismatch")
    if document.get("fractions"):
        raise ContractError("Remote installment payments are outside this version")
    if document.get("dueDate") != payload.get("dueDate"):
        raise ContractError("Remote due date mismatch")
    for field in ("netAmount", "totalAmount"):
        if decimal(document.get(field)) != snapshot[field]:
            raise ContractError("Remote totals differ from approved Odoo totals")
    remote_lines = document.get("originalProducts")
    if not isinstance(remote_lines, list) or len(remote_lines) != len(payload["lines"]):
        raise ContractError("Remote lines differ from approved lines")
    for actual, expected, label in zip(remote_lines, payload["lines"], snapshot["descriptions"]):
        if actual.get("id") != expected["productId"]:
            raise ContractError("Remote product mismatch")
        caption = str(actual.get("concept") or "") + ("\n" + actual["description"] if actual.get("description") else "")
        if caption != label["label"] or actual.get("unit") != label["unit"] or actual.get("itemType") != label["item_type"]:
            raise ContractError("Remote line description or unit mismatch")
        if actual.get("subtype") == "supplied" or any(actual.get(k) for k in ("notSubjectCause", "exemptionCause", "inclusionCause")):
            raise ContractError("Unexpected special fiscal treatment")
        for field in ("quantity", "price", "vat"):
            if decimal(actual.get(field)) != decimal(expected[field]):
                raise ContractError("Remote quantity, price or VAT mismatch")
        for value in (actual.get("retention", 0), actual.get("equivalenceSurcharge", 0),
                      (actual.get("discount") or {}).get("percent", 0),
                      (actual.get("discount") or {}).get("amount", 0), document.get("discountPercent") or 0):
            if decimal(value or 0) != 0:
                raise ContractError("Unexpected remote discount or withholding")
    return True
