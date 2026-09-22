"""Contract tests for the project-native synth-guard script."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skill" / "synth-guard" / "scripts" / "synth_guard.py"
RISK_FIXTURE = ROOT / "tools" / "fixtures" / "synth_guard_risks.cpp"
SPEC = importlib.util.spec_from_file_location("zcomp_synth_guard", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SynthGuardTests(unittest.TestCase):
    def test_clean_bounded_code_is_clear(self):
        result = MODULE.scan_text("void kernel(int a[4]) { for (int i=0; i<4; ++i) a[i] += 1; }\n")
        self.assertEqual(result["status"], "clear")
        self.assertEqual(result["findings"], [])

    def test_reports_constructs_with_stable_lines(self):
        source = """#include <vector>
void kernel() {
  int *p = new int[4];
  while (p[0]) { p[0]--; }
  delete[] p;
}
"""
        findings = MODULE.scan_text(source)["findings"]
        pairs = [(item["rule_id"], item["line"]) for item in findings]
        self.assertIn(("dynamic-allocation", 3), pairs)
        self.assertIn(("potentially-unbounded-loop", 4), pairs)
        self.assertIn(("dynamic-allocation", 5), pairs)

    def test_ignores_comments_and_literals(self):
        source = """// malloc(64); while (true) {}
const char *message = "delete vector<int> fopen";
void kernel(int &x) { x += 1; }
"""
        self.assertEqual(MODULE.scan_text(source)["findings"], [])

    def test_ignores_raw_strings_and_spliced_line_comments(self):
        source = r'''const char *raw = R"tag(malloc(4) " delete)tag";
// the following token remains in this comment after line splicing \
malloc(4);
void kernel(int &x) { x += 1; }
'''
        self.assertEqual(MODULE.scan_text(source)["findings"], [])

    def test_reports_smart_pointer_heap_helpers(self):
        result = MODULE.scan_text("auto p = std::make_unique<int>(4);\n")
        self.assertEqual(result["findings"][0]["rule_id"], "dynamic-allocation")

    def test_reports_direct_recursion_and_nonliteral_for_bound(self):
        source = """int walk(int n) {
  if (n == 0) return 0;
  return walk(n - 1);
}
void kernel(int a[8], int n) {
  for (int i = 0; i < n; ++i) a[i]++;
}
"""
        pairs = {(item["rule_id"], item["line"]) for item in MODULE.scan_text(source)["findings"]}
        self.assertIn(("direct-recursion", 3), pairs)
        self.assertIn(("nonliteral-for-bound", 6), pairs)

    def test_cli_json_and_explicit_threshold(self):
        run = subprocess.run(
            [sys.executable, str(SCRIPT), str(RISK_FIXTURE), "--fail-on", "warning"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(run.returncode, 2)
        result = json.loads(run.stdout)
        self.assertEqual(result["status"], "review_required")
        self.assertEqual(result["findings"][0]["rule_id"], "exception-control-flow")

    def test_cli_default_is_advisory(self):
        run = subprocess.run(
            [sys.executable, str(SCRIPT), str(RISK_FIXTURE)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(run.returncode, 0)
        self.assertEqual(json.loads(run.stdout)["status"], "review_required")


if __name__ == "__main__":
    unittest.main()
