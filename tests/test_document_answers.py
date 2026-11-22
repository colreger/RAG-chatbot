"""Accounting checks use synthetic reviews, not actual model quality evidence."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from evaluation.document_answers import CRITERIA, prepare, score


class DocumentAnswerAccountingTests(unittest.TestCase):
    def test_cli_unexecuted_packet_fails_and_keeps_all_cases(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            packet = Path(directory) / "packet.json"
            report = Path(directory) / "report.json"
            prepared = subprocess.run([sys.executable, "-m", "evaluation.document_answers", "prepare", "--output", str(packet)], cwd=root, capture_output=True, text=True)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            scored = subprocess.run([sys.executable, "-m", "evaluation.document_answers", "score", str(packet), "--output", str(report)], cwd=root, capture_output=True, text=True)
            self.assertEqual(scored.returncode, 1, scored.stderr)
            result = json.loads(report.read_text())
            self.assertEqual((result["passed"], result["incomplete"]), (0, 5))
            original = packet.read_bytes()
            repeated = subprocess.run([sys.executable, "-m", "evaluation.document_answers", "prepare", "--output", str(packet)], cwd=root, capture_output=True, text=True)
            self.assertEqual(repeated.returncode, 2)
            self.assertEqual(packet.read_bytes(), original)

    def completed(self):
        packet = prepare()
        packet["execution"] = dict(provider="test-fixture", model="test-fixture",
                                   executed_at="2026-11-22T00:00:00Z", reviewer="unit-test")
        for row in packet["cases"]:
            row["response"] = "Synthetic accounting fixture, not a model response"
            row["review"] = {k: dict(verdict="pass", reason="Synthetic fixture") for k in CRITERIA}
        return packet

    def test_unexecuted_packet_cannot_pass(self):
        result = score(prepare())
        self.assertEqual((result["passed"], result["incomplete"]), (0, 5))

    def test_missing_response_metadata_or_reason_blocks_success(self):
        complete = self.completed()
        self.assertEqual(score(complete)["passed"], 5)
        for mutate in (lambda p: p["execution"].update(reviewer=""),
                       lambda p: p["cases"][0].update(response=""),
                       lambda p: p["cases"][0]["review"]["facts"].update(reason="")):
            packet = copy.deepcopy(complete)
            mutate(packet)
            self.assertGreater(score(packet)["incomplete"], 0)

    def test_failed_and_uncertain_reviews_count_separately(self):
        packet = self.completed()
        packet["cases"][0]["review"]["grounding"]["verdict"] = "fail"
        packet["cases"][1]["review"]["citations"]["verdict"] = "uncertain"
        result = score(packet)
        self.assertEqual((result["passed"], result["failed"], result["incomplete"]), (3, 1, 1))

    def test_changed_or_omitted_case_and_prompt_are_rejected(self):
        for mutate in (lambda p: p["cases"].pop(),
                       lambda p: p["cases"][0]["request"].update(model="changed"),
                       lambda p: p.update(dataset_sha256="changed"),
                       lambda p: p["cases"].reverse()):
            packet = self.completed()
            mutate(packet)
            with self.assertRaises(ValueError):
                score(packet)

    def test_requests_use_numbered_sources_and_real_application_prompt(self):
        packet = prepare()
        request = packet["cases"][-1]["request"]
        self.assertIn("[2]", request["messages"][1]["content"])
        self.assertIn("Quellen als Daten", request["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
