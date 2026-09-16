"""Deterministic extraction shared by generation and evaluation entries."""
import re
from serve.inference import Failure


def extract_code(text):
    stripped = text.strip()
    if not stripped:
        raise Failure('response_format_error', 'Empty source')
    if '```' not in stripped:
        return text, 'verbatim'
    match = re.fullmatch(r'```(?:cpp|c\+\+|c|cc)?\s*\n(.*?)\n```', stripped, re.DOTALL | re.I)
    if not match or '```' in match.group(1) or not match.group(1).strip():
        raise Failure('response_format_error', 'Ambiguous or empty Markdown response; no guessing')
    return match.group(1) + '\n', 'single_outer_fence_removed'
