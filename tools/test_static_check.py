"""Static-check unit tests using synthetic ASTs; no Vitis or dataset required."""
from pathlib import Path
import hashlib
import tempfile
import time
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.static_check import (
    FAIL, PASS, UNKNOWN, HARD_RULES, apply_rules, combine, result_dict, CheckResult, Finding,
)


def ir(**overrides):
    base = dict(functions=[], calls=[], new_exprs=[], delete_exprs=[],
                virtual_methods=[], exceptions=[], unresolved_calls=False)
    base.update(overrides)
    return base


def fn(name, definition=True, params=None, return_type="void", usr=None, location="k.cpp:1:1"):
    return dict(name=name, usr=usr or name, is_definition=definition, params=params or [],
                return_type=return_type, location=location)


class StaticCheckTests(unittest.TestCase):
    def test_legal_top_matches_contract(self):
        contract = {"name": "TopModule", "return_type": "void",
                    "params": [{"name": "zero", "type": "ap_uint<1>&"}]}
        tree = ir(functions=[fn("TopModule", params=[{"name": "zero", "type": "ap_uint<1>&"}])])
        findings = apply_rules(tree, contract, "TopModule")
        by_id = {f.rule_id: f.verdict for f in findings}
        self.assertEqual(by_id["R01_TOP_INTERFACE"], PASS)
        self.assertEqual(by_id["R02_DISABLED_CONSTRUCT"], PASS)
        self.assertEqual(by_id["R03_RUNTIME_RECURSION"], PASS)
        self.assertEqual(combine(PASS, findings, True), PASS)

    def test_missing_top_is_fail(self):
        contract = {"name": "TopModule", "return_type": "void", "params": []}
        findings = apply_rules(ir(functions=[fn("helper")]), contract, "TopModule")
        self.assertEqual(next(f.verdict for f in findings if f.rule_id == "R01_TOP_INTERFACE"), FAIL)

    def test_signature_mismatch_is_fail(self):
        contract = {"name": "TopModule", "return_type": "void",
                    "params": [{"name": "x", "type": "ap_uint<4>"}, {"name": "y", "type": "ap_uint<4>"}]}
        tree = ir(functions=[fn("TopModule", params=[{"name": "x", "type": "int"}])])
        findings = apply_rules(tree, contract, "TopModule")
        self.assertEqual(next(f.verdict for f in findings if f.rule_id == "R01_TOP_INTERFACE"), FAIL)

    def test_new_is_observed_not_hard_fail(self):
        contract = {"name": "TopModule", "return_type": "void", "params": []}
        tree = ir(functions=[fn("TopModule")], new_exprs=[{"location": "k.cpp:3:5"}])
        findings = apply_rules(tree, contract, "TopModule")
        self.assertEqual(next(f.verdict for f in findings if f.rule_id == "R02_DISABLED_CONSTRUCT"), UNKNOWN)
        self.assertEqual(combine(PASS, findings, True), PASS)
        self.assertNotIn("R02_DISABLED_CONSTRUCT", HARD_RULES)

    def test_direct_recursion_is_fail(self):
        contract = {"name": "TopModule", "return_type": "void", "params": []}
        tree = ir(functions=[fn("TopModule"), fn("walk")],
                  calls=[{"caller_usr": "walk", "callee_usr": "walk", "callee_name": "walk",
                          "resolved": True, "location": "k.cpp:4:3"}])
        findings = apply_rules(tree, contract, "TopModule")
        self.assertEqual(next(f.verdict for f in findings if f.rule_id == "R03_RUNTIME_RECURSION"), FAIL)

    def test_indirect_recursion_is_fail(self):
        contract = {"name": "TopModule", "return_type": "void", "params": []}
        tree = ir(functions=[fn("TopModule"), fn("a"), fn("b")],
                  calls=[
                      {"caller_usr": "a", "callee_usr": "b", "callee_name": "b", "resolved": True, "location": "k.cpp:2:3"},
                      {"caller_usr": "b", "callee_usr": "a", "callee_name": "a", "resolved": True, "location": "k.cpp:3:3"},
                  ])
        findings = apply_rules(tree, contract, "TopModule")
        self.assertEqual(next(f.verdict for f in findings if f.rule_id == "R03_RUNTIME_RECURSION"), FAIL)

    def test_virtual_method_is_not_hard_reject(self):
        contract = {"name": "TopModule", "return_type": "void", "params": []}
        tree = ir(functions=[fn("TopModule")],
                  virtual_methods=[{"name": "step", "location": "k.cpp:6:5"}])
        findings = apply_rules(tree, contract, "TopModule")
        self.assertEqual(next(f.verdict for f in findings if f.rule_id == "R02_DISABLED_CONSTRUCT"), UNKNOWN)
        self.assertEqual(combine(PASS, findings, True), PASS)

    def test_unresolved_call_is_unknown_not_fail(self):
        contract = {"name": "TopModule", "return_type": "void", "params": []}
        tree = ir(functions=[fn("TopModule")], unresolved_calls=True,
                  calls=[{"caller_usr": "TopModule", "callee_usr": "", "callee_name": "",
                          "resolved": False, "location": "k.cpp:2:3"}])
        findings = apply_rules(tree, contract, "TopModule")
        self.assertEqual(next(f.verdict for f in findings if f.rule_id == "R03_RUNTIME_RECURSION"), UNKNOWN)
        self.assertEqual(combine(PASS, findings, True), UNKNOWN)

    def test_missing_contract_is_unknown(self):
        tree = ir(functions=[fn("TopModule")])
        findings = apply_rules(tree, None, "TopModule")
        self.assertEqual(next(f.verdict for f in findings if f.rule_id == "R01_TOP_INTERFACE"), UNKNOWN)

    def test_frontend_fail_does_not_need_rules_for_overall_fail(self):
        self.assertEqual(combine(FAIL, [], True), FAIL)
        self.assertEqual(combine(UNKNOWN, [], True), UNKNOWN)
        self.assertEqual(combine(PASS, [], False), PASS)

    def test_checker_does_not_modify_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source = directory / "candidate.cpp"
            task = directory / "tb.cpp"
            source.write_text("void TopModule() {}\n", encoding="utf-8")
            task.write_text("void TopModule();\n", encoding="utf-8")
            before_source = hashlib.sha256(source.read_bytes()).hexdigest()
            before_task = hashlib.sha256(task.read_bytes()).hexdigest()
            mtime = source.stat().st_mtime_ns
            time.sleep(0.05)
            findings = apply_rules(ir(functions=[fn("TopModule")]),
                                   {"name": "TopModule", "return_type": "void", "params": []},
                                   "TopModule")
            self.assertTrue(findings)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before_source)
            self.assertEqual(hashlib.sha256(task.read_bytes()).hexdigest(), before_task)
            self.assertEqual(source.stat().st_mtime_ns, mtime)

    def test_result_dict_serializes(self):
        result = CheckResult(PASS, "B", PASS, [Finding("R01_TOP_INTERFACE", PASS, None, "ok")], [],
                             {"total_seconds": 0.1})
        payload = result_dict(result)
        self.assertEqual(payload["status"], PASS)
        self.assertEqual(payload["findings"][0]["rule_id"], "R01_TOP_INTERFACE")


if __name__ == "__main__":
    unittest.main()
