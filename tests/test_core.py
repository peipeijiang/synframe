import unittest
import tempfile
import json
import subprocess
import sys
from pathlib import Path

from synframe.batch import instantiate, ready_tasks, retryable_candidates
from synframe.catalog import select, open_catalog, record, query
from synframe.planning import PlanError, plan
from synframe.speech import compare
from synframe.ledger import connect, get, save
from synframe.prompt import compose
from synframe.continuity import validate_piece
from synframe.dispatch import register_graph, register_retry, submit_candidate
from readiness_fixture import attest, mock_payload


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.units = [{"id": str(i), "duration_sec": 5, "text": "frase " + str(i)} for i in range(6)]

    def test_thirty_seconds_with_distinct_caps(self):
        for cap, expected in [(10, 3), (15, 2), (6, 6)]:
            result = plan(self.units, cap)
            self.assertEqual(len(result["clips"]), expected)
            self.assertEqual(result["target_duration_sec"], 30)
            self.assertEqual([i for c in result["clips"] for i in c["unit_ids"]], [str(i) for i in range(6)])
            self.assertTrue(all(c["generation_duration_sec"] <= cap for c in result["clips"]))

    def test_fixed_duration_and_trim(self):
        result = plan([{"id": "1", "duration_sec": 7}], 10, [10])
        self.assertEqual(result["clips"][0]["generation_duration_sec"], 10)
        self.assertEqual(result["clips"][0]["trim_out_sec"], 7)
        result = plan([{"id": "1", "duration_sec": 2}], 15, min_duration=4)
        self.assertEqual(result["clips"][0]["generation_duration_sec"], 4)
        with self.assertRaises(PlanError):
            plan([{"id": "1", "duration_sec": 1}], 15, [1, 2, 3], min_duration=4)

    def test_unknown_and_oversize_rejected(self):
        for cap in [None, 0, float("nan")]:
            with self.assertRaises(PlanError):
                plan(self.units, cap)
        with self.assertRaises(PlanError):
            plan([{"id": "x", "duration_sec": 11}], 10)

    def test_no_duplicate_ids(self):
        with self.assertRaises(PlanError):
            plan([{"id": "x", "duration_sec": 1}, {"id": "x", "duration_sec": 2}], 10)

    def test_known_omni_profile_applies_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            data = {"model_profile": {"model": "omni-flash", "channel": "lk888"},
                    "units": [{"id": "1", "duration_sec": 5}]}
            path.write_text(json.dumps(data))
            run = subprocess.run([sys.executable, "-m", "synframe", "plan", str(path)],
                                 check=True, capture_output=True, text=True)
            result = json.loads(run.stdout)
            self.assertEqual(result["clips"][0]["generation_duration_sec"], 6)
            self.assertEqual(result["model_profile"]["status"], "needs_verification")
            data["allowed_durations_sec"] = [5]
            path.write_text(json.dumps(data))
            self.assertNotEqual(subprocess.run([sys.executable, "-m", "synframe", "plan", str(path)],
                                               capture_output=True).returncode, 0)


