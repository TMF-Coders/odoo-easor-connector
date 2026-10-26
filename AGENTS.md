# Development rules

Target Odoo 19 Community. Do not patch Odoo core. Easor España is the sole fiscal
issuer and accounting authority; local moves are operational mirrors.

Never put credentials, real invoice fixtures or private upstream source here.
Never use the Finnish API or private /v2 as a fallback. Never invent endpoints.
All new writes need explicit scope, preflight, a durable fence, audit and unknown-
outcome recovery. Do not automatically retry remote POSTs. Do not bypass guards
with a public context boolean or sudo exposed through RPC.

Run `python -m unittest discover -s tests -v`, syntax/XML checks and the real Odoo
suite (`bash scripts/test_odoo.sh`). Report each result separately. Mocked API tests
are not proof of live issuance. Update contract coverage, user docs, limits and
UAT for functional changes. Do not merge a fiscal integration without review.
