"""Structured diagnostics: turn a Vitis log into kind-tagged entries.

Extraction and release are separate concerns. This module only turns a stage log
into kind-tagged entries; which kinds may reach the model is decided by
`feedback_policy` in `evaluation/validator.py`, never hard-coded here.

`functional` entries carry hidden-test oracle content (``Mismatch ... expected/got``)
and must stay out of the model prompt except under the ``public_diagnostics`` tier.
``environment`` / ``license`` / ``timeout`` signals are infrastructure noise, never
useful to a repair.
"""
import re

# Diagnostic kinds. `timeout` is detected from run metadata (see classify_category),
# not from a log line, so no line maps to it here.
KINDS = ('compiler', 'synthesis', 'functional', 'environment', 'license', 'timeout')
# Kinds whose entries may enter the prompt under the `compiler_diagnostics` tier.
RELEASABLE_CODE_KINDS = ('compiler', 'synthesis')

_LICENSE_SUBSTRINGS = (
    "license checkout failed", "failed to acquire license", "no valid license",
    "license check failed", "failed to check out license",
)
_ENVIRONMENT_SUBSTRINGS = (
    "error while loading shared libraries", "command not found", "no such file or directory",
    "permission denied", "couldn't create signal pipe", "win32 error 5", "access is denied",
)

# clang-style diagnostic: <path>:<line>:<col>: <severity>: <message>.
_CLANG_DIAG = re.compile(
    r'^(?P<location>\S.*?):(?P<line>\d+):(?P<col>\d+):\s+'
    r'(?P<severity>fatal error|error|warning|note):\s*(?P<message>.*)$')
# Vitis tool marker, e.g. ``ERROR: [SYNCHK 200-128] ...`` or ``ERROR: [HLS 214-124] ...``.
_VITIS_ERROR = re.compile(
    r'^\s*ERROR:\s*\[(?P<code>[A-Za-z]+\s+[0-9][0-9-]*)\]\s*(?P<message>.*)$', re.IGNORECASE)
# Functional run signals (hidden-test oracle and testbench failure markers).
_FUNCTIONAL = re.compile(r'^\s*(?:mismatch at cycle\b|test failed\b|@e simulation failed\b)', re.IGNORECASE)

_CARET_CHARS = set('^~ ')


def _is_caret(line):
    """True for a clang caret line such as ``        ^~~~~~~`` or ``  ^ ~~~~~  ~~~``."""
    stripped = line.strip()
    return bool(stripped) and set(stripped) <= _CARET_CHARS and ('^' in stripped or '~' in stripped)


def _entry(kind, *, location=None, message='', code=None, source=None, caret=None, notes=None):
    return {'kind': kind, 'code': code, 'location': location, 'message': message,
            'source': source, 'caret': caret, 'notes': notes or []}


def _contains_any(lowered, substrings):
    return any(value in lowered for value in substrings)


def classify_category(stage, execution, text):
    """Return the existing failure category string; migrated verbatim from hls.py.

    Category semantics are unchanged: ``compile_error`` / ``functional_or_runtime_error`` /
    ``synthesis_error`` / ``compile_or_csim_error`` / ``license_error`` /
    ``environment_or_dependency_error`` / ``tool_timeout``.
    """
    lowered = text.lower()
    if execution["timed_out"]:
        return "tool_timeout"
    if _contains_any(lowered, _LICENSE_SUBSTRINGS):
        return "license_error"
    if _contains_any(lowered, _ENVIRONMENT_SUBSTRINGS):
        return "environment_or_dependency_error"
    if stage == "synthesis":
        return "synthesis_error"
    compiler_error = re.search(r"^(?!\s*error:\s*\[).*\b(?:fatal )?error:|undefined reference", lowered, re.MULTILINE)
    if compiler_error:
        return "compile_error"
    if ("csim.exe" in lowered or "csim.out" in lowered) and any(value in lowered for value in ["linking", "running", "generating"]):
        return "functional_or_runtime_error"
    return "compile_or_csim_error"


