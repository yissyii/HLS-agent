"""Synthetic extraction tests; no dataset, model service or HLS required."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from serve.code import extract_code
from serve.inference import Failure


def fence(source, language='cpp', marker='```'):
    return marker + language + '\n' + source + marker + '\n'


class CodeExtractionTests(unittest.TestCase):
    def select(self, text, **kwargs):
        details = {}
        source, method = extract_code(text, details=details, **kwargs)
        self.assertEqual(method, details['method'])
        return source, details

    def test_raw_source_preserved_including_inline_backticks(self):
        source = '  const char *s = "```cpp"; // preserve bytes\r\nint kernel() { return 1; }\r\n'
        self.assertEqual(extract_code(source), (source, 'verbatim'))

    def test_outer_fence_and_explanations(self):
        source = 'int kernel() { return 1; }\n'
        self.assertEqual(extract_code(fence(source)), (source, 'single_outer_fence_removed'))
        selected, details = self.select('实现如下：\n' + fence(source) + '\n该实现返回 1。')
        self.assertEqual(selected, source)
        self.assertEqual(details['method'], 'single_code_fence_extracted')

    def test_common_fence_styles_and_languages(self):
        source = 'int kernel() { return 1; }\n'
        for language in ('cpp', 'C++', 'c', 'cc', 'cxx', '', 'cpp title="kernel.cpp"'):
            for marker in ('```', '````', '~~~'):
                with self.subTest(language=language, marker=marker):
                    self.assertEqual(extract_code(fence(source, language, marker))[0], source)

    def test_crlf_source_and_indentation_preserved(self):
        source = '    int kernel() {\r\n        return 1;\r\n    }\r\n'
        self.assertEqual(extract_code('  ```cpp\r\n' + source + '  ```\r\n')[0], source)

    def test_implementation_beats_header_fragment_and_long_testbench(self):
        source = '#include <stdint.h>\nint kernel(int x) { return x+1; }\n'
        testbench = '#include <cassert>\nint main() {\n' + ' assert(kernel(1)==2);\n' * 30 + ' return 0;\n}\n'
        text = fence('int kernel(int x);\n') + fence('return x+1;\n') + fence(testbench) + fence(source)
        selected, details = self.select(text)
        self.assertEqual(selected, source)
        self.assertEqual(details['selected_block'], 3)
        self.assertEqual(details['method'], 'ranked_code_fence_selected')

    def test_known_top_beats_more_helper_functions(self):
        helpers = ''.join('int helper%d() { return %d; }\n' % (i, i) for i in range(8))
        source = 'void kernel(int &x) { ++x; }\n'
        selected, details = self.select(fence(helpers) + fence(source), top_function='kernel')
        self.assertEqual(selected, source)
        self.assertTrue(details['candidates'][1]['target_defined'])

    def test_top_declaration_or_call_is_not_a_definition(self):
        caller = 'int kernel(int);\nint helper() { return kernel(1); }\n'
        source = 'int kernel(int x) { return x+1; }\n'
        selected, details = self.select(fence(caller) + fence(source), top_function='kernel')
        self.assertEqual(selected, source)
        self.assertFalse(details['candidates'][0]['target_defined'])

    def test_balanced_complete_code_beats_broken_target(self):
        complete = 'int fallback() { return 0; }\n'
        selected, _ = self.select(fence('int kernel() { return 0;\n') + fence(complete), top_function='kernel')
        self.assertEqual(selected, complete)

    def test_comments_literals_and_raw_strings_do_not_fake_functions(self):
        source = ('// int main() { { {\nint kernel() {\n'
                  ' const char *s = "} main() {";\n'
                  ' const char *raw = R"tag( } int fake() { )tag";\n'
                  ' /* int other() { */ return 1;\n}\n')
        selected, details = self.select(fence(source), top_function='kernel')
        self.assertEqual(selected, source)
        self.assertEqual(details['candidates'][0]['functions'], ['kernel'])
        self.assertTrue(details['candidates'][0]['structurally_complete'])

    def test_comment_padding_and_ties_choose_first_without_merging(self):
        first = 'int first() { return 1; }\n'
        later = '// ' + 'padding ' * 1000 + '\nint later() { return 2; }\n'
        selected, details = self.select(fence(first) + fence(later))
        self.assertEqual(selected, first)
        self.assertEqual(details['tied_blocks'], [0, 1])
        self.assertEqual(details['tie_break'], 'first_in_response')

    def test_non_cpp_blocks_and_unlabeled_output_are_ignored(self):
        source = 'int kernel() { return 1; }\n'
        selected, details = self.select(fence('g++ kernel.cpp\n', 'bash') + fence('All tests passed\n', '') + fence(source))
        self.assertEqual(selected, source)
        self.assertEqual(details['candidates'][0]['excluded'], 'non_c_cpp_language')
        self.assertEqual(details['candidates'][1]['excluded'], 'unlabeled_non_source')

    def test_unclosed_later_block_does_not_hide_closed_implementation(self):
        source = 'int kernel() { return 1; }\n'
        selected, details = self.select(fence(source) + '另一种实现：\n```cpp\nint kernel() {')
        self.assertEqual(selected, source)
        self.assertEqual(details['candidates'][1]['excluded'], 'unclosed_fence')

    def test_no_usable_source_is_still_rejected(self):
        for text in ('', '  \n', '```cpp\nint kernel() {', fence('  \n'), fence('print(1)\n', 'python')):
            with self.subTest(text=text), self.assertRaises(Failure) as caught:
                extract_code(text)
            self.assertEqual(caught.exception.category, 'response_format_error')

    def test_longer_fence_can_contain_short_fence_in_comment(self):
        source = 'int kernel() { /*\n```\n*/ return 1; }\n'
        self.assertEqual(extract_code(fence(source, marker='````'))[0], source)

    def test_cpp_digit_separators_remain_complete(self):
        _, details = self.select(fence("int kernel() { return 1'000 + 0xA'B; }\n"))
        self.assertTrue(details['candidates'][0]['structurally_complete'])


if __name__ == '__main__':
    unittest.main()