class OperationsTests(unittest.TestCase):
    def test_ledger_recovers_remote_id_and_rejects_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jobs.db"
            row = {"candidate_id": "c1", "batch_id": "b1", "state": "submitted", "request_hash": "h1", "remote_task_id": "t1", "cost": 1.5, "metadata": {"receipt": "ok", "payment": {"receipt": "proof"}}}
            with connect(path) as db:
                save(db, row)
            with connect(path) as db:
                self.assertEqual(get(db, "c1")["remote_task_id"], "t1")
                with self.assertRaises(ValueError):
                    save(db, {**row, "remote_task_id": "t2"})
                save(db, {**row, "state": "unknown"})
                with self.assertRaises(ValueError):
                    save(db, {**row, "state": "draft"})
                update = {k: v for k, v in row.items() if k not in ("cost", "metadata")}
                save(db, {**update, "state": "running"})
                self.assertEqual(get(db, "c1")["cost"], 1.5)
                self.assertEqual(get(db, "c1")["metadata"]["receipt"], "ok")
                with self.assertRaises(ValueError):
                    save(db, {**update, "state": "running", "cost": None})
                with self.assertRaises(ValueError):
                    save(db, {**update, "state": "running", "metadata": {"receipt": None}})
                with self.assertRaises(ValueError):
                    save(db, {**update, "state": "running", "metadata": {"payment": {"receipt": None}}})
                with self.assertRaises(ValueError):
                    save(db, {**row, "candidate_id": "c2", "remote_task_id": None})

    def test_authorized_dispatch_unknown_never_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = mock_payload()
            with connect(Path(tmp) / "jobs.db") as db:
                register_graph(db, "b", [
                    {"candidate_id": "c1", "payload": payload, "depends_on": []},
                    {"candidate_id": "c2", "payload": payload, "depends_on": []},
                    {"candidate_id": "c3", "payload": payload, "depends_on": ["c2"]},
                ])
                for candidate in ['c1', 'c2', 'c3']:
                    attest(db, tmp, 'b', candidate, payload)
                with self.assertRaises(ValueError):
                    submit_candidate(db, "c1", "b", payload, {"approved": False}, lambda p: {})
                result = submit_candidate(db, "c1", "b", payload,
                                          {"approved": True, "batch_id": "b", "candidate_ids": ["c1"]},
                                          lambda p: {"task_id": "remote-1", "cost": 1})
                self.assertEqual(result["remote_task_id"], "remote-1")
                with self.assertRaises(ValueError):
                    submit_candidate(db, "c1", "b", payload,
                                     {"approved": True, "batch_id": "b", "candidate_ids": ["c1"]}, lambda p: {})
                def broken(payload):
                    raise TimeoutError("unknown outcome")
                result = submit_candidate(db, "c2", "b", payload, {"approved": True, "batch_id": "b", "candidate_ids": ["c2"]}, broken)
                self.assertEqual(result["state"], "unknown")
                self.assertEqual(get(db, "c2")["state"], "unknown")
                called = []
                with self.assertRaisesRegex(ValueError, "dependency"):
                    submit_candidate(db, "c3", "b", payload, {"approved": True, "batch_id": "b", "candidate_ids": ["c3"]},
                                     lambda p: called.append(True))
                self.assertEqual(called, [])
                self.assertIsNone(get(db, "c3"))
                with self.assertRaisesRegex(ValueError, "dependencies belong"):
                    submit_candidate(db, "c3", "b", {**payload, "depends_on": ["c2"]},
                                     {"approved": True, "batch_id": "b", "candidate_ids": ["c3"]}, lambda p: called.append(True))
                self.assertEqual(called, [])

    def test_graph_rejects_omission_and_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            with connect(Path(tmp) / "jobs.db") as db:
                with self.assertRaisesRegex(ValueError, "depends_on explicitly"):
                    register_graph(db, "x", [{"candidate_id": "a", "payload": {}}])
                with self.assertRaisesRegex(ValueError, "cycle"):
                    register_graph(db, "x", [
                        {"candidate_id": "a", "payload": {}, "depends_on": ["b"]},
                        {"candidate_id": "b", "payload": {}, "depends_on": ["a"]}])
                with self.assertRaisesRegex(ValueError, "unknown"):
                    register_graph(db, "x", [{"candidate_id": "a", "payload": {}, "depends_on": ["missing"]}])
                register_graph(db, "x", [{"candidate_id": "a", "payload": {"prompt": "original"}, "depends_on": []}])
                attest(db, tmp, 'x', 'a', {'prompt': 'original'})
                calls = []
                with self.assertRaisesRegex(ValueError, "differs"):
                    submit_candidate(db, "a", "x", {"prompt": "changed"},
                                     {"approved": True, "batch_id": "x", "candidate_ids": ["a"]}, lambda p: calls.append(p))
                self.assertEqual(calls, [])

    def test_template_drops_runtime(self):
        batch = instantiate({"rows": [{"prompt": "hello", "task_id": "old", "candidates": [1], "status": "failed"}]}, "new")
        self.assertEqual(batch["rows"], [{"prompt": "hello"}])

    def test_dependencies_and_unknown_not_retried(self):
        tasks = [{"id": "image", "status": "success"}, {"id": "video", "status": "draft", "depends_on": ["image"]}]
        self.assertEqual(ready_tasks(tasks), ["video"])
        self.assertEqual(retryable_candidates([{"id": "a", "status": "unknown"}, {"id": "b", "status": "failed"}]), ["b"])
        with self.assertRaises(ValueError):
            ready_tasks([{"id": "x", "status": "draft", "depends_on": ["missing"]}])

    def test_spanish_comparison_is_advisory(self):
        result = compare("Cuesta diez pesos", "Cuesta veinte pesos", "es-MX", ["diez"])
        self.assertEqual(result["locale"], "es-MX")
        self.assertEqual(result["status"], "needs_human_review")
        self.assertEqual(result["critical_missing"], ["diez"])
        self.assertEqual(result["changes"][0]["type"], "changed")
        self.assertEqual(compare("diez", "diezavos", "es-MX", ["diez"])["critical_missing"], ["diez"])
        timed = compare("cuesta diez pesos", "cuesta veinte pesos", "es-MX", asr_words=[
            {"word": "cuesta", "start": 0, "end": .4, "probability": .9},
            {"word": "veinte", "start": .4, "end": .8, "probability": .6},
            {"word": "pesos", "start": .8, "end": 1.2, "probability": .9}])
        self.assertEqual(timed["changes"][0]["start_sec"], .4)
        self.assertEqual(timed["timestamps"], "asr_estimate")
        missing = compare("cuesta diez pesos", "cuesta pesos", "es-MX", asr_words=[
            {"word": "cuesta", "start": 0, "end": .4},
            {"word": "pesos", "start": .8, "end": 1.2}])
        self.assertEqual(missing["changes"][0]["timing_kind"], "neighbor_bounds")
        self.assertEqual((missing["changes"][0]["start_sec"], missing["changes"][0]["end_sec"]), (.4, .8))

    def test_spanish_prompt_preserves_dialogue(self):
        text = "¡Pruébalo hoy! Así se usa."
        result = compose("Show product", text, "es-MX", 8, 10)
        self.assertIn(text, result["prompt"])
        self.assertFalse(result["provider_validated"])
        with self.assertRaises(ValueError):
            compose("Show product", text, "es-MX", 15, 10)

    def test_catalog_requires_approved_asset(self):
        assets = [{"sku": "1", "rights": "approved", "technical_qc": "pass", "creative_qc": "pass"},
                  {"sku": "2", "rights": "approved", "technical_qc": "pass", "creative_qc": "pass"},
                  {"sku": "1", "rights": "approved", "technical_qc": "pass", "creative_qc": "pending"}]
        self.assertEqual(select(assets, "1"), assets[:1])

    def test_persistent_catalog_provenance_and_sku(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "product.jpg"
            media.write_bytes(b"synthetic")
            path = Path(tmp) / "assets.db"
            with open_catalog(path) as db:
                record(db, {"id": "a", "sku": "sku1", "persona": "p1", "rights": "approved",
                            "technical_qc": "pass", "creative_qc": "pass", "path": str(media),
                            "source": "synthetic", "metadata": {"version": 1}})
            with open_catalog(path) as db:
                self.assertEqual(query(db, "sku1", "p1")[0]["source"], "synthetic")
                self.assertEqual(query(db, "sku2"), [])
                self.assertEqual(query(db, "sku1", "p2"), [])
                media.write_bytes(b"changed")
                self.assertEqual(query(db, "sku1", "p1"), [])

    def test_continuity_checks_references_not_visual_truth(self):
        piece = {"sku": "sku1", "personas": ["p1"], "dialogue_ids": ["L1"],
                 "clips": [{"id": "a", "sku": "sku1", "persona": "p1", "role": "a_roll", "covers": ["L1"]}]}
        self.assertEqual(validate_piece(piece)["visual_identity"], "needs_human_review")
        piece["clips"][0]["sku"] = "sku2"
        self.assertEqual(validate_piece(piece)["status"], "fail")
        self.assertEqual(validate_piece({"sku": "sku1", "clips": []})["status"], "fail")
        piece["clips"][0]["sku"] = "sku1"
        piece["clips"][0]["covers"] = []
        self.assertIn("uncovered dialogue: L1", validate_piece(piece)["errors"])
        ordered = {"sku": "s", "personas": ["p1", "p2"], "primary_persona": "p1",
                   "single_person_continuity": True, "require_narrative_order": True,
                   "dialogue_ids": ["L1", "L2"], "clips": [
                       {"id": "a", "sku": "s", "role": "a_roll", "persona": "p2",
                        "covers": ["L1", "L2"], "narrates": ["L2", "L1"]}]}
        self.assertEqual(len(validate_piece(ordered)["errors"]), 2)
        ordered["clips"].append({"id": "b", "sku": "s", "role": "b_roll", "persona": "p2", "covers": ["L1"]})
        self.assertTrue(any("visible persona" in x for x in validate_piece(ordered)["errors"]))


if __name__ == "__main__":
    unittest.main()
