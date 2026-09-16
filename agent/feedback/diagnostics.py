"""Classify only the feedback explicitly released by the validator."""
import re
from agent.core.contracts import Diagnostic, digest

REPAIRABLE = {'compile_error', 'functional_or_runtime_error', 'synthesis_error', 'compile_or_csim_error'}


def classify(result):
    category = result.outcome.get('category', 'validation_error')
    text = result.feedback_text
    # Directory prefixes and line numbers change between candidate workspaces.
    normalized = re.sub(r'(?:[A-Za-z]:)?[^\s\"<>]*[\\/]', '<path>/', text)
    normalized = re.sub(r':\d+(?::\d+)?', ':<line>', normalized)
    normalized = re.sub(r'\b\d{4}-\d\d-\d\d[^\s]*', '<time>', normalized)
    normalized = ' '.join(normalized.split())
    location = re.search(r'[^\s]+\.(?:cpp|cc|c|h|hpp):\d+(?::\d+)?', text)
    return Diagnostic(category, result.stage, text,
                      digest((result.stage + '\n' + category + '\n' + normalized).encode()),
                      category in REPAIRABLE, location.group(0) if location else None)
