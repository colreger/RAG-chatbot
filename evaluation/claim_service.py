"""Offline claim-service contract evaluation; no model or cloud account needed.

Run from the project root: python -m evaluation.claim_service --output report.json
"""

import argparse
import json
from pathlib import Path
import unittest


class ContractResult(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.rows = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.rows.append({"id": test.id(), "status": "passed"})

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.rows.append({"id": test.id(), "status": "failed"})

    def addError(self, test, err):
        super().addError(test, err)
        self.rows.append({"id": test.id(), "status": "error"})

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.rows.append({"id": test.id(), "status": "skipped"})


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def evaluate():
    root = Path(__file__).resolve().parents[1]
    loader = unittest.TestLoader()
    storage = loader.discover(str(root / "tests"), pattern="test_claim_failures.py")
    # Separate loader: the two directories are independently discoverable roots.
    api = unittest.TestLoader().discover(str(root / "api_tests"), pattern="test_claims_api.py")
    selected = list(cases(storage))
    excluded = []
    for case in cases(api):
        if case.id().rsplit(".", 1)[-1].startswith("test_field_"):
            excluded.append(case.id())
        else:
            selected.append(case)
    result = ContractResult()
    unittest.TestSuite(selected).run(result)
    report = {
        "scope": "Local deterministic claim storage and authenticated HTTP contract",
        "tests_run": result.testsRun,
        "passed": sum(row["status"] == "passed" for row in result.rows),
        "successful": result.wasSuccessful() and not result.skipped and result.testsRun > 0,
        "results": result.rows,
        "excluded_unrelated_field_tests": excluded,
        "limitations": [
            "SQLite outages are injected, not real persistent-database outages.",
            "No hosted LLM, Streamlit browser, cloud deployment or MCP is evaluated.",
            "This does not establish production readiness or regulatory compliance.",
        ],
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate()
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return int(not report["successful"])


if __name__ == "__main__":
    raise SystemExit(main())
