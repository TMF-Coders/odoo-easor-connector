"""Load the pure adapter without importing the Odoo addon initializer."""
import copy
import json
from pathlib import Path
import sys
import types

package = types.ModuleType("easor_test")
package.__path__ = [str(Path(__file__).resolve().parents[1] / "tmf_easor_base")]
sys.modules.setdefault("easor_test", package)
from easor_test.core import build_payload  # noqa: E402


def source():
    return {"currency": "EUR", "country": "ES", "merchant_id": "CLIENT0001", "series_id": "SERIES0001",
            "activity_id": 1, "date": "2026-10-26", "due_date": "2026-11-26", "payment_method": "cash",
            "lines": [{"product_id": "PRODUCT001", "quantity": 2, "price": 100, "vat": 21}]}


def snapshot():
    return {"payload": build_payload(source()), "company_vat": "B00000000", "netAmount": 20000, "totalAmount": 24200,
            "merchant": {"name": "Synthetic Client", "vatId": "B11111111", "address": "Test Street 1", "postalCode": "15001", "city": "Test City"},
            "descriptions": [{"id": "PRODUCT001", "label": "Synthetic Service", "unit": "hour", "item_type": "service"}]}


def document(issued=False):
    s = snapshot()
    line = copy.deepcopy(s["payload"]["lines"][0])
    line["id"] = line.pop("productId")
    line.update(concept="Synthetic Service", unit="hour", itemType="service", discount={"percent": 0})
    return {"id": "INVOICE001", "businessId": 81, "type": "invoice", "date": "2026-10-26", "dueDate": "2026-11-26",
            "currency": "EUR", "originalCurrency": "EUR", "vatType": "iva", "isDraft": not issued, "issued": issued,
            "number": "E2026/0001" if issued else "", "netAmount": 20000, "totalAmount": 24200,
            "merchant": {**s["merchant"], "id": "CLIENT0001", "country": "ES"}, "originalProducts": [line],
            "paymentMethod": "2", "paymentStatus": "pending"}


class Response:
    def __init__(self, value=None, status=200, body=None, content_type="application/json"):
        self.status_code, self.headers, self.closed = status, {"Content-Type": content_type}, False
        self.body = body if body is not None else json.dumps(value or {}).encode()

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []
        self.trust_env = True

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response
