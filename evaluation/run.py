"""Evaluate the demo workflow from the project root with python -m evaluation.run."""

import argparse
import json
import math
from pathlib import Path
from statistics import median
from time import perf_counter

from company_library import CompanyLibrary
from support_workflow import answer_travel_question


def evaluate(cases, library):
    approved = {source.title: source.text for source in library.sources}
    rows = []
    for case in cases:
        started = perf_counter()
        result = answer_travel_question(library, case["account"], case["question"])
        elapsed_ms = (perf_counter() - started) * 1000
        sources = result["sources"]
        expected_title = case["source"]
        expected_sources = (
            [{"title": expected_title, "excerpt": approved[expected_title]}]
            if expected_title is not None
            else []
        )
        checks = {
            "status": result["status"] == case["status"],
            "answer": result["answer"] == case["answer"],
            "sources": sources == expected_sources,
        }
        rows.append({
            "id": case["id"],
            "passed": all(checks.values()),
            "checks": checks,
            "workflow_ms": round(elapsed_ms, 3),
        })

    timings = sorted(row["workflow_ms"] for row in rows)
    return {
        "cases": len(rows),
        "passed": sum(row["passed"] for row in rows),
        "workflow_median_ms": median(timings),
        "workflow_p95_ms": timings[math.ceil(0.95 * len(timings)) - 1],
        "results": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cases = json.loads(Path(__file__).with_name("cases.json").read_text(encoding="utf-8"))
    report = evaluate(cases, CompanyLibrary.load_demo())
    serialized = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return int(report["passed"] != report["cases"])


if __name__ == "__main__":
    raise SystemExit(main())
