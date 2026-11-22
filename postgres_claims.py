"""Read-only PostgreSQL adapter for the fictional local claims demo."""

import os
from pathlib import Path

from claims import ClaimServiceUnavailable


def lookup_postgres_claim(owner: str, claim_id: str) -> tuple[str, str] | None:
    try:
        import psycopg
    except ImportError as exc:
        raise ClaimServiceUnavailable() from exc

    try:
        secret_file = os.environ.get("CLAIMS_DATABASE_URL_FILE")
        url = Path(secret_file).read_text().strip() if secret_file else os.environ.get("CLAIMS_DATABASE_URL", "")
        if not url:
            raise ClaimServiceUnavailable()
        with psycopg.connect(url, connect_timeout=3, options="-c statement_timeout=3000") as database:
            return database.execute(
                "SELECT status, deduction_reason FROM public.claims WHERE id = %s AND owner = %s",
                (claim_id, owner),
            ).fetchone()
    except (psycopg.Error, OSError, ValueError) as exc:
        raise ClaimServiceUnavailable() from exc
