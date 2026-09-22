"""Fast protocol tests for the RAG-off Workflow Skill ablation harness."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.core.contracts import json_digest
from agent.workflow.runtime import WorkflowSkills
from tools.audit_skill_compare import EXPECTED_SKILLS, audit_run
from tools.run_skill_compare import _discover, _policy, _selection_ids


class SkillCompareTests(unittest.TestCase):
    def test_condition_policies_are_rag_off_and_match_the_matrix(self):
        for condition, expected in EXPECTED_SKILLS.items():
            policy = _policy(condition)
            self.assertFalse(policy["rag_enabled"])
            self.assertFalse(policy["skills_enabled"])
            self.assertEqual(WorkflowSkills(policy).enabled, expected)

    def test_selection_is_explicit_and_preserves_declared_order(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "selection.json"
            path.write_text(json.dumps({"tasks": ["Prob016", "Prob041"]}), encoding="utf-8")
            self.assertEqual(_selection_ids(path), ["Prob016", "Prob041"])
            path.write_text(json.dumps({"categories": {"a": ["Prob002"], "b": ["Prob001"]}}),
                            encoding="utf-8")
            self.assertEqual(_selection_ids(path), ["Prob002", "Prob001"])

    def test_smoke_selection_and_dataset_identity_preflight(self):
        class Args:
            dataset = str(ROOT / "data/processed/bench4hls")
            all = False
            selection = str(ROOT / "experiments/skill-ablation/smoke.json")
            task = None
        _, _, tasks, feedback = _discover(Args())
        self.assertEqual([task.name for task in tasks],
                         ["Prob016", "Prob041", "Prob045", "Prob071", "Prob104", "Prob152"])
        self.assertEqual(feedback, "compiler_diagnostics")

    def test_auditor_rejects_any_retrieval_artifact(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            policy = _policy("S0")
            snapshot = WorkflowSkills(policy).snapshot()
            receipt = {
                "rag_enabled": False, "rag_history": [], "policy_sha256": json_digest(policy),
                "workflow_skills_sha256": json_digest(snapshot), "workflow_skills_enabled": [],
                "workflow_requests": 0, "candidates": [], "task_sha256": "task",
            }
            for name, value in (("result.json", receipt), ("policy.json", policy),
                                ("workflow_skills.json", snapshot), ("config.json", {"hls": {}})):
                (directory / name).write_text(json.dumps(value), encoding="utf-8")
            (directory / "events.jsonl").write_text(json.dumps({"event": "run_finished"}) + "\n",
                                                     encoding="utf-8")
            self.assertEqual(audit_run(directory, "S0", policy), [])
            (directory / "retrieval.json").write_text("{}", encoding="utf-8")
            self.assertTrue(any("retrieval" in item for item in audit_run(directory, "S0", policy)))


if __name__ == "__main__":
    unittest.main()
