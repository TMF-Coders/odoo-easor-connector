#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
name="tmf-easor-test-${RANDOM}-$$"
pw="$(openssl rand -hex 16)"
cleanup() { docker rm -f "$name-db" >/dev/null 2>&1 || true; docker network rm "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker network create "$name" >/dev/null
docker run -d --name "$name-db" --network "$name" --network-alias db -e POSTGRES_DB=postgres -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD="$pw" postgres:16 >/dev/null
for attempt in $(seq 1 60); do
  if docker exec "$name-db" pg_isready -U odoo >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$name-db" pg_isready -U odoo
mkdir -p test-results
# Synthetic DB and mocked API only; production secrets are never required.
docker run --rm --network "$name" -v "$PWD:/mnt/extra-addons:ro" \
  -e HOST=db -e USER=odoo -e PASSWORD="$pw" odoo:19.0 \
  odoo -d easor_test \
  --init=tmf_easor_invoicing --without-demo=all --stop-after-init --test-enable \
  --test-tags=/tmf_easor_invoicing --http-port=0 --log-level=test \
  2>&1 | tee test-results/odoo.log
if grep -Eq '(FAIL:|ERROR:|[1-9][0-9]* failed,|[1-9][0-9]* error\(s\))' test-results/odoo.log; then
  echo 'Odoo test failures found'; exit 1
fi
grep -q 'TestEasorInvoicing' test-results/odoo.log || { echo 'Tests were not executed'; exit 1; }
docker image inspect odoo:19.0 --format '{{json .RepoDigests}}' > test-results/odoo-image.txt