def _compiler_block(lines, i, match, seen_notes):
    """Collect one compiler error and its following source, caret and note lines."""
    location = f"{match.group('location')}:{match.group('line')}:{match.group('col')}"
    entry = _entry('compiler', location=location, message=match.group('message'))
    i += 1
    n = len(lines)
    if i < n and not _is_caret(lines[i]) and not _CLANG_DIAG.match(lines[i]):
        entry['source'] = lines[i].rstrip()
        i += 1
    if i < n and _is_caret(lines[i]):
        entry['caret'] = lines[i].rstrip()
        i += 1
    notes = []
    while i < n:
        note = _CLANG_DIAG.match(lines[i])
        if not note or note.group('severity') != 'note':
            break
        message = note.group('message')
        if message not in seen_notes:  # The same note repeats under every sibling error.
            seen_notes.add(message)
            notes.append(message)
        i += 1
        if i < n and not _is_caret(lines[i]) and not _CLANG_DIAG.match(lines[i]):
            i += 1  # note source line
        if i < n and _is_caret(lines[i]):
            i += 1  # note caret line
    entry['notes'] = notes
    return entry, i


def extract_entries(stage, text):
    """Return kind-tagged diagnostic entries from a stage log.

    ``stage`` is reserved for stage-specific rules; current rules are stage-agnostic.
    Generic Vitis markers (``ERROR: [SIM ...]``, ``CSIM Failed``) are not root causes and
    are left out of the entries; they remain in ``diagnostic_tail``.
    """
    lines = text.splitlines()
    entries = []
    seen_notes = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        clang = _CLANG_DIAG.match(line)
        if clang and clang.group('severity') in ('error', 'fatal error'):
            entry, i = _compiler_block(lines, i, clang, seen_notes)
            entries.append(entry)
            continue
        vitis = _VITIS_ERROR.match(line)
        if vitis:
            if vitis.group('code').upper().split()[0] in ('SYNCHK', 'HLS'):
                entries.append(_entry('synthesis', code=vitis.group('code'), message=vitis.group('message')))
            i += 1
            continue
        if _FUNCTIONAL.search(line):
            entries.append(_entry('functional', message=line.strip()))
        elif _contains_any(line.lower(), _LICENSE_SUBSTRINGS):
            entries.append(_entry('license', message=line.strip()))
        elif _contains_any(line.lower(), _ENVIRONMENT_SUBSTRINGS):
            entries.append(_entry('environment', message=line.strip()))
        i += 1
    return entries


def _format_entry(entry):
    kind = entry['kind']
    if kind == 'compiler':
        lines = [f"{entry['location']}: error: {entry['message']}"]
        if entry.get('source'):
            lines.append(entry['source'])
        if entry.get('caret'):
            lines.append(entry['caret'])
        lines.extend('note: ' + note for note in entry.get('notes', []))
        return '\n'.join(lines)
    if kind == 'synthesis':
        code = f"[{entry['code']}] " if entry.get('code') else ''
        return f"ERROR: {code}{entry['message']}"
    return entry.get('message', '')


def format_entries(entries, kinds):
    """Render only the selected kinds into a single text block."""
    selected = set(kinds)
    blocks = [_format_entry(entry) for entry in entries if entry['kind'] in selected]
    return '\n\n'.join(block for block in blocks if block)


def _diagnostic_tail(text, limit=6000):
    """Byte-identical legacy tail: keyword lines plus the last 12 lines, capped."""
    lines = text.splitlines()
    diagnostics = [line for line in lines if re.search(r"error:|ERROR:|FAIL|mismatch|fatal", line, re.IGNORECASE)]
    return ("\n".join(diagnostics[:30]) + "\n" + "\n".join(lines[-12:]))[-limit:]


def extract_diagnostics(stage, execution, text):
    """Full extraction: category, structured entries, releasable code text and raw tail."""
    entries = extract_entries(stage, text)
    return {
        'category': classify_category(stage, execution, text),
        'entries': entries,
        'compiler_text': format_entries(entries, RELEASABLE_CODE_KINDS),
        'diagnostic_tail': _diagnostic_tail(text),
    }
