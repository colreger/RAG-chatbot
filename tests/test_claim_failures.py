"""Offline failure injection at the SQLite/application boundary."""

import sqlite3
import unittest
from unittest.mock import Mock, patch

from claims import ClaimServiceUnavailable, lookup_demo_claim


class ClaimFailureTests(unittest.TestCase):
    def test_connection_error_is_translated_without_exposing_details(self):
        failure = sqlite3.OperationalError("fake-password at /private/claims.sqlite")
        with patch("claims.sqlite3.connect", side_effect=failure):
            with self.assertRaises(ClaimServiceUnavailable) as caught:
                lookup_demo_claim("gandalf", "RK-1042")
        self.assertIs(caught.exception.__cause__, failure)
        self.assertNotIn("fake-password", str(caught.exception))
        self.assertNotIn("/private/claims.sqlite", str(caught.exception))
        self.assertIn("später erneut", str(caught.exception))

    def test_query_error_closes_connection_and_preserves_cause(self):
        failure = sqlite3.DatabaseError("SELECT private FROM claims")
        database = Mock()
        database.execute.side_effect = [Mock(), Mock(), failure]
        with patch("claims.sqlite3.connect", return_value=database):
            with self.assertRaises(ClaimServiceUnavailable) as caught:
                lookup_demo_claim("gandalf", "RK-1042")
        database.close.assert_called_once_with()
        self.assertIs(caught.exception.__cause__, failure)
        self.assertNotIn("SELECT", str(caught.exception))

    def test_next_request_succeeds_after_failure(self):
        with patch("claims.sqlite3.connect", side_effect=sqlite3.OperationalError("test outage")):
            with self.assertRaises(ClaimServiceUnavailable):
                lookup_demo_claim("gandalf", "RK-1042")
        self.assertEqual(
            lookup_demo_claim("gandalf", "RK-1042"),
            ("Teilweise erstattet", "Minibar ist eine private Ausgabe."),
        )


if __name__ == "__main__":
    unittest.main()
