"""Offline request preparation and human-review accounting; never calls a model."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from answer_machine import build_request

DATASET = Path(__file__).with_name("document-answer-cases.json")
CRITERIA = ("facts", "grounding", "citations", "behavior")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def prepare():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Dataset must contain unique, nonempty cases")
    rows = []
    for case in cases:
        request = build_request(case["question"], [SimpleNamespace(text=p) for p in case["passages"]])
        rows.append({"case": case, "request": request, "response": None,
                     "review": {key: {"verdict": "uncertain", "reason": ""} for key in CRITERIA}})
    identity = {"dataset_sha256": digest(dataset),
                "requests_sha256": digest([row["request"] for row in rows])}
    return {"version": 1, **identity, "prepared_at": datetime.now(timezone.utc).isoformat(),
            "execution": {"provider": "", "model": "", "executed_at": "", "reviewer": ""},
            "cases": rows}


def score(packet):
    expected = prepare()
    for key in ("version", "dataset_sha256", "requests_sha256"):
        if packet.get(key) != expected[key]:
            raise ValueError("Dataset or prompt version mismatch")
    rows = packet.get("cases", [])
    if len(rows) != len(expected["cases"]):
        raise ValueError("Missing or extra cases")
    metadata = packet.get("execution", {})
    complete_metadata = all(isinstance(metadata.get(k), str) and metadata[k].strip()
                            for k in ("provider", "model", "executed_at", "reviewer"))
    results = []
    for row, original in zip(rows, expected["cases"]):
        if row.get("case") != original["case"] or row.get("request") != original["request"]:
            raise ValueError("Cases or prepared requests were altered/reordered")
        review = row.get("review", {})
        if set(review) != set(CRITERIA):
            raise ValueError("Review criteria mismatch")
        verdicts = []
        for key in CRITERIA:
            item = review[key]
            if not isinstance(item, dict) or item.get("verdict") not in {"pass", "fail", "uncertain"}:
                raise ValueError("Invalid review verdict")
            reason = item.get("reason")
            if not isinstance(reason, str):
                raise ValueError("Review reason must be text")
            verdicts.append(item["verdict"] if reason.strip() else "uncertain")
        response = row.get("response")
        has_answer = isinstance(response, str) and bool(response.strip())
        status = ("incomplete" if not has_answer or not complete_metadata else
                  "fail" if "fail" in verdicts else
                  "incomplete" if "uncertain" in verdicts else "pass")
        results.append({"id": original["case"]["id"], "status": status})
    return {"kind": "human_review_accounting_not_automated_semantic_judgment",
            "dataset_sha256": expected["dataset_sha256"], "requests_sha256": expected["requests_sha256"],
            "cases": len(results), "passed": sum(r["status"] == "pass" for r in results),
            "failed": sum(r["status"] == "fail" for r in results),
            "incomplete": sum(r["status"] == "incomplete" for r in results), "results": results}


def write_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare").add_argument("--output", type=Path, required=True)
    review = commands.add_parser("score")
    review.add_argument("packet", type=Path)
    review.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            write_new(args.output, prepare())
            return 0
        report = score(json.loads(args.packet.read_text(encoding="utf-8")))
        write_new(args.output, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return int(report["passed"] != report["cases"])
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, f"Evaluation error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
