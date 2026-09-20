"""Pure-function tests for evaluation.diagnostics; no Vitis or model dependency.

Fixtures are self-contained string constants trimmed from real Bench4HLS logs
(Prob065 compile failure, Prob117 functional failure), so they reproduce without
the gitignored ``output/`` tree.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.diagnostics import (
    RELEASABLE_CODE_KINDS,
    classify_category,
    extract_diagnostics,
    extract_entries,
    format_entries,
)

# Two sibling compiler errors sharing the same two notes; trimmed from Prob065 csim.log.
COMPILER_LOG = r"""../../../../input/kernel.cpp:23:9: error: no matching function for call to object of type 'ap_uint<3>'
        new_ena(0) = ena_tens;
        ^~~~~~~
D:/AMDDesignTools/2026.1/Vitis/include\etc\ap_int_base.h:1098:51: note: candidate function not viable: requires 2 arguments, but 1 was provided
  AP_INLINE AP_NODEBUG ap_range_ref<_AP_W, _AP_S> operator()(int Hi, int Lo) {
                                                  ^
D:/AMDDesignTools/2026.1/Vitis/include\etc\ap_int_base.h:1107:51: note: candidate function template not viable: requires 2 arguments, but 1 was provided
  AP_INLINE AP_NODEBUG ap_range_ref<_AP_W, _AP_S> operator()(
                                                  ^
../../../../input/kernel.cpp:24:9: error: no matching function for call to object of type 'ap_uint<3>'
        new_ena(1) = ena_hundreds;
        ^~~~~~~
D:/AMDDesignTools/2026.1/Vitis/include\etc\ap_int_base.h:1098:51: note: candidate function not viable: requires 2 arguments, but 1 was provided
  AP_INLINE AP_NODEBUG ap_range_ref<_AP_W, _AP_S> operator()(int Hi, int Lo) {
                                                  ^
D:/AMDDesignTools/2026.1/Vitis/include\etc\ap_int_base.h:1107:51: note: candidate function template not viable: requires 2 arguments, but 1 was provided
  AP_INLINE AP_NODEBUG ap_range_ref<_AP_W, _AP_S> operator()(
                                                  ^
"""

# Functional failure tail; trimmed from Prob117 csim.log.
FUNCTIONAL_LOG = """\
Mismatch at cycle 48: expected 1, got 0
Mismatch at cycle 79: expected 1, got 0
Test Failed: 6 mismatches detected out of 200 cases.
@E Simulation failed: Function 'main' returns nonzero value '1'.
ERROR: [SIM 211-100] 'csim_design' failed: nonzero return value.
"""

ENV_LOG = "cat.exe: *** fatal error - couldn't create signal pipe, Win32 error 5\n"
SYNTH_LOG = "ERROR: [SYNCHK 200-128] 'csynth_design' failed: non-synthesizable operation.\n"
LICENSE_LOG = "ERROR: license checkout failed: no seats available.\n"

NO_FAILURE = {'timed_out': False}


class DiagnosticsTests(unittest.TestCase):
    def test_compiler_block_full_extraction(self):
        entries = extract_entries('csim', COMPILER_LOG)
        self.assertEqual([e['kind'] for e in entries], ['compiler', 'compiler'])
        first = entries[0]
        self.assertEqual(first['location'], '../../../../input/kernel.cpp:23:9')
        self.assertEqual(first['message'], "no matching function for call to object of type 'ap_uint<3>'")
        self.assertEqual(first['source'], '        new_ena(0) = ena_tens;')
        self.assertEqual(first['caret'], '        ^~~~~~~')

    def test_note_dedup_across_sibling_errors(self):
        entries = extract_entries('csim', COMPILER_LOG)
        # Each note repeats under every error; only the first-seen copy is kept.
        self.assertEqual(entries[0]['notes'], [
            'candidate function not viable: requires 2 arguments, but 1 was provided',
            'candidate function template not viable: requires 2 arguments, but 1 was provided',
        ])
        self.assertEqual(entries[1]['notes'], [])

    def test_functional_mismatch_kind(self):
        entries = extract_entries('csim', FUNCTIONAL_LOG)
        kinds = [e['kind'] for e in entries]
        self.assertEqual(kinds, ['functional'] * 4)  # SIM marker is skipped, not an entry
        self.assertEqual(entries[0]['message'], 'Mismatch at cycle 48: expected 1, got 0')

    def test_environment_and_license_kind(self):
        self.assertEqual(extract_entries('csim', ENV_LOG)[0]['kind'], 'environment')
        self.assertEqual(extract_entries('csim', LICENSE_LOG)[0]['kind'], 'license')

    def test_synthesis_synchk_kind(self):
        entry = extract_entries('synthesis', SYNTH_LOG)[0]
        self.assertEqual(entry['kind'], 'synthesis')
        self.assertEqual(entry['code'], 'SYNCHK 200-128')

    def test_category_mapping_unchanged(self):
        cases = [
            (COMPILER_LOG, 'compile_error'),
            ('undefined reference to `kernel\'', 'compile_error'),
            ('Generating csim.exe\n' + FUNCTIONAL_LOG, 'functional_or_runtime_error'),
            (ENV_LOG, 'environment_or_dependency_error'),
            (LICENSE_LOG, 'license_error'),
        ]
        for text, expected in cases:
            self.assertEqual(classify_category('csim', NO_FAILURE, text), expected, text)
        self.assertEqual(classify_category('synthesis', NO_FAILURE, 'anything'), 'synthesis_error')
        self.assertEqual(classify_category('csim', {'timed_out': True}, ''), 'tool_timeout')
        self.assertEqual(classify_category('csim', NO_FAILURE, 'some unrelated text'), 'compile_or_csim_error')

    def test_format_entries_releases_code_kinds_only(self):
        mixed = (extract_entries('csim', COMPILER_LOG)
                 + extract_entries('csim', FUNCTIONAL_LOG)
                 + extract_entries('csim', ENV_LOG))
        code_text = format_entries(mixed, RELEASABLE_CODE_KINDS)
        self.assertIn('new_ena(0) = ena_tens;', code_text)
        self.assertIn('^~~~~~~', code_text)
        self.assertIn('note: candidate function not viable', code_text)
        self.assertNotIn('Mismatch', code_text)
        self.assertNotIn('signal pipe', code_text)
        functional_text = format_entries(mixed, ('functional',))
        self.assertIn('Mismatch at cycle 48', functional_text)
        self.assertNotIn('new_ena(0)', functional_text)

    def test_extract_diagnostics_separates_tail_from_code_text(self):
        result = extract_diagnostics('csim', NO_FAILURE, COMPILER_LOG + '\n' + FUNCTIONAL_LOG)
        self.assertEqual(result['category'], 'compile_error')
        self.assertIn('new_ena(0) = ena_tens;', result['compiler_text'])
        self.assertIn('note: candidate function', result['compiler_text'])
        self.assertNotIn('Mismatch', result['compiler_text'])
        # The legacy tail still carries the functional oracle (evidence + public tier).
        self.assertIn('Mismatch', result['diagnostic_tail'])
        self.assertIn('no matching function', result['diagnostic_tail'])

    def test_diagnostic_tail_byte_identical(self):
        text = 'a\nERROR: bad\nb\nMismatch at cycle 1: expected 1, got 0\nc\n'
        result = extract_diagnostics('csim', NO_FAILURE, text)
        lines = text.splitlines()
        diagnostics = [line for line in lines if __import__('re').search(r"error:|ERROR:|FAIL|mismatch|fatal", line, __import__('re').IGNORECASE)]
        expected = ("\n".join(diagnostics[:30]) + "\n" + "\n".join(lines[-12:]))[-6000:]
        self.assertEqual(result['diagnostic_tail'], expected)


if __name__ == '__main__':
    unittest.main()
