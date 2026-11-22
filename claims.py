"""Travel claim lookup shared by the local UI and HTTP API."""

from contextlib import closing
import os
import re
import sqlite3

CLAIM_ID_PATTERN = r"^RK-[0-9]{4}$"


class ClaimServiceUnavailable(RuntimeError):
    """Safe application boundary for unavailable claim storage."""

    def __init__(self):
        super().__init__("Der Reisekostenstatus ist vorübergehend nicht verfügbar. Bitte versuche es später erneut.")


def lookup_demo_claim(owner: str, claim_id: str) -> tuple[str, str] | None:
    # Each request gets an isolated, in-memory fixture. No real records are persisted.
    try:
        with closing(sqlite3.connect(":memory:")) as database:
            database.execute(
                """CREATE TABLE claims (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    status TEXT NOT NULL, deduction_reason TEXT NOT NULL
                )"""
            )
            database.execute(
                "INSERT INTO claims VALUES (?, ?, ?, ?)",
                ("RK-1042", "gandalf", "Teilweise erstattet", "Minibar ist eine private Ausgabe."),
            )
            return database.execute(
                "SELECT status, deduction_reason FROM claims WHERE id = ? AND owner = ?",
                (claim_id, owner),
            ).fetchone()
    except sqlite3.Error as exc:
        raise ClaimServiceUnavailable() from exc


def claim_with_policy(library, owner: str, claim_id: str) -> dict[str, str] | None:
    if not re.fullmatch(CLAIM_ID_PATTERN, claim_id):
        raise ValueError("Ungültige Vorgangsnummer. Beispiel: RK-1042.")
    backend = os.environ.get("CLAIMS_BACKEND", "sqlite-demo")
    if backend == "postgres":
        from postgres_claims import lookup_postgres_claim
        row = lookup_postgres_claim(owner, claim_id)
    elif backend == "sqlite-demo":
        row = lookup_demo_claim(owner, claim_id)
    else:
        raise ClaimServiceUnavailable()
    if row is None:
        return None
    for source in library.search(owner, "Minibar private Ausgaben"):
        if source.title == "Private Ausgaben bei Dienstreisen":
            return {
                "status": row[0],
                "deduction_reason": row[1],
                "policy_title": source.title,
                "policy_excerpt": source.text,
            }
    raise LookupError("Richtlinie nicht verfügbar.")
