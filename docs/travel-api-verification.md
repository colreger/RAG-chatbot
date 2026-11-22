# Travel API: local deployment and CI

The authenticated travel-claim flow now supports an explicitly selected
PostgreSQL backend, safe database failures, request telemetry and two read-only
local MCP tools. The SQLite demo remains the default outside Compose.

The API container includes only the required source files and fictional policy
fixture. It runs as UID 10001 with a read-only filesystem and mounts generated
credentials at runtime. Compose binds API and PostgreSQL ports to loopback.
The database application role is SELECT-only; account filtering remains in SQL.

## Run locally

Use Python 3.13 with `requirements-api.lock` in a virtual environment.

```sh
python scripts/setup_claims_postgres.py
docker compose -f compose.claims.yaml up -d --build --wait
python -m evaluation.ci --integration --output-dir /tmp/travel-api-ci
docker compose -f compose.claims.yaml stop
```

Setup refuses to overwrite credentials. Keep `.claims-local` together with its
database volume. Do not commit that directory. On Macs where Docker cannot read
the checkout, copy only the required runtime files into a new private directory:

```sh
python scripts/setup_claims_postgres.py --docker-runtime /private/tmp/claims-runtime
export CLAIMS_RUNTIME_DIR=/private/tmp/claims-runtime
```

Keep that setting for all subsequent Compose commands. For isolated Compose
project names, set `CLAIMS_TEST_CONTAINER` to the API container name when running
the log verifier; its default is `support-claims-learning-api-1`.

## MCP verification

The bridge binds one account from an owner-only credentials file at startup.
Tools accept a claim reference or question, never an account override, SQL or
arbitrary destination. Authorization remains in the authenticated HTTP API.
Response metadata carries the API request ID; this is correlation, not full
distributed tracing.

```sh
python -m venv .venv-mcp
.venv-mcp/bin/python -m pip install -r requirements-mcp.lock
.venv-mcp/bin/python -m unittest discover -s tests -p test_mcp_claims.py -v
.venv-mcp/bin/python -m evaluation.mcp_protocol --output /tmp/mcp-protocol.json
```

Run protocol checks while the dedicated API/PostgreSQL stack is running.
The credentials are fictional and local; this does not implement remote MCP
OAuth or a public service.

## CI scope

`.github/workflows/claims-api.yml` runs offline checks, builds the API, starts
PostgreSQL, exercises actual HTTP and log behavior, then checks real MCP stdio
sessions. It uploads reports and stops the dedicated containers. A checked-in
workflow is not proof of a successful hosted run; inspect the resulting Actions
run after publication of this branch.

The local runner fails on failures, empty results or malformed reports.
Infrastructure checks are separate from deterministic workflow Evals and human
answer-review accounting. The prepared document-answer cases have not been
executed against a hosted LLM. Their accounting tests do not measure answer
quality.

## Limits

This change covers the travel workflow. Other local experimental features are
not introduced by this branch. HTTP Basic is for loopback learning only.
Metrics are process-local and authenticated with the demo account, without a
separate operator role. Cloud deployment, public identity, persistent telemetry
and production reliability remain outside the verified scope.
