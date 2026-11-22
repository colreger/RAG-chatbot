"""Run repeatable local CI checks and keep reports separate from credentials."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--integration", action="store_true", help="Also check a running PostgreSQL and API Compose stack")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    steps = [
        ("setup", ["-m", "unittest", "discover", "-s", "tests", "-p", "test_claims_setup.py", "-v"], None),
        ("observability", ["-m", "unittest", "discover", "-s", "tests", "-p", "test_request_observability.py", "-v"], None),
        ("answer-eval-accounting", ["-m", "unittest", "discover", "-s", "tests", "-p", "test_document_answers.py", "-v"], None),
        ("claim-contract", ["-m", "evaluation.claim_service", "--output", str(output / "claim-contract.json")], "successful"),
        ("workflow", ["-m", "evaluation.run", "--output", str(output / "workflow.json")], "workflow"),
    ]
    if args.integration:
        for name, module in (("postgres", "evaluation.postgres_claims"), ("container", "evaluation.container_http"), ("live-observability", "evaluation.observability")):
            steps.append((name, ["-m", module, "--output", str(output / f"{name}.json")], "successful"))
    results = []
    for name, command, gate in steps:
        environment = dict(os.environ, CLAIMS_BACKEND="sqlite-demo", RAG_ALLOW_EXTERNAL="false", LANGSMITH_TRACING="false")
        completed = subprocess.run([sys.executable, *command], cwd=ROOT, env=environment, capture_output=True, text=True)
        (output / f"{name}.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
        passed = completed.returncode == 0
        count = 0
        if gate and passed:
            try:
                report = json.loads((output / f"{name}.json").read_text())
                if gate == "workflow":
                    count = report["cases"]
                    passed = count > 0 and report["passed"] == count
                else:
                    count = report["tests_run"]
                    passed = report["successful"] is True and count > 0 and report["passed"] == count
            except (OSError, ValueError, KeyError, TypeError):
                passed = False
        elif not gate and passed:
            match = re.search(r"Ran ([1-9][0-9]*) tests?", completed.stderr)
            passed = match is not None
            count = int(match.group(1)) if match else 0
        results.append({"step": name, "passed": passed, "checks": count, "exit_code": completed.returncode})
    report = {
        "scope": "Local CI with running containers" if args.integration else "Local offline CI",
        "successful": all(row["passed"] for row in results),
        "steps": results,
        "limits": ["Not evidence of a hosted CI run, cloud deployment, production readiness or independent user proficiency."],
    }
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(not report["successful"])


if __name__ == "__main__":
    raise SystemExit(main())
