"""Tests of E0 evidence comparison, not a second set of propulsion formula tests."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import tactical_realtime_baseline as baseline


class BaselineEvidenceTests(unittest.TestCase):
    def test_difference_keeps_event_order_and_scalar_types(self):
        self.assertEqual(baseline.first_difference({"events": [1, 2]}, {"events": [2, 1]})["path"], "$.events[0]")
        self.assertIsNotNone(baseline.first_difference({"fuel": 1.0}, {"fuel": 1}))
        self.assertIsNotNone(baseline.first_difference({"seq": 1}, {"seq": True}))

    def test_difference_finds_nested_damage_and_missing_domains(self):
        old = {"ship": {"modules": [{"durability": 50.0}]}, "events": []}
        new = {"ship": {"modules": [{"durability": 49.0}]}, "events": []}
        self.assertEqual(baseline.first_difference(old, new)["path"], "$.ship.modules[0].durability")
        self.assertEqual(baseline.first_difference(old, {"ship": old["ship"]})["missing"], ["events"])
        self.assertIsNone(baseline.first_difference(old, json.loads(json.dumps(old))))

    def test_frozen_tape_and_nearest_rank(self):
        tape = baseline.load_tape(baseline.TAPE)
        self.assertEqual([(c["id"], len(c["steps"])) for c in tape["cases"]], [("mixed72", 72), ("transitions120", 120)])
        stats = baseline.summary([dict(total_s=i / 1000, integration_s=0, projection_s=0) for i in range(1, 21)])
        self.assertEqual(stats["p95_ms"], 19)
        self.assertEqual(stats["p99_ms"], 20)

    def test_replay_rejects_missing_extra_and_changed_frames(self):
        class Worker:
            def __init__(self): self.index = 0
            def resources(self): return {"resource": "same"}
            def step(self, item): self.index += 1
            def observe(self): return {"step": self.index}

        tape = {"cases": [{"id": "probe", "steps": [{}]}]}
        with tempfile.TemporaryDirectory() as folder, patch.object(baseline, "LegacyWorker", Worker):
            run = Path(folder)
            baseline.write_json(run / "resources.json", {"resource": "same"})
            for frames, expected in [([0, 1], "PASS"), ([0], "FAIL"), ([0, 1, 2], "FAIL"), ([0, 9], "FAIL")]:
                with gzip.open(run / "probe.jsonl.gz", "wt", encoding="utf-8") as stream:
                    for step in frames: stream.write(json.dumps({"step": step}) + "\n")
                self.assertEqual(baseline.replay(run, tape)["status"], expected)
            baseline.write_json(run / "resources.json", {"resource": "changed"})
            self.assertEqual(baseline.replay(run, tape)["boundary"], "resources")


if __name__ == "__main__":
    unittest.main()
